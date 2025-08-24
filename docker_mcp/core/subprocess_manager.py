"""Centralized subprocess management with proper resource handling."""

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from typing import Any, Optional

import structlog

from docker_mcp.core.exceptions import DockerCommandError

logger = structlog.get_logger()

DEFAULT_TIMEOUT = 30  # Default timeout in seconds
LONG_TIMEOUT = 300  # 5 minutes for long operations
KILL_TIMEOUT = 5  # Time to wait after SIGTERM before SIGKILL


class SubprocessManager:
    """Manages subprocess execution with proper resource cleanup."""
    
    def __init__(self):
        self._active_processes: set[asyncio.subprocess.Process] = set()
        self._cleanup_lock = asyncio.Lock()
    
    async def run_command(
        self,
        cmd: list[str],
        *,
        timeout: Optional[float] = None,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        stdin: Optional[str] = None,
    ) -> "SubprocessResult":
        """
        Run a command asynchronously with proper resource management.
        
        Args:
            cmd: Command and arguments as a list
            timeout: Timeout in seconds (default: DEFAULT_TIMEOUT)
            check: Raise exception if command fails
            capture_output: Capture stdout and stderr
            text: Return output as text instead of bytes
            cwd: Working directory for the command
            env: Environment variables
            stdin: Input to provide to the command
            
        Returns:
            SubprocessResult with returncode, stdout, and stderr
            
        Raises:
            DockerCommandError: If check=True and command fails
            asyncio.TimeoutError: If command times out
        """
        if timeout is None:
            timeout = DEFAULT_TIMEOUT
            
        # Log the command execution
        logger.debug(
            "Executing command",
            command=" ".join(cmd),
            timeout=timeout,
            cwd=cwd,
        )
        
        # Set up process creation arguments
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": env or os.environ.copy(),
        }
        
        if capture_output:
            kwargs["stdout"] = asyncio.subprocess.PIPE
            kwargs["stderr"] = asyncio.subprocess.PIPE
        
        if stdin is not None:
            kwargs["stdin"] = asyncio.subprocess.PIPE
        
        process = None
        try:
            # Create the subprocess
            process = await asyncio.create_subprocess_exec(
                *cmd,
                **kwargs
            )
            
            # Track the process
            async with self._cleanup_lock:
                self._active_processes.add(process)
            
            # Communicate with timeout
            try:
                if stdin is not None and text:
                    stdin_bytes = stdin.encode() if isinstance(stdin, str) else stdin
                else:
                    stdin_bytes = stdin if stdin is not None else None
                    
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(input=stdin_bytes),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Command timed out, terminating process",
                    command=" ".join(cmd),
                    timeout=timeout,
                    pid=process.pid,
                )
                
                # Try graceful termination first
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=KILL_TIMEOUT)
                except asyncio.TimeoutError:
                    # Force kill if graceful termination fails
                    logger.warning(
                        "Process did not terminate gracefully, sending SIGKILL",
                        pid=process.pid,
                    )
                    process.kill()
                    await process.wait()
                
                raise asyncio.TimeoutError(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")
            
            # Process output
            stdout = ""
            stderr = ""
            
            if capture_output:
                if text:
                    stdout = stdout_bytes.decode() if stdout_bytes else ""
                    stderr = stderr_bytes.decode() if stderr_bytes else ""
                else:
                    stdout = stdout_bytes if stdout_bytes else b""
                    stderr = stderr_bytes if stderr_bytes else b""
            
            # Create result
            result = SubprocessResult(
                returncode=process.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                cmd=cmd,
            )
            
            # Check for errors if requested
            if check and process.returncode != 0:
                error_msg = stderr.strip() if stderr else stdout.strip() if stdout else "Command failed"
                raise DockerCommandError(
                    f"Command failed with exit code {process.returncode}: {error_msg}"
                )
            
            return result
            
        finally:
            # Clean up the process from tracking
            if process is not None:
                async with self._cleanup_lock:
                    self._active_processes.discard(process)
                
                # Ensure process is fully terminated
                if process.returncode is None:
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=KILL_TIMEOUT)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                    except ProcessLookupError:
                        # Process already terminated
                        pass
    
    async def run_ssh_command(
        self,
        host_config: Any,  # Avoid circular import
        remote_cmd: str | list[str],
        *,
        timeout: Optional[float] = None,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
        stdin: Optional[str] = None,
    ) -> "SubprocessResult":
        """
        Run a command on a remote host via SSH.
        
        Args:
            host_config: Host configuration object with SSH details
            remote_cmd: Command to run on the remote host
            timeout: Timeout in seconds
            check: Raise exception if command fails
            capture_output: Capture stdout and stderr
            text: Return output as text instead of bytes
            stdin: Input to provide to the command
            
        Returns:
            SubprocessResult with command output
        """
        # Build SSH command
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        
        # Add port if not default
        if hasattr(host_config, "port") and host_config.port != 22:
            ssh_cmd.extend(["-p", str(host_config.port)])
        
        # Add identity file if specified
        if hasattr(host_config, "identity_file") and host_config.identity_file:
            ssh_cmd.extend(["-i", host_config.identity_file])
        
        # Add common SSH options for non-interactive use
        ssh_cmd.extend([
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
        ])
        
        ssh_cmd.append(f"{host_config.user}@{host_config.hostname}")
        
        # Add remote command
        if isinstance(remote_cmd, list):
            ssh_cmd.extend(remote_cmd)
        else:
            ssh_cmd.append(remote_cmd)
        
        # Execute via SSH
        return await self.run_command(
            ssh_cmd,
            timeout=timeout,
            check=check,
            capture_output=capture_output,
            text=text,
            stdin=stdin,
        )
    
    async def cleanup_all(self):
        """Cleanup all active processes."""
        async with self._cleanup_lock:
            processes = list(self._active_processes)
        
        if not processes:
            return
        
        logger.info(f"Cleaning up {len(processes)} active processes")
        
        # Terminate all processes
        for process in processes:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        
        # Wait for graceful termination
        await asyncio.sleep(KILL_TIMEOUT)
        
        # Force kill any remaining processes
        for process in processes:
            if process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:
                    pass
        
        # Clear the set
        async with self._cleanup_lock:
            self._active_processes.clear()


class SubprocessResult:
    """Result of a subprocess execution."""
    
    def __init__(
        self,
        returncode: int,
        stdout: str | bytes,
        stderr: str | bytes,
        cmd: list[str],
    ):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cmd = cmd
    
    @property
    def success(self) -> bool:
        """Check if the command succeeded."""
        return self.returncode == 0
    
    def check_returncode(self):
        """Raise an exception if the command failed."""
        if self.returncode != 0:
            error_msg = (
                self.stderr.strip() if self.stderr 
                else self.stdout.strip() if self.stdout 
                else "Command failed"
            )
            if isinstance(error_msg, bytes):
                error_msg = error_msg.decode()
            raise DockerCommandError(
                f"Command failed with exit code {self.returncode}: {error_msg}"
            )


# Global instance for convenience
_subprocess_manager = SubprocessManager()


async def run_command(*args, **kwargs) -> SubprocessResult:
    """Convenience function to run a command using the global subprocess manager."""
    return await _subprocess_manager.run_command(*args, **kwargs)


async def run_ssh_command(*args, **kwargs) -> SubprocessResult:
    """Convenience function to run an SSH command using the global subprocess manager."""
    return await _subprocess_manager.run_ssh_command(*args, **kwargs)


async def cleanup_all():
    """Cleanup all active subprocesses."""
    await _subprocess_manager.cleanup_all()


@asynccontextmanager
async def managed_subprocess():
    """Context manager for subprocess management with automatic cleanup."""
    manager = SubprocessManager()
    try:
        yield manager
    finally:
        await manager.cleanup_all()


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    loop = asyncio.get_event_loop()
    
    def handle_signal(signum, frame):
        logger.info(f"Received signal {signum}, cleaning up subprocesses")
        asyncio.create_task(cleanup_all())
        # Let the main program handle the actual shutdown
        if signum in (signal.SIGTERM, signal.SIGINT):
            sys.exit(0)
    
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
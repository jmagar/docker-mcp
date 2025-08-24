"""Tests for subprocess resource management."""

import asyncio
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docker_mcp.core.exceptions import DockerCommandError
from docker_mcp.core.subprocess_manager import (
    SubprocessManager,
    SubprocessResult,
    cleanup_all,
    managed_subprocess,
    run_command,
    run_ssh_command,
    setup_signal_handlers,
)


@pytest.fixture
async def subprocess_manager():
    """Create a subprocess manager for testing."""
    manager = SubprocessManager()
    yield manager
    # Cleanup any remaining processes
    await manager.cleanup_all()


@pytest.mark.asyncio
class TestSubprocessManager:
    """Test subprocess manager functionality."""
    
    async def test_run_simple_command(self, subprocess_manager):
        """Test running a simple command."""
        result = await subprocess_manager.run_command(["echo", "hello"])
        assert result.success
        assert result.stdout.strip() == "hello"
        assert result.stderr == ""
        assert result.returncode == 0
    
    async def test_run_command_with_error(self, subprocess_manager):
        """Test command that returns non-zero exit code."""
        with pytest.raises(DockerCommandError) as exc_info:
            await subprocess_manager.run_command(
                ["sh", "-c", "exit 1"],
                check=True
            )
        assert "exit code 1" in str(exc_info.value)
    
    async def test_run_command_no_check(self, subprocess_manager):
        """Test command with check=False doesn't raise on error."""
        result = await subprocess_manager.run_command(
            ["sh", "-c", "exit 1"],
            check=False
        )
        assert not result.success
        assert result.returncode == 1
    
    async def test_command_timeout(self, subprocess_manager):
        """Test command timeout handling."""
        with pytest.raises(asyncio.TimeoutError) as exc_info:
            await subprocess_manager.run_command(
                ["sleep", "10"],
                timeout=0.1
            )
        assert "timed out after 0.1 seconds" in str(exc_info.value)
    
    async def test_command_with_stdin(self, subprocess_manager):
        """Test providing input to command."""
        result = await subprocess_manager.run_command(
            ["cat"],
            stdin="test input",
            timeout=1
        )
        assert result.stdout == "test input"
    
    async def test_command_with_environment(self, subprocess_manager):
        """Test command with custom environment."""
        result = await subprocess_manager.run_command(
            ["sh", "-c", "echo $TEST_VAR"],
            env={"TEST_VAR": "test_value", "PATH": os.environ.get("PATH", "")}
        )
        assert result.stdout.strip() == "test_value"
    
    async def test_command_with_cwd(self, subprocess_manager):
        """Test command with working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await subprocess_manager.run_command(
                ["pwd"],
                cwd=tmpdir
            )
            assert result.stdout.strip() == tmpdir
    
    async def test_binary_output(self, subprocess_manager):
        """Test handling binary output."""
        result = await subprocess_manager.run_command(
            ["echo", "test"],
            text=False
        )
        assert isinstance(result.stdout, bytes)
        assert result.stdout == b"test\n"
    
    async def test_concurrent_commands(self, subprocess_manager):
        """Test running multiple commands concurrently."""
        tasks = [
            subprocess_manager.run_command(["echo", f"task{i}"])
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 5
        for i, result in enumerate(results):
            assert result.stdout.strip() == f"task{i}"
    
    async def test_cleanup_all_processes(self, subprocess_manager):
        """Test cleanup terminates all active processes."""
        # Start a long-running process
        task = asyncio.create_task(
            subprocess_manager.run_command(["sleep", "10"])
        )
        
        # Give it time to start
        await asyncio.sleep(0.1)
        
        # Should have one active process
        assert len(subprocess_manager._active_processes) == 1
        
        # Cleanup should terminate it
        await subprocess_manager.cleanup_all()
        assert len(subprocess_manager._active_processes) == 0
        
        # Task should be cancelled or timeout
        with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
            await task
    
    async def test_process_tracking(self, subprocess_manager):
        """Test that processes are properly tracked and cleaned up."""
        # Start multiple processes
        tasks = [
            asyncio.create_task(
                subprocess_manager.run_command(["sleep", "0.5"])
            )
            for _ in range(3)
        ]
        
        # Give them time to start
        await asyncio.sleep(0.1)
        
        # Should have 3 active processes
        assert len(subprocess_manager._active_processes) == 3
        
        # Wait for them to complete
        await asyncio.gather(*tasks)
        
        # Should have no active processes
        assert len(subprocess_manager._active_processes) == 0
    
    async def test_ssh_command(self, subprocess_manager):
        """Test SSH command building."""
        # Mock host configuration
        host_config = MagicMock()
        host_config.hostname = "example.com"
        host_config.user = "testuser"
        host_config.port = 2222
        host_config.identity_file = "/path/to/key"
        
        with patch.object(subprocess_manager, 'run_command') as mock_run:
            mock_run.return_value = SubprocessResult(
                returncode=0,
                stdout="test output",
                stderr="",
                cmd=[]
            )
            
            result = await subprocess_manager.run_ssh_command(
                host_config,
                "ls -la",
                timeout=30
            )
            
            # Verify SSH command was built correctly
            mock_run.assert_called_once()
            ssh_cmd = mock_run.call_args[0][0]
            
            assert ssh_cmd[0] == "ssh"
            assert "-p" in ssh_cmd
            assert "2222" in ssh_cmd
            assert "-i" in ssh_cmd
            assert "/path/to/key" in ssh_cmd
            assert "testuser@example.com" in ssh_cmd
            assert "ls -la" in ssh_cmd
    
    async def test_ssh_command_default_port(self, subprocess_manager):
        """Test SSH command with default port."""
        host_config = MagicMock()
        host_config.hostname = "example.com"
        host_config.user = "testuser"
        host_config.port = 22
        host_config.identity_file = None
        
        with patch.object(subprocess_manager, 'run_command') as mock_run:
            mock_run.return_value = SubprocessResult(
                returncode=0,
                stdout="test output",
                stderr="",
                cmd=[]
            )
            
            await subprocess_manager.run_ssh_command(
                host_config,
                ["docker", "ps"]
            )
            
            ssh_cmd = mock_run.call_args[0][0]
            
            # Should not include port flag for default port
            assert "-p" not in ssh_cmd
            assert "docker" in ssh_cmd
            assert "ps" in ssh_cmd


@pytest.mark.asyncio
class TestResourceLeaks:
    """Test for resource leaks and zombie processes."""
    
    async def test_no_zombie_processes(self):
        """Ensure terminated processes don't become zombies."""
        manager = SubprocessManager()
        
        # Create and terminate multiple processes
        for _ in range(10):
            try:
                await manager.run_command(
                    ["sleep", "10"],
                    timeout=0.1
                )
            except asyncio.TimeoutError:
                pass  # Expected
        
        # Cleanup
        await manager.cleanup_all()
        
        # Check for zombie processes (platform-specific)
        if sys.platform != "win32":
            result = await manager.run_command(
                ["sh", "-c", "ps aux | grep -c defunct || true"],
                check=False
            )
            zombie_count = int(result.stdout.strip())
            assert zombie_count == 0, f"Found {zombie_count} zombie processes"
    
    async def test_file_descriptor_cleanup(self):
        """Test that file descriptors are properly closed."""
        manager = SubprocessManager()
        
        # Get initial FD count
        if sys.platform != "win32":
            pid = os.getpid()
            fd_dir = Path(f"/proc/{pid}/fd")
            if fd_dir.exists():
                initial_fds = len(list(fd_dir.iterdir()))
                
                # Run many commands
                for _ in range(20):
                    result = await manager.run_command(["echo", "test"])
                    assert result.success
                
                # Cleanup and check FD count
                await manager.cleanup_all()
                await asyncio.sleep(0.1)  # Let OS clean up
                
                final_fds = len(list(fd_dir.iterdir()))
                # Allow small variance due to system operations
                assert final_fds <= initial_fds + 2, \
                    f"File descriptor leak: {initial_fds} -> {final_fds}"
    
    async def test_graceful_termination(self):
        """Test graceful process termination."""
        manager = SubprocessManager()
        
        # Start a process that handles SIGTERM
        script = '''
import signal
import time
import sys

def handler(signum, frame):
    print("Received SIGTERM")
    sys.exit(0)

signal.signal(signal.SIGTERM, handler)
time.sleep(10)
'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script)
            script_path = f.name
        
        try:
            task = asyncio.create_task(
                manager.run_command([sys.executable, script_path])
            )
            
            # Give it time to start
            await asyncio.sleep(0.1)
            
            # Cleanup should send SIGTERM first
            await manager.cleanup_all()
            
            try:
                await asyncio.wait_for(task, timeout=1)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass  # Expected
        finally:
            Path(script_path).unlink()
    
    async def test_force_kill_unresponsive(self):
        """Test force killing unresponsive processes."""
        manager = SubprocessManager()
        
        # Start a process that ignores SIGTERM
        script = '''
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(30)
'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script)
            script_path = f.name
        
        try:
            with pytest.raises(asyncio.TimeoutError):
                await manager.run_command(
                    [sys.executable, script_path],
                    timeout=0.5
                )
            
            # Should have no lingering processes
            await asyncio.sleep(0.5)
            assert len(manager._active_processes) == 0
        finally:
            Path(script_path).unlink()


@pytest.mark.asyncio
class TestContextManager:
    """Test context manager functionality."""
    
    async def test_managed_subprocess_context(self):
        """Test managed_subprocess context manager."""
        async with managed_subprocess() as manager:
            result = await manager.run_command(["echo", "test"])
            assert result.success
            
            # Start a long-running process
            task = asyncio.create_task(
                manager.run_command(["sleep", "10"])
            )
            
            await asyncio.sleep(0.1)
            assert len(manager._active_processes) > 0
        
        # After context exit, processes should be cleaned up
        assert len(manager._active_processes) == 0
        
        with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
            await task


@pytest.mark.asyncio
class TestGlobalFunctions:
    """Test global convenience functions."""
    
    async def test_global_run_command(self):
        """Test global run_command function."""
        result = await run_command(["echo", "global test"])
        assert result.success
        assert "global test" in result.stdout
    
    async def test_global_run_ssh_command(self):
        """Test global run_ssh_command function."""
        host_config = MagicMock()
        host_config.hostname = "example.com"
        host_config.user = "testuser"
        host_config.port = 22
        host_config.identity_file = None
        
        with patch('docker_mcp.core.subprocess_manager._subprocess_manager.run_command') as mock_run:
            mock_run.return_value = SubprocessResult(
                returncode=0,
                stdout="ssh test",
                stderr="",
                cmd=[]
            )
            
            result = await run_ssh_command(host_config, "ls")
            assert result.stdout == "ssh test"
    
    async def test_global_cleanup(self):
        """Test global cleanup_all function."""
        # Start a command using global function
        task = asyncio.create_task(
            run_command(["sleep", "10"])
        )
        
        await asyncio.sleep(0.1)
        
        # Global cleanup should terminate it
        await cleanup_all()
        
        with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
            await task


class TestSignalHandlers:
    """Test signal handler setup."""
    
    def test_setup_signal_handlers(self):
        """Test signal handler registration."""
        # Save original handlers
        original_sigterm = signal.signal(signal.SIGTERM, signal.SIG_DFL)
        original_sigint = signal.signal(signal.SIGINT, signal.SIG_DFL)
        
        try:
            setup_signal_handlers()
            
            # Check handlers are registered
            current_sigterm = signal.signal(signal.SIGTERM, signal.SIG_DFL)
            current_sigint = signal.signal(signal.SIGINT, signal.SIG_DFL)
            
            assert current_sigterm != signal.SIG_DFL
            assert current_sigint != signal.SIG_DFL
        finally:
            # Restore original handlers
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGINT, original_sigint)


class TestSubprocessResult:
    """Test SubprocessResult class."""
    
    def test_result_success(self):
        """Test successful result."""
        result = SubprocessResult(
            returncode=0,
            stdout="output",
            stderr="",
            cmd=["echo", "test"]
        )
        assert result.success
        assert result.stdout == "output"
    
    def test_result_failure(self):
        """Test failed result."""
        result = SubprocessResult(
            returncode=1,
            stdout="",
            stderr="error message",
            cmd=["false"]
        )
        assert not result.success
        assert result.stderr == "error message"
    
    def test_check_returncode_success(self):
        """Test check_returncode with success."""
        result = SubprocessResult(
            returncode=0,
            stdout="output",
            stderr="",
            cmd=["echo", "test"]
        )
        result.check_returncode()  # Should not raise
    
    def test_check_returncode_failure(self):
        """Test check_returncode with failure."""
        result = SubprocessResult(
            returncode=1,
            stdout="",
            stderr="error message",
            cmd=["false"]
        )
        
        with pytest.raises(DockerCommandError) as exc_info:
            result.check_returncode()
        
        assert "exit code 1" in str(exc_info.value)
        assert "error message" in str(exc_info.value)
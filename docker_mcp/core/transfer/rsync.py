"""Rsync transfer implementation for file synchronization between hosts."""

import asyncio
import re
import subprocess
from typing import Any

import structlog

from .base import BaseTransfer
from ..config_loader import DockerHost
from ..exceptions import DockerMCPError

logger = structlog.get_logger()


class RsyncError(DockerMCPError):
    """Rsync transfer operation failed."""
    pass


class RsyncTransfer(BaseTransfer):
    """Transfer files between hosts using rsync."""
    
    def __init__(self):
        super().__init__()
        self.logger = logger.bind(component="rsync_transfer")
    
    def get_transfer_type(self) -> str:
        """Get the name/type of this transfer method."""
        return "rsync"
    
    async def validate_requirements(self, host: DockerHost) -> tuple[bool, str]:
        """Validate that rsync is available on the host.
        
        Args:
            host: Host configuration to validate
            
        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        ssh_cmd = self.build_ssh_cmd(host)
        check_cmd = ssh_cmd + ["which rsync > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"]
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    check_cmd, check=False, capture_output=True, text=True
                ),
            )
            
            if "OK" in result.stdout:
                return True, ""
            else:
                return False, f"rsync not available on host {host.hostname}"
                
        except Exception as e:
            return False, f"Failed to check rsync availability: {str(e)}"
    
    async def transfer(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_path: str,
        target_path: str,
        compress: bool = True,
        delete: bool = False,
        dry_run: bool = False,
        **kwargs
    ) -> dict[str, Any]:
        """Transfer files between hosts using rsync.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_path: Path on source host
            target_path: Path on target host
            compress: Use compression during transfer
            delete: Delete files on target not in source
            dry_run: Perform dry run only
            **kwargs: Additional rsync options (ignored)
            
        Returns:
            Transfer result with statistics
        """
        # Build SSH command to connect to source host
        ssh_cmd = self.build_ssh_cmd(source_host)
        
        # Build rsync options
        rsync_opts = ["-avP"]
        if compress:
            rsync_opts.append("-z")
        if delete:
            rsync_opts.append("--delete")
        if dry_run:
            rsync_opts.append("--dry-run")
        
        # Build target URL for rsync running ON source host
        target_url = f"{target_host.user}@{target_host.hostname}:{target_path}"
        
        # Build rsync command that will run on the source host
        rsync_inner_cmd = f"rsync {' '.join(rsync_opts)} {source_path} {target_url}"
        
        # Handle SSH key for target host connection (nested SSH)
        if target_host.identity_file:
            rsync_inner_cmd = f"rsync {' '.join(rsync_opts)} -e 'ssh -i {target_host.identity_file}' {source_path} {target_url}"
        
        # Full command: SSH into source, then run rsync from there to target
        rsync_cmd = ssh_cmd + [rsync_inner_cmd]
        
        self.logger.info(
            "Starting rsync transfer",
            source=f"{source_host.hostname}:{source_path}",
            target=target_url,
            compress=compress,
            delete=delete,
            dry_run=dry_run,
        )
        
        # Execute rsync
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                rsync_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            raise RsyncError(f"Rsync failed: {result.stderr}")
        
        # Parse rsync output for statistics
        stats = self._parse_stats(result.stdout)
        
        return {
            "success": True,
            "transfer_type": "rsync",
            "source": f"{source_host.hostname}:{source_path}",
            "target": target_url,
            "stats": stats,
            "dry_run": dry_run,
            "output": result.stdout,
        }
    
    def _parse_stats(self, output: str) -> dict[str, Any]:
        """Parse rsync output for transfer statistics.
        
        Args:
            output: Rsync command output
            
        Returns:
            Dictionary with transfer statistics
        """
        stats = {
            "files_transferred": 0,
            "total_size": 0,
            "transfer_rate": "",
            "speedup": 1.0,
        }
        
        # Parse rsync summary statistics
        for line in output.split("\n"):
            if "Number of files transferred:" in line:
                match = re.search(r"(\d+)", line)
                if match:
                    stats["files_transferred"] = int(match.group(1))
            elif "Total transferred file size:" in line:
                match = re.search(r"([\d,]+) bytes", line)
                if match:
                    stats["total_size"] = int(match.group(1).replace(",", ""))
            elif "sent" in line and "received" in line:
                # Parse transfer rate from summary line
                match = re.search(r"(\d+\.?\d*) (\w+/sec)", line)
                if match:
                    stats["transfer_rate"] = f"{match.group(1)} {match.group(2)}"
            elif "speedup is" in line:
                match = re.search(r"speedup is (\d+\.?\d*)", line)
                if match:
                    stats["speedup"] = float(match.group(1))
        
        return stats
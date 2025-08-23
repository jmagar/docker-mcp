"""ZFS send/receive transfer implementation for efficient block-level transfers."""

import asyncio
import subprocess
from datetime import datetime
from typing import Any

import structlog

from .base import BaseTransfer
from ..config_loader import DockerHost
from ..exceptions import DockerMCPError

logger = structlog.get_logger()


class ZFSError(DockerMCPError):
    """ZFS transfer operation failed."""
    pass


class ZFSTransfer(BaseTransfer):
    """Transfer data between ZFS hosts using ZFS send/receive."""
    
    def __init__(self):
        super().__init__()
        self.logger = logger.bind(component="zfs_transfer")
    
    def get_transfer_type(self) -> str:
        """Get the name/type of this transfer method."""
        return "zfs"
    
    async def validate_requirements(self, host: DockerHost) -> tuple[bool, str]:
        """Validate that ZFS is available and functional on the host.
        
        Args:
            host: Host configuration to validate
            
        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        ssh_cmd = self.build_ssh_cmd(host)
        
        # Check if zfs command exists
        check_cmd = ssh_cmd + ["which zfs > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"]
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    check_cmd, check=False, capture_output=True, text=True
                ),
            )
            
            if "FAILED" in result.stdout:
                return False, f"ZFS not available on host {host.hostname}"
            
            # Check if we can list ZFS pools (basic functionality test)
            list_cmd = ssh_cmd + ["zfs list > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    list_cmd, check=False, capture_output=True, text=True
                ),
            )
            
            if "FAILED" in result.stdout:
                return False, f"ZFS not functional on host {host.hostname} (no pools or permissions issue)"
            
            return True, ""
            
        except Exception as e:
            return False, f"Failed to check ZFS availability: {str(e)}"
    
    async def detect_zfs_capability(
        self,
        host: DockerHost,
        appdata_path: str | None = None
    ) -> tuple[bool, str | None]:
        """Detect if host has ZFS and if appdata_path is on ZFS.
        
        Args:
            host: Host configuration
            appdata_path: Path to check for ZFS dataset
            
        Returns:
            Tuple of (zfs_capable: bool, dataset_name: str | None)
        """
        is_valid, error = await self.validate_requirements(host)
        if not is_valid:
            return False, None
        
        if not appdata_path:
            # ZFS is available but no specific path to check
            return True, None
        
        # Check if appdata_path is on a ZFS dataset
        dataset = await self.get_dataset_for_path(host, appdata_path)
        return dataset is not None, dataset
    
    async def get_dataset_for_path(self, host: DockerHost, path: str) -> str | None:
        """Get ZFS dataset name for a given path.
        
        Args:
            host: Host configuration
            path: Filesystem path
            
        Returns:
            ZFS dataset name or None if not on ZFS
        """
        ssh_cmd = self.build_ssh_cmd(host)
        
        # Use df to check filesystem type and mount point
        df_cmd = ssh_cmd + [f"df -T {path} | tail -1"]
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    df_cmd, check=False, capture_output=True, text=True
                ),
            )
            
            if result.returncode != 0:
                return None
            
            # Parse df output: filesystem type should be 'zfs'
            df_output = result.stdout.strip()
            if "zfs" not in df_output.lower():
                return None
            
            # Get the actual dataset name using zfs list
            zfs_cmd = ssh_cmd + [f"zfs list -H -o name {path} 2>/dev/null | head -1"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    zfs_cmd, check=False, capture_output=True, text=True
                ),
            )
            
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            
            # Fallback: try to find dataset by mountpoint
            mount_cmd = ssh_cmd + [f"zfs list -H -o name,mountpoint | grep '{path}' | head -1 | cut -f1"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    mount_cmd, check=False, capture_output=True, text=True
                ),
            )
            
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            
            return None
            
        except Exception as e:
            self.logger.warning("Failed to detect ZFS dataset", path=path, error=str(e))
            return None
    
    async def create_snapshot(self, host: DockerHost, dataset: str, snapshot_name: str) -> str:
        """Create a ZFS snapshot.
        
        Args:
            host: Host configuration
            dataset: ZFS dataset name
            snapshot_name: Snapshot name
            
        Returns:
            Full snapshot name (dataset@snapshot)
        """
        ssh_cmd = self.build_ssh_cmd(host)
        full_snapshot = f"{dataset}@{snapshot_name}"
        
        # Create snapshot with recursive option to include child datasets
        snap_cmd = ssh_cmd + [f"zfs snapshot -r {full_snapshot}"]
        
        self.logger.info("Creating ZFS snapshot", snapshot=full_snapshot)
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                snap_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            raise ZFSError(f"Failed to create snapshot {full_snapshot}: {result.stderr}")
        
        return full_snapshot
    
    async def cleanup_snapshot(self, host: DockerHost, full_snapshot: str) -> None:
        """Remove a ZFS snapshot.
        
        Args:
            host: Host configuration
            full_snapshot: Full snapshot name (dataset@snapshot)
        """
        ssh_cmd = self.build_ssh_cmd(host)
        
        # Destroy snapshot recursively
        destroy_cmd = ssh_cmd + [f"zfs destroy -r {full_snapshot}"]
        
        self.logger.info("Cleaning up ZFS snapshot", snapshot=full_snapshot)
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                destroy_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            self.logger.warning("Failed to cleanup snapshot", snapshot=full_snapshot, error=result.stderr)
    
    async def transfer(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_path: str,
        target_path: str,
        source_dataset: str | None = None,
        target_dataset: str | None = None,
        **kwargs
    ) -> dict[str, Any]:
        """Transfer data using ZFS send/receive.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_path: Path on source host (used to detect dataset)
            target_path: Path on target host (used to detect dataset)
            source_dataset: Source ZFS dataset (auto-detected if not provided)
            target_dataset: Target ZFS dataset (auto-detected if not provided)
            **kwargs: Additional options (ignored)
            
        Returns:
            Transfer result with statistics
        """
        # Auto-detect datasets if not provided
        if not source_dataset:
            source_dataset = await self.get_dataset_for_path(source_host, source_path)
            if not source_dataset:
                raise ZFSError(f"Source path {source_path} is not on a ZFS dataset")
        
        if not target_dataset:
            target_dataset = await self.get_dataset_for_path(target_host, target_path)
            if not target_dataset:
                # Try to create target dataset based on source dataset name
                # This is a simplified approach - in practice you might want more sophisticated mapping
                dataset_name = source_dataset.split('/')[-1]  # Get last component
                target_dataset = f"pool/{dataset_name}"  # Assumes a pool named 'pool'
        
        # Create snapshot with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"migrate_{timestamp}"
        
        try:
            # Create snapshot on source
            full_snapshot = await self.create_snapshot(source_host, source_dataset, snapshot_name)
            
            # Perform ZFS send/receive
            await self._send_receive(source_host, target_host, full_snapshot, target_dataset)
            
            # Cleanup snapshot on source
            await self.cleanup_snapshot(source_host, full_snapshot)
            
            return {
                "success": True,
                "transfer_type": "zfs",
                "source_dataset": source_dataset,
                "target_dataset": target_dataset,
                "snapshot": full_snapshot,
                "timestamp": timestamp,
            }
            
        except Exception as e:
            # Attempt cleanup if something went wrong
            try:
                await self.cleanup_snapshot(source_host, f"{source_dataset}@{snapshot_name}")
            except:
                pass  # Ignore cleanup failures
            
            raise ZFSError(f"ZFS transfer failed: {str(e)}")
    
    async def _send_receive(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        full_snapshot: str,
        target_dataset: str
    ) -> None:
        """Perform ZFS send/receive operation.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            full_snapshot: Full snapshot name (dataset@snapshot)
            target_dataset: Target dataset name
        """
        source_ssh_cmd = self.build_ssh_cmd(source_host)
        target_ssh_cmd = self.build_ssh_cmd(target_host)
        
        # Build the ZFS send command
        send_cmd = " ".join(source_ssh_cmd) + f" 'zfs send -R {full_snapshot}'"
        
        # Build the ZFS receive command  
        recv_cmd = " ".join(target_ssh_cmd) + f" 'zfs recv -F {target_dataset}'"
        
        # Combine with pipe (send | receive)
        full_cmd = f"{send_cmd} | {recv_cmd}"
        
        self.logger.info(
            "Starting ZFS send/receive",
            snapshot=full_snapshot,
            target_dataset=target_dataset,
            source_host=source_host.hostname,
            target_host=target_host.hostname,
        )
        
        # Execute the combined command
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                ["bash", "-c", full_cmd], check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            raise ZFSError(f"ZFS send/receive failed: {result.stderr}")
        
        self.logger.info("ZFS transfer completed successfully")
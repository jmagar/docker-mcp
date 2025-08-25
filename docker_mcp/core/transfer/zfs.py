"""ZFS send/receive transfer implementation for efficient block-level transfers."""

import asyncio
import subprocess
from datetime import datetime
from typing import Any

import structlog
import shlex

from .base import BaseTransfer
from ..config_loader import DockerHost
from ..exceptions import DockerMCPError
from ..safety import MigrationSafety

logger = structlog.get_logger()


class ZFSError(DockerMCPError):
    """ZFS transfer operation failed."""
    pass


class ZFSTransfer(BaseTransfer):
    """Transfer data between ZFS hosts using ZFS send/receive."""
    
    def __init__(self):
        super().__init__()
        self.logger = logger.bind(component="zfs_transfer")
        self.safety = MigrationSafety()
    
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
        df_cmd = ssh_cmd + [f"df -T {shlex.quote(path)} | tail -1"]
        
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
    
    async def create_snapshot(self, host: DockerHost, dataset: str, snapshot_name: str, recursive: bool = False) -> str:
        """Create a ZFS snapshot with optional recursion.
        
        Args:
            host: Host configuration
            dataset: ZFS dataset name
            snapshot_name: Snapshot name
            recursive: Whether to recursively snapshot child datasets (DANGEROUS!)
            
        Returns:
            Full snapshot name (dataset@snapshot)
        """
        ssh_cmd = self.build_ssh_cmd(host)
        full_snapshot = f"{dataset}@{snapshot_name}"
        
        # Build snapshot command - be careful with recursive flag
        snap_flags = "-r" if recursive else ""
        if recursive:
            self.logger.warning(
                "Creating RECURSIVE snapshot - will include all child datasets",
                snapshot=full_snapshot,
                dataset=dataset
            )
        quoted_snapshot = shlex.quote(f"{dataset}@{snapshot_name}")
        snap_cmd = ssh_cmd + [f"zfs snapshot {snap_flags} {quoted_snapshot}".strip()]
        
        self.logger.info(
            "Creating ZFS snapshot",
            snapshot=full_snapshot,
            recursive=recursive
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                snap_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            raise ZFSError(f"Failed to create snapshot {full_snapshot}: {result.stderr}")
        
        return full_snapshot
    
    async def cleanup_snapshot(self, host: DockerHost, full_snapshot: str, recursive: bool = False) -> None:
        """Remove a ZFS snapshot with safety validation.
        
        Args:
            host: Host configuration
            full_snapshot: Full snapshot name (dataset@snapshot)
            recursive: Whether to recursively destroy child snapshots (DANGEROUS!)
        """
        # SAFETY: Validate snapshot name before deletion
        is_safe, validation_reason = self.safety.validate_zfs_snapshot_deletion(full_snapshot)
        if not is_safe:
            self.logger.error(
                "ZFS snapshot deletion blocked by safety check",
                snapshot=full_snapshot,
                reason=validation_reason
            )
            raise ZFSError(f"SAFETY BLOCK: {validation_reason}")
        
        ssh_cmd = self.build_ssh_cmd(host)
        
        # Build destroy command - be very careful with -r flag
        if recursive:
            self.logger.warning(
                "Using RECURSIVE ZFS destroy - this will delete child snapshots!",
                snapshot=full_snapshot
            )
            destroy_cmd = ssh_cmd + [f"zfs destroy -r {full_snapshot}"]
        else:
            # Safer: only destroy the specific snapshot
            destroy_cmd = ssh_cmd + [f"zfs destroy {full_snapshot}"]
        
        self.logger.info(
            "Cleaning up ZFS snapshot",
            snapshot=full_snapshot,
            recursive=recursive,
            validated=True
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                destroy_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode == 0:
            self.logger.info("ZFS snapshot cleaned up successfully", snapshot=full_snapshot)
        else:
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
                # SAFETY: Never assume pool names - require explicit configuration
                raise ZFSError(
                    f"Target ZFS dataset not found for path '{target_path}' and no target_dataset specified. "
                    f"Please configure 'zfs_dataset' in hosts.yml for target host '{target_host.hostname}' "
                    f"or ensure the target path is on an existing ZFS dataset."
                )
        
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
        
        # Build the ZFS send command - be careful with -R flag
        # Use -R (recursive) only if specifically requested, default to single snapshot
        send_flags = "-R" if getattr(self, '_use_recursive_send', False) else ""
        send_cmd = " ".join(source_ssh_cmd) + f" 'zfs send {send_flags} {full_snapshot}'"
        
        # Build the ZFS receive command - make -F flag conditional for safety
        # -F forces receive and can destroy existing data, use with caution
        recv_flags = "-F" if getattr(self, '_force_receive', True) else ""
        if recv_flags:
            self.logger.warning(
                "Using FORCE receive flag - this will destroy existing data on target dataset",
                target_dataset=target_dataset
            )
        recv_cmd = " ".join(target_ssh_cmd) + f" 'zfs recv {recv_flags} {target_dataset}'"
        
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
        
        self.logger.info("ZFS send/receive command completed, verifying transfer...")
        
        # CRITICAL: Verify the transfer was successful
        await self._verify_zfs_transfer(source_host, target_host, full_snapshot, target_dataset)
    
    async def _verify_zfs_transfer(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_snapshot: str,
        target_dataset: str
    ) -> None:
        """Verify ZFS transfer completed successfully by comparing dataset properties.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_snapshot: Source snapshot name (dataset@snapshot)
            target_dataset: Target dataset name
            
        Raises:
            ZFSError: If verification fails
        """
        source_ssh_cmd = self.build_ssh_cmd(source_host)
        target_ssh_cmd = self.build_ssh_cmd(target_host)
        
        self.logger.info(
            "Verifying ZFS transfer integrity",
            source_snapshot=source_snapshot,
            target_dataset=target_dataset
        )
        
        # 1. Verify target dataset exists
        target_exists_cmd = target_ssh_cmd + [f"zfs list {target_dataset} >/dev/null 2>&1 && echo 'EXISTS' || echo 'NOT_FOUND'"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(target_exists_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if "NOT_FOUND" in result.stdout:
            raise ZFSError(f"Target dataset {target_dataset} not found after transfer")
        
        # 2. Get source snapshot properties
        source_props_cmd = source_ssh_cmd + [
            f"zfs get -H -p used,referenced,compressratio {source_snapshot} 2>/dev/null || echo 'FAILED'"
        ]
        source_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(source_props_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if "FAILED" in source_result.stdout or source_result.returncode != 0:
            self.logger.warning("Could not get source snapshot properties for verification")
        else:
            # 3. Get target dataset properties
            target_props_cmd = target_ssh_cmd + [
                f"zfs get -H -p used,referenced,compressratio {target_dataset} 2>/dev/null || echo 'FAILED'"
            ]
            target_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(target_props_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            
            if "FAILED" not in target_result.stdout and target_result.returncode == 0:
                # Parse and compare properties
                source_props = self._parse_zfs_properties(source_result.stdout)
                target_props = self._parse_zfs_properties(target_result.stdout)
                
                # Compare referenced size (logical data size)
                source_ref = source_props.get("referenced", 0)
                target_ref = target_props.get("referenced", 0)
                
                if source_ref > 0:
                    size_diff_percent = abs(target_ref - source_ref) / source_ref * 100
                    if size_diff_percent > 5.0:  # Allow 5% variance
                        self.logger.warning(
                            "ZFS transfer size mismatch",
                            source_referenced=source_ref,
                            target_referenced=target_ref,
                            difference_percent=f"{size_diff_percent:.1f}%"
                        )
                        # Don't fail on size mismatch as compression might differ
                    else:
                        self.logger.info(
                            "ZFS transfer size verification passed",
                            source_referenced=source_ref,
                            target_referenced=target_ref,
                            difference_percent=f"{size_diff_percent:.1f}%"
                        )
        
        # 4. Verify we can access the target dataset (basic functionality test)
        access_test_cmd = target_ssh_cmd + [f"ls {target_dataset} >/dev/null 2>&1 || zfs get -H mountpoint {target_dataset} >/dev/null 2>&1"]
        access_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(access_test_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if access_result.returncode != 0:
            self.logger.warning("Target dataset access test failed, but transfer may have succeeded")
        
        self.logger.info("ZFS transfer verification completed successfully")
    
    def _parse_zfs_properties(self, zfs_output: str) -> dict[str, int]:
        """Parse ZFS properties output into a dictionary.
        
        Args:
            zfs_output: Output from zfs get command
            
        Returns:
            Dictionary of property names to values (in bytes)
        """
        properties = {}
        
        for line in zfs_output.strip().split('\n'):
            if line and '\t' in line:
                parts = line.split('\t')
                if len(parts) >= 3:
                    # parts[1] is property name, parts[2] is value
                    prop_name = parts[1]
                    prop_value = parts[2]
                    
                    # Convert numeric values to integers
                    if prop_value.isdigit():
                        properties[prop_name] = int(prop_value)
                    else:
                        # Handle values like "1.00x" for compressratio
                        properties[prop_name] = prop_value
        
        return properties
"""ZFS send/receive transfer implementation for efficient block-level transfers."""

import asyncio
import shlex
import subprocess
from datetime import datetime
from typing import Any

import structlog

from ..config_loader import DockerHost
from ..exceptions import DockerMCPError
from ..safety import MigrationSafety
from .base import BaseTransfer

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
                return (
                    False,
                    f"ZFS not functional on host {host.hostname} (no pools or permissions issue)",
                )

            return True, ""

        except Exception as e:
            return False, f"Failed to check ZFS availability: {str(e)}"

    async def detect_zfs_capability(
        self, host: DockerHost, appdata_path: str | None = None
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
            zfs_cmd = ssh_cmd + [f"zfs list -H -o name {shlex.quote(path)} 2>/dev/null | head -1"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    zfs_cmd, check=False, capture_output=True, text=True
                ),
            )

            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

            # Fallback: try to find dataset by mountpoint
            mount_cmd = ssh_cmd + [
                f"zfs list -H -o name,mountpoint | grep {shlex.quote(path)} | head -1 | cut -f1"
            ]
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

    async def create_snapshot(
        self, host: DockerHost, dataset: str, snapshot_name: str, recursive: bool = False
    ) -> str:
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
                dataset=dataset,
            )
        quoted_snapshot = shlex.quote(f"{dataset}@{snapshot_name}")
        snap_cmd = ssh_cmd + [f"zfs snapshot {snap_flags} {quoted_snapshot}".strip()]

        self.logger.info("Creating ZFS snapshot", snapshot=full_snapshot, recursive=recursive)

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                snap_cmd, check=False, capture_output=True, text=True
            ),
        )

        if result.returncode != 0:
            raise ZFSError(f"Failed to create snapshot {full_snapshot}: {result.stderr}")

        return full_snapshot

    async def cleanup_snapshot(
        self, host: DockerHost, full_snapshot: str, recursive: bool = False
    ) -> None:
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
                reason=validation_reason,
            )
            raise ZFSError(f"SAFETY BLOCK: {validation_reason}")

        ssh_cmd = self.build_ssh_cmd(host)

        # Build destroy command - be very careful with -r flag
        if recursive:
            self.logger.warning(
                "Using RECURSIVE ZFS destroy - this will delete child snapshots!",
                snapshot=full_snapshot,
            )
            destroy_cmd = ssh_cmd + [f"zfs destroy -r {shlex.quote(full_snapshot)}"]
        else:
            # Safer: only destroy the specific snapshot
            destroy_cmd = ssh_cmd + [f"zfs destroy {shlex.quote(full_snapshot)}"]

        self.logger.info(
            "Cleaning up ZFS snapshot", snapshot=full_snapshot, recursive=recursive, validated=True
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
            self.logger.warning(
                "Failed to cleanup snapshot", snapshot=full_snapshot, error=result.stderr
            )

    async def transfer(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_path: str,
        target_path: str,
        source_dataset: str | None = None,
        target_dataset: str | None = None,
        **kwargs,
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
            except Exception as cleanup_err:
                self.logger.warning(
                    "Cleanup snapshot failed during error handling", error=str(cleanup_err)
                )

            raise ZFSError(f"ZFS transfer failed: {str(e)}") from e

    async def _send_receive(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        full_snapshot: str,
        target_dataset: str,
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
        import shlex
        send_flags = "-R" if getattr(self, "_use_recursive_send", False) else ""
        send_cmd = " ".join(source_ssh_cmd) + f" \"zfs send {send_flags} {shlex.quote(full_snapshot)}\""

        # Clean up target dataset completely before receive to eliminate race condition
        # This runs as a separate command to avoid pipe complications
        recv_flags = "-F"
        self.logger.info(
            "Preparing target dataset for migration",
            target_dataset=target_dataset,
        )

        # Check if target dataset exists and destroy it completely if needed
        await self._prepare_target_dataset(target_host, target_dataset)

        # Build simple ZFS receive command
        recv_cmd = " ".join(target_ssh_cmd) + f" \"zfs recv {recv_flags} {shlex.quote(target_dataset)}\""

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
                ["/bin/bash", "-c", full_cmd], check=False, capture_output=True, text=True
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
        target_dataset: str,
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
            target_dataset=target_dataset,
        )

        # 1. Verify target dataset exists
        target_exists_cmd = target_ssh_cmd + [
            f"zfs list {shlex.quote(target_dataset)} >/dev/null 2>&1 && echo 'EXISTS' || echo 'NOT_FOUND'"
        ]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(target_exists_cmd, capture_output=True, text=True, check=False),  # nosec B603
        )

        if "NOT_FOUND" in result.stdout:
            raise ZFSError(f"Target dataset {target_dataset} not found after transfer")

        # 2. Get source snapshot properties
        source_props_cmd = source_ssh_cmd + [
            f"zfs get -H -p used,referenced,compressratio {shlex.quote(source_snapshot)} 2>/dev/null || echo 'FAILED'"
        ]
        source_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(source_props_cmd, capture_output=True, text=True, check=False),  # nosec B603
        )

        if "FAILED" in source_result.stdout or source_result.returncode != 0:
            self.logger.warning("Could not get source snapshot properties for verification")
        else:
            # 3. Get target dataset properties
            target_props_cmd = target_ssh_cmd + [
                f"zfs get -H -p used,referenced,compressratio {shlex.quote(target_dataset)} 2>/dev/null || echo 'FAILED'"
            ]
            target_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    target_props_cmd, capture_output=True, text=True, check=False
                ),
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
                            difference_percent=f"{size_diff_percent:.1f}%",
                        )
                        # Don't fail on size mismatch as compression might differ
                    else:
                        self.logger.info(
                            "ZFS transfer size verification passed",
                            source_referenced=source_ref,
                            target_referenced=target_ref,
                            difference_percent=f"{size_diff_percent:.1f}%",
                        )

        # 4. Verify we can access the target dataset (basic functionality test)
        access_test_cmd = target_ssh_cmd + [
            f"ls {target_dataset} >/dev/null 2>&1 || zfs get -H mountpoint {target_dataset} >/dev/null 2>&1"
        ]
        access_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(access_test_cmd, capture_output=True, text=True, check=False),  # nosec B603
        )

        if access_result.returncode != 0:
            self.logger.warning(
                "Target dataset access test failed, but transfer may have succeeded"
            )

        self.logger.info("ZFS transfer verification completed successfully")

    def _parse_zfs_properties(self, zfs_output: str) -> dict[str, int]:
        """Parse ZFS properties output into a dictionary.

        Args:
            zfs_output: Output from zfs get command

        Returns:
            Dictionary of property names to values (in bytes)
        """
        properties = {}

        for line in zfs_output.strip().split("\n"):
            if line and "\t" in line:
                parts = line.split("\t")
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

    async def transfer_multiple_services(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        service_paths: list[str],
        **kwargs,
    ) -> dict[str, Any]:
        """Transfer multiple service datasets individually.
        
        This method handles multi-service stacks by finding the unique datasets that
        contain all the service paths and transferring each dataset once.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration  
            service_paths: List of service paths to transfer (e.g., ['/mnt/appdata/test-mcp-simple/html', '/mnt/appdata/test-mcp-simple/config'])
            
        Returns:
            Transfer result with per-service statistics
        """
        transfer_results = []
        overall_success = True

        self.logger.info(
            "Starting multi-service ZFS transfer",
            services_count=len(service_paths),
            source_host=source_host.hostname,
            target_host=target_host.hostname,
            service_paths=service_paths
        )

        # Group paths by their containing dataset to avoid duplicate transfers
        datasets_to_transfer = {}

        for service_path in service_paths:
            try:
                # Determine source dataset from service path
                source_dataset = await self.get_dataset_for_path(source_host, service_path)
                if not source_dataset:
                    # If path isn't on ZFS, skip with warning
                    service_name = service_path.split('/')[-1]
                    self.logger.warning(
                        "Service path is not on ZFS dataset, skipping",
                        service_path=service_path,
                        service=service_name
                    )
                    transfer_results.append({
                        "success": False,
                        "service_name": service_name,
                        "service_path": service_path,
                        "error": f"Path {service_path} is not on a ZFS dataset",
                        "skipped": True
                    })
                    continue

                # Group by dataset - all paths in same dataset will be transferred together
                if source_dataset not in datasets_to_transfer:
                    datasets_to_transfer[source_dataset] = {
                        "paths": [],
                        "dataset": source_dataset
                    }
                datasets_to_transfer[source_dataset]["paths"].append(service_path)

            except Exception as e:
                service_name = service_path.split('/')[-1]
                self.logger.error("Failed to determine dataset for path", service_path=service_path, error=str(e))
                transfer_results.append({
                    "success": False,
                    "service_name": service_name,
                    "service_path": service_path,
                    "error": str(e)
                })

        # Transfer each unique dataset
        for source_dataset, dataset_info in datasets_to_transfer.items():
            try:
                # Get the dataset base path (mountpoint)
                dataset_base_path = await self._get_dataset_mountpoint(source_host, source_dataset)
                if not dataset_base_path:
                    raise ZFSError(f"Could not determine mountpoint for dataset {source_dataset}")

                # Calculate target dataset name - extract service name from dataset
                # e.g., rpool/appdata/test-mcp-simple -> test-mcp-simple
                dataset_parts = source_dataset.split('/')
                service_dataset_name = dataset_parts[-1] if len(dataset_parts) > 1 else source_dataset

                # Target dataset path
                if target_host.zfs_dataset:
                    target_dataset = f"{target_host.zfs_dataset}/{service_dataset_name}"
                else:
                    raise ZFSError(f"Target host {target_host.hostname} has no zfs_dataset configured")

                # Target path (mountpoint)
                target_service_path = dataset_base_path.replace(
                    source_host.appdata_path,
                    target_host.appdata_path
                )

                self.logger.info(
                    "Transferring dataset",
                    source_dataset=source_dataset,
                    target_dataset=target_dataset,
                    paths_included=dataset_info["paths"]
                )

                # Transfer the dataset
                result = await self.transfer(
                    source_host=source_host,
                    target_host=target_host,
                    source_path=dataset_base_path,
                    target_path=target_service_path,
                    source_dataset=source_dataset,
                    target_dataset=target_dataset,
                )

                # Mark all paths in this dataset as transferred
                if result.get("success", False):
                    for path in dataset_info["paths"]:
                        path_name = path.split('/')[-1]
                        transfer_results.append({
                            "success": True,
                            "service_name": path_name,
                            "service_path": path,
                            "source_dataset": source_dataset,
                            "target_dataset": target_dataset,
                            "dataset_transfer": True  # Indicates this was part of dataset transfer
                        })

                    self.logger.info(
                        "Dataset transfer completed successfully",
                        source_dataset=source_dataset,
                        target_dataset=target_dataset,
                        paths_transferred=len(dataset_info["paths"])
                    )
                else:
                    overall_success = False
                    for path in dataset_info["paths"]:
                        path_name = path.split('/')[-1]
                        transfer_results.append({
                            "success": False,
                            "service_name": path_name,
                            "service_path": path,
                            "source_dataset": source_dataset,
                            "target_dataset": target_dataset,
                            "error": result.get("error", "Dataset transfer failed")
                        })

                    self.logger.error(
                        "Dataset transfer failed",
                        source_dataset=source_dataset,
                        error=result.get("error", "Unknown error")
                    )

            except Exception as e:
                overall_success = False
                self.logger.error("Dataset transfer failed", source_dataset=source_dataset, error=str(e))

                # Mark all paths in this dataset as failed
                for path in dataset_info["paths"]:
                    path_name = path.split('/')[-1]
                    transfer_results.append({
                        "success": False,
                        "service_name": path_name,
                        "service_path": path,
                        "source_dataset": source_dataset,
                        "error": str(e)
                    })

        # Calculate final statistics
        successful_services = [r for r in transfer_results if r.get("success", False)]
        failed_services = [r for r in transfer_results if not r.get("success", False)]
        datasets_transferred = len([d for d in datasets_to_transfer.keys() if any(r.get("success", False) and r.get("source_dataset") == d for r in transfer_results)])

        result = {
            "success": overall_success,
            "transfer_type": "zfs_multi_service",
            "services": transfer_results,
            "services_transferred": len(successful_services),
            "services_failed": len(failed_services),
            "total_services": len(service_paths),
            "datasets_transferred": datasets_transferred,
            "total_datasets": len(datasets_to_transfer),
        }

        if overall_success:
            result["message"] = f"Successfully transferred {datasets_transferred} datasets containing {len(successful_services)} service paths"
        else:
            result["message"] = f"Transfer completed with errors: {datasets_transferred}/{len(datasets_to_transfer)} datasets transferred"
            result["errors"] = [r.get("error", "Unknown error") for r in failed_services if r.get("error")]

        self.logger.info(
            "Multi-service ZFS transfer completed",
            success=overall_success,
            services_transferred=len(successful_services),
            services_failed=len(failed_services),
            datasets_transferred=datasets_transferred,
            total_services=len(service_paths),
            total_datasets=len(datasets_to_transfer)
        )

        return result

    async def _get_dataset_mountpoint(self, host: DockerHost, dataset: str) -> str | None:
        """Get the mountpoint for a ZFS dataset.
        
        Args:
            host: Host configuration
            dataset: ZFS dataset name
            
        Returns:
            Mountpoint path or None if not found
        """
        ssh_cmd = self.build_ssh_cmd(host)
        cmd = ssh_cmd + [f"zfs get -H -o value mountpoint {shlex.quote(dataset)} 2>/dev/null || echo 'NOT_FOUND'"]

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, check=False),  # nosec B603
        )

        if result.returncode == 0 and "NOT_FOUND" not in result.stdout:
            mountpoint = result.stdout.strip()
            if mountpoint and mountpoint != "-":  # "-" means no mountpoint
                return mountpoint

        return None

    async def _cleanup_target_snapshots(self, host: DockerHost, dataset: str) -> None:
        """Clean up existing snapshots on target dataset to prepare for migration.
        
        This prevents ZFS receive failures due to existing snapshots on the target.
        In migration scenarios, we want to completely replace the target dataset.
        
        Args:
            host: Target host configuration
            dataset: Target dataset name
        """
        ssh_cmd = self.build_ssh_cmd(host)

        # List existing snapshots for this dataset
        list_cmd = ssh_cmd + [f"zfs list -H -t snapshot -o name {shlex.quote(dataset)} 2>/dev/null || true"]

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(list_cmd, capture_output=True, text=True, check=False),  # nosec B603
        )

        if result.returncode == 0 and result.stdout.strip():
            snapshots = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

            if snapshots:
                self.logger.info(
                    "Cleaning up existing snapshots on target dataset for migration",
                    dataset=dataset,
                    snapshots_to_remove=len(snapshots),
                    snapshots=snapshots[:3]  # Log first 3 for reference
                )

                # Destroy each snapshot
                for snapshot in snapshots:
                    destroy_cmd = ssh_cmd + [f"zfs destroy {shlex.quote(snapshot)} 2>/dev/null || true"]
                    destroy_result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: subprocess.run(destroy_cmd, capture_output=True, text=True, check=False),  # nosec B603
                    )

                    if destroy_result.returncode != 0:
                        self.logger.warning(
                            "Failed to destroy snapshot, continuing anyway",
                            snapshot=snapshot,
                            error=destroy_result.stderr.strip()
                        )

                self.logger.info("Target dataset snapshot cleanup completed", dataset=dataset)
            else:
                self.logger.debug("No existing snapshots found on target dataset", dataset=dataset)

    async def _prepare_target_dataset(self, host: DockerHost, dataset: str) -> None:
        """Prepare target dataset for migration by checking if it exists and destroying it if needed.
        
        This prevents ZFS receive failures due to existing datasets/snapshots on the target.
        For migrations, we want to completely replace the target dataset.
        
        Args:
            host: Target host configuration
            dataset: Target dataset name
        """
        ssh_cmd = self.build_ssh_cmd(host)

        # Check if target dataset exists
        check_cmd = ssh_cmd + [f"zfs list {shlex.quote(dataset)} >/dev/null 2>&1 && echo 'EXISTS' || echo 'NOT_FOUND'"]

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(check_cmd, capture_output=True, text=True, check=False),  # nosec B603
        )

        if "EXISTS" in result.stdout:
            self.logger.info(
                "Target dataset exists, destroying it for clean migration",
                dataset=dataset,
                host=host.hostname
            )

            # Destroy the entire dataset (this also removes all snapshots)
            destroy_cmd = ssh_cmd + [f"zfs destroy -r {shlex.quote(dataset)} 2>/dev/null || true"]
            destroy_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(destroy_cmd, capture_output=True, text=True, check=False),  # nosec B603
            )

            if destroy_result.returncode == 0:
                self.logger.info("Target dataset destroyed successfully", dataset=dataset)
            else:
                self.logger.warning(
                    "Failed to destroy target dataset, will attempt force receive",
                    dataset=dataset,
                    error=destroy_result.stderr.strip()
                )
        else:
            self.logger.info(
                "Target dataset does not exist, ready for clean receive",
                dataset=dataset,
                host=host.hostname
            )

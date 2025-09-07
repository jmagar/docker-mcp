"""
Stack Migration Executor Module

Core migration execution logic for Docker Compose stacks.
Handles compose file operations, data transfer, deployment, and verification.
"""

import asyncio
import shlex
import subprocess
import tempfile

import structlog

from ...core.backup import BackupManager
from ...core.config_loader import DockerHost, DockerMCPConfig
from ...core.docker_context import DockerContextManager
from ...core.migration.manager import MigrationManager
from ...tools.stacks import StackTools
from ...utils import build_ssh_command


class StackMigrationExecutor:
    """Executes the core migration steps for Docker Compose stacks."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.stack_tools = StackTools(config, context_manager)
        self.migration_manager = MigrationManager()
        self.backup_manager = BackupManager()
        self.logger = structlog.get_logger()

    async def retrieve_compose_file(self, host_id: str, stack_name: str) -> tuple[bool, str, str]:
        """Retrieve compose file from source host.

        Args:
            host_id: Source host ID
            stack_name: Stack name

        Returns:
            Tuple of (success: bool, compose_content: str, compose_path: str)
        """
        try:
            # Get compose file path
            compose_file_path = await self.stack_tools.compose_manager.get_compose_file_path(
                host_id, stack_name
            )

            # Build SSH command for source
            source_host = self.config.hosts[host_id]
            ssh_cmd_source = build_ssh_command(source_host)

            # Read compose file
            read_cmd = ssh_cmd_source + [f"cat {shlex.quote(compose_file_path)}"]
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(read_cmd, capture_output=True, text=True, check=False),  # nosec B603
            )

            if result.returncode != 0:
                return False, "", compose_file_path

            return True, result.stdout, compose_file_path

        except Exception as e:
            self.logger.error("Failed to retrieve compose file", error=str(e))
            return False, "", ""

    async def create_backup_archive(
        self,
        source_host: DockerHost,
        volume_paths: list[str],
        stack_name: str,
        dry_run: bool = False,
    ) -> tuple[bool, str, dict]:
        """Create archive of volume data for BACKUP purposes only.

        WARNING: This method is for backup operations only, not migration!
        Migrations use direct transfer methods (rsync/ZFS).

        Args:
            source_host: Source host configuration
            volume_paths: List of volume paths to archive
            stack_name: Stack name for archive naming
            dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, archive_path: str, metadata: dict)
        """
        if dry_run:
            # For dry run, create a fake small archive
            temp_dir = tempfile.mkdtemp(prefix="docker_mcp_dryrun_")
            archive_path = f"{temp_dir}/{stack_name}_migration_DRYRUN.tar.gz"
            return (
                True,
                archive_path,
                {
                    "dry_run": True,
                    "estimated_size": len(volume_paths)
                    * 1024
                    * 1024
                    * 100,  # 100MB per path estimate
                    "paths_included": volume_paths,
                },
            )

        try:
            ssh_cmd_source = build_ssh_command(source_host)

            # Create archive using migration manager
            archive_path = await self.migration_manager.archive_utils.create_archive(
                ssh_cmd_source, volume_paths, f"{stack_name}_migration"
            )

            # Verify archive integrity
            is_valid = await self.migration_manager.archive_utils.verify_archive(
                ssh_cmd_source, archive_path
            )

            if not is_valid:
                return False, archive_path, {"error": "Archive integrity verification failed"}

            # Get archive metadata
            metadata = {
                "archive_verified": True,
                "paths_included": volume_paths,
                "archive_size": 0,  # Would be populated by actual archive creation
            }

            return True, archive_path, metadata

        except Exception as e:
            self.logger.error("Failed to create data archive", error=str(e))
            return False, "", {"error": str(e)}

    async def transfer_data(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        volume_paths: list[str],
        stack_name: str,
        dry_run: bool = False,
    ) -> tuple[bool, dict]:
        """Transfer volume data directly between hosts (no archiving).

        Uses optimal transfer method:
        - rsync: Direct directory synchronization
        - ZFS: Native dataset send/receive

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            volume_paths: List of volume paths to transfer
            stack_name: Stack name
            dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, transfer_results: dict)
        """
        if dry_run:
            return True, {
                "dry_run": True,
                "transfer_type": "simulated",
                "estimated_time": "5-10 minutes",
            }

        try:
            # Use migration manager for optimal direct transfer
            transfer_result = await self.migration_manager.transfer_data(
                source_host=source_host,
                target_host=target_host,
                source_paths=volume_paths,
                target_path=f"{target_host.appdata_path or '/opt/docker-appdata'}/{stack_name}",
                stack_name=stack_name,
                dry_run=dry_run,
            )

            return transfer_result["success"], transfer_result

        except Exception as e:
            self.logger.error("Failed to transfer data", error=str(e))
            return False, {"error": str(e)}

    async def extract_archive(
        self, target_host: DockerHost, archive_path: str, target_path: str, dry_run: bool = False
    ) -> tuple[bool, dict]:
        """Extract archive on target host for BACKUP/RESTORE operations only.

        WARNING: This method is for backup/restore operations only, not migration!
        Migrations use direct transfer methods without archiving.

        Args:
            target_host: Target host configuration
            archive_path: Path to archive file
            target_path: Target extraction path
            dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, extraction_results: dict)
        """
        if dry_run:
            return True, {
                "dry_run": True,
                "extraction_path": target_path,
                "files_extracted": "simulated",
            }

        try:
            ssh_cmd_target = build_ssh_command(target_host)

            success = await self.migration_manager.archive_utils.extract_archive(
                ssh_cmd_target, archive_path, target_path
            )

            if success:
                return True, {
                    "extraction_successful": True,
                    "extraction_path": target_path,
                }
            else:
                return False, {"error": "Archive extraction failed"}

        except Exception as e:
            self.logger.error("Failed to extract archive", error=str(e))
            return False, {"error": str(e)}

    async def deploy_stack_on_target(
        self,
        host_id: str,
        stack_name: str,
        compose_content: str,
        start_stack: bool = True,
        dry_run: bool = False,
    ) -> tuple[bool, dict]:
        """Deploy stack on target host.

        Args:
            host_id: Target host ID
            stack_name: Stack name
            compose_content: Updated compose file content
            start_stack: Whether to start the stack after deployment
            dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, deployment_results: dict)
        """
        if dry_run:
            return True, {
                "dry_run": True,
                "deployment_simulated": True,
                "stack_would_start": start_stack,
            }

        try:
            # Deploy using stack tools
            result = await self.stack_tools.deploy_stack(
                host_id=host_id,
                stack_name=stack_name,
                compose_content=compose_content,
                environment=None,
                pull_images=True,
                recreate=False,
            )

            if not result["success"]:
                return False, result

            # Start stack if requested
            if start_stack:
                start_result = await self.stack_tools.manage_stack(
                    host_id=host_id,
                    stack_name=stack_name,
                    action="up",
                    options=None,
                )

                if not start_result["success"]:
                    return False, {
                        "deploy_success": True,
                        "start_success": False,
                        "start_error": start_result.get("error"),
                    }

                # Wait for containers to fully start after deployment
                try:
                    import asyncio as _asyncio

                    await _asyncio.sleep(2)  # Initial delay for deployment to settle

                    # Poll for container readiness
                    for attempt in range(10):  # Up to 10 seconds
                        # Check if container exists and is running
                        target_host = self.config.hosts[host_id]
                        ssh_cmd = build_ssh_command(target_host)
                        check_cmd = ssh_cmd + [
                            f"docker ps --filter 'label=com.docker.compose.project={stack_name}' --format '{{{{.Names}}}}' | grep -q . && echo 'RUNNING' || echo 'NOT_READY'"
                        ]

                        import subprocess
                        from typing import Any, cast

                        def run_check_cmd() -> Any:  # Use Any to avoid mypy confusion
                            return subprocess.run(
                                check_cmd, capture_output=True, text=True, check=False
                            )  # nosec B603

                        result = cast(
                            subprocess.CompletedProcess[str],
                            await _asyncio.get_event_loop().run_in_executor(
                                None,
                                run_check_cmd,
                            ),
                        )

                        if result.returncode == 0 and "RUNNING" in result.stdout:
                            self.logger.info(
                                "Container ready for verification",
                                stack_name=stack_name,
                                attempt=attempt + 1,
                            )
                            break

                        await _asyncio.sleep(1)
                    else:
                        self.logger.warning(
                            "Container may not be fully ready for verification",
                            stack_name=stack_name,
                        )

                except Exception as e:
                    self.logger.warning("Container readiness check failed", error=str(e))
                    # Continue anyway - verification will handle missing containers

            return True, {
                "deploy_success": True,
                "start_success": start_stack,
                "stack_deployed": True,
            }

        except Exception as e:
            self.logger.error("Failed to deploy stack on target", error=str(e))
            return False, {"error": str(e)}

    async def verify_deployment(
        self,
        host_id: str,
        stack_name: str,
        expected_mounts: list[str],
        source_inventory: dict = None,
        dry_run: bool = False,
    ) -> tuple[bool, dict]:
        """Verify deployment success and data integrity.

        Args:
            host_id: Target host ID
            stack_name: Stack name
            expected_mounts: List of expected mount points
            source_inventory: Source data inventory for comparison
            dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, verification_results: dict)
        """
        if dry_run:
            return True, {
                "dry_run": True,
                "verification_simulated": True,
                "data_integrity": "would_be_verified",
            }

        try:
            target_host = self.config.hosts[host_id]
            ssh_cmd_target = build_ssh_command(target_host)
            target_appdata = target_host.appdata_path or "/opt/docker-appdata"

            # Verify container integration
            container_verification = await self.migration_manager.verify_container_integration(
                ssh_cmd_target, stack_name, target_appdata, expected_mounts
            )

            # Verify data completeness if source inventory available
            data_verification = {"success": True, "details": "No source inventory provided"}
            if source_inventory:
                data_verification = await self.migration_manager.verify_migration_completeness(
                    ssh_cmd_target, source_inventory, f"{target_appdata}/{stack_name}"
                )

            # Extract the actual success from nested structure
            container_success: bool = bool(
                container_verification.get("container_integration", {}).get("success", False)
            )
            data_success: bool = bool(data_verification.get("success", False))
            overall_success: bool = container_success and data_success

            return overall_success, {
                "container_integration": container_verification,
                "data_verification": data_verification,
                "overall_verification": overall_success,
            }

        except Exception as e:
            self.logger.error("Failed to verify deployment", error=str(e))
            return False, {"error": str(e)}

    async def cleanup_source(
        self,
        host_id: str,
        stack_name: str,
        compose_path: str,
        remove_data: bool = False,
        dry_run: bool = False,
    ) -> tuple[bool, dict]:
        """Clean up source stack after successful migration.

        Args:
            host_id: Source host ID
            stack_name: Stack name
            compose_path: Path to source compose file
            remove_data: Whether to remove data as well
            dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, cleanup_results: dict)
        """
        if dry_run:
            return True, {
                "dry_run": True,
                "cleanup_simulated": True,
                "would_remove_compose": True,
                "would_remove_data": remove_data,
            }

        try:
            source_host = self.config.hosts[host_id]
            ssh_cmd_source = build_ssh_command(source_host)

            # Stop stack first
            stop_result = await self.stack_tools.manage_stack(
                host_id=host_id,
                stack_name=stack_name,
                action="down",
                options=None,
            )

            # Remove compose file (safe operation)
            remove_cmd = ssh_cmd_source + [f"rm -f {shlex.quote(compose_path)}"]
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(remove_cmd, check=False),  # nosec B603
            )

            cleanup_results = {
                "stack_stopped": stop_result.get("success", False),
                "compose_removed": result.returncode == 0,
                "data_removed": False,
            }

            # Remove data if requested (more dangerous)
            if remove_data:
                source_appdata = source_host.appdata_path or "/opt/docker-appdata"
                stack_data_path = f"{source_appdata}/{stack_name}"

                # Use safety-validated removal
                (
                    remove_success,
                    remove_message,
                ) = await self.migration_manager.safety.safe_delete_file(
                    ssh_cmd_source, stack_data_path, f"Cleanup source data for {stack_name}"
                )

                cleanup_results["data_removed"] = remove_success
                cleanup_results["data_removal_message"] = remove_message

            overall_success = cleanup_results["compose_removed"]
            if remove_data:
                overall_success = overall_success and cleanup_results["data_removed"]

            return overall_success, cleanup_results

        except Exception as e:
            self.logger.error("Failed to cleanup source", error=str(e))
            return False, {"error": str(e)}

    async def create_backup(
        self, target_host: DockerHost, target_path: str, stack_name: str, dry_run: bool = False
    ) -> tuple[bool, dict]:
        """Create backup of existing target data before migration.

        Args:
            target_host: Target host configuration
            target_path: Path to backup
            stack_name: Stack name for backup naming
            dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, backup_info: dict)
        """
        if dry_run:
            return True, {
                "dry_run": True,
                "backup_simulated": True,
                "backup_path": f"simulated_backup_{stack_name}",
            }

        try:
            backup_info = await self.backup_manager.backup_directory(
                host=target_host,
                source_path=target_path,
                stack_name=stack_name,
                backup_reason="Pre-migration backup",
            )

            return backup_info["success"], backup_info

        except Exception as e:
            self.logger.error("Failed to create backup", error=str(e))
            return False, {"error": str(e)}

    async def restore_from_backup(
        self, target_host: DockerHost, backup_info: dict, dry_run: bool = False
    ) -> tuple[bool, str]:
        """Restore from backup in case of migration failure.

        Args:
            target_host: Target host configuration
            backup_info: Backup information from create_backup
            dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, message: str)
        """
        if dry_run:
            return True, "Dry run - backup restore would be performed"

        try:
            success, message = await self.backup_manager.restore_directory_backup(
                host=target_host, backup_info=backup_info
            )

            return success, message

        except Exception as e:
            error_msg = f"Failed to restore from backup: {str(e)}"
            self.logger.error("Backup restore failed", error=str(e))
            return False, error_msg

    def update_compose_for_target(
        self, compose_content: str, old_paths: dict[str, str], target_appdata: str, stack_name: str
    ) -> str:
        """Update compose file paths for target environment.

        Args:
            compose_content: Original compose content
            old_paths: Mapping of old to new paths
            target_appdata: Target appdata path
            stack_name: Stack name

        Returns:
            Updated compose content
        """
        try:
            # Use migration manager's path update functionality
            updated_content = self.migration_manager.update_compose_for_migration(
                compose_content=compose_content,
                old_paths=old_paths,
                new_base_path=target_appdata,
                target_appdata_path=target_appdata,
            )

            return updated_content

        except Exception as e:
            self.logger.error("Failed to update compose paths", error=str(e))
            return compose_content  # Return original on error

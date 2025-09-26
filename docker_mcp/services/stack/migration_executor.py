"""
Stack Migration Executor Module

Core migration execution logic for Docker Compose stacks.
Handles compose file operations, data transfer, deployment, and verification.
"""

import asyncio
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

import structlog

from ...core.backup import BackupInfo, BackupManager
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
        self.migration_manager = MigrationManager(
            transfer_method=config.transfer.method,
            docker_image=config.transfer.docker_image
        )
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
            try:
                result = await asyncio.to_thread(
                    subprocess.run,  # nosec B603
                    read_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                self.logger.error("Compose read timed out", host_id=host_id, stack_name=stack_name)
                return False, "", compose_file_path

            if result.returncode != 0:
                return False, "", compose_file_path

            return True, result.stdout, compose_file_path

        except Exception as e:
            self.logger.error(
                "Failed to retrieve compose file",
                error=str(e),
                host_id=host_id,
                stack_name=stack_name,
            )
            return False, "", ""

    async def validate_host_compatibility(
        self, source_host: DockerHost, target_host: DockerHost, stack_name: str
    ) -> tuple[bool, dict[str, Any]]:
        """Validate source and target host compatibility for migration.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            stack_name: Stack name for migration

        Returns:
            Tuple of (success: bool, validation_results: dict)
        """
        validation_results = {
            "compatibility_checks": {},
            "warnings": [],
            "errors": []
        }

        try:
            source_ssh = build_ssh_command(source_host)
            target_ssh = build_ssh_command(target_host)
            target_appdata = target_host.appdata_path or "/opt/docker-appdata"
            target_stack_path = f"{target_appdata}/{stack_name}"

            # Run all validation checks
            await self._validate_docker_version(source_ssh, target_ssh, validation_results)
            await self._validate_target_storage(target_ssh, target_appdata, validation_results)
            await self._validate_network_connectivity(source_ssh, target_host.hostname, validation_results)
            await self._validate_target_permissions(target_ssh, target_stack_path, validation_results)

            # Determine overall compatibility
            overall_success = self._determine_overall_compatibility(validation_results)
            validation_results["overall_compatible"] = overall_success

            self._log_validation_results(
                overall_success, source_host.hostname, target_host.hostname,
                stack_name, validation_results
            )

            return overall_success, validation_results

        except Exception as e:
            validation_results["errors"].append(f"Compatibility validation failed: {str(e)}")
            validation_results["overall_compatible"] = False

            self.logger.error(
                "Host compatibility validation error",
                source_host=source_host.hostname,
                target_host=target_host.hostname,
                error=str(e)
            )

            return False, validation_results

    async def _validate_docker_version(
        self, source_ssh: list[str], target_ssh: list[str], validation_results: dict[str, Any]
    ) -> None:
        """Validate Docker version compatibility between hosts."""
        source_version_cmd = source_ssh + ["docker", "version", "--format", "json"]
        target_version_cmd = target_ssh + ["docker", "version", "--format", "json"]

        try:
            source_result = await asyncio.to_thread(
                subprocess.run, source_version_cmd, capture_output=True, text=True, check=False, timeout=30
            )
            target_result = await asyncio.to_thread(
                subprocess.run, target_version_cmd, capture_output=True, text=True, check=False, timeout=30
            )

            if source_result.returncode == 0 and target_result.returncode == 0:
                validation_results["compatibility_checks"]["docker_version"] = {
                    "source_accessible": True,
                    "target_accessible": True,
                    "status": "compatible"
                }
            else:
                validation_results["errors"].append("Failed to verify Docker version compatibility")
                validation_results["compatibility_checks"]["docker_version"] = {
                    "source_accessible": source_result.returncode == 0,
                    "target_accessible": target_result.returncode == 0,
                    "status": "failed"
                }
        except subprocess.TimeoutExpired:
            validation_results["errors"].append("Docker version check timed out")
            validation_results["compatibility_checks"]["docker_version"] = {"status": "timeout"}

    async def _validate_target_storage(
        self, target_ssh: list[str], target_appdata: str, validation_results: dict[str, Any]
    ) -> None:
        """Validate target storage availability."""
        storage_check_cmd = target_ssh + ["df", "-h", target_appdata]

        try:
            storage_result = await asyncio.to_thread(
                subprocess.run, storage_check_cmd, capture_output=True, text=True, check=False, timeout=30
            )

            if storage_result.returncode == 0:
                validation_results["compatibility_checks"]["storage"] = {
                    "target_path_accessible": True,
                    "status": "available"
                }
            else:
                validation_results["errors"].append(f"Target storage path {target_appdata} not accessible")
                validation_results["compatibility_checks"]["storage"] = {
                    "target_path_accessible": False,
                    "status": "failed"
                }
        except subprocess.TimeoutExpired:
            validation_results["warnings"].append("Storage check timed out, continuing with migration")
            validation_results["compatibility_checks"]["storage"] = {"status": "timeout"}

    async def _validate_network_connectivity(
        self, source_ssh: list[str], target_hostname: str, validation_results: dict[str, Any]
    ) -> None:
        """Validate network connectivity between hosts."""
        network_check_cmd = source_ssh + ["ping", "-c", "1", "-W", "5", target_hostname]

        try:
            network_result = await asyncio.to_thread(
                subprocess.run, network_check_cmd, capture_output=True, text=True, check=False, timeout=30
            )

            validation_results["compatibility_checks"]["network"] = {
                "accessible": network_result.returncode == 0,
                "status": "reachable" if network_result.returncode == 0 else "unreachable"
            }

            if network_result.returncode != 0:
                validation_results["warnings"].append("Network connectivity test failed, but migration may still work")
        except subprocess.TimeoutExpired:
            validation_results["warnings"].append("Network check timed out")
            validation_results["compatibility_checks"]["network"] = {"status": "timeout"}

    async def _validate_target_permissions(
        self, target_ssh: list[str], target_stack_path: str, validation_results: dict[str, Any]
    ) -> None:
        """Validate target directory permissions."""
        mkdir_cmd = target_ssh + ["mkdir", "-p", target_stack_path]

        try:
            mkdir_result = await asyncio.to_thread(
                subprocess.run, mkdir_cmd, capture_output=True, text=True, check=False, timeout=30
            )

            if mkdir_result.returncode == 0:
                validation_results["compatibility_checks"]["permissions"] = {
                    "directory_creation": True,
                    "status": "writable"
                }
            else:
                validation_results["errors"].append(f"Cannot create target directory {target_stack_path}")
                validation_results["compatibility_checks"]["permissions"] = {
                    "directory_creation": False,
                    "status": "failed"
                }
        except subprocess.TimeoutExpired:
            validation_results["errors"].append("Directory creation check timed out")
            validation_results["compatibility_checks"]["permissions"] = {"status": "timeout"}

    def _determine_overall_compatibility(self, validation_results: dict[str, Any]) -> bool:
        """Determine overall compatibility based on all validation checks."""
        has_errors = len(validation_results["errors"]) > 0
        critical_failures = any(
            check.get("status") == "failed"
            for check in validation_results["compatibility_checks"].values()
            if isinstance(check, dict)
        )
        return not has_errors and not critical_failures

    def _log_validation_results(
        self, overall_success: bool, source_hostname: str, target_hostname: str,
        stack_name: str, validation_results: dict[str, Any]
    ) -> None:
        """Log validation results."""
        if overall_success:
            self.logger.info(
                "Host compatibility validation passed",
                source_host=source_hostname,
                target_host=target_hostname,
                stack_name=stack_name,
                warnings=len(validation_results["warnings"])
            )
        else:
            self.logger.error(
                "Host compatibility validation failed",
                source_host=source_hostname,
                target_host=target_hostname,
                stack_name=stack_name,
                errors=validation_results["errors"]
            )

    async def create_backup_archive(
        self,
        source_host: DockerHost,
        volume_paths: list[str],
        stack_name: str,
        dry_run: bool = False,
    ) -> tuple[bool, str, dict]:
        """Create archive of volume data for BACKUP purposes only.

        WARNING: This method is for backup operations only, not migration!
        Migrations use direct transfer methods (rsync).

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
                return (
                    False,
                    archive_path,
                    {"success": False, "error": "Archive integrity verification failed"},
                )

            # Get archive metadata
            metadata = {
                "archive_verified": True,
                "paths_included": volume_paths,
                "archive_size": 0,  # Would be populated by actual archive creation
            }

            return True, archive_path, metadata

        except Exception as e:
            self.logger.error(
                "Failed to create data archive",
                error=str(e),
                host_id=source_host.hostname,
                stack_name=stack_name,
            )
            return False, "", {"success": False, "error": str(e)}

    async def execute_migration_with_progress(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        stack_name: str,
        volume_paths: list[str],
        compose_content: str,
        dry_run: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None
    ) -> tuple[bool, dict[str, Any]]:
        """Execute migration with detailed progress reporting.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            stack_name: Stack name to migrate
            volume_paths: List of volume paths to transfer
            compose_content: Updated compose file content
            dry_run: Whether this is a dry run
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (success: bool, migration_results: dict)
        """
        migration_context = self._initialize_migration_context(
            source_host, target_host, stack_name
        )

        update_progress = self._create_progress_updater(
            migration_context, progress_callback
        )

        try:
            # Execute migration steps sequentially
            success = await self._execute_migration_steps(
                migration_context, update_progress, source_host, target_host,
                stack_name, volume_paths, compose_content, dry_run
            )

            if success:
                self._finalize_successful_migration(migration_context)

            return success, migration_context

        except Exception as e:
            return self._handle_migration_exception(
                e, migration_context, update_progress
            )

    def _initialize_migration_context(
        self, source_host: DockerHost, target_host: DockerHost, stack_name: str
    ) -> dict[str, Any]:
        """Initialize migration context with all required fields."""
        migration_steps = [
            {"name": "validate_compatibility", "description": "Validating host compatibility"},
            {"name": "stop_source", "description": "Stopping source stack"},
            {"name": "create_backup", "description": "Creating backup of target data"},
            {"name": "transfer_data", "description": "Transferring stack data"},
            {"name": "deploy_target", "description": "Deploying stack on target"},
            {"name": "verify_deployment", "description": "Verifying deployment success"}
        ]

        return {
            "migration_id": f"{source_host.hostname}_{target_host.hostname}_{stack_name}",
            "total_steps": len(migration_steps),
            "completed_steps": 0,
            "current_step": None,
            "step_results": {},
            "overall_success": False,
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "errors": [],
            "warnings": []
        }

    def _create_progress_updater(
        self, migration_context: dict[str, Any],
        progress_callback: Callable[[dict[str, Any]], None] | None
    ) -> Callable[[str, str, dict | None], None]:
        """Create progress update function with logging and callback."""
        def update_progress(step_name: str, status: str, details: dict | None = None):
            migration_context["current_step"] = {"name": step_name, "status": status}
            if details:
                migration_context["step_results"][step_name] = details

            if status == "completed":
                migration_context["completed_steps"] += 1

            # Log progress
            self.logger.info(
                "Migration progress update",
                migration_id=migration_context["migration_id"],
                step=step_name,
                status=status,
                progress=f"{migration_context['completed_steps']}/{migration_context['total_steps']}"
            )

            # Call progress callback if provided
            if progress_callback:
                try:
                    progress_callback(migration_context.copy())
                except Exception as e:
                    self.logger.warning("Progress callback failed", error=str(e))

        return update_progress

    async def _execute_migration_steps(
        self, migration_context: dict[str, Any], update_progress: Callable,
        source_host: DockerHost, target_host: DockerHost, stack_name: str,
        volume_paths: list[str], compose_content: str, dry_run: bool
    ) -> bool:
        """Execute all migration steps in sequence."""
        # Step 1: Validate compatibility
        if not await self._execute_compatibility_step(
            update_progress, source_host, target_host, stack_name, migration_context, dry_run
        ):
            return False

        # Step 2: Stop source stack
        if not await self._execute_stop_source_step(
            update_progress, source_host, stack_name, migration_context, dry_run
        ):
            return False

        # Step 3: Create backup
        await self._execute_backup_step(
            update_progress, target_host, stack_name, migration_context, dry_run
        )

        # Step 4: Transfer data
        if not await self._execute_transfer_step(
            update_progress, source_host, target_host, volume_paths,
            stack_name, migration_context, dry_run
        ):
            return False

        # Step 5: Deploy on target
        if not await self._execute_deploy_step(
            update_progress, target_host, stack_name, compose_content, migration_context, dry_run
        ):
            return False

        # Step 6: Verify deployment
        await self._execute_verify_step(
            update_progress, target_host, stack_name, volume_paths, migration_context, dry_run
        )

        return True

    async def _execute_compatibility_step(
        self, update_progress: Callable, source_host: DockerHost, target_host: DockerHost,
        stack_name: str, migration_context: dict[str, Any], dry_run: bool
    ) -> bool:
        """Execute compatibility validation step."""
        update_progress("validate_compatibility", "in_progress")
        compat_success, compat_results = await self.validate_host_compatibility(
            source_host, target_host, stack_name
        )

        if not compat_success and not dry_run:
            update_progress("validate_compatibility", "failed", compat_results)
            migration_context["errors"].append("Host compatibility validation failed")
            return False

        update_progress("validate_compatibility", "completed", compat_results)
        return True

    async def _execute_stop_source_step(
        self, update_progress: Callable, source_host: DockerHost, stack_name: str,
        migration_context: dict[str, Any], dry_run: bool
    ) -> bool:
        """Execute source stack stop step."""
        update_progress("stop_source", "in_progress")
        if not dry_run:
            stop_result = await self.stack_tools.manage_stack(
                source_host.hostname.replace(".", "_"), stack_name, "down"
            )
            if not stop_result.get("success", False):
                update_progress("stop_source", "failed", stop_result)
                migration_context["errors"].append(f"Failed to stop source stack: {stop_result.get('error')}")
                return False
        else:
            stop_result = {"success": True, "dry_run": True}

        update_progress("stop_source", "completed", stop_result)
        return True

    async def _execute_backup_step(
        self, update_progress: Callable, target_host: DockerHost, stack_name: str,
        migration_context: dict[str, Any], dry_run: bool
    ) -> None:
        """Execute backup creation step."""
        update_progress("create_backup", "in_progress")
        target_appdata = target_host.appdata_path or "/opt/docker-appdata"
        target_path = f"{target_appdata}/{stack_name}"

        backup_success, backup_info = await self.create_backup(
            target_host, target_path, stack_name, dry_run
        )

        backup_results = backup_info if isinstance(backup_info, dict) else {"backup_path": backup_info}
        if not backup_success and not dry_run:
            migration_context["warnings"].append("Backup creation failed, continuing with migration")

        update_progress("create_backup", "completed", backup_results)

    async def _execute_transfer_step(
        self, update_progress: Callable, source_host: DockerHost, target_host: DockerHost,
        volume_paths: list[str], stack_name: str, migration_context: dict[str, Any], dry_run: bool
    ) -> bool:
        """Execute data transfer step."""
        update_progress("transfer_data", "in_progress")
        transfer_success, transfer_results = await self.transfer_data(
            source_host, target_host, volume_paths, stack_name, None, dry_run
        )

        if not transfer_success:
            update_progress("transfer_data", "failed", transfer_results)
            migration_context["errors"].append(f"Data transfer failed: {transfer_results.get('error')}")
            return False

        update_progress("transfer_data", "completed", transfer_results)
        return True

    async def _execute_deploy_step(
        self, update_progress: Callable, target_host: DockerHost, stack_name: str,
        compose_content: str, migration_context: dict[str, Any], dry_run: bool
    ) -> bool:
        """Execute deployment step."""
        update_progress("deploy_target", "in_progress")
        deploy_success, deploy_results = await self.deploy_stack_on_target(
            target_host.hostname.replace(".", "_"), stack_name, compose_content, True, dry_run
        )

        if not deploy_success:
            update_progress("deploy_target", "failed", deploy_results)
            migration_context["errors"].append(f"Target deployment failed: {deploy_results.get('error')}")
            return False

        update_progress("deploy_target", "completed", deploy_results)
        return True

    async def _execute_verify_step(
        self, update_progress: Callable, target_host: DockerHost, stack_name: str,
        volume_paths: list[str], migration_context: dict[str, Any], dry_run: bool
    ) -> None:
        """Execute deployment verification step."""
        update_progress("verify_deployment", "in_progress")
        verify_success, verify_results = await self.verify_deployment(
            target_host.hostname.replace(".", "_"), stack_name, volume_paths, None, dry_run
        )

        if not verify_success:
            update_progress("verify_deployment", "failed", verify_results)
            migration_context["warnings"].append(f"Deployment verification failed: {verify_results.get('error')}")
        else:
            update_progress("verify_deployment", "completed", verify_results)

    def _finalize_successful_migration(self, migration_context: dict[str, Any]) -> None:
        """Finalize successful migration context."""
        migration_context["overall_success"] = True
        migration_context["end_time"] = datetime.now().isoformat()
        migration_context["current_step"] = {"name": "completed", "status": "success"}

        self.logger.info(
            "Migration completed successfully",
            migration_id=migration_context["migration_id"],
            duration_steps=migration_context["completed_steps"],
            warnings=len(migration_context["warnings"])
        )

    def _handle_migration_exception(
        self, exception: Exception, migration_context: dict[str, Any], update_progress: Callable
    ) -> tuple[bool, dict[str, Any]]:
        """Handle unexpected migration exceptions."""
        current_step = migration_context.get("current_step", {}).get("name", "unknown")
        update_progress(current_step, "failed", {"error": str(exception)})

        migration_context["errors"].append(f"Migration failed at step {current_step}: {str(exception)}")
        migration_context["overall_success"] = False
        migration_context["end_time"] = datetime.now().isoformat()

        self.logger.error(
            "Migration failed with exception",
            migration_id=migration_context["migration_id"],
            step=current_step,
            error=str(exception)
        )

        return False, migration_context

    async def transfer_data(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        volume_paths: list[str],
        stack_name: str,
        path_mappings: dict[str, str] | None = None,
        dry_run: bool = False,
    ) -> tuple[bool, dict]:
        """Transfer volume data directly between hosts (no archiving).

        Uses rsync for direct directory synchronization

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            volume_paths: List of volume paths to transfer
            path_mappings: Optional mapping of source paths to target paths
        stack_name: Stack name
        dry_run: Whether this is a dry run

        Returns:
            Tuple of (success: bool, transfer_results: dict)
        """
        if dry_run:
            return True, {
                "success": True,
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
                path_mappings=path_mappings,
                dry_run=dry_run,
            )

            return transfer_result["success"], transfer_result

        except Exception as e:
            self.logger.error(
                "Failed to transfer data",
                error=str(e),
                host_id=source_host.hostname,
                stack_name=stack_name,
            )
            return False, {"success": False, "error": str(e)}

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
                "success": True,
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
                    "success": True,
                    "extraction_successful": True,
                    "extraction_path": target_path,
                }
            else:
                return False, {"success": False, "error": "Archive extraction failed"}

        except Exception as e:
            self.logger.error("Failed to extract archive", error=str(e))
            return False, {"success": False, "error": str(e)}

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
                "success": True,
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

            container_ready = False
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
                    await asyncio.sleep(2)  # Initial delay for deployment to settle

                    # Poll for container readiness
                    for attempt in range(10):  # Up to 10 seconds
                        # Check if container exists and is running
                        target_host = self.config.hosts[host_id]
                        ssh_cmd = build_ssh_command(target_host)
                        check_cmd = ssh_cmd + [
                            "sh",
                            "-c",
                            (
                                "docker ps --filter "
                                f"'label=com.docker.compose.project={shlex.quote(stack_name)}' "
                                "--format '{{{{.Names}}}}' | grep -q . && "
                                "echo 'RUNNING' || echo 'NOT_READY'"
                            ),
                        ]

                        # typing.cast import at module level (line 10)

                        result = cast(
                            subprocess.CompletedProcess[str],
                            await asyncio.to_thread(
                                subprocess.run,  # nosec B603
                                check_cmd,
                                capture_output=True,
                                text=True,
                                check=False,
                                timeout=10,
                            ),
                        )

                        # Safely check if result has the expected stdout attribute and contains "RUNNING"
                        if (
                            result.returncode == 0
                            and hasattr(result, "stdout")
                            and isinstance(result.stdout, str)
                            and "RUNNING" in result.stdout
                        ):
                            container_ready = True
                            self.logger.info(
                                "Container ready for verification",
                                stack_name=stack_name,
                                host_id=host_id,
                                attempt=attempt + 1,
                            )
                            break

                        await asyncio.sleep(1)
                    else:
                        self.logger.warning(
                            "Container may not be fully ready for verification",
                            stack_name=stack_name,
                            host_id=host_id,
                        )

                except Exception as e:
                    self.logger.warning("Container readiness check failed", error=str(e))
                    # Continue anyway - verification will handle missing containers

            return True, {
                "success": True,
                "deploy_success": True,
                "start_success": start_stack,
                "stack_deployed": True,
                "container_ready": container_ready,
            }

        except Exception as e:
            self.logger.error("Failed to deploy stack on target", error=str(e))
            return False, {"success": False, "error": str(e)}

    async def verify_deployment(
        self,
        host_id: str,
        stack_name: str,
        expected_mounts: list[str],
        source_inventory: dict[str, Any] | None = None,
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
                "success": True,
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
            # Handle both top-level and nested success fields
            data_success: bool = bool(
                data_verification.get("success")
                or data_verification.get("data_transfer", {}).get("success", False)
            )
            overall_success: bool = container_success and data_success

            return overall_success, {
                "success": overall_success,
                "container_integration": container_verification,
                "data_verification": data_verification,
                "overall_verification": overall_success,
            }

        except Exception as e:
            self.logger.error("Failed to verify deployment", error=str(e))
            return False, {"success": False, "error": str(e)}

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
                "success": True,
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
            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                remove_cmd,
                check=False,
                timeout=10,
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
            return False, {"success": False, "error": str(e)}

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
                "success": True,
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

            # Convert BackupInfo to dict and determine success
            backup_dict = backup_info.model_dump()
            # Consider backup successful if backup_path is not None
            success = backup_info.backup_path is not None
            return success, backup_dict

        except Exception as e:
            self.logger.error("Failed to create backup", error=str(e))
            return False, {"success": False, "error": str(e)}

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
            # Convert dict back to BackupInfo object
            backup_obj = BackupInfo.model_validate(backup_info)
            success, message = await self.backup_manager.restore_directory_backup(
                host=target_host, backup_info=backup_obj
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

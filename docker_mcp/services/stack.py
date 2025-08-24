"""
Stack Management Service

Business logic for Docker Compose stack operations with formatted output.
"""

import asyncio
from typing import Any

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ..core.backup import BackupManager
from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.migration import MigrationManager
from ..tools.stacks import StackTools


class StackService:
    """Service for Docker Compose stack management operations."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.stack_tools = StackTools(config, context_manager)
        self.migration_manager = MigrationManager()
        self.backup_manager = BackupManager()
        self.logger = structlog.get_logger()

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """Validate host exists in configuration."""
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

    async def deploy_stack(
        self,
        host_id: str,
        stack_name: str,
        compose_content: str,
        environment: dict[str, str] | None = None,
        pull_images: bool = True,
        recreate: bool = False,
    ) -> ToolResult:
        """Deploy a Docker Compose stack to a remote host."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use stack tools to deploy
            result = await self.stack_tools.deploy_stack(
                host_id, stack_name, compose_content, environment, pull_images, recreate
            )

            if result["success"]:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text", text=f"Success: Stack '{stack_name}' deployed to {host_id}"
                        )
                    ],
                    structured_content=result,
                )
            else:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: Failed to deploy stack '{stack_name}': {result.get('error', 'Unknown error')}",
                        )
                    ],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to deploy stack", host_id=host_id, stack_name=stack_name, error=str(e)
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to deploy stack: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                },
            )

    async def manage_stack(
        self, host_id: str, stack_name: str, action: str, options: dict[str, Any] | None = None
    ) -> ToolResult:
        """Unified stack lifecycle management."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use stack tools to manage stack
            result = await self.stack_tools.manage_stack(host_id, stack_name, action, options)

            if result["success"]:
                message_lines = self._format_stack_action_result(result, stack_name, action)

                return ToolResult(
                    content=[TextContent(type="text", text="\n".join(message_lines))],
                    structured_content=result,
                )
            else:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: Failed to {action} stack '{stack_name}': {result.get('error', 'Unknown error')}",
                        )
                    ],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to manage stack",
                host_id=host_id,
                stack_name=stack_name,
                action=action,
                error=str(e),
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to {action} stack: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "action": action,
                },
            )

    def _format_stack_action_result(
        self, result: dict[str, Any], stack_name: str, action: str
    ) -> list[str]:
        """Format stack action result for display."""
        message_lines = [f"Success: Stack '{stack_name}' {action} completed"]

        # Add specific output for certain actions
        if action == "ps" and result.get("data", {}).get("services"):
            services = result["data"]["services"]
            message_lines.append("\nServices:")
            for service in services:
                name = service.get("Name", "Unknown")
                status = service.get("Status", "Unknown")
                message_lines.append(f"  {name}: {status}")

        return message_lines

    async def list_stacks(self, host_id: str) -> ToolResult:
        """List Docker Compose stacks on a host."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use stack tools to list stacks
            result = await self.stack_tools.list_stacks(host_id)

            if result["success"]:
                summary_lines = self._format_stacks_list(result, host_id)

                return ToolResult(
                    content=[TextContent(type="text", text="\n".join(summary_lines))],
                    structured_content=result,
                )
            else:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: Failed to list stacks: {result.get('error', 'Unknown error')}",
                        )
                    ],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error("Failed to list stacks", host_id=host_id, error=str(e))
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to list stacks: {str(e)}")],
                structured_content={"success": False, "error": str(e), "host_id": host_id},
            )

    def _format_stacks_list(self, result: dict[str, Any], host_id: str) -> list[str]:
        """Format stacks list for display."""
        stacks = result["stacks"]
        summary_lines = [
            f"Docker Compose Stacks on {host_id}",
            f"Found {len(stacks)} stacks",
            "",
        ]

        for stack in stacks:
            status_indicator = "●" if "running" in stack.get("status", "").lower() else "○"
            services = stack.get("services", [])
            services_info = f" ({len(services)} services)" if services else ""

            summary_lines.append(
                f"{status_indicator} {stack['name']}{services_info}\n"
                f"    Status: {stack.get('status', 'Unknown')}\n"
                f"    Created: {stack.get('created', 'Unknown')}"
            )

        return summary_lines
    
    async def migrate_stack(
        self,
        source_host_id: str,
        target_host_id: str,
        stack_name: str,
        skip_stop_source: bool = False,  # Changed: must explicitly skip stopping
        start_target: bool = True,
        remove_source: bool = False,
        dry_run: bool = False,
    ) -> ToolResult:
        """Migrate a Docker Compose stack between hosts with data integrity protection.
        
        This method ensures safe migration by:
        1. ALWAYS stopping containers unless explicitly skipped (prevents corruption)
        2. Verifying all containers are stopped before archiving
        3. Waiting for filesystem sync after stopping containers
        4. Verifying archive integrity before transfer
        5. Using atomic operations where possible
        6. Providing dry-run mode for testing
        
        Args:
            source_host_id: Source host ID
            target_host_id: Target host ID
            stack_name: Name of the stack to migrate
            skip_stop_source: Skip stopping the stack (DANGEROUS - only if already stopped)
            start_target: Start the stack on target after migration
            remove_source: Remove stack from source after successful migration
            dry_run: Perform dry run without actual changes
            
        Returns:
            ToolResult with migration status
            
        Raises:
            Will return error ToolResult if:
            - Containers are still running and skip_stop_source=True
            - Archive creation or verification fails
            - Transfer fails
        """
        try:
            # Validate hosts
            for host_id in [source_host_id, target_host_id]:
                is_valid, error_msg = self._validate_host(host_id)
                if not is_valid:
                    return ToolResult(
                        content=[TextContent(type="text", text=f"Error: {error_msg}")],
                        structured_content={"success": False, "error": error_msg},
                    )
            
            source_host = self.config.hosts[source_host_id]
            target_host = self.config.hosts[target_host_id]
            
            # Get appdata paths
            source_appdata = source_host.appdata_path or "/opt/docker-appdata"
            target_appdata = target_host.appdata_path or "/opt/docker-appdata"
            
            self.logger.info(
                "Starting stack migration",
                source=source_host_id,
                target=target_host_id,
                stack=stack_name,
                dry_run=dry_run,
            )
            
            migration_steps = []
            
            # Step 1: Get compose file from source
            migration_steps.append("📋 Retrieving compose configuration...")
            # Use compose manager to detect the actual compose file name (.yml or .yaml)
            compose_file_path = await self.stack_tools.compose_manager.get_compose_file_path(source_host_id, stack_name)
            
            # Build SSH command for source
            ssh_cmd_source = ["ssh", "-o", "StrictHostKeyChecking=no"]
            if source_host.identity_file:
                ssh_cmd_source.extend(["-i", source_host.identity_file])
            ssh_cmd_source.append(f"{source_host.user}@{source_host.hostname}")
            
            # Read compose file
            read_cmd = ssh_cmd_source + [f"cat {compose_file_path}"]
            import subprocess
            result = subprocess.run(read_cmd, capture_output=True, text=True, check=False)  # nosec B603
            
            if result.returncode != 0:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: Failed to read compose file: {result.stderr}")],
                    structured_content={"success": False, "error": f"Compose file not found: {result.stderr}"},
                )
            
            compose_content = result.stdout
            
            # Step 2: Parse volumes from compose
            migration_steps.append("🔍 Analyzing volume configuration...")
            volumes_info = await self.migration_manager.parse_compose_volumes(compose_content, source_appdata)
            
            # Step 3: Stop source stack (default behavior) and verify all containers are down
            if not skip_stop_source and not dry_run:
                migration_steps.append(f"⏹️  Stopping stack on {source_host_id}...")
                stop_result = await self.stack_tools.manage_stack(
                    source_host_id, stack_name, "down", {"remove_volumes": False}
                )
                if not stop_result["success"]:
                    return ToolResult(
                        content=[TextContent(type="text", text=f"Error: Failed to stop source stack: {stop_result.get('error')}")],
                        structured_content=stop_result,
                    )
                
                # Verify all containers are actually stopped
                migration_steps.append("🔍 Verifying all containers are stopped...")
                verify_cmd = ssh_cmd_source + [f"docker ps --filter 'label=com.docker.compose.project={stack_name}' --format '{{{{.Names}}}}'"]
                verify_result = subprocess.run(verify_cmd, capture_output=True, text=True, check=False)  # nosec B603
                
                if verify_result.stdout.strip():
                    running_containers = verify_result.stdout.strip().split('\n')
                    return ToolResult(
                        content=[TextContent(
                            type="text", 
                            text=f"Error: Some containers are still running: {', '.join(running_containers)}\n"
                                 f"Please ensure all containers are stopped before migration to prevent data corruption."
                        )],
                        structured_content={
                            "success": False,
                            "error": "Containers still running",
                            "running_containers": running_containers,
                        },
                    )
                
                # CRITICAL: Wait for database flushes and filesystem sync after container stop
                migration_steps.append("⏳ Waiting for database flush and filesystem sync (10s)...")
                await asyncio.sleep(10)  # Increased from 2s to ensure databases flush to disk
                
                # Additional safety: Force filesystem sync
                sync_cmd = ssh_cmd_source + ["sync"]
                subprocess.run(sync_cmd, capture_output=True, check=False)  # nosec B603
                migration_steps.append("✅ Filesystem sync completed")
                
            elif skip_stop_source and not dry_run:
                # If explicitly skipping stop, verify containers are already down
                migration_steps.append("⚠️  Checking if stack containers are running...")
                check_cmd = ssh_cmd_source + [f"docker ps --filter 'label=com.docker.compose.project={stack_name}' --format '{{{{.Names}}}}'"]
                check_result = subprocess.run(check_cmd, capture_output=True, text=True, check=False)  # nosec B603
                
                if check_result.stdout.strip():
                    running_containers = check_result.stdout.strip().split('\n')
                    return ToolResult(
                        content=[TextContent(
                            type="text",
                            text=f"Error: Stack has running containers: {', '.join(running_containers)}\n"
                                 f"Migration requires all containers to be stopped to prevent data corruption.\n"
                                 f"Remove skip_stop_source flag or manually stop the stack first."
                        )],
                        structured_content={
                            "success": False,
                            "error": "Cannot migrate with running containers",
                            "running_containers": running_containers,
                            "suggestion": "Remove skip_stop_source flag or stop stack manually",
                        },
                    )
            
            # Step 4: Get volume locations and create source inventory
            migration_steps.append("📦 Creating volume archives...")
            volume_paths = await self.migration_manager.get_volume_locations(
                ssh_cmd_source, volumes_info["named_volumes"]
            )
            
            # Add bind mounts to paths
            all_paths = list(volume_paths.values()) + volumes_info["bind_mounts"]
            
            # Step 4.1: Create source inventory for verification
            source_inventory = None
            if all_paths and not dry_run:
                migration_steps.append("📊 Creating source data inventory...")
                source_inventory = await self.migration_manager.create_source_inventory(
                    ssh_cmd_source, all_paths
                )
                migration_steps.append(
                    f"✅ Inventory created: {source_inventory['total_files']} files, "
                    f"{self._format_size(source_inventory['total_size'])}, "
                    f"{len(source_inventory['critical_files'])} critical files"
                )
            
            if all_paths and not dry_run:
                archive_path = await self.migration_manager.archive_utils.create_archive(
                    ssh_cmd_source, all_paths, f"{stack_name}_migration"
                )
                migration_steps.append(f"✅ Archive created: {archive_path}")
                
                # Verify archive integrity
                verify_archive_cmd = ssh_cmd_source + [f"tar tzf {archive_path} > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"]
                verify_archive = subprocess.run(verify_archive_cmd, capture_output=True, text=True, check=False)  # nosec B603
                
                if "FAILED" in verify_archive.stdout:
                    return ToolResult(
                        content=[TextContent(type="text", text=f"Error: Archive verification failed. The archive may be corrupted.")],
                        structured_content={"success": False, "error": "Archive integrity check failed", "archive_path": archive_path},
                    )
                migration_steps.append("✅ Archive integrity verified")
            
            # Step 5: Prepare target directories
            migration_steps.append(f"📁 Preparing target directories on {target_host_id}...")
            target_stack_dir = await self.migration_manager.prepare_target_directories(
                self._build_ssh_cmd(target_host), target_appdata, stack_name
            )
            
            # Step 5.1: CRITICAL - Backup existing target data before ANY changes
            backup_info = None
            if not dry_run:
                migration_steps.append(f"💾 Creating backup of existing target data...")
                try:
                    # Determine backup method based on transfer type
                    transfer_type, _ = await self.migration_manager.choose_transfer_method(source_host, target_host)
                    
                    if transfer_type == "zfs" and target_host.zfs_dataset:
                        # ZFS backup using snapshot
                        backup_info = await self.backup_manager.backup_zfs_dataset(
                            target_host, target_host.zfs_dataset, stack_name,
                            f"Pre-migration backup before {stack_name} migration from {source_host_id}"
                        )
                        migration_steps.append(f"✅ ZFS backup created: {backup_info['snapshot_name']} ({backup_info['backup_size_human']})")
                    else:
                        # Directory backup using tar
                        backup_info = await self.backup_manager.backup_directory(
                            target_host, target_stack_dir, stack_name,
                            f"Pre-migration backup before {stack_name} migration from {source_host_id}"
                        )
                        if backup_info['backup_path']:
                            migration_steps.append(f"✅ Directory backup created: {backup_info['backup_path']} ({backup_info['backup_size_human']})")
                        else:
                            migration_steps.append("ℹ️  No existing data to backup on target")
                            
                except Exception as e:
                    self.logger.warning("Backup creation failed", error=str(e), stack=stack_name, target=target_host_id)
                    migration_steps.append(f"⚠️  Backup failed: {str(e)} - continuing with migration (RISKY)")
            
            # Step 6: Transfer archive to target
            if all_paths and not dry_run:
                migration_steps.append("🚀 Transferring data to target host...")
                transfer_result = await self.migration_manager.rsync_transfer.transfer(
                    source_host, target_host, archive_path, f"/tmp/{stack_name}_migration.tar.gz",
                    compress=True, delete=False, dry_run=dry_run
                )
                
                if transfer_result["success"]:
                    migration_steps.append(f"✅ Transfer complete: {transfer_result['stats']}")
                
                # ATOMIC EXTRACTION: Clean extraction to prevent stale files and path nesting
                migration_steps.append("📦 Extracting data with atomic replacement...")
                
                # Method 1: Atomic replacement - extract to temp, then replace
                extract_cmd = self._build_ssh_cmd(target_host) + [
                    f"mkdir -p {target_stack_dir}.tmp && "
                    f"tar xzf /tmp/{stack_name}_migration.tar.gz -C {target_stack_dir}.tmp && "
                    f"rm -rf {target_stack_dir}.old && "
                    f"test -d {target_stack_dir} && mv {target_stack_dir} {target_stack_dir}.old || true && "
                    f"mv {target_stack_dir}.tmp {target_stack_dir} && "
                    f"rm -rf {target_stack_dir}.old && "
                    f"echo 'EXTRACT_SUCCESS' || echo 'EXTRACT_FAILED'"
                ]
                
                result = subprocess.run(extract_cmd, capture_output=True, text=True, check=False)  # nosec B603
                
                if "EXTRACT_FAILED" in result.stdout or result.returncode != 0:
                    # Fallback: Force overwrite existing files
                    migration_steps.append("⚠️  Atomic extraction failed, trying force overwrite...")
                    fallback_cmd = self._build_ssh_cmd(target_host) + [
                        f"cd {target_stack_dir} && "
                        f"tar xzf /tmp/{stack_name}_migration.tar.gz --overwrite --no-same-owner"
                    ]
                    fallback_result = subprocess.run(fallback_cmd, capture_output=True, text=True, check=False)  # nosec B603
                    
                    if fallback_result.returncode != 0:
                        return ToolResult(
                            content=[TextContent(type="text", text=f"❌ Archive extraction failed: {fallback_result.stderr}")],
                            structured_content={"success": False, "error": f"Extraction failed: {fallback_result.stderr}"},
                        )
                    else:
                        migration_steps.append("✅ Force overwrite extraction completed")
                else:
                    migration_steps.append("✅ Atomic extraction completed - no stale files")
            
            # Step 7: Update compose file for target paths
            migration_steps.append("📝 Updating compose configuration for target...")
            updated_compose = self.migration_manager.update_compose_for_migration(
                compose_content, volume_paths, target_stack_dir, target_appdata
            )
            
            # Step 8: CRITICAL - Verify data transfer FIRST (before deployment)
            verification_results = None
            data_verification_passed = True
            
            if not dry_run and source_inventory:
                migration_steps.append("🔍 Verifying data transfer completeness...")
                
                # Verify data transfer completeness WITHOUT containers running
                data_verification = await self.migration_manager.verify_migration_completeness(
                    self._build_ssh_cmd(target_host), source_inventory, target_appdata, stack_name
                )
                
                data_verification_passed = data_verification["data_transfer"]["success"]
                
                if data_verification_passed:
                    migration_steps.append("✅ Data Transfer Verification Passed:")
                    migration_steps.append(f"   • Files: ✓ {data_verification['data_transfer']['file_match_percentage']:.1f}% match ({data_verification['data_transfer']['files_found']}/{data_verification['data_transfer']['files_expected']})")
                    migration_steps.append(f"   • Size: ✓ {data_verification['data_transfer']['size_match_percentage']:.1f}% match ({self._format_size(data_verification['data_transfer']['size_found'])})")
                    migration_steps.append(f"   • Critical Files: ✓ {sum(1 for v in data_verification['data_transfer']['critical_files_verified'].values() if v.get('verified'))} of {len(data_verification['data_transfer']['critical_files_verified'])} verified")
                else:
                    migration_steps.append("❌ Data Transfer Verification FAILED:")
                    for issue in data_verification["issues"]:
                        migration_steps.append(f"   • {issue}")
                    migration_steps.append("⚠️  Stack deployment CANCELLED - data verification failed")
                    
                    # Attempt rollback if backup was created
                    if backup_info and backup_info.get("success"):
                        migration_steps.append("🔄 Attempting rollback from backup...")
                        try:
                            if backup_info["type"] == "zfs_snapshot":
                                rollback_success, rollback_msg = await self.backup_manager.restore_zfs_backup(target_host, backup_info)
                            else:
                                rollback_success, rollback_msg = await self.backup_manager.restore_directory_backup(target_host, backup_info)
                            
                            if rollback_success:
                                migration_steps.append(f"✅ Rollback successful: {rollback_msg}")
                            else:
                                migration_steps.append(f"❌ Rollback failed: {rollback_msg}")
                                
                        except Exception as rollback_error:
                            migration_steps.append(f"❌ Rollback error: {str(rollback_error)}")
                    else:
                        migration_steps.append("⚠️  No backup available for rollback")
            
            # Step 9: Deploy stack ONLY if data verification passed
            deploy_success = False
            if start_target and not dry_run and data_verification_passed:
                migration_steps.append(f"🚀 Data verified - deploying stack on {target_host_id}...")
                deploy_result = await self.stack_tools.deploy_stack(
                    target_host_id, stack_name, updated_compose
                )
                if deploy_result["success"]:
                    deploy_success = True
                    migration_steps.append(f"✅ Stack deployed successfully on {target_host_id}")
                    
                    # Wait for container to stabilize
                    migration_steps.append("⏳ Waiting for container stabilization...")
                    await asyncio.sleep(5)
                    
                    # Now verify container integration with DYNAMIC mount detection
                    migration_steps.append("🔍 Verifying container integration...")
                    expected_volumes = self._extract_expected_mounts(updated_compose, target_appdata, stack_name)
                    container_verification = await self.migration_manager.verify_container_integration(
                        self._build_ssh_cmd(target_host), stack_name, target_appdata, expected_volumes
                    )
                    
                    # Combine all verification results
                    verification_results = {
                        "data_transfer": data_verification["data_transfer"] if not dry_run and source_inventory else {"success": True},
                        "container_integration": container_verification["container_integration"],
                        "all_issues": (data_verification["issues"] if not dry_run and source_inventory else []) + container_verification["issues"],
                        "overall_success": data_verification_passed and deploy_success and container_verification["container_integration"]["success"]
                    }
                    
                    if container_verification["container_integration"]["success"]:
                        migration_steps.append("✅ Container Integration Verified:")
                        migration_steps.append(f"   • Container: ✓ {'Running & Healthy' if container_verification['container_integration']['container_healthy'] else 'Running'}")
                        migration_steps.append(f"   • Mounts: ✓ {'Correctly Mounted' if container_verification['container_integration']['mount_paths_correct'] else 'Mounted'}")
                        migration_steps.append(f"   • Data Access: ✓ {'Accessible' if container_verification['container_integration']['data_accessible'] else 'Limited'}")
                    else:
                        migration_steps.append("⚠️  Container Integration Issues:")
                        for issue in container_verification["issues"]:
                            migration_steps.append(f"   • {issue}")
                else:
                    migration_steps.append(f"❌ Failed to deploy on target: {deploy_result.get('error')}")
                    verification_results = {
                        "data_transfer": data_verification["data_transfer"] if not dry_run and source_inventory else {"success": True},
                        "container_integration": {"success": False},
                        "all_issues": [f"Deployment failed: {deploy_result.get('error')}"],
                        "overall_success": False
                    }
            elif start_target and not dry_run and not data_verification_passed:
                # Data verification failed - don't deploy
                verification_results = {
                    "data_transfer": data_verification["data_transfer"] if not dry_run and source_inventory else {"success": False},
                    "container_integration": {"success": False},
                    "all_issues": data_verification["issues"] if not dry_run and source_inventory else ["Data verification failed"],
                    "overall_success": False
                }
            
            # Step 10: Remove from source if requested (only if migration was successful)
            if remove_source and not dry_run and verification_results and verification_results["overall_success"]:
                migration_steps.append(f"🗑️  Migration successful - removing stack from {source_host_id}...")
                # SAFETY: Only remove compose file, not entire directory
                remove_cmd = ssh_cmd_source + [f"rm -f {compose_file_path}"]  # Changed from rm -rf to rm -f
                subprocess.run(remove_cmd, check=False)  # nosec B603
            elif remove_source and not dry_run:
                migration_steps.append(f"⚠️  Skipping source removal - migration verification failed")
            
            # Build detailed migration summary
            migration_summary = "\n".join(migration_steps)
            
            # Add configuration and path details
            config_details = [
                "\n📋 Migration Details:",
                f"   • Source: {source_host.hostname} ({source_host_id})",
                f"   • Target: {target_host.hostname} ({target_host_id})",
                f"   • Stack: {stack_name}",
                f"   • Docker Compose: {compose_file_path}",
                f"   • Source Appdata: {source_appdata}",
                f"   • Target Appdata: {target_appdata}",
                f"   • Volumes Migrated: {len(all_paths) if 'all_paths' in locals() else 0}",
            ]
            
            if 'all_paths' in locals() and all_paths:
                config_details.append("   • Volume Paths:")
                for path in all_paths:
                    config_details.append(f"     - {path}")
            
            if 'transfer_result' in locals() and transfer_result.get("stats"):
                stats = transfer_result["stats"]
                config_details.extend([
                    "   • Transfer Statistics:",
                    f"     - Files: {stats.get('files_transferred', 0)}",
                    f"     - Size: {stats.get('total_size', 0):,} bytes",
                    f"     - Rate: {stats.get('transfer_rate', 'N/A')}",
                ])
            
            full_summary = migration_summary + "\n" + "\n".join(config_details)
            
            return ToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Migration {'(DRY RUN) ' if dry_run else ''}completed:\n\n{full_summary}"
                )],
                structured_content={
                    "success": verification_results["overall_success"] if verification_results else False,
                    "source_host": source_host_id,
                    "target_host": target_host_id,
                    "stack_name": stack_name,
                    "source_appdata": source_appdata,
                    "target_appdata": target_appdata,
                    "compose_file": compose_file_path,
                    "volumes_migrated": len(all_paths) if 'all_paths' in locals() else 0,
                    "volume_paths": all_paths if 'all_paths' in locals() else [],
                    "transfer_stats": transfer_result.get("stats", {}) if 'transfer_result' in locals() else {},
                    "source_summary": {
                        "total_files": source_inventory["total_files"] if source_inventory else 0,
                        "total_size": source_inventory["total_size"] if source_inventory else 0,
                        "total_size_human": self._format_size(source_inventory["total_size"]) if source_inventory else "0 B",
                        "critical_files": len(source_inventory.get("critical_files", {})) if source_inventory else 0
                    },
                    "verification_summary": {
                        "data_transfer": {
                            "files_match": f"{verification_results['data_transfer']['file_match_percentage']:.1f}%" if verification_results else "N/A",
                            "size_match": f"{verification_results['data_transfer']['size_match_percentage']:.1f}%" if verification_results else "N/A",
                            "critical_verified": sum(1 for v in verification_results['data_transfer']['critical_files_verified'].values() if v.get('verified')) if verification_results else 0,
                            "critical_total": len(verification_results['data_transfer']['critical_files_verified']) if verification_results else 0,
                            "issues": len(verification_results.get('all_issues', [])) if verification_results else 0
                        },
                        "container": {
                            "running": verification_results['container_integration']['container_running'] if verification_results else False,
                            "healthy": verification_results['container_integration']['container_healthy'] if verification_results else False,
                            "mounts_correct": verification_results['container_integration']['mount_paths_correct'] if verification_results else False
                        },
                        "overall_success": verification_results['overall_success'] if verification_results else True
                    } if verification_results else {},
                    "backup_summary": {
                        "backup_created": backup_info is not None and backup_info.get("success", False),
                        "backup_type": backup_info.get("type") if backup_info else None,
                        "backup_path": backup_info.get("backup_path") if backup_info and backup_info.get("type") == "directory" else None,
                        "backup_snapshot": backup_info.get("snapshot_name") if backup_info and backup_info.get("type") == "zfs_snapshot" else None,
                        "backup_size": backup_info.get("backup_size", 0) if backup_info else 0,
                        "backup_size_human": backup_info.get("backup_size_human", "0 B") if backup_info else "0 B",
                        "rollback_available": backup_info is not None and backup_info.get("success", False)
                    },
                    "dry_run": dry_run,
                    "steps": migration_steps,
                },
            )
            
        except Exception as e:
            self.logger.error(
                "Stack migration failed",
                source=source_host_id,
                target=target_host_id,
                stack=stack_name,
                error=str(e),
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Migration failed: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "source_host": source_host_id,
                    "target_host": target_host_id,
                    "stack_name": stack_name,
                },
            )
    
    def _build_ssh_cmd(self, host) -> list[str]:
        """Build SSH command for a host."""
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        if host.identity_file:
            ssh_cmd.extend(["-i", host.identity_file])
        if host.port != 22:
            ssh_cmd.extend(["-p", str(host.port)])
        ssh_cmd.append(f"{host.user}@{host.hostname}")
        return ssh_cmd
    
    def _format_size(self, size_bytes: int) -> str:
        """Format bytes into human-readable string."""
        if size_bytes == 0:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                if unit == 'B':
                    return f"{int(size_bytes)} {unit}"
                else:
                    return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"
    
    def _extract_expected_mounts(self, compose_content: str, target_appdata: str, stack_name: str) -> list[str]:
        """Extract expected volume mounts from compose file content.
        
        Args:
            compose_content: Docker Compose YAML content
            target_appdata: Target appdata path
            stack_name: Stack name
            
        Returns:
            List of expected mount strings in format "source:destination"
        """
        try:
            import yaml
            compose_data = yaml.safe_load(compose_content)
            expected_mounts = []
            
            # Parse services for volume mounts
            services = compose_data.get("services", {})
            for service_name, service_config in services.items():
                volumes = service_config.get("volumes", [])
                
                for volume in volumes:
                    if isinstance(volume, str):
                        # Parse string format volume
                        if ":" in volume:
                            parts = volume.split(":", 2)  # Handle mode like "rw"
                            if len(parts) >= 2:
                                source_path = parts[0]
                                container_path = parts[1]
                                
                                # Convert relative paths to absolute
                                if source_path.startswith("."):
                                    source_path = f"{target_appdata}/{stack_name}/{source_path[2:]}"
                                elif not source_path.startswith("/"):
                                    # Named volume - convert to expected bind mount
                                    source_path = f"{target_appdata}/{stack_name}"
                                
                                expected_mount = f"{source_path}:{container_path}"
                                if expected_mount not in expected_mounts:
                                    expected_mounts.append(expected_mount)
                                    
                    elif isinstance(volume, dict):
                        # Parse dictionary format volume
                        volume_type = volume.get("type", "bind")
                        if volume_type == "bind":
                            source = volume.get("source", "")
                            target = volume.get("target", "")
                            
                            if source and target:
                                # Convert relative source to absolute
                                if not source.startswith("/"):
                                    source = f"{target_appdata}/{stack_name}/{source}"
                                
                                expected_mount = f"{source}:{target}"
                                if expected_mount not in expected_mounts:
                                    expected_mounts.append(expected_mount)
            
            if expected_mounts:
                self.logger.info(
                    "Extracted expected mounts from compose file",
                    stack=stack_name,
                    mounts=expected_mounts
                )
                return expected_mounts
            else:
                # Fallback to default if no mounts found
                default_mount = f"{target_appdata}/{stack_name}:/data"
                self.logger.warning(
                    "No volume mounts found in compose file, using default",
                    stack=stack_name,
                    default_mount=default_mount
                )
                return [default_mount]
                
        except Exception as e:
            # Fallback on any parsing error
            default_mount = f"{target_appdata}/{stack_name}:/data"
            self.logger.error(
                "Failed to parse compose file for mounts, using default",
                stack=stack_name,
                error=str(e),
                default_mount=default_mount
            )
            return [default_mount]

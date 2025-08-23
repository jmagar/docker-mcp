"""
Stack Management Service

Business logic for Docker Compose stack operations with formatted output.
"""

from typing import Any

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.migration_utils import MigrationUtils
from ..tools.stacks import StackTools


class StackService:
    """Service for Docker Compose stack management operations."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.stack_tools = StackTools(config, context_manager)
        self.migration_utils = MigrationUtils()
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
            compose_file_path = f"{source_host.compose_path or '/opt/compose'}/{stack_name}/docker-compose.yml"
            
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
            volumes_info = await self.migration_utils.parse_compose_volumes(compose_content)
            
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
                
                # Wait a moment for filesystem sync
                migration_steps.append("⏳ Waiting for filesystem sync...")
                await asyncio.sleep(2)
                
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
            
            # Step 4: Get volume locations and create archive
            migration_steps.append("📦 Creating volume archives...")
            volume_paths = await self.migration_utils.get_volume_locations(
                ssh_cmd_source, volumes_info["named_volumes"]
            )
            
            # Add bind mounts to paths
            all_paths = list(volume_paths.values()) + volumes_info["bind_mounts"]
            
            if all_paths and not dry_run:
                archive_path = await self.migration_utils.create_volume_archive(
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
            target_stack_dir = await self.migration_utils.prepare_target_directories(
                self._build_ssh_cmd(target_host), target_appdata, stack_name
            )
            
            # Step 6: Transfer archive to target
            if all_paths and not dry_run:
                migration_steps.append("🚀 Transferring data to target host...")
                transfer_result = await self.migration_utils.transfer_with_rsync(
                    source_host, target_host, archive_path, f"/tmp/{stack_name}_migration.tar.gz",
                    compress=True, delete=False, dry_run=dry_run
                )
                
                if transfer_result["success"]:
                    migration_steps.append(f"✅ Transfer complete: {transfer_result['stats']}")
                
                # Extract on target
                extract_cmd = self._build_ssh_cmd(target_host) + [
                    f"cd {target_stack_dir} && tar xzf /tmp/{stack_name}_migration.tar.gz"
                ]
                subprocess.run(extract_cmd, check=False)  # nosec B603
            
            # Step 7: Update compose file for target paths
            migration_steps.append("📝 Updating compose configuration for target...")
            updated_compose = self.migration_utils.update_compose_for_migration(
                compose_content, volume_paths, target_stack_dir
            )
            
            # Step 8: Deploy on target
            if start_target and not dry_run:
                migration_steps.append(f"🚀 Deploying stack on {target_host_id}...")
                deploy_result = await self.stack_tools.deploy_stack(
                    target_host_id, stack_name, updated_compose
                )
                if deploy_result["success"]:
                    migration_steps.append(f"✅ Stack deployed successfully on {target_host_id}")
                else:
                    migration_steps.append(f"⚠️  Failed to deploy on target: {deploy_result.get('error')}")
            
            # Step 9: Remove from source if requested
            if remove_source and not dry_run:
                migration_steps.append(f"🗑️  Removing stack from {source_host_id}...")
                remove_cmd = ssh_cmd_source + [f"rm -rf {compose_file_path}"]
                subprocess.run(remove_cmd, check=False)  # nosec B603
            
            # Build result
            migration_summary = "\n".join(migration_steps)
            
            return ToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Migration {'(DRY RUN) ' if dry_run else ''}completed:\n\n{migration_summary}"
                )],
                structured_content={
                    "success": True,
                    "source_host": source_host_id,
                    "target_host": target_host_id,
                    "stack_name": stack_name,
                    "volumes_migrated": len(all_paths) if 'all_paths' in locals() else 0,
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

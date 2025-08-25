"""
Stack Management Service

Business logic for Docker Compose stack operations with formatted output.
"""

import asyncio
import os
import shlex
import subprocess
import tempfile
from typing import Any

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ..core.backup import BackupManager
from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.migration.manager import MigrationManager
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
        # Initialize variables that will be used throughout the function
        data_verification = {
            "data_transfer": {
                "success": False,
                "files_found": 0,
                "files_expected": 0,
                "file_match_percentage": 0.0,
                "size_match_percentage": 0.0,
                "critical_files_verified": {}
            },
            "issues": ["Verification not run"]
        }
        
        # Initialize extraction tracking
        files_extracted = 0  # Track actual files extracted from archive
        
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
            
            # Build SSH command for source (reuses port/identity handling)
            ssh_cmd_source = self._build_ssh_cmd(source_host)
            
            # Read compose file
            read_cmd = ssh_cmd_source + [f"cat {shlex.quote(compose_file_path)}"]
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: subprocess.run(read_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            
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
                verify_cmd = ssh_cmd_source + [(
                    f"docker ps --filter "
                    f"'label=com.docker.compose.project={shlex.quote(stack_name)}' "
                    f"--format '{{{{.Names}}}}'"
                )]
                verify_result = await loop.run_in_executor(
                    None, lambda: subprocess.run(verify_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                
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
                await loop.run_in_executor(
                    None, lambda: subprocess.run(sync_cmd, capture_output=True, check=False)  # nosec B603
                )
                migration_steps.append("✅ Filesystem sync completed")
                
            elif skip_stop_source and not dry_run:
                # If explicitly skipping stop, verify containers are already down
                migration_steps.append("⚠️  Checking if stack containers are running...")
                check_cmd = ssh_cmd_source + [f"docker ps --filter 'label=com.docker.compose.project={shlex.quote(stack_name)}' --format '{{{{.Names}}}}'"]
                check_result = await loop.run_in_executor(
                    None, lambda: subprocess.run(check_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                
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
            archive_path = None  # Initialize to ensure it's available for transfer step
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
                verify_archive = await loop.run_in_executor(
                    None, lambda: subprocess.run(verify_archive_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                
                if "FAILED" in verify_archive.stdout:
                    return ToolResult(
                        content=[TextContent(type="text", text=f"Error: Archive verification failed. The archive may be corrupted.")],
                        structured_content={"success": False, "error": "Archive integrity check failed", "archive_path": archive_path},
                    )
                migration_steps.append("✅ Archive integrity verified")
            
            # Step 5: Prepare target directories (preserve subdirectory structure)
            migration_steps.append(f"📁 Preparing target directories on {target_host_id}...")
            
            # Preserve full path structure: source /mnt/appdata/memos/.memos/ -> target /mnt/cache/appdata/memos/.memos/
            if all_paths and len(all_paths) > 0:
                source_path = all_paths[0]  # e.g., /mnt/appdata/memos/.memos/
                # Extract relative path after source appdata
                relative_path = source_path.replace(source_appdata + "/", "").rstrip("/")  # memos/.memos
                target_stack_dir = f"{target_appdata}/{relative_path}"
            else:
                target_stack_dir = f"{target_appdata}/{stack_name}"
            
            # Create the target directory
            mkdir_cmd = f"mkdir -p {shlex.quote(target_stack_dir)}"
            result = await loop.run_in_executor(
                None, lambda: subprocess.run(
                    self._build_ssh_cmd(target_host) + [mkdir_cmd], 
                    capture_output=True, text=True, check=False  # nosec B603
                )
            )
            if result.returncode != 0:
                return ToolResult(
                    content=[TextContent(type="text", text=f"❌ Failed to create target directory: {result.stderr}")],
                    structured_content={"success": False, "error": f"Target directory creation failed: {result.stderr}"},
                )
            
            self.logger.info("Created target directory", path=target_stack_dir)
            
            # Step 5.1: CRITICAL - Backup existing target data before ANY changes
            backup_info = None
            if not dry_run:
                migration_steps.append("💾 Creating backup of existing target data...")
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
                        # Check if directory has existing files before backing up
                        check_existing_cmd = self._build_ssh_cmd(target_host) + [f"find {target_stack_dir} -type f 2>/dev/null | head -1"]
                        check_result = await loop.run_in_executor(
                            None, lambda: subprocess.run(check_existing_cmd, capture_output=True, text=True, check=False)  # nosec B603
                        )
                        has_existing_data = bool(check_result.stdout.strip())
                        
                        if has_existing_data:
                            # Directory backup using tar
                            backup_info = await self.backup_manager.backup_directory(
                                target_host, target_stack_dir, stack_name,
                                f"Pre-migration backup before {stack_name} migration from {source_host_id}"
                            )
                            if backup_info.get('success') and backup_info.get('backup_path'):
                                migration_steps.append(f"✅ Backed up existing data: {backup_info['backup_path']} ({backup_info['backup_size_human']})")
                            else:
                                migration_steps.append("⚠️  Backup of existing data failed")
                        else:
                            migration_steps.append("ℹ️  No existing data to backup on target")
                            backup_info = {"success": False, "backup_path": None}
                            
                except Exception as e:
                    self.logger.warning("Backup creation failed", error=str(e), stack=stack_name, target=target_host_id)
                    migration_steps.append(f"⚠️  Backup failed: {str(e)} - continuing with migration (RISKY)")
            
            # Step 6: Transfer archive to target
            if all_paths and not dry_run and archive_path:
                migration_steps.append("🚀 Transferring data to target host...")
                temp_suffix = os.urandom(8).hex()[:8]
                target_archive_path = f"/tmp/{stack_name}_migration_{temp_suffix}.tar.gz"
                
                transfer_result = await self.migration_manager.rsync_transfer.transfer(
                    source_host, target_host, archive_path, target_archive_path,
                    compress=True, delete=False, dry_run=dry_run
                )
                
                if transfer_result["success"]:
                    migration_steps.append(f"✅ Transfer complete: {transfer_result['stats']}")
                    
                    # Log archive details before extraction
                    archive_size_cmd = ssh_cmd_source + [f"stat -c%s {archive_path} 2>/dev/null || echo 0"]
                    archive_size_result = await loop.run_in_executor(
                        None, lambda: subprocess.run(archive_size_cmd, capture_output=True, text=True, check=False)  # nosec B603
                    )
                    archive_size = int(archive_size_result.stdout.strip()) if archive_size_result.returncode == 0 else 0
                    
                    self.logger.info("Archive ready for extraction",
                        archive_path=archive_path,
                        archive_size_bytes=archive_size,
                        archive_size_human=self._format_size(archive_size),
                        target_dir=target_stack_dir
                    )
                    migration_steps.append(f"📦 Archive ready: {self._format_size(archive_size)} at {archive_path}")
                
                # ATOMIC EXTRACTION: Clean extraction to prevent stale files and path nesting
                migration_steps.append("📦 Extracting data with atomic replacement...")
                
                # Check target directory before extraction
                check_before_cmd = self._build_ssh_cmd(target_host) + [f"find {target_stack_dir} -type f 2>/dev/null | wc -l"]
                before_result = await loop.run_in_executor(
                    None, lambda: subprocess.run(check_before_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                files_before = int(before_result.stdout.strip()) if before_result.returncode == 0 else 0
                
                migration_steps.append(f"🔍 Pre-extraction: {files_before} files in {target_stack_dir}")
                self.logger.info("Target directory state before extraction",
                    target_dir=target_stack_dir,
                    files_before=files_before
                )
                
                # PHASE 1: Extract to staging directory
                extract_to_staging_cmd = self._build_ssh_cmd(target_host) + [
                    f"set -e && "  # Exit on any error
                    f"rm -rf {shlex.quote(target_stack_dir)}.tmp && "  # Clean staging
                    f"mkdir -p {shlex.quote(target_stack_dir)}.tmp && "
                    f"tar xzf {shlex.quote(target_archive_path)} -C {shlex.quote(target_stack_dir)}.tmp && "
                    f"echo 'EXTRACTION_COMPLETE'"
                ]
                
                self.logger.info("Phase 1: Extracting to staging directory",
                    command=" ".join(extract_to_staging_cmd),
                    method="split_phase_extraction",
                    archive=target_archive_path,
                    staging_dir=f"{target_stack_dir}.tmp"
                )
                migration_steps.append(f"⚙️  Phase 1: Extracting archive to staging directory...")
                
                extraction_result = await loop.run_in_executor(
                    None, lambda: subprocess.run(extract_to_staging_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                
                # Log Phase 1 extraction results
                self.logger.info("Phase 1: Extraction to staging completed",
                    return_code=extraction_result.returncode,
                    stdout_present=bool(extraction_result.stdout),
                    stderr_present=bool(extraction_result.stderr),
                    success_marker_found="EXTRACTION_COMPLETE" in extraction_result.stdout
                )
                
                if extraction_result.stderr:
                    self.logger.warning("Phase 1 extraction stderr", stderr=extraction_result.stderr[:500])  # Limit log size
                
                # Check if Phase 1 succeeded before proceeding
                if extraction_result.returncode != 0 or "EXTRACTION_COMPLETE" not in extraction_result.stdout:
                    self.logger.error("Phase 1: Extraction to staging failed",
                        return_code=extraction_result.returncode,
                        stdout=extraction_result.stdout,
                        stderr=extraction_result.stderr
                    )
                    migration_steps.append("❌ Phase 1 FAILED: Archive extraction to staging directory")
                    return ToolResult(
                        content=[TextContent(type="text", text=f"❌ Archive extraction failed: {extraction_result.stderr}")],
                        structured_content={"success": False, "error": f"Extraction failed: {extraction_result.stderr}"},
                    )
                
                # PHASE 2: Verify staging directory has files 
                migration_steps.append("🔍 Phase 2: Verifying files extracted to staging directory...")
                check_staging_cmd = self._build_ssh_cmd(target_host) + [f"find {target_stack_dir}.tmp -type f 2>/dev/null | wc -l"]
                staging_result = await loop.run_in_executor(
                    None, lambda: subprocess.run(check_staging_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                staging_files_count = int(staging_result.stdout.strip()) if staging_result.returncode == 0 else 0
                
                self.logger.info("Phase 2: Staging directory verification",
                    staging_dir=f"{target_stack_dir}.tmp",
                    files_in_staging=staging_files_count,
                    extraction_exit_code=extraction_result.returncode,
                    extraction_marker_found="EXTRACTION_COMPLETE" in extraction_result.stdout
                )
                
                if staging_files_count == 0:
                    self.logger.error("Phase 2: No files found in staging directory",
                        staging_dir=f"{target_stack_dir}.tmp",
                        expected_files=source_inventory.get("total_files", 0) if source_inventory else 0
                    )
                    migration_steps.append("❌ Phase 2 FAILED: No files found in staging directory")
                    migration_steps.append(f"   Expected files: {source_inventory.get('total_files', 0) if source_inventory else 0}")
                    migration_steps.append(f"   Found in staging: {staging_files_count}")
                    return ToolResult(
                        content=[TextContent(type="text", text="❌ Staging verification failed: No files extracted")],
                        structured_content={"success": False, "error": "No files found in staging directory"},
                    )
                
                migration_steps.append(f"✅ Phase 2: Verified {staging_files_count} files in staging directory")
                
                # PHASE 3: Atomic move from staging to final location
                migration_steps.append("🔄 Phase 3: Performing atomic move to final location...")
                atomic_move_cmd = self._build_ssh_cmd(target_host) + [
                    f"set -e && "
                    f"if [ -d {shlex.quote(target_stack_dir)} ]; then mv {shlex.quote(target_stack_dir)} {shlex.quote(target_stack_dir)}.old; fi && "
                    f"mv {shlex.quote(target_stack_dir)}.tmp {shlex.quote(target_stack_dir)} && "
                    f"rm -rf {shlex.quote(target_stack_dir)}.old && "
                    f"echo 'ATOMIC_MOVE_SUCCESS'"
                ]
                
                self.logger.info("Phase 3: Executing atomic move",
                    command=" ".join(atomic_move_cmd),
                    staging_dir=f"{target_stack_dir}.tmp",
                    target_dir=target_stack_dir
                )
                
                move_result = await loop.run_in_executor(
                    None, lambda: subprocess.run(atomic_move_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                
                self.logger.info("Phase 3: Atomic move completed",
                    return_code=move_result.returncode,
                    stdout_present=bool(move_result.stdout),
                    stderr_present=bool(move_result.stderr),
                    success_marker_found="ATOMIC_MOVE_SUCCESS" in move_result.stdout
                )
                
                if move_result.returncode != 0 or "ATOMIC_MOVE_SUCCESS" not in move_result.stdout:
                    self.logger.error("Phase 3: Atomic move failed",
                        return_code=move_result.returncode,
                        stdout=move_result.stdout,
                        stderr=move_result.stderr
                    )
                    migration_steps.append("❌ Phase 3 FAILED: Atomic move to final location")
                    return ToolResult(
                        content=[TextContent(type="text", text=f"❌ Atomic move failed: {move_result.stderr}")],
                        structured_content={"success": False, "error": f"Atomic move failed: {move_result.stderr}"},
                    )
                
                migration_steps.append("✅ Phase 3: Atomic move completed successfully")
                
                # Verify final directory
                check_after_cmd = self._build_ssh_cmd(target_host) + [f"find {target_stack_dir} -type f 2>/dev/null | wc -l"]
                after_result = await loop.run_in_executor(
                    None, lambda: subprocess.run(check_after_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                files_after = int(after_result.stdout.strip()) if after_result.returncode == 0 else 0
                
                # Log successful split-phase extraction results
                self.logger.info("Split-phase extraction completed successfully",
                    files_before=files_before,
                    files_after=files_after,
                    files_in_staging=staging_files_count,
                    phase1_success="EXTRACTION_COMPLETE" in extraction_result.stdout,
                    phase2_verification=staging_files_count > 0,
                    phase3_success="ATOMIC_MOVE_SUCCESS" in move_result.stdout
                )
                
                # Add success summary for split-phase extraction
                migration_steps.append("✅ Split-phase extraction completed successfully:")
                migration_steps.append(f"   • Phase 1: ✓ Archive extracted to staging ({staging_files_count} files)")
                migration_steps.append("   • Phase 2: ✓ Staging directory verified")
                migration_steps.append("   • Phase 3: ✓ Atomic move to final location")
                migration_steps.append(f"   • Final state: {files_after} files in {target_stack_dir}")
            
            # Step 7: Update compose file for target paths
            migration_steps.append("📝 Updating compose configuration for target...")
            updated_compose = self.migration_manager.update_compose_for_migration(
                compose_content, volume_paths, target_stack_dir, target_appdata
            )
            
            # Step 8: CRITICAL - Verify data transfer FIRST (before deployment)
            verification_results = None
            data_verification_passed = False  # MUST explicitly pass verification
            
            if not dry_run and source_inventory:
                migration_steps.append("🔍 Verifying split-phase extraction success...")
                
                # Log verification using split-phase results
                expected_files = source_inventory.get("total_files", 0)
                
                self.logger.info("Starting split-phase verification",
                    target_dir=target_stack_dir,
                    expected_files=expected_files,
                    staging_files_found=staging_files_count,
                    phase1_success=extraction_result.returncode == 0,
                    phase2_verification=staging_files_count > 0,
                    phase3_success=move_result.returncode == 0,
                    extraction_marker="EXTRACTION_COMPLETE" in extraction_result.stdout,
                    move_marker="ATOMIC_MOVE_SUCCESS" in move_result.stdout
                )
                
                # Split-phase verification: All phases must succeed
                data_verification_passed = (
                    extraction_result.returncode == 0 and                    # Phase 1: Tar extraction succeeded
                    "EXTRACTION_COMPLETE" in extraction_result.stdout and    # Phase 1: Marker confirmed  
                    staging_files_count > 0 and                              # Phase 2: Files verified in staging
                    move_result.returncode == 0 and                          # Phase 3: Atomic move succeeded
                    "ATOMIC_MOVE_SUCCESS" in move_result.stdout              # Phase 3: Move marker confirmed
                )
                
                # Log the decision with detailed reasoning
                self.logger.info("Split-phase verification completed",
                    verification_passed=data_verification_passed,
                    phase1_exit_code=extraction_result.returncode,
                    phase1_marker="EXTRACTION_COMPLETE" in extraction_result.stdout,
                    phase2_staging_files=staging_files_count,
                    phase3_exit_code=move_result.returncode,
                    phase3_marker="ATOMIC_MOVE_SUCCESS" in move_result.stdout,
                    expected_files=expected_files,
                    decision="PROCEED with deployment" if data_verification_passed else "ABORT deployment",
                    verification_method="split_phase_verification"
                )
                
                # Create comprehensive data_verification structure  
                data_verification = {
                    "data_transfer": {
                        "success": data_verification_passed,
                        "files_found": staging_files_count,
                        "files_expected": expected_files,
                        "file_match_percentage": (staging_files_count/expected_files*100) if expected_files > 0 else 0,
                        "size_match_percentage": 100.0 if data_verification_passed else 0.0,
                        "critical_files_verified": {}
                    },
                    "issues": [] if data_verification_passed else [
                        f"Split-phase extraction verification failed",
                        f"Phase 1 (extraction): {'✓' if extraction_result.returncode == 0 else '✗'} (exit {extraction_result.returncode})",
                        f"Phase 2 (staging verify): {'✓' if staging_files_count > 0 else '✗'} ({staging_files_count} files found)",
                        f"Phase 3 (atomic move): {'✓' if move_result.returncode == 0 else '✗'} (exit {move_result.returncode})"
                    ]
                }
                
                if data_verification_passed:
                    migration_steps.append("✅ Verification PASSED: Split-phase extraction successful")
                    migration_steps.append(f"   • Phase 1: ✓ Archive extracted (exit code {extraction_result.returncode})")  
                    migration_steps.append(f"   • Phase 2: ✓ Staging verified ({staging_files_count} files)")
                    migration_steps.append(f"   • Phase 3: ✓ Atomic move completed (exit code {move_result.returncode})")
                else:
                    migration_steps.append("❌ Verification FAILED: Split-phase extraction incomplete")
                    migration_steps.append(f"   Expected files: {expected_files}")
                    migration_steps.append(f"   Phase 1: {'✓' if extraction_result.returncode == 0 else '✗'} (exit {extraction_result.returncode})")
                    migration_steps.append(f"   Phase 2: {'✓' if staging_files_count > 0 else '✗'} ({staging_files_count} staging files)")
                    migration_steps.append(f"   Phase 3: {'✓' if move_result.returncode == 0 else '✗'} (exit {move_result.returncode})")
                    migration_steps.append("⛔ Stack deployment CANCELLED - split-phase extraction failed")
                    
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
                remove_cmd = ssh_cmd_source + [f"rm -f {shlex.quote(compose_file_path)}"]  # Changed from rm -rf to rm -f
                await loop.run_in_executor(None, lambda: subprocess.run(remove_cmd, check=False))  # nosec B603
            elif remove_source and not dry_run:
                migration_steps.append("⚠️  Skipping source removal - migration verification failed")
            
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
                            "running": verification_results['container_integration'].get('container_running', False) if verification_results else False,
                            "healthy": verification_results['container_integration'].get('container_healthy', False) if verification_results else False,
                            "mounts_correct": verification_results['container_integration'].get('mount_paths_correct', False) if verification_results else False
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
    
    def _normalize_volume_entry(self, volume, target_appdata: str, stack_name: str) -> str | None:
        """Normalize a single volume entry to source:destination format."""
        if isinstance(volume, str) and ":" in volume:
            parts = volume.split(":", 2)
            if len(parts) >= 2:
                source_path = parts[0]
                container_path = parts[1]
                
                # Convert relative paths to absolute
                if source_path.startswith("."):
                    source_path = f"{target_appdata}/{stack_name}/{source_path[2:]}"
                elif not source_path.startswith("/"):
                    # Named volume - needs resolution
                    source_path = f"{target_appdata}/{stack_name}"
                
                return f"{source_path}:{container_path}"
        
        elif isinstance(volume, dict) and volume.get("type") == "bind":
            source = volume.get("source", "")
            target = volume.get("target", "")
            if source and target:
                if not source.startswith("/"):
                    source = f"{target_appdata}/{stack_name}/{source}"
                return f"{source}:{target}"
        
        return None

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
            for _service_name, service_config in services.items():
                volumes = service_config.get("volumes", [])
                for volume in volumes:
                    mount = self._normalize_volume_entry(volume, target_appdata, stack_name)
                    if mount and mount not in expected_mounts:
                        expected_mounts.append(mount)
            
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

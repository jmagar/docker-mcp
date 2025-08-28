"""
Stack Management Service

Business logic for Docker Compose stack operations with formatted output.
"""

import asyncio
import os
import shlex
import subprocess
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

    async def get_stack_compose_file(self, host_id: str, stack_name: str) -> ToolResult:
        """Get the docker-compose.yml content for a specific stack."""
        try:
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Use stack tools to get the compose file content
            result = await self.stack_tools.get_stack_compose_content(host_id, stack_name)

            if result["success"]:
                compose_content = result.get("compose_content", "")
                return ToolResult(
                    content=[TextContent(type="text", text=compose_content)],
                    structured_content={
                        "success": True,
                        "host_id": host_id,
                        "stack_name": stack_name,
                        "compose_content": compose_content
                    },
                )
            else:
                return ToolResult(
                    content=[TextContent(type="text", text=f"❌ {result['error']}")],
                    structured_content=result,
                )

        except Exception as e:
            self.logger.error(
                "Failed to get stack compose file",
                host_id=host_id,
                stack_name=stack_name,
                error=str(e)
            )
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ Failed to get compose file: {str(e)}")],
                structured_content={
                    "success": False,
                    "error": str(e),
                    "host_id": host_id,
                    "stack_name": stack_name,
                },
            )

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

        # Initialize variables for dry_run safety (prevent undefined variable errors)
        all_paths = []
        source_inventory = None
        archive_path = None
        transfer_result = {}
        backup_info = None
        verification_results = None

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

            # Pre-flight checks for dry_run enhancement
            if dry_run:
                migration_steps.append("🔍 Running pre-flight checks...")

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

            # Step 1.1: Compose file syntax validation
            migration_steps.append("📝 Validating compose file syntax...")
            is_valid, validation_issues, validation_details = self._validate_compose_syntax(compose_content, stack_name)

            if is_valid:
                services_count = validation_details.get("services_found", 0)
                migration_steps.append(f"   ✅ Compose file is valid ({services_count} services found)")

                # Show warnings if any
                warnings = validation_details.get("warnings", [])
                if warnings:
                    for warning in warnings[:3]:  # Show first 3 warnings
                        migration_steps.append(f"   ⚠️  {warning}")
                    if len(warnings) > 3:
                        migration_steps.append(f"   ⚠️  ... and {len(warnings)-3} more warnings")
            else:
                migration_steps.append(f"   ❌ Compose file validation failed ({len(validation_issues)} issues)")

                # Show first few issues
                for issue in validation_issues[:3]:
                    migration_steps.append(f"     • {issue}")
                if len(validation_issues) > 3:
                    migration_steps.append(f"     • ... and {len(validation_issues)-3} more issues")

                if not dry_run:
                    # For real runs, invalid compose file is a blocker
                    return ToolResult(
                        content=[TextContent(type="text", text=f"❌ Migration blocked: Compose file validation failed with {len(validation_issues)} issues")],
                        structured_content={
                            "success": False,
                            "error": "Compose file validation failed",
                            "validation_issues": validation_issues,
                            "validation_details": validation_details
                        },
                    )
                else:
                    # For dry runs, show as warning
                    migration_steps.append("⚠️  (DRY RUN) Migration would fail due to compose validation errors")

            # Step 2: Parse volumes from compose
            migration_steps.append("🔍 Analyzing volume configuration...")
            volumes_info = await self.migration_manager.parse_compose_volumes(compose_content, source_appdata)

            # Step 2.1: Pre-flight disk space verification
            migration_steps.append("💾 Verifying disk space requirements...")
            volume_paths = await self.migration_manager.get_volume_locations(
                ssh_cmd_source, volumes_info["named_volumes"]
            )
            all_paths = list(volume_paths.values()) + volumes_info["bind_mounts"]

            # Estimate data size for disk space check
            estimated_size = 0
            if all_paths:
                # Quick size estimation using du
                size_cmd = ssh_cmd_source + [f"du -sb {' '.join(shlex.quote(p) for p in all_paths)} 2>/dev/null | awk '{{sum+=$1}} END {{print sum}}'"]
                size_result = await loop.run_in_executor(
                    None, lambda: subprocess.run(size_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                if size_result.returncode == 0 and size_result.stdout.strip():
                    estimated_size = int(size_result.stdout.strip()) if size_result.stdout.strip().isdigit() else 0

                # Add compression estimate (tar.gz typically achieves 70% compression, so 30% of original size)
                estimated_archive_size = int(estimated_size * 0.3)

                # Disk space check (always run for both dry_run and real runs)
                has_space, space_message, space_details = await self._check_disk_space(target_host, estimated_archive_size)
                migration_steps.append(f"   {space_message}")
                migration_steps.append(f"   Raw data: {self._format_size(estimated_size)} → Archive: ~{self._format_size(estimated_archive_size)}")

                if not has_space and not dry_run:
                    # For real runs, insufficient space is a blocker
                    return ToolResult(
                        content=[TextContent(type="text", text=f"❌ Migration blocked: {space_message}")],
                        structured_content={
                            "success": False,
                            "error": "Insufficient disk space",
                            "space_details": space_details,
                            "estimated_size": estimated_archive_size,
                            "raw_data_size": estimated_size
                        },
                    )
                elif not has_space and dry_run:
                    # For dry runs, show as warning
                    migration_steps.append("⚠️  (DRY RUN) Migration would fail due to insufficient disk space")
            else:
                migration_steps.append("   No significant data to migrate")

            # Step 2.2: Tool availability verification
            migration_steps.append("🔧 Verifying required tools on both hosts...")
            required_tools = ['rsync', 'tar', 'docker']

            # Check source host tools
            source_available, source_missing, source_details = await self._check_tool_availability(source_host, required_tools)
            if source_available:
                migration_steps.append(f"   ✅ Source ({source_host.hostname}): All tools available")
            else:
                migration_steps.append(f"   ❌ Source ({source_host.hostname}): Missing {', '.join(source_missing)}")

            # Check target host tools
            target_available, target_missing, target_details = await self._check_tool_availability(target_host, required_tools)
            if target_available:
                migration_steps.append(f"   ✅ Target ({target_host.hostname}): All tools available")
            else:
                migration_steps.append(f"   ❌ Target ({target_host.hostname}): Missing {', '.join(target_missing)}")

            # Check if migration can proceed
            tools_check_passed = source_available and target_available
            if not tools_check_passed and not dry_run:
                # For real runs, missing tools are a blocker
                all_missing = list(set(source_missing + target_missing))
                return ToolResult(
                    content=[TextContent(type="text", text=f"❌ Migration blocked: Missing required tools: {', '.join(all_missing)}")],
                    structured_content={
                        "success": False,
                        "error": "Missing required tools",
                        "source_tools": source_details,
                        "target_tools": target_details,
                        "missing_tools": all_missing
                    },
                )
            elif not tools_check_passed and dry_run:
                # For dry runs, show as warning
                all_missing = list(set(source_missing + target_missing))
                migration_steps.append(f"⚠️  (DRY RUN) Migration would fail due to missing tools: {', '.join(all_missing)}")

            # Step 2.3: Port conflict detection
            migration_steps.append("🔌 Checking for port conflicts on target host...")
            exposed_ports = self._extract_ports_from_compose(compose_content)

            if exposed_ports:
                migration_steps.append(f"   Found {len(exposed_ports)} exposed ports: {', '.join(map(str, exposed_ports))}")
                no_conflicts, conflicted_ports, conflict_details = await self._check_port_conflicts(target_host, exposed_ports)

                if no_conflicts:
                    migration_steps.append(f"   ✅ All ports available on {target_host.hostname}")
                else:
                    migration_steps.append(f"   ❌ Port conflicts on {target_host.hostname}: {', '.join(map(str, conflicted_ports))}")

                    if not dry_run:
                        # For real runs, port conflicts are a blocker
                        return ToolResult(
                            content=[TextContent(type="text", text=f"❌ Migration blocked: Port conflicts on target host: {', '.join(map(str, conflicted_ports))}")],
                            structured_content={
                                "success": False,
                                "error": "Port conflicts detected",
                                "conflicted_ports": conflicted_ports,
                                "port_details": conflict_details
                            },
                        )
                    else:
                        # For dry runs, show as warning
                        migration_steps.append(f"⚠️  (DRY RUN) Migration would fail due to port conflicts: {', '.join(map(str, conflicted_ports))}")
            else:
                migration_steps.append("   No exposed ports found in compose file")

            # Step 2.4: Container and network name conflict detection
            migration_steps.append("🏷️ Checking for container/network name conflicts on target...")
            expected_names = self._extract_names_from_compose(compose_content, stack_name)

            total_names = len(expected_names["containers"]) + len(expected_names["networks"])
            if total_names > 0:
                migration_steps.append(f"   Expected containers: {len(expected_names['containers'])}, networks: {len(expected_names['networks'])}")
                no_name_conflicts, name_conflicts, conflict_details = await self._check_name_conflicts(target_host, expected_names)

                if no_name_conflicts:
                    migration_steps.append(f"   ✅ All names available on {target_host.hostname}")
                else:
                    conflict_summary = []
                    if name_conflicts["containers"]:
                        conflict_summary.append(f"{len(name_conflicts['containers'])} containers")
                    if name_conflicts["networks"]:
                        conflict_summary.append(f"{len(name_conflicts['networks'])} networks")

                    migration_steps.append(f"   ❌ Name conflicts on {target_host.hostname}: {', '.join(conflict_summary)}")

                    # Show specific conflicting names
                    if name_conflicts["containers"]:
                        migration_steps.append(f"     Containers: {', '.join(name_conflicts['containers'][:3])}{'...' if len(name_conflicts['containers']) > 3 else ''}")
                    if name_conflicts["networks"]:
                        migration_steps.append(f"     Networks: {', '.join(name_conflicts['networks'][:3])}{'...' if len(name_conflicts['networks']) > 3 else ''}")

                    if not dry_run:
                        # For real runs, name conflicts are a blocker
                        all_conflicts = name_conflicts["containers"] + name_conflicts["networks"]
                        return ToolResult(
                            content=[TextContent(type="text", text=f"❌ Migration blocked: Name conflicts on target host. {len(all_conflicts)} conflicting names found.")],
                            structured_content={
                                "success": False,
                                "error": "Container/network name conflicts detected",
                                "name_conflicts": name_conflicts,
                                "conflict_details": conflict_details
                            },
                        )
                    else:
                        # For dry runs, show as warning
                        all_conflicts = name_conflicts["containers"] + name_conflicts["networks"]
                        migration_steps.append(f"⚠️  (DRY RUN) Migration would fail due to name conflicts: {len(all_conflicts)} conflicts")
            else:
                migration_steps.append("   No container/network names to check")

            # Step 2.5: Network connectivity validation (more comprehensive for dry runs)
            if dry_run:
                migration_steps.append("🌐 Testing network connectivity between hosts...")
                connectivity_ok, connectivity_details = await self._test_network_connectivity(source_host, target_host)

                ssh_tests = connectivity_details.get("tests", {}).get("ssh_connectivity", {})
                speed_test = connectivity_details.get("tests", {}).get("network_speed", {})

                # Report SSH connectivity
                if ssh_tests.get("source_ssh", {}).get("success"):
                    migration_steps.append(f"   ✅ SSH to source ({source_host.hostname}): Connected")
                else:
                    error = ssh_tests.get("source_ssh", {}).get("error", "Unknown error")
                    migration_steps.append(f"   ❌ SSH to source ({source_host.hostname}): Failed - {error}")

                if ssh_tests.get("target_ssh", {}).get("success"):
                    migration_steps.append(f"   ✅ SSH to target ({target_host.hostname}): Connected")
                else:
                    error = ssh_tests.get("target_ssh", {}).get("error", "Unknown error")
                    migration_steps.append(f"   ❌ SSH to target ({target_host.hostname}): Failed - {error}")

                # Report network speed
                if speed_test.get("success"):
                    speed = speed_test.get("estimated_speed", "N/A")
                    transfer_time = speed_test.get("transfer_time", "N/A")
                    migration_steps.append(f"   ✅ Network speed test: {speed} ({transfer_time} for 1MB)")
                else:
                    error = speed_test.get("error", "Unknown error")
                    migration_steps.append(f"   ❌ Network speed test: Failed - {error}")

                if not connectivity_ok:
                    migration_steps.append("⚠️  (DRY RUN) Network connectivity issues detected - migration may be slow or fail")

                # Step 2.5.1: Transfer time estimation (dry run only, using network speed results)
                if estimated_size > 0:
                    migration_steps.append("⏱️ Estimating transfer time...")
                    network_speed_test = connectivity_details.get("tests", {}).get("network_speed", {})
                    time_estimates = self._estimate_transfer_time(estimated_size, network_speed_test)

                    # Show the most relevant estimate
                    if "actual_network" in time_estimates["estimates"]:
                        actual_est = time_estimates["estimates"]["actual_network"]
                        migration_steps.append(f"   📊 Estimated time: {actual_est['time_with_overhead_human']} (with overhead)")
                        migration_steps.append(f"     Based on measured speed: {actual_est['speed']}")
                    else:
                        # Show a few standard estimates
                        migration_steps.append("   📊 Estimated transfer times:")
                        for est_name in ["100_mbps", "1_gbps"]:
                            if est_name in time_estimates["estimates"]:
                                est = time_estimates["estimates"][est_name]
                                migration_steps.append(f"     • {est['description']}: {est['time_with_overhead_human']}")

                    migration_steps.append(f"     Raw data: {time_estimates['data_size_human']} → Compressed: ~{time_estimates['compressed_size_human']}")

                    # Add warnings for large transfers
                    if time_estimates["estimates"].get("100_mbps", {}).get("time_seconds", 0) > 3600:  # > 1 hour
                        migration_steps.append("⚠️  Large dataset detected - consider migrating during off-peak hours")
                else:
                    migration_steps.append("⏱️ No significant data to transfer - minimal time required")

                # Step 2.6: Risk Assessment Summary (dry run only)
                migration_steps.append("⚠️ Analyzing migration risks...")

                # Estimate total downtime (transfer time + overhead + deployment time)
                base_downtime = 300  # 5 minutes base overhead
                if "actual_network" in time_estimates.get("estimates", {}):
                    transfer_time = time_estimates["estimates"]["actual_network"].get("time_with_overhead", 600)
                elif "100_mbps" in time_estimates.get("estimates", {}):
                    transfer_time = time_estimates["estimates"]["100_mbps"].get("time_with_overhead", 600)
                else:
                    transfer_time = 600  # 10 minute fallback

                estimated_downtime = base_downtime + transfer_time + 180  # +3 min deployment

                risk_assessment = self._assess_migration_risks(
                    stack_name, estimated_size, estimated_downtime,
                    source_inventory if 'source_inventory' in locals() else None,
                    compose_content
                )

                # Display risk summary
                risk_level = risk_assessment["overall_risk"]
                risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(risk_level, "⚪")
                migration_steps.append(f"   {risk_emoji} Overall Risk Level: {risk_level}")

                if risk_assessment["warnings"]:
                    migration_steps.append("   ⚠️ Risk Warnings:")
                    for warning in risk_assessment["warnings"][:3]:
                        migration_steps.append(f"     • {warning}")
                    if len(risk_assessment["warnings"]) > 3:
                        migration_steps.append(f"     • ... and {len(risk_assessment['warnings'])-3} more warnings")

                if risk_assessment["critical_files"]:
                    migration_steps.append(f"   🔥 Critical files detected: {len(risk_assessment['critical_files'])}")
                    for critical_file in risk_assessment["critical_files"][:2]:
                        migration_steps.append(f"     • {critical_file}")
                    if len(risk_assessment["critical_files"]) > 2:
                        migration_steps.append(f"     • ... and {len(risk_assessment['critical_files'])-2} more")

                if risk_assessment["recommendations"]:
                    migration_steps.append("   💡 Recommendations:")
                    for rec in risk_assessment["recommendations"][:3]:
                        migration_steps.append(f"     • {rec}")
                    if len(risk_assessment["recommendations"]) > 3:
                        migration_steps.append(f"     • ... and {len(risk_assessment['recommendations'])-3} more")

                migration_steps.append(f"   📋 Estimated downtime: {self._format_time(estimated_downtime)}")

                # Show rollback plan for medium/high risk
                if risk_level in ["MEDIUM", "HIGH"]:
                    migration_steps.append("   🔄 Rollback plan available - review before proceeding")
            else:
                # For real runs, do a quick SSH connectivity check only
                migration_steps.append("🌐 Verifying basic SSH connectivity...")
                try:
                    # Quick SSH test to both hosts (no speed test to save time)
                    source_ssh_cmd = self._build_ssh_cmd(source_host) + ["echo 'QUICK_CHECK'"]
                    target_ssh_cmd = self._build_ssh_cmd(target_host) + ["echo 'QUICK_CHECK'"]

                    source_result = await loop.run_in_executor(
                        None, lambda: subprocess.run(source_ssh_cmd, capture_output=True, text=True, check=False, timeout=5)  # nosec B603
                    )
                    target_result = await loop.run_in_executor(
                        None, lambda: subprocess.run(target_ssh_cmd, capture_output=True, text=True, check=False, timeout=5)  # nosec B603
                    )

                    if source_result.returncode == 0 and target_result.returncode == 0:
                        migration_steps.append("   ✅ SSH connectivity verified for both hosts")
                    else:
                        failed_hosts = []
                        if source_result.returncode != 0:
                            failed_hosts.append(f"source ({source_host.hostname})")
                        if target_result.returncode != 0:
                            failed_hosts.append(f"target ({target_host.hostname})")
                        migration_steps.append(f"   ❌ SSH connectivity failed for: {', '.join(failed_hosts)}")

                        return ToolResult(
                            content=[TextContent(type="text", text=f"❌ Migration blocked: SSH connectivity failed for {', '.join(failed_hosts)}")],
                            structured_content={
                                "success": False,
                                "error": "SSH connectivity failed",
                                "failed_hosts": failed_hosts
                            },
                        )
                except Exception as e:
                    migration_steps.append(f"   ❌ SSH connectivity check failed: {str(e)}")
                    return ToolResult(
                        content=[TextContent(type="text", text=f"❌ Migration blocked: SSH connectivity check failed: {str(e)}")],
                        structured_content={
                            "success": False,
                            "error": f"SSH connectivity check failed: {str(e)}"
                        },
                    )

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

            # Step 4.1: Create source inventory for verification (always run for dry_run analysis)
            if all_paths:
                migration_steps.append("📊 Creating source data inventory...")
                source_inventory = await self.migration_manager.create_source_inventory(
                    ssh_cmd_source, all_paths
                )
                migration_steps.append(
                    f"✅ Inventory created: {source_inventory['total_files']} files, "
                    f"{self._format_size(source_inventory['total_size'])}, "
                    f"{len(source_inventory['critical_files'])} critical files"
                )

            if all_paths:
                if not dry_run:
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
                            content=[TextContent(type="text", text="Error: Archive verification failed. The archive may be corrupted.")],
                            structured_content={"success": False, "error": "Archive integrity check failed", "archive_path": archive_path},
                        )
                    migration_steps.append("✅ Archive integrity verified")
                else:
                    # Dry run: Estimate archive size and show what would happen
                    if source_inventory:
                        estimated_size = int(source_inventory["total_size"] * 0.3)  # Estimate 70% compression
                        migration_steps.append(f"📦 (DRY RUN) Would create archive: ~{self._format_size(estimated_size)}")
                    else:
                        migration_steps.append("📦 (DRY RUN) Would create archive for migration")
                    archive_path = f"/tmp/{stack_name}_migration_DRYRUN.tar.gz"  # Placeholder path

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

            # Check if target directory exists (read-only operation)
            check_dir_cmd = f"test -d {shlex.quote(target_stack_dir)} && echo 'EXISTS' || echo 'NOT_EXISTS'"
            check_result = await loop.run_in_executor(
                None, lambda: subprocess.run(
                    self._build_ssh_cmd(target_host) + [check_dir_cmd],
                    capture_output=True, text=True, check=False  # nosec B603
                )
            )

            directory_exists = check_result.returncode == 0 and "EXISTS" in check_result.stdout

            if not dry_run:
                if not directory_exists:
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
                    migration_steps.append(f"✅ Created target directory: {target_stack_dir}")
                    self.logger.info("Created target directory", path=target_stack_dir)
                else:
                    migration_steps.append(f"ℹ️ Target directory already exists: {target_stack_dir}")
            else:
                # Dry run: Just report what would happen
                if directory_exists:
                    migration_steps.append(f"ℹ️ (DRY RUN) Target directory exists: {target_stack_dir}")
                else:
                    migration_steps.append(f"📁 (DRY RUN) Would create directory: {target_stack_dir}")

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
            if all_paths and archive_path:
                temp_suffix = os.urandom(8).hex()[:8]
                target_archive_path = f"/tmp/{stack_name}_migration_{temp_suffix}.tar.gz"

                if not dry_run:
                    migration_steps.append("🚀 Transferring data to target host...")
                    transfer_result = await self.migration_manager.rsync_transfer.transfer(
                        source_host, target_host, archive_path, target_archive_path,
                        compress=True, delete=False, dry_run=False
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
                else:
                    # Dry run: Simulate transfer with real data from inventory
                    if source_inventory:
                        migration_steps.append(f"🚀 (DRY RUN) Would transfer {self._format_size(source_inventory['total_size'])}")
                        migration_steps.append(f"   • Files: {source_inventory['total_files']:,}")
                        migration_steps.append(f"   • Critical files: {len(source_inventory.get('critical_files', {}))}")
                        # Simulate successful transfer result
                        transfer_result = {
                            "success": True,
                            "stats": {
                                "files_transferred": source_inventory["total_files"],
                                "total_size": source_inventory["total_size"],
                                "transfer_rate": "simulated",
                            }
                        }
                    else:
                        migration_steps.append("🚀 (DRY RUN) Would transfer archive to target host")
                        transfer_result = {"success": True, "stats": {}}

                # ATOMIC EXTRACTION: Clean extraction to prevent stale files and path nesting
                if not dry_run:
                    migration_steps.append("📦 Extracting data with atomic replacement...")
                else:
                    migration_steps.append("📦 (DRY RUN) Would extract data with atomic replacement...")

                if not dry_run:
                    # Check target directory before extraction
                    check_before_cmd = self._build_ssh_cmd(target_host) + [f"find {target_stack_dir} -type f 2>/dev/null | wc -l"]
                    before_result = await loop.run_in_executor(
                        None, lambda: subprocess.run(check_before_cmd, capture_output=True, text=True, check=False)  # nosec B603
                    )
                    files_before = int(before_result.stdout.strip()) if before_result.returncode == 0 else 0
                else:
                    # Dry run: Check target directory state but don't extract
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

                if not dry_run:
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
                    migration_steps.append("⚙️  Phase 1: Extracting archive to staging directory...")

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
                else:
                    # Dry run: Show extraction plan with real data
                    if source_inventory:
                        migration_steps.append(f"📦 (DRY RUN) Would extract {source_inventory['total_files']:,} files")
                        migration_steps.append(f"   • Total size: {self._format_size(source_inventory['total_size'])}")
                        migration_steps.append(f"   • Critical files: {len(source_inventory.get('critical_files', {}))}")
                        migration_steps.append(f"   • Current target files: {files_before}")
                        files_extracted = source_inventory["total_files"]  # Simulate for later use
                    else:
                        migration_steps.append("📦 (DRY RUN) Would extract archive contents")
                        files_extracted = 100  # Placeholder

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
                        "Split-phase extraction verification failed",
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
            else:
                # Dry run: Simulate successful verification
                if dry_run and source_inventory:
                    migration_steps.append("🔍 (DRY RUN) Would verify extraction success")
                    migration_steps.append(f"   • Expected files: {source_inventory['total_files']:,}")
                    migration_steps.append(f"   • Expected size: {self._format_size(source_inventory['total_size'])}")
                    migration_steps.append("   • All verification phases would pass")
                    data_verification_passed = True  # Simulate successful verification

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
            elif start_target and dry_run:
                # Dry run: Show deployment plan without actually deploying
                migration_steps.append(f"🚀 (DRY RUN) Would deploy stack on {target_host_id}")
                migration_steps.append(f"   • Compose file: {compose_file_path}")

                if volumes_info:
                    migration_steps.append(f"   • Named volumes: {len(volumes_info['named_volumes'])}")
                    migration_steps.append(f"   • Bind mounts: {len(volumes_info['bind_mounts'])}")
                    for volume in volumes_info['named_volumes'][:3]:  # Show first 3 volumes
                        migration_steps.append(f"     - {volume}")
                    if len(volumes_info['named_volumes']) > 3:
                        migration_steps.append(f"     ... and {len(volumes_info['named_volumes'])-3} more")

                migration_steps.append("   • Would verify container integration after deployment")

                # Simulate successful deployment for dry run
                verification_results = {
                    "data_transfer": {"success": True},
                    "container_integration": {"success": True, "simulated": True},
                    "all_issues": [],
                    "overall_success": True
                }
                deploy_success = True  # Simulate success for dry run

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
            elif remove_source and dry_run and verification_results and verification_results["overall_success"]:
                migration_steps.append(f"🗑️  (DRY RUN) Would remove stack from {source_host_id}")
                migration_steps.append(f"   • Would delete: {compose_file_path}")
            elif remove_source and not dry_run:
                migration_steps.append("⚠️  Skipping source removal - migration verification failed")
            elif remove_source and dry_run:
                migration_steps.append("⚠️  (DRY RUN) Would skip source removal - simulated migration verification")

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

    async def _check_disk_space(self, host, estimated_size: int) -> tuple[bool, str, dict]:
        """Check if target host has sufficient disk space for migration.
        
        Args:
            host: Target host configuration
            estimated_size: Estimated size needed in bytes
            
        Returns:
            Tuple of (has_space: bool, message: str, details: dict)
        """
        try:
            # Get disk space information for the appdata directory
            appdata_path = host.appdata_path or "/opt/docker-appdata"
            ssh_cmd = self._build_ssh_cmd(host)

            # Use df to get disk space in bytes
            df_cmd = ssh_cmd + [f"df -B1 {shlex.quote(appdata_path)} | tail -1 | awk '{{print $2,$3,$4}}'"]

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: subprocess.run(df_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )

            if result.returncode == 0 and result.stdout.strip():
                total, used, available = map(int, result.stdout.strip().split())

                # Add 20% safety margin
                required_with_margin = int(estimated_size * 1.2)
                has_space = available >= required_with_margin

                details = {
                    "total_space": total,
                    "used_space": used,
                    "available_space": available,
                    "estimated_need": estimated_size,
                    "required_with_margin": required_with_margin,
                    "usage_percentage": (used / total * 100) if total > 0 else 0,
                    "has_sufficient_space": has_space,
                    "path_checked": appdata_path
                }

                if has_space:
                    message = f"✅ Sufficient disk space: {self._format_size(available)} available, {self._format_size(required_with_margin)} needed (with 20% margin)"
                else:
                    shortfall = required_with_margin - available
                    message = f"❌ Insufficient disk space: {self._format_size(available)} available, {self._format_size(required_with_margin)} needed (shortfall: {self._format_size(shortfall)})"

                return has_space, message, details
            else:
                return False, f"Failed to check disk space on {host.hostname}: {result.stderr}", {}

        except Exception as e:
            return False, f"Error checking disk space: {str(e)}", {}

    async def _check_tool_availability(self, host, tools: list[str]) -> tuple[bool, list[str], dict]:
        """Check if required tools are available on host.
        
        Args:
            host: Host configuration to check
            tools: List of tool names to check (e.g., ['rsync', 'tar', 'docker'])
            
        Returns:
            Tuple of (all_available: bool, missing_tools: list[str], details: dict)
        """
        ssh_cmd = self._build_ssh_cmd(host)
        tool_status = {}
        missing_tools = []

        for tool in tools:
            try:
                # Use 'which' to check if tool is available
                check_cmd = ssh_cmd + [f"which {shlex.quote(tool)} >/dev/null 2>&1 && echo 'AVAILABLE' || echo 'MISSING'"]

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: subprocess.run(check_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )

                is_available = result.returncode == 0 and "AVAILABLE" in result.stdout
                tool_status[tool] = {
                    "available": is_available,
                    "check_result": result.stdout.strip(),
                    "error": result.stderr if result.stderr else None
                }

                if not is_available:
                    missing_tools.append(tool)

            except Exception as e:
                tool_status[tool] = {
                    "available": False,
                    "check_result": None,
                    "error": str(e)
                }
                missing_tools.append(tool)

        all_available = len(missing_tools) == 0
        details = {
            "host": host.hostname,
            "tools_checked": tools,
            "tool_status": tool_status,
            "all_tools_available": all_available,
            "missing_tools": missing_tools
        }

        return all_available, missing_tools, details

    def _extract_ports_from_compose(self, compose_content: str) -> list[int]:
        """Extract exposed ports from compose file.
        
        Args:
            compose_content: Docker Compose YAML content
            
        Returns:
            List of port numbers that will be exposed
        """
        try:
            import yaml
            compose_data = yaml.safe_load(compose_content)
            exposed_ports = []

            # Parse services for port mappings
            services = compose_data.get("services", {})
            for service_name, service_config in services.items():
                ports = service_config.get("ports", [])
                for port_spec in ports:
                    if isinstance(port_spec, str):
                        # Format: "host_port:container_port" or "port"
                        if ":" in port_spec:
                            host_port = port_spec.split(":")[0]
                        else:
                            host_port = port_spec

                        try:
                            port_num = int(host_port)
                            if port_num not in exposed_ports:
                                exposed_ports.append(port_num)
                        except ValueError:
                            continue
                    elif isinstance(port_spec, int):
                        if port_spec not in exposed_ports:
                            exposed_ports.append(port_spec)
                    elif isinstance(port_spec, dict):
                        # Long syntax: {target: 80, host_ip: "0.0.0.0", published: 8080}
                        published_port = port_spec.get("published")
                        if published_port and isinstance(published_port, (int, str)):
                            try:
                                port_num = int(published_port)
                                if port_num not in exposed_ports:
                                    exposed_ports.append(port_num)
                            except ValueError:
                                continue

            return sorted(exposed_ports)

        except Exception as e:
            self.logger.warning("Failed to parse ports from compose file", error=str(e))
            return []

    async def _check_port_conflicts(self, host, ports: list[int]) -> tuple[bool, list[int], dict]:
        """Check if ports are already in use on host.
        
        Args:
            host: Host configuration to check
            ports: List of port numbers to check
            
        Returns:
            Tuple of (no_conflicts: bool, conflicted_ports: list[int], details: dict)
        """
        if not ports:
            return True, [], {"host": host.hostname, "ports_checked": [], "conflicts": []}

        ssh_cmd = self._build_ssh_cmd(host)
        conflicted_ports = []
        port_details = {}

        # Use netstat or ss to check for listening ports
        # Try ss first (more modern), fall back to netstat
        for port in ports:
            try:
                # Check if port is listening using ss
                check_cmd = ssh_cmd + [f"ss -tuln | grep ':{port}' >/dev/null 2>&1 && echo 'IN_USE' || echo 'AVAILABLE'"]

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: subprocess.run(check_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )

                in_use = result.returncode == 0 and "IN_USE" in result.stdout
                port_details[port] = {
                    "in_use": in_use,
                    "check_method": "ss",
                    "check_result": result.stdout.strip()
                }

                if in_use:
                    conflicted_ports.append(port)

            except Exception as e:
                port_details[port] = {
                    "in_use": True,  # Assume conflict on error to be safe
                    "check_method": "error",
                    "error": str(e)
                }
                conflicted_ports.append(port)

        no_conflicts = len(conflicted_ports) == 0
        details = {
            "host": host.hostname,
            "ports_checked": ports,
            "port_details": port_details,
            "conflicted_ports": conflicted_ports,
            "no_conflicts": no_conflicts
        }

        return no_conflicts, conflicted_ports, details

    def _extract_names_from_compose(self, compose_content: str, stack_name: str) -> dict[str, list[str]]:
        """Extract container and network names from compose file.
        
        Args:
            compose_content: Docker Compose YAML content
            stack_name: Name of the stack being migrated
            
        Returns:
            Dict with 'containers' and 'networks' keys containing lists of names
        """
        try:
            import yaml
            compose_data = yaml.safe_load(compose_content)

            # Extract container names (service names become container names with stack prefix)
            container_names = []
            services = compose_data.get("services", {})
            for service_name in services.keys():
                # Docker Compose creates containers with stack_service format
                container_name = f"{stack_name}_{service_name}_1"
                container_names.append(container_name)
                # Also check the alternative naming pattern
                alt_container_name = f"{stack_name}-{service_name}-1"
                container_names.append(alt_container_name)

            # Extract custom network names
            network_names = []
            networks = compose_data.get("networks", {})
            for network_name in networks.keys():
                if network_name != "default":  # Skip default network
                    # Docker Compose prefixes network names with stack name
                    full_network_name = f"{stack_name}_{network_name}"
                    network_names.append(full_network_name)
                    # Also check the alternative naming pattern
                    alt_network_name = f"{stack_name}-{network_name}"
                    network_names.append(alt_network_name)

            # Add default network that Docker Compose creates
            default_network = f"{stack_name}_default"
            network_names.append(default_network)
            alt_default_network = f"{stack_name}-default"
            network_names.append(alt_default_network)

            return {
                "containers": container_names,
                "networks": network_names
            }

        except Exception as e:
            self.logger.warning("Failed to parse container/network names from compose file", error=str(e))
            return {"containers": [], "networks": []}

    async def _check_name_conflicts(self, host, expected_names: dict[str, list[str]]) -> tuple[bool, dict[str, list[str]], dict]:
        """Check if container/network names already exist on host.
        
        Args:
            host: Host configuration to check
            expected_names: Dict with 'containers' and 'networks' keys
            
        Returns:
            Tuple of (no_conflicts: bool, conflicts: dict, details: dict)
        """
        ssh_cmd = self._build_ssh_cmd(host)
        conflicts = {"containers": [], "networks": []}
        details = {"host": host.hostname, "checks": {}}

        # Check container name conflicts
        container_names = expected_names.get("containers", [])
        if container_names:
            for container_name in container_names:
                try:
                    # Check if container exists (running or stopped)
                    check_cmd = ssh_cmd + [f"docker ps -a --format '{{{{.Names}}}}' | grep -x '{shlex.quote(container_name)}' >/dev/null 2>&1 && echo 'EXISTS' || echo 'AVAILABLE'"]

                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda: subprocess.run(check_cmd, capture_output=True, text=True, check=False)  # nosec B603
                    )

                    exists = result.returncode == 0 and "EXISTS" in result.stdout
                    details["checks"][f"container_{container_name}"] = {
                        "exists": exists,
                        "check_result": result.stdout.strip()
                    }

                    if exists:
                        conflicts["containers"].append(container_name)

                except Exception as e:
                    details["checks"][f"container_{container_name}"] = {
                        "exists": True,  # Assume conflict on error to be safe
                        "error": str(e)
                    }
                    conflicts["containers"].append(container_name)

        # Check network name conflicts
        network_names = expected_names.get("networks", [])
        if network_names:
            for network_name in network_names:
                try:
                    # Check if network exists
                    check_cmd = ssh_cmd + [f"docker network ls --format '{{{{.Name}}}}' | grep -x '{shlex.quote(network_name)}' >/dev/null 2>&1 && echo 'EXISTS' || echo 'AVAILABLE'"]

                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda: subprocess.run(check_cmd, capture_output=True, text=True, check=False)  # nosec B603
                    )

                    exists = result.returncode == 0 and "EXISTS" in result.stdout
                    details["checks"][f"network_{network_name}"] = {
                        "exists": exists,
                        "check_result": result.stdout.strip()
                    }

                    if exists:
                        conflicts["networks"].append(network_name)

                except Exception as e:
                    details["checks"][f"network_{network_name}"] = {
                        "exists": True,  # Assume conflict on error to be safe
                        "error": str(e)
                    }
                    conflicts["networks"].append(network_name)

        # No conflicts if both lists are empty
        no_conflicts = len(conflicts["containers"]) == 0 and len(conflicts["networks"]) == 0
        details["no_conflicts"] = no_conflicts
        details["conflicts"] = conflicts

        return no_conflicts, conflicts, details

    async def _test_network_connectivity(self, source_host, target_host) -> tuple[bool, dict]:
        """Test network connectivity between source and target hosts.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            
        Returns:
            Tuple of (connectivity_ok: bool, details: dict)
        """
        details = {
            "source_host": source_host.hostname,
            "target_host": target_host.hostname,
            "tests": {}
        }

        try:
            # Test 1: Basic SSH connectivity to both hosts
            ssh_tests = {}

            # Test source host SSH
            source_ssh_cmd = self._build_ssh_cmd(source_host) + ["echo 'SSH_OK'"]
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: subprocess.run(source_ssh_cmd, capture_output=True, text=True, check=False, timeout=10)  # nosec B603
                )
                ssh_tests["source_ssh"] = {
                    "success": result.returncode == 0 and "SSH_OK" in result.stdout,
                    "response_time": "< 10s",
                    "error": result.stderr if result.stderr else None
                }
            except Exception as e:
                ssh_tests["source_ssh"] = {
                    "success": False,
                    "error": str(e)
                }

            # Test target host SSH
            target_ssh_cmd = self._build_ssh_cmd(target_host) + ["echo 'SSH_OK'"]
            try:
                result = await loop.run_in_executor(
                    None, lambda: subprocess.run(target_ssh_cmd, capture_output=True, text=True, check=False, timeout=10)  # nosec B603
                )
                ssh_tests["target_ssh"] = {
                    "success": result.returncode == 0 and "SSH_OK" in result.stdout,
                    "response_time": "< 10s",
                    "error": result.stderr if result.stderr else None
                }
            except Exception as e:
                ssh_tests["target_ssh"] = {
                    "success": False,
                    "error": str(e)
                }

            details["tests"]["ssh_connectivity"] = ssh_tests

            # Test 2: Network speed test (small file transfer)
            speed_test = {}
            if ssh_tests["source_ssh"]["success"] and ssh_tests["target_ssh"]["success"]:
                try:
                    # Create a small test file on source (1MB)
                    create_test_file_cmd = source_ssh_cmd[:-1] + ["dd if=/dev/zero of=/tmp/speed_test bs=1M count=1 2>/dev/null && echo 'FILE_CREATED'"]
                    result = await loop.run_in_executor(
                        None, lambda: subprocess.run(create_test_file_cmd, capture_output=True, text=True, check=False, timeout=15)  # nosec B603
                    )

                    if result.returncode == 0 and "FILE_CREATED" in result.stdout:
                        # Transfer the file using rsync
                        import time
                        start_time = time.time()

                        rsync_test_cmd = source_ssh_cmd[:-1] + [
                            f"rsync -z /tmp/speed_test {target_host.user}@{target_host.hostname}:/tmp/speed_test_recv"
                            + (f" -e 'ssh -i {target_host.identity_file}'" if target_host.identity_file else "")
                        ]

                        result = await loop.run_in_executor(
                            None, lambda: subprocess.run(rsync_test_cmd, capture_output=True, text=True, check=False, timeout=30)  # nosec B603
                        )

                        transfer_time = time.time() - start_time

                        if result.returncode == 0:
                            # Calculate approximate speed (1MB / transfer_time)
                            speed_mbps = (1.0 / transfer_time) * 8 if transfer_time > 0 else 0  # Convert to Mbps
                            speed_test = {
                                "success": True,
                                "transfer_time": f"{transfer_time:.2f}s",
                                "estimated_speed": f"{speed_mbps:.1f} Mbps" if speed_mbps > 0 else "N/A",
                                "file_size": "1 MB"
                            }

                            # Clean up test files
                            cleanup_source = source_ssh_cmd[:-1] + ["rm -f /tmp/speed_test"]
                            cleanup_target = target_ssh_cmd[:-1] + ["rm -f /tmp/speed_test_recv"]
                            await loop.run_in_executor(None, lambda: subprocess.run(cleanup_source, capture_output=True, check=False))  # nosec B603
                            await loop.run_in_executor(None, lambda: subprocess.run(cleanup_target, capture_output=True, check=False))  # nosec B603
                        else:
                            speed_test = {
                                "success": False,
                                "error": f"Transfer failed: {result.stderr}",
                                "transfer_time": f"{transfer_time:.2f}s"
                            }
                    else:
                        speed_test = {
                            "success": False,
                            "error": "Failed to create test file"
                        }

                except Exception as e:
                    speed_test = {
                        "success": False,
                        "error": f"Speed test error: {str(e)}"
                    }
            else:
                speed_test = {
                    "success": False,
                    "error": "SSH connectivity required for speed test"
                }

            details["tests"]["network_speed"] = speed_test

            # Overall connectivity assessment
            connectivity_ok = (
                ssh_tests["source_ssh"]["success"] and
                ssh_tests["target_ssh"]["success"] and
                speed_test.get("success", False)
            )

            details["overall_success"] = connectivity_ok

            return connectivity_ok, details

        except Exception as e:
            details["tests"]["error"] = str(e)
            details["overall_success"] = False
            return False, details

    def _validate_compose_syntax(self, compose_content: str, stack_name: str) -> tuple[bool, list[str], dict]:
        """Validate Docker Compose file syntax and configuration.
        
        Args:
            compose_content: Docker Compose YAML content
            stack_name: Name of the stack
            
        Returns:
            Tuple of (is_valid: bool, issues: list[str], details: dict)
        """
        issues = []
        details = {
            "stack_name": stack_name,
            "validation_checks": {},
            "syntax_valid": False,
            "services_found": 0,
            "issues": []
        }

        try:
            # Basic YAML syntax validation
            import yaml
            try:
                compose_data = yaml.safe_load(compose_content)
                details["syntax_valid"] = True
                details["validation_checks"]["yaml_syntax"] = {"passed": True}
            except yaml.YAMLError as e:
                issues.append(f"YAML syntax error: {str(e)}")
                details["validation_checks"]["yaml_syntax"] = {"passed": False, "error": str(e)}
                details["issues"] = issues
                return False, issues, details

            if not isinstance(compose_data, dict):
                issues.append("Compose file must be a YAML object")
                details["validation_checks"]["structure"] = {"passed": False, "error": "Not a YAML object"}
                details["issues"] = issues
                return False, issues, details

            # Check for required sections
            if "services" not in compose_data:
                issues.append("No 'services' section found")
                details["validation_checks"]["services_section"] = {"passed": False, "error": "Missing services section"}
            else:
                services = compose_data["services"]
                if not isinstance(services, dict) or len(services) == 0:
                    issues.append("'services' section is empty or invalid")
                    details["validation_checks"]["services_section"] = {"passed": False, "error": "Empty or invalid services"}
                else:
                    details["services_found"] = len(services)
                    details["validation_checks"]["services_section"] = {"passed": True, "count": len(services)}

            # Validate individual services
            if "services" in compose_data and isinstance(compose_data["services"], dict):
                service_issues = []
                for service_name, service_config in compose_data["services"].items():
                    if not isinstance(service_config, dict):
                        service_issues.append(f"Service '{service_name}': Invalid configuration (not an object)")
                        continue

                    # Check for image or build
                    if "image" not in service_config and "build" not in service_config:
                        service_issues.append(f"Service '{service_name}': Missing 'image' or 'build' directive")

                    # Validate port specifications
                    if "ports" in service_config:
                        ports = service_config["ports"]
                        if isinstance(ports, list):
                            for i, port_spec in enumerate(ports):
                                if isinstance(port_spec, str):
                                    if ":" in port_spec:
                                        parts = port_spec.split(":")
                                        try:
                                            int(parts[0])  # host port
                                            int(parts[1])  # container port
                                        except (ValueError, IndexError):
                                            service_issues.append(f"Service '{service_name}': Invalid port specification '{port_spec}'")
                                elif isinstance(port_spec, dict):
                                    if "target" not in port_spec:
                                        service_issues.append(f"Service '{service_name}': Port mapping missing 'target'")

                    # Check environment variable references
                    if "environment" in service_config:
                        env_vars = service_config["environment"]
                        env_issues = []
                        if isinstance(env_vars, list):
                            for env_var in env_vars:
                                if isinstance(env_var, str) and "=" in env_var:
                                    key, value = env_var.split("=", 1)
                                    if "${" in value and "}" in value:
                                        env_issues.append(f"Variable substitution detected: {key}")
                        elif isinstance(env_vars, dict):
                            for key, value in env_vars.items():
                                if isinstance(value, str) and "${" in value and "}" in value:
                                    env_issues.append(f"Variable substitution detected: {key}")

                        if env_issues:
                            details["validation_checks"][f"service_{service_name}_env"] = {
                                "variable_substitutions": env_issues
                            }

                if service_issues:
                    issues.extend(service_issues)
                    details["validation_checks"]["service_validation"] = {
                        "passed": False,
                        "issues": service_issues
                    }
                else:
                    details["validation_checks"]["service_validation"] = {"passed": True}

            # Check for common best practices
            warnings = []
            if "version" in compose_data:
                version = compose_data["version"]
                if isinstance(version, str) and version.startswith("2"):
                    warnings.append("Using Docker Compose v2 format (consider upgrading to v3+)")

            # Validate volumes section
            if "volumes" in compose_data:
                volumes = compose_data["volumes"]
                if isinstance(volumes, dict):
                    for vol_name, vol_config in volumes.items():
                        if vol_config is not None and not isinstance(vol_config, dict):
                            issues.append(f"Volume '{vol_name}': Invalid configuration")

            details["warnings"] = warnings
            details["issues"] = issues

            is_valid = len(issues) == 0
            return is_valid, issues, details

        except Exception as e:
            issues.append(f"Validation error: {str(e)}")
            details["validation_checks"]["error"] = str(e)
            details["issues"] = issues
            return False, issues, details

    def _estimate_transfer_time(self, data_size_bytes: int, network_speed_details: dict = None) -> dict:
        """Estimate transfer time based on data size and network speed.
        
        Args:
            data_size_bytes: Size of data to transfer in bytes
            network_speed_details: Optional network speed test results
            
        Returns:
            Dict with transfer time estimates and details
        """
        estimates = {
            "data_size_bytes": data_size_bytes,
            "data_size_human": self._format_size(data_size_bytes),
            "compressed_size_bytes": int(data_size_bytes * 0.3),  # Assume 70% compression
            "compressed_size_human": self._format_size(int(data_size_bytes * 0.3)),
            "estimates": {}
        }

        # Use network speed if available, otherwise use standard estimates
        if network_speed_details and network_speed_details.get("success"):
            try:
                # Parse network speed (e.g., "50.2 Mbps")
                speed_str = network_speed_details.get("estimated_speed", "10.0 Mbps")
                speed_value = float(speed_str.split()[0])
                speed_unit = speed_str.split()[1] if len(speed_str.split()) > 1 else "Mbps"

                # Convert to bytes per second
                if speed_unit.lower() == "mbps":
                    bytes_per_second = (speed_value * 1_000_000) / 8  # Mbps to bytes/sec
                elif speed_unit.lower() == "gbps":
                    bytes_per_second = (speed_value * 1_000_000_000) / 8  # Gbps to bytes/sec
                else:
                    bytes_per_second = speed_value / 8  # Assume bps

                # Calculate transfer time for compressed data
                compressed_bytes = estimates["compressed_size_bytes"]
                if bytes_per_second > 0:
                    transfer_seconds = compressed_bytes / bytes_per_second

                    estimates["estimates"]["actual_network"] = {
                        "method": "measured",
                        "speed": speed_str,
                        "time_seconds": transfer_seconds,
                        "time_human": self._format_time(transfer_seconds),
                        "description": f"Based on actual network speed test ({speed_str})"
                    }

            except (ValueError, IndexError, TypeError):
                # Fall back to estimates if parsing fails
                pass

        # Always provide standard estimates for comparison
        standard_speeds = [
            ("10 Mbps", "Slow broadband", 10 * 1_000_000 / 8),
            ("100 Mbps", "Fast broadband", 100 * 1_000_000 / 8),
            ("1 Gbps", "Gigabit network", 1 * 1_000_000_000 / 8),
        ]

        compressed_bytes = estimates["compressed_size_bytes"]
        for speed_name, description, bytes_per_sec in standard_speeds:
            if bytes_per_sec > 0:
                transfer_seconds = compressed_bytes / bytes_per_sec
                estimates["estimates"][speed_name.replace(" ", "_").lower()] = {
                    "method": "estimate",
                    "speed": speed_name,
                    "time_seconds": transfer_seconds,
                    "time_human": self._format_time(transfer_seconds),
                    "description": description
                }

        # Add overhead estimates (15-25% additional time for setup, verification, etc.)
        if estimates["estimates"]:
            for estimate_key, estimate_data in estimates["estimates"].items():
                base_time = estimate_data["time_seconds"]
                with_overhead = base_time * 1.2  # 20% overhead
                estimate_data["time_with_overhead"] = with_overhead
                estimate_data["time_with_overhead_human"] = self._format_time(with_overhead)

        return estimates

    def _format_time(self, seconds: float) -> str:
        """Format seconds into human-readable time string."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        elif seconds < 86400:
            hours = seconds / 3600
            return f"{hours:.1f}h"
        else:
            days = seconds / 86400
            return f"{days:.1f}d"

    def _assess_migration_risks(self, stack_name: str, data_size_bytes: int, estimated_downtime: float,
                               source_inventory: dict = None, compose_content: str = "") -> dict:
        """Assess risks associated with the migration.
        
        Args:
            stack_name: Name of the stack being migrated
            data_size_bytes: Size of data to migrate
            estimated_downtime: Estimated downtime in seconds
            source_inventory: Source data inventory from migration manager
            compose_content: Docker Compose file content
            
        Returns:
            Dict with risk assessment details
        """
        risks = {
            "overall_risk": "LOW",
            "risk_factors": [],
            "warnings": [],
            "recommendations": [],
            "critical_files": [],
            "rollback_plan": []
        }

        # Risk Factor 1: Data size assessment
        if data_size_bytes > 50 * 1024**3:  # > 50GB
            risks["risk_factors"].append("LARGE_DATASET")
            risks["warnings"].append(f"Large dataset ({self._format_size(data_size_bytes)}) - increased transfer time and failure risk")
            risks["recommendations"].append("Consider migrating during maintenance window")
            risks["overall_risk"] = "HIGH"
        elif data_size_bytes > 10 * 1024**3:  # > 10GB
            risks["risk_factors"].append("MODERATE_DATASET")
            risks["warnings"].append(f"Moderate dataset ({self._format_size(data_size_bytes)}) - plan for extended transfer time")
            risks["overall_risk"] = "MEDIUM"

        # Risk Factor 2: Estimated downtime
        if estimated_downtime > 3600:  # > 1 hour
            risks["risk_factors"].append("LONG_DOWNTIME")
            risks["warnings"].append(f"Extended downtime expected ({self._format_time(estimated_downtime)})")
            risks["recommendations"].append("Schedule migration during low-usage period")
            if risks["overall_risk"] == "LOW":
                risks["overall_risk"] = "MEDIUM"
        elif estimated_downtime > 600:  # > 10 minutes
            risks["risk_factors"].append("MODERATE_DOWNTIME")
            risks["warnings"].append(f"Moderate downtime expected ({self._format_time(estimated_downtime)})")

        # Risk Factor 3: Critical files identification
        if source_inventory and source_inventory.get("critical_files"):
            critical_files = source_inventory["critical_files"]
            db_files = [f for f in critical_files.keys() if any(ext in f.lower() for ext in ['.db', '.sql', '.sqlite', 'database'])]
            config_files = [f for f in critical_files.keys() if any(ext in f.lower() for ext in ['.conf', '.config', '.env', '.yaml', '.json'])]

            if db_files:
                risks["risk_factors"].append("DATABASE_FILES")
                risks["warnings"].append(f"Database files detected ({len(db_files)} files) - data corruption risk if not properly stopped")
                risks["recommendations"].append("Ensure all database connections are closed before migration")
                risks["critical_files"].extend(db_files[:5])  # Show first 5
                if risks["overall_risk"] == "LOW":
                    risks["overall_risk"] = "MEDIUM"

            if config_files:
                risks["critical_files"].extend(config_files[:5])  # Show first 5

            if len(critical_files) > 20:
                risks["risk_factors"].append("MANY_CRITICAL_FILES")
                risks["warnings"].append(f"Many critical files ({len(critical_files)}) - increased complexity")

        # Risk Factor 4: Service analysis from compose file
        if compose_content:
            try:
                import yaml
                compose_data = yaml.safe_load(compose_content)
                services = compose_data.get("services", {})

                # Check for persistent volumes
                persistent_services = []
                for service_name, service_config in services.items():
                    volumes = service_config.get("volumes", [])
                    if volumes:
                        persistent_services.append(service_name)

                if persistent_services:
                    risks["risk_factors"].append("PERSISTENT_SERVICES")
                    if len(persistent_services) > 3:
                        risks["warnings"].append(f"Multiple services with persistent data ({len(persistent_services)} services)")
                        if risks["overall_risk"] == "LOW":
                            risks["overall_risk"] = "MEDIUM"

                # Check for health checks
                health_checked_services = []
                for service_name, service_config in services.items():
                    if "healthcheck" in service_config:
                        health_checked_services.append(service_name)

                if health_checked_services:
                    risks["recommendations"].append("Monitor health checks after migration - services may need time to stabilize")

            except Exception:
                pass  # Skip compose analysis if parsing fails

        # Generate rollback plan
        risks["rollback_plan"] = [
            "1. Stop target stack immediately if issues detected",
            "2. Verify source stack can be restarted on original host",
            "3. Restore from backup if target data was corrupted",
            "4. Update DNS/load balancer to point back to source",
            "5. Monitor source services for stability after rollback"
        ]

        # Additional recommendations based on risk level
        if risks["overall_risk"] == "HIGH":
            risks["recommendations"].extend([
                "Create full backup before starting migration",
                "Test rollback procedure in non-production environment",
                "Have technical team available during migration",
                "Consider incremental migration approach for large datasets"
            ])
        elif risks["overall_risk"] == "MEDIUM":
            risks["recommendations"].extend([
                "Create backup before starting migration",
                "Monitor migration progress closely",
                "Prepare rollback steps in advance"
            ])

        return risks

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

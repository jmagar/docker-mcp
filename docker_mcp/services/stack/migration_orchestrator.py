"""
Stack Migration Orchestrator Module

High-level coordination of Docker Compose stack migrations.
Orchestrates validation, execution, and verification using specialized modules.
"""

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ...core.config_loader import DockerMCPConfig
from ...core.docker_context import DockerContextManager
from ...utils import format_size
from .migration_executor import StackMigrationExecutor
from .network import StackNetwork
from .risk_assessment import StackRiskAssessment
from .validation import StackValidation
from .volume_utils import StackVolumeUtils


class StackMigrationOrchestrator:
    """Orchestrates Docker stack migrations using specialized modules."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self.logger = structlog.get_logger()

        # Initialize specialized modules
        self.validation = StackValidation()
        self.network = StackNetwork()
        self.risk_assessment = StackRiskAssessment()
        self.volume_utils = StackVolumeUtils()
        self.executor = StackMigrationExecutor(config, context_manager)

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """Validate host exists in configuration."""
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

    async def migrate_stack(
        self,
        source_host_id: str,
        target_host_id: str,
        stack_name: str,
        skip_stop_source: bool = False,
        start_target: bool = True,
        remove_source: bool = False,
        dry_run: bool = False,
    ) -> ToolResult:
        """Orchestrate Docker Compose stack migration between hosts.

        This method coordinates all migration phases using specialized modules:
        1. Pre-flight validation and checks
        2. Data archiving and transfer
        3. Stack deployment and verification
        4. Risk assessment and reporting

        Args:
            source_host_id: Source host ID
            target_host_id: Target host ID
            stack_name: Name of the stack to migrate
            skip_stop_source: Skip stopping the stack (DANGEROUS - only if already stopped)
            start_target: Start the stack on target after migration
            remove_source: Remove stack from source after successful migration
            dry_run: Perform dry run without actual changes

        Returns:
            ToolResult with migration status and detailed results
        """
        migration_steps = []
        migration_data = {
            "source_host_id": source_host_id,
            "target_host_id": target_host_id,
            "stack_name": stack_name,
            "dry_run": dry_run,
            "overall_success": False,
        }

        try:
            # Step 1: Validate hosts
            for host_id in [source_host_id, target_host_id]:
                is_valid, error_msg = self._validate_host(host_id)
                if not is_valid:
                    return ToolResult(
                        content=[TextContent(type="text", text=f"Error: {error_msg}")],
                        structured_content={"success": False, "error": error_msg},
                    )

            source_host = self.config.hosts[source_host_id]
            target_host = self.config.hosts[target_host_id]
            migration_steps.append("✅ Host validation completed")

            # Step 2: Retrieve and validate compose file
            migration_steps.append("📋 Retrieving compose configuration...")
            success, compose_content, compose_path = await self.executor.retrieve_compose_file(
                source_host_id, stack_name
            )

            if not success:
                return self._create_error_result("Failed to retrieve compose file", migration_data)

            # Validate compose syntax
            is_valid, issues, validation_details = self.validation.validate_compose_syntax(
                compose_content, stack_name
            )
            if not is_valid:
                return self._create_error_result(
                    f"Compose validation failed: {issues}", migration_data
                )

            migration_steps.append(
                f"✅ Compose file validated ({validation_details['services_found']} services)"
            )
            migration_data["compose_validation"] = validation_details

            # Step 3: Pre-flight checks
            migration_steps.append("🔍 Running pre-flight checks...")

            # Extract volumes and estimate data size
            expected_mounts = self.volume_utils.extract_expected_mounts(
                compose_content, target_host.appdata_path or "/opt/docker-appdata", stack_name
            )
            estimated_data_size = self.volume_utils.get_volume_size_estimate(expected_mounts)
            migration_data["estimated_data_size"] = estimated_data_size

            # Check disk space
            has_space, space_message, space_details = await self.validation.check_disk_space(
                target_host, estimated_data_size
            )
            migration_steps.append(f"💾 {space_message}")
            migration_data["disk_space_check"] = space_details

            if not has_space and not dry_run:
                return self._create_error_result("Insufficient disk space", migration_data)

            # Check required tools
            required_tools = ["docker", "tar", "rsync"]
            (
                tools_available,
                missing_tools,
                tool_details,
            ) = await self.validation.check_tool_availability(target_host, required_tools)

            if tools_available:
                migration_steps.append("🛠️  All required tools available")
            else:
                migration_steps.append(f"⚠️  Missing tools: {', '.join(missing_tools)}")
                if not dry_run:
                    return self._create_error_result(
                        f"Missing required tools: {missing_tools}", migration_data
                    )

            migration_data["tool_availability"] = tool_details

            # Step 4: Network connectivity test
            migration_steps.append("🌐 Testing network connectivity...")
            connectivity_ok, network_details = await self.network.test_network_connectivity(
                source_host, target_host
            )

            if connectivity_ok:
                migration_steps.append("✅ Network connectivity verified")

                # Estimate transfer time if speed test successful
                speed_test = network_details.get("tests", {}).get("network_speed", {})
                if speed_test.get("success"):
                    transfer_estimates = self.network.estimate_transfer_time(
                        estimated_data_size, speed_test
                    )
                    migration_steps.append(
                        f"⏱️  Estimated transfer time: {transfer_estimates.get('estimates', {}).get('actual_network', {}).get('time_human', 'unknown')}"
                    )
                    migration_data["transfer_estimates"] = transfer_estimates
            else:
                migration_steps.append("⚠️  Network connectivity issues detected")
                if not dry_run:
                    return self._create_error_result(
                        "Network connectivity test failed", migration_data
                    )

            migration_data["network_test"] = network_details

            # Step 5: Risk assessment
            migration_steps.append("🎯 Assessing migration risks...")
            risks = self.risk_assessment.assess_migration_risks(
                stack_name=stack_name,
                data_size_bytes=estimated_data_size,
                estimated_downtime=300,  # 5 minutes base estimate
                source_inventory=None,  # Would be populated with actual inventory
                compose_content=compose_content,
            )

            risk_score = self.risk_assessment.calculate_risk_score(risks)
            migration_steps.append(
                f"📊 Risk level: {risks['overall_risk']} (score: {risk_score}/100)"
            )

            if risks["warnings"]:
                for warning in risks["warnings"][:3]:  # Show first 3 warnings
                    migration_steps.append(f"⚠️  {warning}")

            migration_data["risk_assessment"] = risks

            # Step 6: Execute migration (if not dry run)
            if not dry_run:
                migration_steps.append("🚀 Starting migration execution...")

                # Create backup of target if data exists
                target_path = f"{target_host.appdata_path or '/opt/docker-appdata'}/{stack_name}"
                backup_success, backup_info = await self.executor.create_backup(
                    target_host, target_path, stack_name, dry_run
                )

                if backup_success:
                    migration_steps.append("💾 Target backup created")
                    migration_data["backup_info"] = backup_info

                # Stop source stack if not skipped
                if not skip_stop_source:
                    stop_result = await self.executor.stack_tools.manage_stack(
                        source_host_id, stack_name, "down", None
                    )
                    if stop_result["success"]:
                        migration_steps.append("⏹️  Source stack stopped")
                    else:
                        migration_steps.append("⚠️  Failed to stop source stack")

                # Create and transfer data archive
                (
                    archive_success,
                    archive_path,
                    archive_metadata,
                ) = await self.executor.create_data_archive(
                    source_host, expected_mounts, stack_name, dry_run
                )

                if not archive_success:
                    return self._create_error_result(
                        "Failed to create data archive", migration_data
                    )

                migration_steps.append("📦 Data archive created")

                # Transfer archive
                transfer_success, transfer_results = await self.executor.transfer_archive(
                    source_host, target_host, archive_path, stack_name, dry_run
                )

                if not transfer_success:
                    return self._create_error_result("Data transfer failed", migration_data)

                migration_steps.append("🚚 Data transfer completed")

                # Extract archive on target
                extract_success, extract_results = await self.executor.extract_archive(
                    target_host, archive_path, target_path, dry_run
                )

                if not extract_success:
                    return self._create_error_result("Archive extraction failed", migration_data)

                migration_steps.append("📂 Data extraction completed")

                # Update compose file for target environment
                updated_compose = self.executor.update_compose_for_target(
                    compose_content,
                    {},
                    target_host.appdata_path or "/opt/docker-appdata",
                    stack_name,
                )

                # Deploy stack on target
                deploy_success, deploy_results = await self.executor.deploy_stack_on_target(
                    target_host_id, stack_name, updated_compose, start_target, dry_run
                )

                if not deploy_success:
                    return self._create_error_result("Stack deployment failed", migration_data)

                migration_steps.append("🎯 Stack deployed on target")

                # Verify deployment
                verify_success, verify_results = await self.executor.verify_deployment(
                    target_host_id, stack_name, expected_mounts, None, dry_run
                )

                if verify_success:
                    migration_steps.append("✅ Deployment verification passed")
                    migration_data["overall_success"] = True

                    # Cleanup source if requested
                    if remove_source:
                        cleanup_success, cleanup_results = await self.executor.cleanup_source(
                            source_host_id, stack_name, compose_path, remove_source, dry_run
                        )
                        if cleanup_success:
                            migration_steps.append("🗑️  Source cleanup completed")
                        migration_data["source_cleanup"] = cleanup_results

                else:
                    migration_steps.append("❌ Deployment verification failed")

                migration_data.update(
                    {
                        "archive_metadata": archive_metadata,
                        "transfer_results": transfer_results,
                        "extract_results": extract_results,
                        "deploy_results": deploy_results,
                        "verify_results": verify_results,
                    }
                )

            else:
                # Dry run summary
                migration_steps.extend(
                    [
                        "🧪 Dry run completed - no actual changes made",
                        f"✅ Migration feasibility: {risks['overall_risk']} risk",
                        f"📊 Estimated data size: {format_size(estimated_data_size)}",
                        "⏱️  Estimated downtime: 5-15 minutes",
                    ]
                )
                migration_data["overall_success"] = True

            # Final summary
            final_message = "\n".join(
                [
                    f"{'🧪 DRY RUN - ' if dry_run else ''}Stack Migration: {stack_name}",
                    f"Source: {source_host_id} → Target: {target_host_id}",
                    "",
                    *migration_steps,
                ]
            )

            return ToolResult(
                content=[TextContent(type="text", text=final_message)],
                structured_content=migration_data,
            )

        except Exception as e:
            self.logger.error("Migration orchestration failed", error=str(e))
            return self._create_error_result(f"Migration failed: {str(e)}", migration_data)

    def _create_error_result(self, error_message: str, migration_data: dict) -> ToolResult:
        """Create standardized error result."""
        migration_data.update(
            {
                "success": False,
                "error": error_message,
            }
        )

        return ToolResult(
            content=[TextContent(type="text", text=f"❌ Migration Error: {error_message}")],
            structured_content=migration_data,
        )

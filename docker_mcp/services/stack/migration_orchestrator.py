"""Stack migration orchestrator."""

from typing import TYPE_CHECKING, Any

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from structlog.stdlib import BoundLogger

if TYPE_CHECKING:
    from docker_mcp.core.docker_context import DockerContextManager

from ...core.config_loader import DockerHost, DockerMCPConfig
from ...utils import format_size
from .migration_executor import StackMigrationExecutor
from .network import StackNetwork
from .risk_assessment import StackRiskAssessment
from .validation import StackValidation
from .volume_utils import StackVolumeUtils


class StackMigrationOrchestrator:
    """Orchestrates stack migrations between Docker hosts.

    Coordinates all aspects of migration including validation, data transfer,
    deployment verification, and rollback capabilities.
    """

    def __init__(
        self,
        config: DockerMCPConfig,
        context_manager: "DockerContextManager",
    ):
        """Initialize migration orchestrator and its dependencies."""
        self.config = config
        # Build internal module instances
        self.validation = StackValidation()
        self.executor = StackMigrationExecutor(config, context_manager)
        self.network = StackNetwork()
        self.risk_assessment = StackRiskAssessment()
        self.volume_utils = StackVolumeUtils()
        self.logger: BoundLogger = structlog.get_logger().bind(component="migration_orchestrator")

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
            validation_result = await self._validate_hosts(
                source_host_id, target_host_id, migration_steps
            )
            if isinstance(validation_result, ToolResult):
                return validation_result
            source_host, target_host = validation_result

            # Step 2: Retrieve and validate compose file
            compose_result = await self._retrieve_and_validate_compose(
                source_host_id, stack_name, migration_steps, migration_data
            )
            if isinstance(compose_result, ToolResult):
                return compose_result
            compose_content, compose_path = compose_result

            # Step 3: Pre-flight checks
            preflight_result = await self._run_preflight_checks(
                source_host,
                target_host,
                compose_content,
                stack_name,
                migration_steps,
                migration_data,
                dry_run,
            )
            if isinstance(preflight_result, ToolResult):
                return preflight_result
            expected_mounts, estimated_data_size = preflight_result

            # Step 4: Network connectivity test
            network_result = await self._test_network_connectivity(
                source_host,
                target_host,
                estimated_data_size,
                migration_steps,
                migration_data,
                dry_run,
            )
            if isinstance(network_result, ToolResult):
                return network_result

            # Step 5: Risk assessment
            risks = await self._assess_migration_risks(
                stack_name, estimated_data_size, compose_content, migration_steps, migration_data
            )

            # Step 6: Execute migration (if not dry run)
            if not dry_run:
                migration_result = await self._execute_migration(
                    source_host_id,
                    target_host_id,
                    source_host,
                    target_host,
                    stack_name,
                    skip_stop_source,
                    start_target,
                    remove_source,
                    expected_mounts,
                    compose_content,
                    compose_path,
                    migration_steps,
                    migration_data,
                )
                if isinstance(migration_result, ToolResult):
                    return migration_result
            else:
                await self._handle_dry_run(
                    risks, estimated_data_size, migration_steps, migration_data
                )

            # Final summary
            return self._create_final_result(
                stack_name, source_host_id, target_host_id, dry_run, migration_steps, migration_data
            )

        except Exception as e:
            self.logger.error("Migration orchestration failed", error=str(e))
            return self._create_error_result(f"Migration failed: {str(e)}", migration_data)

    async def _validate_hosts(
        self, source_host_id: str, target_host_id: str, migration_steps: list[str]
    ) -> ToolResult | tuple[DockerHost, DockerHost]:
        """Validate source and target hosts."""
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
        return source_host, target_host

    async def _retrieve_and_validate_compose(
        self,
        source_host_id: str,
        stack_name: str,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> ToolResult | tuple[str, str]:
        """Retrieve and validate compose file."""
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
            return self._create_error_result(f"Compose validation failed: {issues}", migration_data)

        services_found = validation_details.get("services_found", "unknown")
        migration_steps.append(f"✅ Compose file validated ({services_found} services)")
        migration_data["compose_validation"] = validation_details
        return compose_content, compose_path

    async def _run_preflight_checks(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        compose_content: str,
        stack_name: str,
        migration_steps: list[str],
        migration_data: dict[str, Any],
        dry_run: bool,
    ) -> ToolResult | tuple[list[str], int]:
        """Run pre-flight checks including disk space and tool availability."""
        migration_steps.append("🔍 Running pre-flight checks...")

        # Extract volumes and estimate data size
        expected_mounts = self.volume_utils.extract_expected_mounts(
            compose_content, source_host.appdata_path or "/opt/docker-appdata", stack_name
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
        return expected_mounts, estimated_data_size

    async def _test_network_connectivity(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        estimated_data_size: int,
        migration_steps: list[str],
        migration_data: dict[str, Any],
        dry_run: bool,
    ) -> ToolResult | bool:
        """Test network connectivity between hosts."""
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
                actual_time = (
                    transfer_estimates.get("estimates", {})
                    .get("actual_network", {})
                    .get("time_human", "unknown")
                )
                migration_steps.append(f"⏱️  Estimated transfer time: {actual_time}")
                migration_data["transfer_estimates"] = transfer_estimates
        else:
            migration_steps.append("⚠️  Network connectivity issues detected")
            if not dry_run:
                return self._create_error_result("Network connectivity test failed", migration_data)

        migration_data["network_test"] = network_details
        return True

    async def _assess_migration_risks(
        self,
        stack_name: str,
        estimated_data_size: int,
        compose_content: str,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Assess migration risks."""
        migration_steps.append("🎯 Assessing migration risks...")
        risks = self.risk_assessment.assess_migration_risks(
            stack_name=stack_name,
            data_size_bytes=estimated_data_size,
            estimated_downtime=300,
            source_inventory={},
            compose_content=compose_content,
        )

        risk_score = self.risk_assessment.calculate_risk_score(risks)
        migration_steps.append(f"📊 Risk level: {risks['overall_risk']} (score: {risk_score}/100)")

        if risks["warnings"]:
            for warning in risks["warnings"][:3]:
                migration_steps.append(f"⚠️  {warning}")

        migration_data["risk_assessment"] = risks
        return risks

    async def _execute_migration(
        self,
        source_host_id: str,
        target_host_id: str,
        source_host: DockerHost,
        target_host: DockerHost,
        stack_name: str,
        skip_stop_source: bool,
        start_target: bool,
        remove_source: bool,
        expected_mounts: list[str],
        compose_content: str,
        compose_path: str,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> ToolResult | bool | dict[str, Any]:
        """Execute the actual migration process."""
        migration_steps.append("🚀 Starting migration execution...")

        # Execute migration phases
        await self._stop_source_stack(source_host_id, stack_name, skip_stop_source, migration_steps)

        path_mappings, source_paths = self._prepare_path_mappings(
            target_host, stack_name, expected_mounts
        )

        transfer_results = await self._transfer_migration_data(
            source_host, target_host, source_paths, stack_name, migration_steps, migration_data
        )
        if isinstance(transfer_results, ToolResult):
            return transfer_results
        if not transfer_results.get("success", True):
            return self._create_error_result("Data transfer failed", migration_data)

        deploy_results = await self._deploy_target_stack(
            target_host_id,
            stack_name,
            compose_content,
            path_mappings,
            target_host,
            start_target,
            migration_steps,
            migration_data,
        )
        if isinstance(deploy_results, ToolResult):
            return deploy_results
        if not deploy_results.get("success", True):
            return self._create_error_result("Stack deployment failed", migration_data)

        verify_results = await self._verify_and_cleanup(
            target_host_id,
            stack_name,
            expected_mounts,
            path_mappings,
            remove_source,
            source_host_id,
            compose_path,
            migration_steps,
            migration_data,
        )

        # Update migration data with all results
        migration_data.update(
            {
                "transfer_results": transfer_results,
                "deploy_results": deploy_results,
                "verify_results": verify_results,
            }
        )

        return True

    async def _stop_source_stack(
        self,
        source_host_id: str,
        stack_name: str,
        skip_stop_source: bool,
        migration_steps: list[str],
    ) -> None:
        """Stop source stack if not skipped."""
        if not skip_stop_source:
            stop_result = await self.executor.stack_tools.manage_stack(
                source_host_id, stack_name, "down", None
            )
            if stop_result["success"]:
                migration_steps.append("⏹️  Source stack stopped")
            else:
                migration_steps.append("⚠️  Failed to stop source stack")

    def _prepare_path_mappings(
        self, target_host: DockerHost, stack_name: str, expected_mounts: list[str]
    ) -> tuple[dict[str, str], list[str]]:
        """Prepare path mappings and source paths for transfer."""
        source_paths = []
        path_mappings = {}
        target_appdata_path = target_host.appdata_path or "/opt/docker-appdata"

        for mount in expected_mounts:
            if ":" in mount:
                source_path = mount.split(":", 1)[0]
                source_paths.append(source_path)

                if f"/{stack_name}/" in source_path:
                    relative_part = source_path.split(f"/{stack_name}/", 1)[1]
                    target_path = f"{target_appdata_path}/{stack_name}/{relative_part}"
                    path_mappings[source_path] = target_path
                else:
                    path_mappings[source_path] = f"{target_appdata_path}/{stack_name}"

        return path_mappings, source_paths

    async def _transfer_migration_data(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_paths: list[str],
        stack_name: str,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> ToolResult | dict[str, Any]:
        """Transfer data between hosts."""
        transfer_success, transfer_results = await self.executor.transfer_data(
            source_host, target_host, source_paths, stack_name, False
        )
        if not transfer_success:
            return self._create_error_result("Data transfer failed", migration_data)

        migration_steps.append("🚚 Direct data transfer completed")
        migration_data["transfer_type"] = transfer_results.get("transfer_type", "unknown")

        if migration_data["transfer_type"] == "rsync":
            migration_steps.append("⚙️  Transfer method: direct rsync sync")

        return transfer_results

    async def _deploy_target_stack(
        self,
        target_host_id: str,
        stack_name: str,
        compose_content: str,
        path_mappings: dict[str, str],
        target_host: DockerHost,
        start_target: bool,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> ToolResult | dict[str, Any]:
        """Deploy stack on target host."""
        target_appdata_path = target_host.appdata_path or "/opt/docker-appdata"
        updated_compose = self.executor.update_compose_for_target(
            compose_content, path_mappings, target_appdata_path, stack_name
        )

        deploy_success, deploy_results = await self.executor.deploy_stack_on_target(
            target_host_id, stack_name, updated_compose, start_target, False
        )
        if not deploy_success:
            return self._create_error_result("Stack deployment failed", migration_data)

        migration_steps.append("🎯 Stack deployed on target")
        return deploy_results

    async def _verify_and_cleanup(
        self,
        target_host_id: str,
        stack_name: str,
        expected_mounts: list[str],
        path_mappings: dict[str, str],
        remove_source: bool,
        source_host_id: str,
        compose_path: str,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify deployment and cleanup source if requested."""
        # Prepare target expected mounts
        target_expected_mounts = []
        for mount in expected_mounts:
            if ":" in mount:
                source_path, container_path = mount.split(":", 1)
                target_path = path_mappings.get(source_path, source_path)
                target_expected_mounts.append(f"{target_path}:{container_path}")
            else:
                target_expected_mounts.append(mount)

        # Verify deployment
        verify_success, verify_results = await self.executor.verify_deployment(
            target_host_id, stack_name, target_expected_mounts, {}, False
        )

        if verify_success:
            migration_steps.append("✅ Deployment verification passed")
            migration_data["overall_success"] = True

            if remove_source:
                cleanup_success, cleanup_results = await self.executor.cleanup_source(
                    source_host_id, stack_name, compose_path, remove_source, False
                )
                if cleanup_success:
                    migration_steps.append("🗑️  Source cleanup completed")
                migration_data["source_cleanup"] = cleanup_results
        else:
            migration_steps.append("❌ Deployment verification failed")

        return verify_results

    async def _handle_dry_run(
        self,
        risks: dict[str, Any],
        estimated_data_size: int,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> None:
        """Handle dry run summary."""
        migration_steps.extend(
            [
                "🧪 Dry run completed - no actual changes made",
                f"✅ Migration feasibility: {risks['overall_risk']} risk",
                f"📊 Estimated data size: {format_size(estimated_data_size)}",
                "⏱️  Estimated downtime: 5-15 minutes",
            ]
        )
        migration_data.setdefault("success", True)

    def _create_final_result(
        self,
        stack_name: str,
        source_host_id: str,
        target_host_id: str,
        dry_run: bool,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> ToolResult:
        """Create final migration result."""
        final_message = "\n".join(
            [
                f"{'🧪 DRY RUN - ' if dry_run else ''}Stack Migration: {stack_name}",
                f"Source: {source_host_id} → Target: {target_host_id}",
                "",
                *migration_steps,
            ]
        )

        migration_data.setdefault("success", True)
        return ToolResult(
            content=[TextContent(type="text", text=final_message)],
            structured_content=migration_data,
        )

    def _create_error_result(
        self, error_message: str, migration_data: dict[str, Any]
    ) -> ToolResult:
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

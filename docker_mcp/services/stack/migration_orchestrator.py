"""Stack migration orchestrator."""

import posixpath
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import structlog
import yaml
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
            source_host,
            target_host,
            source_paths,
            path_mappings,
            stack_name,
            migration_steps,
            migration_data,
        )
        if isinstance(transfer_results, ToolResult):
            return transfer_results
        if not transfer_results.get("success", True):
            return self._create_error_result("Data transfer failed", migration_data)

        compose_adjustment = await self._ensure_target_ports_available(
            target_host,
            compose_content,
            stack_name,
            migration_steps,
            migration_data,
        )
        if isinstance(compose_adjustment, ToolResult):
            return compose_adjustment
        compose_content = compose_adjustment

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

                stack_marker = f"/{stack_name}"
                target_path = None

                if stack_marker in source_path:
                    _, suffix = source_path.split(stack_marker, 1)
                    suffix = suffix.lstrip("/")

                    if suffix.startswith("-") and suffix != "-":
                        # Preserve sibling naming like stack-name-redis
                        target_basename = f"{stack_name}{suffix}"
                        target_path = posixpath.join(target_appdata_path, target_basename)
                    elif suffix:
                        target_path = posixpath.join(target_appdata_path, stack_name, suffix)
                    else:
                        target_path = posixpath.join(target_appdata_path, stack_name)

                if target_path is None:
                    source_basename = PurePosixPath(source_path.rstrip("/")).name
                    target_path = posixpath.join(target_appdata_path, source_basename)

                path_mappings[source_path] = target_path

        return path_mappings, source_paths

    async def _ensure_target_ports_available(
        self,
        target_host: DockerHost,
        compose_content: str,
        stack_name: str,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> ToolResult | str:
        """Ensure host ports defined in compose are available on the target host.

        If conflicts are detected, automatically remap to the next available ports.
        """
        # Check if any host ports are exposed
        host_ports = self.validation.extract_ports_from_compose(compose_content)
        if not host_ports:
            return self._handle_no_ports_case(migration_steps, migration_data, target_host, compose_content)

        # Check for port conflicts
        all_available, conflicts, details = await self.validation.check_port_conflicts(
            target_host, host_ports
        )
        migration_data["port_check"] = details

        if all_available:
            migration_steps.append("🔌 Target host ports available")
            return compose_content

        # Handle port conflicts by remapping
        return await self._handle_port_conflicts(
            compose_content, conflicts, migration_steps, migration_data, target_host
        )

    def _handle_no_ports_case(
        self, migration_steps: list[str], migration_data: dict[str, Any],
        target_host: DockerHost, compose_content: str
    ) -> str:
        """Handle the case where no host ports are exposed."""
        migration_steps.append("🔌 No host ports exposed; skipping port reassignment")
        migration_data["port_check"] = {
            "host": target_host.hostname,
            "ports_checked": [],
            "conflicting_ports": [],
            "all_ports_available": True,
            "port_details": {},
        }
        return compose_content

    async def _handle_port_conflicts(
        self, compose_content: str, conflicts: list[int], migration_steps: list[str],
        migration_data: dict[str, Any], target_host: DockerHost
    ) -> ToolResult | str:
        """Handle port conflicts by remapping to available ports."""
        conflicts_set = set(conflicts)
        migration_steps.append("⚠️  Port conflicts detected on target; remapping host ports")

        # Parse compose file
        try:
            compose_data = yaml.safe_load(compose_content)
        except yaml.YAMLError as exc:
            details_msg = f"Failed to parse compose for port adjustment: {exc}"
            return self._create_error_result(details_msg, migration_data)

        # Process port remapping
        adjustments, updated_compose = await self._remap_conflicting_ports(
            compose_data, conflicts_set, target_host, migration_data
        )

        if isinstance(adjustments, ToolResult):
            return adjustments  # Error occurred during remapping

        if not adjustments:
            return self._create_error_result(
                "Port conflicts detected but no adjustments were made", migration_data
            )

        # Update migration data and logs
        migration_data["port_adjustments"] = adjustments
        summary = ", ".join(
            f"{item['service']}: {item['original_port']}→{item['new_port']}"
            for item in adjustments
        )
        migration_steps.append(f"🔁 Adjusted host ports on target ({summary})")

        return yaml.safe_dump(updated_compose, sort_keys=False)

    async def _remap_conflicting_ports(
        self, compose_data: dict[str, Any], conflicts_set: set[int],
        target_host: DockerHost, migration_data: dict[str, Any]
    ) -> tuple[list[dict[str, Any]] | ToolResult, dict[str, Any]]:
        """Remap conflicting ports to available ones."""
        services = compose_data.get("services") or {}
        adjustments: list[dict[str, Any]] = []
        reserved_ports = set()

        for service_name, service_config in services.items():
            ports_list = service_config.get("ports")
            if not isinstance(ports_list, list):
                continue

            updated_ports, service_adjustments = await self._process_service_ports(
                service_name, ports_list, conflicts_set, reserved_ports, target_host, migration_data
            )

            if isinstance(service_adjustments, ToolResult):
                return service_adjustments, compose_data

            if updated_ports:
                service_config["ports"] = updated_ports
                adjustments.extend(service_adjustments)

        return adjustments, compose_data

    async def _process_service_ports(
        self, service_name: str, ports_list: list[Any], conflicts_set: set[int],
        reserved_ports: set[int], target_host: DockerHost, migration_data: dict[str, Any]
    ) -> tuple[list[Any], list[dict[str, Any]] | ToolResult]:
        """Process ports for a single service."""
        updated_ports: list[Any] = []
        service_adjustments: list[dict[str, Any]] = []

        for port_entry in ports_list:
            details_map = self._extract_port_entry_details(port_entry)
            host_port = details_map.get("host_port")

            if host_port is None or not isinstance(host_port, int):
                updated_ports.append(port_entry)
                continue

            if host_port not in conflicts_set:
                updated_ports.append(port_entry)
                reserved_ports.add(host_port)
                continue

            # Handle port conflict
            result = await self._resolve_port_conflict(
                service_name, port_entry, details_map, host_port,
                reserved_ports, target_host, migration_data
            )

            if isinstance(result, ToolResult):
                return [], result

            updated_entry, new_port = result
            updated_ports.append(updated_entry)
            reserved_ports.add(new_port)
            service_adjustments.append({
                "service": service_name,
                "original_port": host_port,
                "new_port": new_port,
                "container_port": details_map.get("container_port"),
            })
            conflicts_set.discard(host_port)

        return updated_ports, service_adjustments

    async def _resolve_port_conflict(
        self, service_name: str, port_entry: Any, details_map: dict[str, Any],
        host_port: int, reserved_ports: set[int], target_host: DockerHost,
        migration_data: dict[str, Any]
    ) -> tuple[Any, int] | ToolResult:
        """Resolve a single port conflict."""
        try:
            new_port = await self.validation.find_available_port(
                target_host,
                starting_port=host_port + 1,
                avoid_ports=reserved_ports,
            )
        except RuntimeError as exc:
            error_message = (
                f"Unable to resolve port conflict for service '{service_name}' on port {host_port}: {exc}"
            )
            return self._create_error_result(error_message, migration_data)

        updated_entry = self._rebuild_port_entry(port_entry, details_map, new_port)
        return updated_entry, new_port

    def _extract_port_entry_details(self, port_entry: Any) -> dict[str, Any]:
        """Normalize different port specifications into a common form."""
        details: dict[str, Any] = {
            "host_port": None,
            "container_port": None,
            "protocol": None,
            "ip": None,
            "original_type": type(port_entry),
        }

        if isinstance(port_entry, str):
            self._parse_string_port_entry(port_entry, details)
        elif isinstance(port_entry, dict):
            self._parse_dict_port_entry(port_entry, details)
        elif isinstance(port_entry, int):
            details["container_port"] = port_entry

        return details

    def _parse_string_port_entry(self, port_entry: str, details: dict[str, Any]) -> None:
        """Parse string format port entry."""
        # Extract protocol if present
        protocol, base = self._extract_protocol(port_entry)
        details["protocol"] = protocol

        # Extract IP and port segments
        ip_part, remainder = self._extract_ip_part(base)
        segments = remainder.split(":") if remainder else []

        # Parse port segments
        self._parse_port_segments(segments, ip_part, details)

    def _extract_protocol(self, port_entry: str) -> tuple[str | None, str]:
        """Extract protocol from port entry."""
        if "/" in port_entry:
            base, protocol = port_entry.split("/", 1)
            return protocol, base
        return None, port_entry

    def _extract_ip_part(self, base: str) -> tuple[str | None, str]:
        """Extract IP part from port entry."""
        remainder = base
        ip_part = None

        # Handle IPv6 format [::1]:8080
        if remainder.startswith("[") and "]" in remainder:
            end_idx = remainder.find("]")
            ip_part = remainder[: end_idx + 1]
            remainder = remainder[end_idx + 1 :]

        # Remove leading colon
        if remainder.startswith(":"):
            remainder = remainder[1:]

        return ip_part, remainder

    def _parse_port_segments(self, segments: list[str], ip_part: str | None, details: dict[str, Any]) -> None:
        """Parse port segments from the remainder."""
        if len(segments) == 1:
            # Only container port specified
            container_port = segments[0]
            host_port = None
            ip_value = ip_part
        else:
            # Host and container ports specified
            container_port = segments[-1]
            host_port = segments[-2] if len(segments) >= 2 else None
            if len(segments) > 2:
                ip_value = ip_part or ":".join(segments[:-2])
            else:
                ip_value = ip_part

        # Set parsed values
        details["ip"] = ip_value or None
        self._set_port_value(details, "container_port", container_port)
        if host_port:
            self._set_port_value(details, "host_port", host_port)

    def _set_port_value(self, details: dict[str, Any], key: str, port_value: str | int) -> None:
        """Set port value as integer if possible."""
        if port_value and str(port_value).isdigit():
            details[key] = int(port_value)
        else:
            details[key] = port_value

    def _parse_dict_port_entry(self, port_entry: dict[str, Any], details: dict[str, Any]) -> None:
        """Parse dictionary format port entry."""
        details["protocol"] = port_entry.get("protocol")
        details["container_port"] = port_entry.get("target") or port_entry.get("containerPort")

        # Look for host port in various field names
        host_port_fields = ["published", "hostPort", "host_port", "external"]
        for key in host_port_fields:
            if key in port_entry and port_entry[key] is not None:
                host_value = self._safe_int_conversion(port_entry[key])
                if host_value is not None:
                    details["host_port"] = host_value
                    details.setdefault("host_port_field", key)
                    break

    def _safe_int_conversion(self, value: Any) -> int | None:
        """Safely convert value to integer."""
        try:
            return int(value) if isinstance(value, int) else int(str(value))
        except (TypeError, ValueError):
            return None

    def _rebuild_port_entry(
        self, port_entry: Any, details_map: dict[str, Any], new_host_port: int
    ) -> Any:
        """Rebuild a port entry with an updated host port while preserving format."""

        if isinstance(port_entry, str):
            container_port = details_map.get("container_port")
            protocol = details_map.get("protocol")
            ip_value = details_map.get("ip")

            segments = []
            if ip_value:
                segments.append(str(ip_value))
            segments.append(str(new_host_port))
            if container_port is not None:
                segments.append(str(container_port))

            rebuilt = ":".join(segments)
            if protocol:
                rebuilt = f"{rebuilt}/{protocol}"

            return rebuilt

        if isinstance(port_entry, dict):
            updated_entry = dict(port_entry)
            field = details_map.get("host_port_field")
            if field:
                original_value = updated_entry.get(field)
                updated_entry[field] = (
                    str(new_host_port)
                    if isinstance(original_value, str)
                    else new_host_port
                )
            else:
                updated_entry["published"] = new_host_port
            return updated_entry

        # For integers or unsupported formats, return original entry unchanged
        return port_entry

    async def _transfer_migration_data(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_paths: list[str],
        path_mappings: dict[str, str],
        stack_name: str,
        migration_steps: list[str],
        migration_data: dict[str, Any],
    ) -> ToolResult | dict[str, Any]:
        """Transfer data between hosts."""
        transfer_success, transfer_results = await self.executor.transfer_data(
            source_host,
            target_host,
            source_paths,
            stack_name,
            path_mappings=path_mappings,
            dry_run=False,
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

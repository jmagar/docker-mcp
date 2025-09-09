"""
Stack Validation Module

All validation and checking logic for Docker Compose stacks.
Handles compose syntax validation, resource checks, conflict detection, etc.
"""

import asyncio
import shlex
import subprocess
from typing import Any

import structlog

from ...core.config_loader import DockerHost
from ...utils import build_ssh_command, format_size


class StackValidation:
    """Validation and resource checking for Docker Compose stacks."""

    def __init__(self):
        self.logger = structlog.get_logger()

    def validate_compose_syntax(
        self, compose_content: str, stack_name: str
    ) -> tuple[bool, list[str], dict]:
        """Validate Docker Compose file syntax and configuration.

        Args:
            compose_content: Docker Compose YAML content
            stack_name: Name of the stack

        Returns:
            Tuple of (is_valid: bool, issues: list[str], details: dict)
        """
        issues: list[str] = []
        details: dict[str, Any] = {
            "stack_name": stack_name,
            "validation_checks": {},
            "syntax_valid": False,
            "services_found": 0,
            "issues": [],
        }

        try:
            # Basic YAML syntax validation
            compose_data = self._validate_yaml_syntax(compose_content, issues, details)
            if compose_data is None:
                return False, issues, details

            # Validate compose structure
            structure_valid = self._validate_compose_structure(compose_data, issues, details)
            if not structure_valid:
                return False, issues, details

            # Validate individual services
            self._validate_services(compose_data.get("services", {}), issues, details)

            details["issues"] = issues
            return len(issues) == 0, issues, details

        except Exception as e:
            error_msg = f"Failed to validate compose file: {str(e)}"
            issues.append(error_msg)
            details["validation_checks"]["general_error"] = {"passed": False, "error": error_msg}
            details["issues"] = issues
            return False, issues, details

    def _validate_yaml_syntax(
        self, compose_content: str, issues: list[str], details: dict
    ) -> dict | None:
        """Validate YAML syntax and return parsed data."""
        import yaml

        try:
            compose_data = yaml.safe_load(compose_content)
            details["syntax_valid"] = True
            details["validation_checks"]["yaml_syntax"] = {"passed": True}
            return compose_data
        except yaml.YAMLError as e:
            issues.append(f"YAML syntax error: {str(e)}")
            details["validation_checks"]["yaml_syntax"] = {"passed": False, "error": str(e)}
            details["issues"] = issues
            return None

    def _validate_compose_structure(
        self, compose_data: Any, issues: list[str], details: dict
    ) -> bool:
        """Validate basic compose file structure."""
        if not isinstance(compose_data, dict):
            issues.append("Compose file must be a YAML object")
            details["validation_checks"]["structure"] = {
                "passed": False,
                "error": "Not a YAML object",
            }
            details["issues"] = issues
            return False

        # Check for required sections
        if "services" not in compose_data:
            issues.append("No 'services' section found")
            details["validation_checks"]["services_section"] = {
                "passed": False,
                "error": "Missing services section",
            }
            return False

        services = compose_data["services"]
        if not isinstance(services, dict) or len(services) == 0:
            issues.append("'services' section is empty or invalid")
            details["validation_checks"]["services_section"] = {
                "passed": False,
                "error": "Empty or invalid services",
            }
            return False

        details["services_found"] = len(services)
        details["validation_checks"]["services_section"] = {
            "passed": True,
            "count": len(services),
        }
        return True

    def _validate_services(self, services: dict, issues: list[str], details: dict) -> None:
        """Validate individual services configuration."""
        service_issues = []

        for service_name, service_config in services.items():
            if not isinstance(service_config, dict):
                service_issues.append(
                    f"Service '{service_name}': Invalid configuration (not an object)"
                )
                continue

            # Check for image or build
            if "image" not in service_config and "build" not in service_config:
                service_issues.append(
                    f"Service '{service_name}': Missing 'image' or 'build' directive"
                )

            # Validate port and volume specifications
            self._validate_service_ports(service_name, service_config.get("ports"), service_issues)
            self._validate_service_volumes(
                service_name, service_config.get("volumes"), service_issues
            )

        issues.extend(service_issues)
        details["validation_checks"]["service_validation"] = {
            "passed": len(service_issues) == 0,
            "issues_found": len(service_issues),
        }

    def _validate_service_ports(
        self, service_name: str, ports: Any, service_issues: list[str]
    ) -> None:
        """Validate service port specifications."""
        if ports is None or not isinstance(ports, list):
            return

        for port_spec in ports:
            if isinstance(port_spec, str):
                if ":" in port_spec:
                    parts = port_spec.split(":")
                    try:
                        int(parts[0])  # host port
                        int(parts[1])  # container port
                    except (ValueError, IndexError):
                        service_issues.append(
                            f"Service '{service_name}': Invalid port specification '{port_spec}'"
                        )
            elif isinstance(port_spec, dict):
                if "target" not in port_spec:
                    service_issues.append(
                        f"Service '{service_name}': Port object missing 'target' field"
                    )

    def _validate_service_volumes(
        self, service_name: str, volumes: Any, service_issues: list[str]
    ) -> None:
        """Validate service volume specifications."""
        if volumes is None or not isinstance(volumes, list):
            return

        for volume_spec in volumes:
            if isinstance(volume_spec, str):
                if ":" not in volume_spec and not volume_spec.startswith("/"):
                    service_issues.append(
                        f"Service '{service_name}': Invalid volume specification '{volume_spec}'"
                    )

    async def check_disk_space(
        self, host: DockerHost, estimated_size: int
    ) -> tuple[bool, str, dict]:
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
            ssh_cmd = build_ssh_command(host)

            # Use df to get disk space in bytes
            df_cmd = ssh_cmd + [
                f"df -B1 {shlex.quote(appdata_path)} | tail -1 | awk '{{print $2,$3,$4}}'"
            ]
            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                df_cmd, capture_output=True, text=True, check=False
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
                    "path_checked": appdata_path,
                }

                if has_space:
                    message = f"✅ Sufficient disk space: {format_size(available)} available, {format_size(required_with_margin)} needed (with 20% margin)"
                else:
                    shortfall = required_with_margin - available
                    message = f"❌ Insufficient disk space: {format_size(available)} available, {format_size(required_with_margin)} needed (shortfall: {format_size(shortfall)})"

                return has_space, message, details
            else:
                return False, f"Failed to check disk space on {host.hostname}: {result.stderr}", {}

        except Exception as e:
            return False, f"Error checking disk space: {str(e)}", {}

    async def check_tool_availability(
        self, host: DockerHost, tools: list[str]
    ) -> tuple[bool, list[str], dict]:
        """Check if required tools are available on host.

        Args:
            host: Host configuration to check
            tools: List of tool names to check (e.g., ['rsync', 'tar', 'docker'])

        Returns:
            Tuple of (all_available: bool, missing_tools: list[str], details: dict)
        """
        ssh_cmd = build_ssh_command(host)
        tool_status = {}
        missing_tools = []

        for tool in tools:
            try:
                # Use 'which' to check if tool is available
                check_cmd = ssh_cmd + [
                    f"which {shlex.quote(tool)} >/dev/null 2>&1 && echo 'AVAILABLE' || echo 'MISSING'"
                ]
                result = await asyncio.to_thread(
                    subprocess.run,
                    check_cmd,
                    capture_output=True,
                    text=True,
                    check=False,  # nosec B603
                )

                is_available = result.returncode == 0 and "AVAILABLE" in result.stdout
                tool_status[tool] = {
                    "available": is_available,
                    "check_result": result.stdout.strip(),
                    "error": result.stderr if result.stderr else None,
                }

                if not is_available:
                    missing_tools.append(tool)

            except Exception as e:
                tool_status[tool] = {"available": False, "check_result": None, "error": str(e)}
                missing_tools.append(tool)

        all_available = len(missing_tools) == 0
        details = {
            "host": host.hostname,
            "tools_checked": tools,
            "tool_status": tool_status,
            "all_tools_available": all_available,
            "missing_tools": missing_tools,
        }

        return all_available, missing_tools, details

    def extract_ports_from_compose(self, compose_content: str) -> list[int]:
        """Extract exposed ports from compose file.

        Args:
            compose_content: Docker Compose YAML content

        Returns:
            List of port numbers that will be exposed
        """
        try:
            import yaml

            compose_data = yaml.safe_load(compose_content)
            if not compose_data:
                return []

            exposed_ports = []
            services = compose_data.get("services", {})

            for _service_name, service_config in services.items():
                service_ports = self._extract_service_ports(service_config)
                exposed_ports.extend(service_ports)

            return sorted(list(set(exposed_ports)))  # Remove duplicates and sort

        except Exception as e:
            self.logger.warning("Failed to parse ports from compose file", error=str(e))
            return []

    def _extract_service_ports(self, service_config: dict) -> list[int]:
        """Extract ports from a single service configuration."""
        ports = service_config.get("ports", [])
        service_ports = []

        for port_spec in ports:
            parsed_port = self._parse_port_specification(port_spec)
            if parsed_port is not None:
                service_ports.append(parsed_port)

        return service_ports

    def _parse_port_specification(self, port_spec) -> int | None:
        """Parse a single port specification into a port number."""
        if isinstance(port_spec, str):
            return self._parse_port_string(port_spec)
        elif isinstance(port_spec, int):
            return port_spec
        elif isinstance(port_spec, dict):
            return self._parse_port_dict(port_spec)
        else:
            return None

    def _parse_port_string(self, port_spec: str) -> int | None:
        """Parse string format ports like 'host_port:container_port' or 'port'."""
        try:
            if ":" in port_spec:
                host_port = port_spec.split(":")[0]
            else:
                host_port = port_spec

            return int(host_port)
        except ValueError:
            return None

    def _parse_port_dict(self, port_spec: dict) -> int | None:
        """Parse dictionary format ports like {target: 80, published: 8080}."""
        published_port = port_spec.get("published")
        if published_port and isinstance(published_port, int | str):
            try:
                return int(published_port)
            except ValueError:
                return None
        return None

    async def check_port_conflicts(
        self, host: DockerHost, ports: list[int]
    ) -> tuple[bool, list[int], dict]:
        """Check if ports are already in use on host.

        Args:
            host: Host configuration to check
            ports: List of port numbers to check

        Returns:
            Tuple of (all_available: bool, conflicting_ports: list[int], details: dict)
        """
        if not ports:
            return True, [], {"ports_checked": [], "conflicts": {}}

        ssh_cmd = build_ssh_command(host)
        conflicting_ports = []
        port_details = {}

        for port in ports:
            try:
                # Check if port is in use using netstat or ss
                check_cmd = ssh_cmd + [
                    f"(netstat -tuln 2>/dev/null | grep ':{port} ' || ss -tuln 2>/dev/null | grep ':{port} ') && echo 'IN_USE' || echo 'AVAILABLE'"
                ]
                result = await asyncio.to_thread(
                    subprocess.run,
                    check_cmd,
                    capture_output=True,
                    text=True,
                    check=False,  # nosec B603
                )

                is_in_use = result.returncode == 0 and "IN_USE" in result.stdout
                port_details[port] = {
                    "in_use": is_in_use,
                    "check_result": result.stdout.strip(),
                    "error": result.stderr if result.stderr else None,
                }

                if is_in_use:
                    conflicting_ports.append(port)

            except Exception as e:
                port_details[port] = {"in_use": True, "check_result": None, "error": str(e)}
                conflicting_ports.append(port)

        all_available = len(conflicting_ports) == 0
        details = {
            "host": host.hostname,
            "ports_checked": ports,
            "port_details": port_details,
            "all_ports_available": all_available,
            "conflicting_ports": conflicting_ports,
        }

        return all_available, conflicting_ports, details

    def extract_names_from_compose(self, compose_content: str) -> tuple[list[str], list[str]]:
        """Extract service and network names from compose file.

        Args:
            compose_content: Docker Compose YAML content

        Returns:
            Tuple of (service_names: list[str], network_names: list[str])
        """
        try:
            import yaml

            compose_data = yaml.safe_load(compose_content)
            service_names = []
            network_names = []

            # Extract service names
            services = compose_data.get("services", {})
            service_names = list(services.keys())

            # Extract network names
            networks = compose_data.get("networks", {})
            network_names = list(networks.keys())

            return service_names, network_names

        except Exception as e:
            self.logger.warning("Failed to parse names from compose file", error=str(e))
            return [], []

    async def check_name_conflicts(
        self, host: DockerHost, service_names: list[str], network_names: list[str]
    ) -> tuple[bool, list[str], dict]:
        """Check for container and network name conflicts.

        Args:
            host: Host configuration to check
            service_names: List of service names to check
            network_names: List of network names to check

        Returns:
            Tuple of (no_conflicts: bool, conflicting_names: list[str], details: dict)
        """
        ssh_cmd = build_ssh_command(host)
        conflicting_names = []
        name_details = {}

        # Check service/container name conflicts
        for service_name in service_names:
            try:
                check_cmd = ssh_cmd + [
                    f"docker ps -a --filter name=^{shlex.quote(service_name)}$ --format '{{{{.Names}}}}' | grep -x {shlex.quote(service_name)} && echo 'CONFLICT' || echo 'AVAILABLE'"
                ]
                result = await asyncio.to_thread(
                    subprocess.run,
                    check_cmd,
                    capture_output=True,
                    text=True,
                    check=False,  # nosec B603
                )

                has_conflict = result.returncode == 0 and "CONFLICT" in result.stdout
                name_details[f"container_{service_name}"] = {
                    "type": "container",
                    "has_conflict": has_conflict,
                    "check_result": result.stdout.strip(),
                }

                if has_conflict:
                    conflicting_names.append(f"container:{service_name}")

            except Exception as e:
                name_details[f"container_{service_name}"] = {
                    "type": "container",
                    "has_conflict": True,
                    "error": str(e),
                }
                conflicting_names.append(f"container:{service_name}")

        # Check network name conflicts
        for network_name in network_names:
            try:
                check_cmd = ssh_cmd + [
                    f"docker network ls --filter name=^{shlex.quote(network_name)}$ --format '{{{{.Name}}}}' | grep -x {shlex.quote(network_name)} && echo 'CONFLICT' || echo 'AVAILABLE'"
                ]
                result = await asyncio.to_thread(
                    subprocess.run,
                    check_cmd,
                    capture_output=True,
                    text=True,
                    check=False,  # nosec B603
                )

                has_conflict = result.returncode == 0 and "CONFLICT" in result.stdout
                name_details[f"network_{network_name}"] = {
                    "type": "network",
                    "has_conflict": has_conflict,
                    "check_result": result.stdout.strip(),
                }

                if has_conflict:
                    conflicting_names.append(f"network:{network_name}")

            except Exception as e:
                name_details[f"network_{network_name}"] = {
                    "type": "network",
                    "has_conflict": True,
                    "error": str(e),
                }
                conflicting_names.append(f"network:{network_name}")

        no_conflicts = len(conflicting_names) == 0
        details = {
            "host": host.hostname,
            "service_names_checked": service_names,
            "network_names_checked": network_names,
            "name_details": name_details,
            "no_conflicts": no_conflicts,
            "conflicting_names": conflicting_names,
        }

        return no_conflicts, conflicting_names, details

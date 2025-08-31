"""
Stack Validation Module

All validation and checking logic for Docker Compose stacks.
Handles compose syntax validation, resource checks, conflict detection, etc.
"""

import asyncio
import shlex
import subprocess

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
        issues = []
        details = {
            "stack_name": stack_name,
            "validation_checks": {},
            "syntax_valid": False,
            "services_found": 0,
            "issues": [],
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
                details["validation_checks"]["structure"] = {
                    "passed": False,
                    "error": "Not a YAML object",
                }
                details["issues"] = issues
                return False, issues, details

            # Check for required sections
            if "services" not in compose_data:
                issues.append("No 'services' section found")
                details["validation_checks"]["services_section"] = {
                    "passed": False,
                    "error": "Missing services section",
                }
            else:
                services = compose_data["services"]
                if not isinstance(services, dict) or len(services) == 0:
                    issues.append("'services' section is empty or invalid")
                    details["validation_checks"]["services_section"] = {
                        "passed": False,
                        "error": "Empty or invalid services",
                    }
                else:
                    details["services_found"] = len(services)
                    details["validation_checks"]["services_section"] = {
                        "passed": True,
                        "count": len(services),
                    }

            # Validate individual services
            if "services" in compose_data and isinstance(compose_data["services"], dict):
                service_issues = []
                for service_name, service_config in compose_data["services"].items():
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

                    # Validate port specifications
                    if "ports" in service_config:
                        ports = service_config["ports"]
                        if isinstance(ports, list):
                            for _i, port_spec in enumerate(ports):
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

                    # Validate volume specifications
                    if "volumes" in service_config:
                        volumes = service_config["volumes"]
                        if isinstance(volumes, list):
                            for volume_spec in volumes:
                                if isinstance(volume_spec, str):
                                    if ":" not in volume_spec and not volume_spec.startswith("/"):
                                        service_issues.append(
                                            f"Service '{service_name}': Invalid volume specification '{volume_spec}'"
                                        )

                issues.extend(service_issues)
                details["validation_checks"]["service_validation"] = {
                    "passed": len(service_issues) == 0,
                    "issues_found": len(service_issues),
                }

            details["issues"] = issues
            return len(issues) == 0, issues, details

        except Exception as e:
            error_msg = f"Failed to validate compose file: {str(e)}"
            issues.append(error_msg)
            details["validation_checks"]["general_error"] = {"passed": False, "error": error_msg}
            details["issues"] = issues
            return False, issues, details

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

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(df_cmd, capture_output=True, text=True, check=False),  # noqa: S603
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

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda cmd=check_cmd: subprocess.run(
                        cmd, capture_output=True, text=True, check=False
                    ),  # noqa: S603
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
            exposed_ports = []

            # Parse services for port mappings
            services = compose_data.get("services", {})
            for _service_name, service_config in services.items():
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
                        if published_port and isinstance(published_port, int | str):
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

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda cmd=check_cmd: subprocess.run(  # noqa: S603
                        cmd, capture_output=True, text=True, check=False
                    ),
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

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda cmd=check_cmd: subprocess.run(  # noqa: S603
                        cmd, capture_output=True, text=True, check=False
                    ),
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

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda cmd=check_cmd: subprocess.run(  # noqa: S603
                        cmd, capture_output=True, text=True, check=False
                    ),
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

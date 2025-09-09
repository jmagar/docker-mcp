"""
Configuration Management Service

Business logic for configuration discovery, import, and management operations.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docker_mcp.core.docker_context import DockerContextManager

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ..core.compose_manager import ComposeManager
from ..core.config_loader import DockerMCPConfig, save_config
from ..core.docker_context import DockerContextManager
from ..core.ssh_config_parser import SSHConfigParser
from ..utils import validate_host


class ConfigService:
    """Service for configuration management operations."""

    def __init__(self, config: DockerMCPConfig, context_manager: "DockerContextManager"):
        self.config = config
        self.context_manager = context_manager
        self.compose_manager = ComposeManager(config, context_manager)
        self.logger = structlog.get_logger()

    async def update_host_config(self, host_id: str, compose_path: str) -> ToolResult:
        """Update host configuration with compose file path."""
        try:
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return ToolResult(
                    content=[TextContent(type="text", text=f"Error: {error_msg}")],
                    structured_content={"success": False, "error": error_msg},
                )

            # Update the host configuration
            self.config.hosts[host_id].compose_path = compose_path

            # In a production system, this would persist to hosts.yml
            # For now, we'll just update the in-memory configuration

            self.logger.info(
                "Host configuration updated", host_id=host_id, compose_path=compose_path
            )

            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Success: Updated compose path for {host_id} to {compose_path}",
                    )
                ],
                structured_content={
                    "success": True,
                    "host_id": host_id,
                    "compose_path": compose_path,
                    "message": "Host configuration updated (in-memory only)",
                },
            )

        except Exception as e:
            self.logger.error(
                "Failed to update host config",
                host_id=host_id,
                compose_path=compose_path,
                error=str(e),
            )
            return ToolResult(
                content=[
                    TextContent(type="text", text=f"❌ Failed to update host config: {str(e)}")
                ],
                structured_content={"success": False, "error": str(e), "host_id": host_id},
            )

    async def discover_compose_paths(self, host_id: str | None = None) -> ToolResult:
        """Discover Docker Compose file locations and guide user through configuration."""
        try:
            discovery_results = []
            hosts_to_check = [host_id] if host_id else list(self.config.hosts.keys())

            if host_id:
                is_valid, error_msg = validate_host(self.config, host_id)
                if not is_valid:
                    return ToolResult(
                        content=[TextContent(type="text", text=f"Error: {error_msg}")],
                        structured_content={"success": False, "error": error_msg},
                    )

            # Discover compose locations for each host
            discovery_results = await self._perform_discovery(hosts_to_check)

            # Format results for user
            summary_lines, recommendations = self._format_discovery_results(discovery_results)

            return ToolResult(
                content=[TextContent(type="text", text="\n".join(summary_lines))],
                structured_content={
                    "success": True,
                    "discovery_results": discovery_results,
                    "recommendations": recommendations,
                    "hosts_analyzed": len(discovery_results),
                },
            )

        except Exception as e:
            self.logger.error("Failed to discover compose paths", host_id=host_id, error=str(e))
            return ToolResult(
                content=[
                    TextContent(type="text", text=f"❌ Failed to discover compose paths: {str(e)}")
                ],
                structured_content={"success": False, "error": str(e), "host_id": host_id},
            )

    async def _perform_discovery(self, hosts_to_check: list[str]) -> list[dict[str, Any]]:
        """Perform compose path discovery for specified hosts."""
        discovery_results = []

        for current_host_id in hosts_to_check:
            if not self.config.hosts[current_host_id].enabled:
                continue

            self.logger.info(f"Discovering compose locations for {current_host_id}")
            discovery_result = await self.compose_manager.discover_compose_locations(
                current_host_id
            )
            discovery_results.append(discovery_result)

        return discovery_results

    def _format_discovery_results(
        self, discovery_results: list[dict[str, Any]]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Format discovery results for display."""
        summary_lines = ["Docker Compose Path Discovery Results", "=" * 45, ""]
        recommendations = []

        for result in discovery_results:
            host_id = result["host_id"]
            host_config = self.config.hosts[host_id]

            summary_lines.extend(self._format_host_discovery(result, host_config, recommendations))

        # Add configuration instructions
        if recommendations:
            summary_lines.extend(self._format_recommendations(recommendations))
        else:
            summary_lines.append(
                "✅ All hosts are properly configured or have no stacks requiring configuration."
            )

        return summary_lines, recommendations

    def _format_host_discovery(
        self, result: dict[str, Any], host_config: Any, recommendations: list[dict[str, Any]]
    ) -> list[str]:
        """Format discovery results for a single host."""
        host_id = result["host_id"]
        lines = [
            f"Host: {host_id} ({host_config.hostname})",
            "-" * 30,
        ]

        # Show current configuration status
        current_path = host_config.compose_path
        if current_path:
            lines.append(f"Currently configured path: {current_path}")
        else:
            lines.append("No compose path currently configured")

        # Show discovery results
        lines.append(f"Analysis: {result['analysis']}")

        if result["stacks_found"]:
            lines.append(f"Found stacks ({len(result['stacks_found'])}):")
            for stack in result["stacks_found"]:
                lines.append(f"  • {stack['name']}: {stack['compose_file']}")

        # Show compose locations breakdown
        if result["compose_locations"]:
            lines.append("Compose file locations:")
            for location, data in result["compose_locations"].items():
                stacks_list = ", ".join(data["stacks"])
                lines.append(f"  • {location}: {data['count']} stacks ({stacks_list})")

        # Generate recommendation
        suggested_path = result["suggested_path"]
        if suggested_path:
            self._add_recommendation(
                lines, recommendations, host_id, current_path, suggested_path, result
            )

        lines.append("")
        return lines

    def _add_recommendation(
        self,
        lines: list[str],
        recommendations: list[dict[str, Any]],
        host_id: str,
        current_path: str | None,
        suggested_path: str,
        result: dict[str, Any],
    ) -> None:
        """Add recommendation for host configuration."""
        if current_path == suggested_path:
            lines.append("✅ Current configuration matches discovery results")
        elif current_path:
            lines.append(f"💡 Recommendation: Consider changing to {suggested_path}")
            recommendations.append(
                {
                    "host_id": host_id,
                    "current_path": current_path,
                    "suggested_path": suggested_path,
                    "reason": result["analysis"],
                }
            )
        else:
            lines.append(f"💡 Recommendation: Set compose_path to {suggested_path}")
            recommendations.append(
                {
                    "host_id": host_id,
                    "current_path": None,
                    "suggested_path": suggested_path,
                    "reason": result["analysis"],
                }
            )

    def _format_recommendations(self, recommendations: list[dict[str, Any]]) -> list[str]:
        """Format configuration recommendations."""
        lines = ["Configuration Recommendations:", "=" * 30, ""]

        for rec in recommendations:
            if rec["current_path"]:
                lines.append(
                    f"Host '{rec['host_id']}': Update compose path from {rec['current_path']} to {rec['suggested_path']}"
                )
            else:
                lines.append(
                    f"Host '{rec['host_id']}': Set compose path to {rec['suggested_path']}"
                )
            lines.append(f"  Reason: {rec['reason']}")
            lines.append(
                f"  Command: update_host_config(host_id='{rec['host_id']}', compose_path='{rec['suggested_path']}')"
            )
            lines.append("")

        lines.extend(
            [
                "Note: Each stack will be stored in its own subdirectory:",
                "  {compose_path}/{stack_name}/docker-compose.yml",
                "",
                "Example: If compose_path is '/mnt/user/compose' and you deploy",
                "a stack named 'myapp', it will be stored at:",
                "  /mnt/user/compose/myapp/docker-compose.yml",
            ]
        )

        return lines

    async def import_ssh_config(
        self,
        ssh_config_path: str | None = None,
        selected_hosts: str | None = None,
        config_path: str | None = None,
    ) -> ToolResult:
        """Import hosts from SSH config with interactive selection and compose path discovery."""
        try:
            # Initialize SSH config parser
            ssh_parser = SSHConfigParser(ssh_config_path)

            # Validate SSH config file
            is_valid, status_message = ssh_parser.validate_config_file()
            if not is_valid:
                return ToolResult(
                    content=[
                        TextContent(type="text", text=f"❌ SSH Config Error: {status_message}")
                    ],
                    structured_content={"success": False, "error": status_message},
                )

            # Get importable hosts
            importable_hosts = ssh_parser.get_importable_hosts()
            if not importable_hosts:
                return ToolResult(
                    content=[
                        TextContent(type="text", text="❌ No importable hosts found in SSH config")
                    ],
                    structured_content={"success": False, "error": "No importable hosts found"},
                )

            # Handle host selection
            if selected_hosts is None:
                return self._show_host_selection(importable_hosts)

            # Parse and import selected hosts
            hosts_to_import = self._parse_host_selection(selected_hosts, importable_hosts)
            if isinstance(hosts_to_import, ToolResult):  # Error case
                return hosts_to_import

            # Process selected hosts
            imported_hosts, compose_path_configs = await self._import_selected_hosts(
                hosts_to_import
            )

            if not imported_hosts:
                return ToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text="❌ No new hosts to import (all selected hosts already exist)",
                        )
                    ],
                    structured_content={"success": False, "error": "No new hosts to import"},
                )

            # Save configuration
            if config_path:
                save_config(self.config, config_path)

            # Build result summary
            summary_lines = self._format_import_results(imported_hosts, compose_path_configs)

            self.logger.info(
                "SSH config import completed",
                imported_hosts=len(imported_hosts),
                compose_paths_configured=len(compose_path_configs),
            )

            return ToolResult(
                content=[TextContent(type="text", text="\n".join(summary_lines))],
                structured_content={
                    "success": True,
                    "imported_hosts": imported_hosts,
                    "compose_path_configs": compose_path_configs,
                    "total_imported": len(imported_hosts),
                },
            )

        except Exception as e:
            self.logger.error("SSH config import failed", error=str(e))
            return ToolResult(
                content=[TextContent(type="text", text=f"❌ SSH config import failed: {str(e)}")],
                structured_content={"success": False, "error": str(e)},
            )

    def _show_host_selection(self, importable_hosts: list) -> ToolResult:
        """Show available hosts for selection."""
        summary_lines = [
            "SSH Config Import - Host Selection",
            "=" * 40,
            "",
            f"Found {len(importable_hosts)} importable hosts in SSH config:",
            "",
        ]

        for i, entry in enumerate(importable_hosts, 1):
            hostname = entry.hostname or entry.name
            user = entry.user or "root"
            port_info = f":{entry.port}" if entry.port != 22 else ""

            summary_lines.append(f"  [{i}] {entry.name}")
            summary_lines.append(f"      → {user}@{hostname}{port_info}")
            if entry.identity_file:
                summary_lines.append(f"      → Key: {entry.identity_file}")
            summary_lines.append("")

        summary_lines.extend(
            [
                "To import specific hosts, use:",
                '  import_ssh_config(selected_hosts="1,3,5")  # Import hosts 1, 3, and 5',
                '  import_ssh_config(selected_hosts="all")     # Import all hosts',
                "",
                "To import all hosts:",
                '  import_ssh_config(selected_hosts="all")',
            ]
        )

        return ToolResult(
            content=[TextContent(type="text", text="\n".join(summary_lines))],
            structured_content={
                "success": True,
                "action": "selection_required",
                "importable_hosts": [
                    {
                        "index": i,
                        "name": entry.name,
                        "hostname": entry.hostname or entry.name,
                        "user": entry.user or "root",
                        "port": entry.port,
                    }
                    for i, entry in enumerate(importable_hosts, 1)
                ],
            },
        )

    def _parse_host_selection(
        self, selected_hosts: str, importable_hosts: list
    ) -> list | ToolResult:
        """Parse host selection string and return hosts to import."""
        if selected_hosts.lower() == "all":
            return importable_hosts

        try:
            indices = [int(x.strip()) for x in selected_hosts.split(",")]
            hosts_to_import = []
            for idx in indices:
                if 1 <= idx <= len(importable_hosts):
                    hosts_to_import.append(importable_hosts[idx - 1])
                else:
                    return ToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"❌ Invalid host index: {idx}. Valid range: 1-{len(importable_hosts)}",
                            )
                        ],
                        structured_content={
                            "success": False,
                            "error": f"Invalid host index: {idx}",
                        },
                    )
            return hosts_to_import
        except ValueError:
            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="❌ Invalid selection format. Use comma-separated numbers or 'all'",
                    )
                ],
                structured_content={"success": False, "error": "Invalid selection format"},
            )

    async def _import_selected_hosts(
        self,
        hosts_to_import: list,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Import selected hosts with compose path discovery."""
        imported_hosts = []
        compose_path_configs = []

        for ssh_entry in hosts_to_import:
            host_id = ssh_entry.name

            # Skip if host already exists
            if host_id in self.config.hosts:
                self.logger.warning("Host already exists, skipping", host_id=host_id)
                continue

            # Convert SSH entry to DockerHost
            docker_host = ssh_entry.to_docker_host()

            # Temporarily add host to config for compose path discovery
            self.config.hosts[host_id] = docker_host

            # Discover compose path for this host
            compose_path = await self._discover_compose_path_for_host(host_id)

            # Set compose path if discovered or provided
            if compose_path:
                docker_host.compose_path = compose_path
                compose_path_configs.append(
                    {
                        "host_id": host_id,
                        "compose_path": compose_path,
                        "discovered": True,
                    }
                )

            # Update the host in configuration
            self.config.hosts[host_id] = docker_host
            imported_hosts.append(
                {
                    "host_id": host_id,
                    "hostname": docker_host.hostname,
                    "user": docker_host.user,
                    "port": docker_host.port,
                    "compose_path": docker_host.compose_path,
                }
            )

        return imported_hosts, compose_path_configs

    async def _discover_compose_path_for_host(
        self,
        host_id: str,
    ) -> str | None:
        """Discover compose path for a specific host."""
        # Try to discover compose path
        try:
            discovery_result = await self.compose_manager.discover_compose_locations(host_id)
            return discovery_result.get("suggested_path")
        except Exception as e:
            self.logger.debug(
                "Could not discover compose path for new host",
                host_id=host_id,
                error=str(e),
            )
            return None

    def _format_import_results(
        self,
        imported_hosts: list[dict[str, Any]],
        compose_path_configs: list[dict[str, Any]],
    ) -> list[str]:
        """Format import results for display."""
        summary_lines = [
            "✅ SSH Config Import Completed",
            "=" * 35,
            "",
            f"Successfully imported {len(imported_hosts)} hosts:",
            "",
        ]

        for host_info in imported_hosts:
            summary_lines.append(
                f"• {host_info['host_id']} ({host_info['user']}@{host_info['hostname']})"
            )
            if host_info["compose_path"]:
                summary_lines.append(f"  Compose path: {host_info['compose_path']}")
            summary_lines.append("")

        if compose_path_configs:
            summary_lines.extend(["Compose Path Configuration:", "─" * 28])
            for config in compose_path_configs:
                source = "discovered" if config["discovered"] else "manually set"
                summary_lines.append(f"• {config['host_id']}: {config['compose_path']} ({source})")
            summary_lines.append("")

        summary_lines.extend(
            [
                "Configuration saved to hosts.yml and hot-reloaded.",
                "You can now use these hosts with deploy_stack and other tools.",
            ]
        )

        return summary_lines

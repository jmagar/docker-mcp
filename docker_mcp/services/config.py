"""
Configuration Management Service

Business logic for configuration discovery, import, and management operations.
"""

import asyncio
import difflib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docker_mcp.core.docker_context import DockerContextManager
    from docker_mcp.core.ssh_config_parser import SSHConfigEntry

import structlog
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from ..core.compose_manager import ComposeManager
from ..core.config_loader import DockerMCPConfig, save_config
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
        """Perform compose path discovery for specified hosts with concurrency."""
        enabled_hosts = [
            host_id for host_id in hosts_to_check if self.config.hosts[host_id].enabled
        ]

        if not enabled_hosts:
            return []

        self.logger.info(
            "Starting compose discovery", total_hosts=len(enabled_hosts), hosts=enabled_hosts
        )

        # Run discovery operations concurrently
        async def discover_single_host(host_id: str) -> dict[str, Any]:
            self.logger.info("Discovering compose locations", host_id=host_id)
            return await self.compose_manager.discover_compose_locations(host_id)

        # Run discovery operations concurrently with error handling (Python 3.10 compatible)
        tasks = [asyncio.create_task(discover_single_host(host_id)) for host_id in enabled_hosts]

        # Use asyncio.gather with return_exceptions for Python 3.10 compatibility
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and handle exceptions
        discovery_results: list[dict[str, Any]] = []
        failed_count = 0
        for i, result in enumerate(results):
            host_id = enabled_hosts[i]
            if isinstance(result, Exception):
                failed_count += 1
                self.logger.error("Discovery failed for host", host_id=host_id, error=str(result))
            elif isinstance(result, dict):
                discovery_results.append(result)

        if failed_count > 0:
            self.logger.warning(
                "Discovery completed with some failures",
                successful=len(discovery_results),
                failed=failed_count,
                total=len(enabled_hosts),
            )

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
            is_valid, status_message = await asyncio.to_thread(ssh_parser.validate_config_file)
            if not is_valid:
                return ToolResult(
                    content=[
                        TextContent(type="text", text=f"❌ SSH Config Error: {status_message}")
                    ],
                    structured_content={"success": False, "error": status_message},
                )

            # Get importable hosts
            importable_hosts = await asyncio.to_thread(ssh_parser.get_importable_hosts)
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
            config_file_to_use = config_path or getattr(self.config, "config_file", None)
            if config_file_to_use:
                await asyncio.to_thread(save_config, self.config, config_file_to_use)

            # Build result summary
            summary_lines = self._format_import_results(imported_hosts, compose_path_configs, config_file_to_use)

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

    def _show_host_selection(self, importable_hosts: list["SSHConfigEntry"]) -> ToolResult:
        """Show available hosts for selection."""
        summary_lines = [
            "SSH Config Import - Host Selection",
            "=" * 40,
            "",
            f"Found {len(importable_hosts)} importable hosts in SSH config:",
            "",
        ]

        # Display hosts with 1-based indexing for user-friendly selection
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
                "To import specific hosts, use any of these formats:",
                "",
                "By index:",
                '  import_ssh_config(selected_hosts="1,3,5")    # Import hosts 1, 3, and 5',
                "",
                "By hostname:",
                '  import_ssh_config(selected_hosts="dookie,squirts,slam")  # Import by name',
                "",
                "Mixed format:",
                '  import_ssh_config(selected_hosts="1,dookie,5")  # Mix indices and names',
                "",
                "Import all hosts:",
                '  import_ssh_config(selected_hosts="all")',
                "",
                "Note: Hostname matching is fuzzy and case-insensitive.",
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

    def _fuzzy_match_host(
        self, query: str, importable_hosts: list["SSHConfigEntry"]
    ) -> tuple["SSHConfigEntry", float] | None:
        """Find best matching host using fuzzy string matching.

        Args:
            query: The search term (hostname or partial match)
            importable_hosts: List of SSH host entries to search

        Returns:
            Tuple of (matched_host, confidence_score) or None if no good match
        """
        best_match = None
        best_score = 0.0
        min_threshold = 0.6  # Minimum similarity score to consider a match

        for host in importable_hosts:
            # Get all searchable strings for this host
            searchable_strings = [
                host.name.lower(),  # SSH config name
                (host.hostname or host.name).lower(),  # Hostname
            ]

            # Check for exact matches first
            query_lower = query.lower().strip()
            for search_string in searchable_strings:
                if query_lower == search_string:
                    return host, 1.0  # Perfect match

            # Check for partial matches
            for search_string in searchable_strings:
                # Check if query is contained in string (partial match)
                if query_lower in search_string:
                    score = len(query_lower) / len(search_string)
                    if score > best_score:
                        best_match = host
                        best_score = score

                # Use difflib for fuzzy matching
                similarity = difflib.SequenceMatcher(None, query_lower, search_string).ratio()
                if similarity > best_score and similarity >= min_threshold:
                    best_match = host
                    best_score = similarity

        return (best_match, best_score) if best_match else None

    def _parse_host_selection(
        self, selected_hosts: str, importable_hosts: list["SSHConfigEntry"]
    ) -> list["SSHConfigEntry"] | ToolResult:
        """Parse host selection string and return hosts to import.

        Supports multiple formats:
        - "all" - select all hosts
        - "1,3,5" - numeric indices
        - "dookie,squirts,slam" - hostnames with fuzzy matching
        - "1,dookie,3" - mixed indices and hostnames
        """
        if selected_hosts.lower().strip() == "all":
            return importable_hosts

        # Split by comma and clean up whitespace
        selection_items = [item.strip() for item in selected_hosts.split(",") if item.strip()]

        if not selection_items:
            return self._create_empty_selection_error()

        hosts_to_import, errors = self._process_selection_items(selection_items, importable_hosts)

        if errors:
            return self._create_selection_error(errors, importable_hosts)

        return self._remove_duplicate_hosts(hosts_to_import)

    def _create_empty_selection_error(self) -> ToolResult:
        """Create error result for empty selection."""
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text="❌ Empty selection. Provide host indices, hostnames, or 'all'",
                )
            ],
            structured_content={"success": False, "error": "Empty selection"},
        )

    def _process_selection_items(
        self, selection_items: list[str], importable_hosts: list["SSHConfigEntry"]
    ) -> tuple[list["SSHConfigEntry"], list[str]]:
        """Process each selection item and return hosts and errors."""
        hosts_to_import = []
        errors = []

        for item in selection_items:
            host, error = self._process_single_selection_item(item, importable_hosts)
            if host:
                hosts_to_import.append(host)
            if error:
                errors.append(error)

        return hosts_to_import, errors

    def _process_single_selection_item(
        self, item: str, importable_hosts: list["SSHConfigEntry"]
    ) -> tuple["SSHConfigEntry | None", str | None]:
        """Process a single selection item and return host and optional error."""
        # Try to parse as numeric index first (user provides 1-based, convert to 0-based array access)
        try:
            idx = int(item)
            if 1 <= idx <= len(importable_hosts):
                # Convert from 1-based user input to 0-based array index
                return importable_hosts[idx - 1], None
            else:
                return None, f"Index {idx} out of range (1-{len(importable_hosts)})"
        except ValueError:
            # Not a number, try hostname matching
            pass

        # Try fuzzy matching for hostname
        match_result = self._fuzzy_match_host(item, importable_hosts)
        if match_result:
            matched_host, confidence = match_result

            # Log fuzzy matches with low confidence for user awareness
            if confidence < 0.9:
                self.logger.info(
                    "Fuzzy matched hostname",
                    query=item,
                    matched=matched_host.name,
                    confidence=confidence,
                )
            return matched_host, None
        else:
            return None, f"No match found for '{item}'"

    def _create_selection_error(
        self, errors: list[str], importable_hosts: list["SSHConfigEntry"]
    ) -> ToolResult:
        """Create error result for selection errors."""
        error_msg = "Selection errors: " + "; ".join(errors)
        available_names = [host.name for host in importable_hosts]
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"❌ {error_msg}\n\nAvailable hosts: {', '.join(available_names)}\nUse indices (1-{len(importable_hosts)}), hostnames, or 'all'",
                )
            ],
            structured_content={
                "success": False,
                "error": error_msg,
                "available_hosts": available_names,
            },
        )

    def _remove_duplicate_hosts(
        self, hosts_to_import: list["SSHConfigEntry"]
    ) -> list["SSHConfigEntry"]:
        """Remove duplicate hosts while preserving order."""
        unique_hosts = []
        seen_names = set()
        for host in hosts_to_import:
            if host.name not in seen_names:
                unique_hosts.append(host)
                seen_names.add(host.name)
        return unique_hosts

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
        config_file_path: str | None = None,
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

        # Show actual config file path where configuration was saved
        config_file_message = f"Configuration saved to {config_file_path}." if config_file_path else "Configuration updated in memory."
        summary_lines.extend(
            [
                config_file_message,
                "You can now use these hosts with deploy_stack and other tools.",
            ]
        )

        return summary_lines

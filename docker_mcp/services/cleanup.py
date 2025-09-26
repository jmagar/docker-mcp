"""
Docker Cleanup Service

Business logic for Docker cleanup and disk usage operations.
"""

import asyncio
import re
from typing import Any

import structlog

from ..core.config_loader import DockerHost, DockerMCPConfig
from ..utils import build_ssh_command, format_size, validate_host

# Constants
TOP_CANDIDATES = 10
TOP_CONSUMERS = 10

# Docker container parsing constants
CONTAINER_SIZE_COLUMN_INDEX = 5  # Column after SIZE in docker ps output
CONTAINER_CREATED_COLUMN_INDEX = 8  # CREATED column index
MIN_CONTAINER_COLUMNS = 9  # Minimum columns expected for complete container info


class CleanupService:
    """Service for Docker cleanup and disk usage operations."""

    def __init__(self, config: DockerMCPConfig):
        self.config = config
        self.logger = structlog.get_logger().bind(service="CleanupService")

    async def docker_cleanup(self, host_id: str, cleanup_type: str) -> dict[str, Any]:
        """Perform Docker cleanup operations on a host.

        Args:
            host_id: Target Docker host identifier
            cleanup_type: Type of cleanup (check, safe, moderate, aggressive)

        Returns:
            Cleanup results and statistics
        """
        try:
            # Validate host
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return {"success": False, "error": error_msg}

            host = self.config.hosts[host_id]

            self.logger.info(
                "Starting Docker cleanup",
                host_id=host_id,
                operation="docker_cleanup",
                cleanup_type=cleanup_type,
                hostname=host.hostname,
            )

            if cleanup_type == "check":
                return await self._check_cleanup(host, host_id)
            elif cleanup_type == "safe":
                return await self._safe_cleanup(host, host_id)
            elif cleanup_type == "moderate":
                return await self._moderate_cleanup(host, host_id)
            elif cleanup_type == "aggressive":
                return await self._aggressive_cleanup(host, host_id)
            else:
                return {"success": False, "error": f"Invalid cleanup_type: {cleanup_type}"}

        except Exception as e:
            self.logger.error(
                "Docker cleanup failed", host_id=host_id, cleanup_type=cleanup_type, error=str(e)
            )
            return {"success": False, "error": str(e)}

    async def docker_disk_usage(
        self, host_id: str, include_details: bool = False
    ) -> dict[str, Any]:
        """Check Docker disk usage on a host.

        Args:
            host_id: Target Docker host identifier
            include_details: Include detailed top consumers (default: False for smaller response)

        Returns:
            Disk usage information and statistics
        """
        try:
            # Validate host
            is_valid, error_msg = validate_host(self.config, host_id)
            if not is_valid:
                return {"success": False, "error": error_msg}

            host = self.config.hosts[host_id]

            self.logger.info(
                "Checking Docker disk usage",
                host_id=host_id,
                operation="docker_disk_usage",
                hostname=host.hostname,
            )

            # Get disk usage summary
            summary_cmd = build_ssh_command(host) + ["docker", "system", "df"]
            proc = await asyncio.create_subprocess_exec(
                *summary_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # nosec B603
            try:
                summary_stdout, summary_stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=60
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return {"success": False, "error": "Timeout getting docker disk usage summary"}

            if proc.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to get disk usage: {summary_stderr.decode()}",
                }

            # Get detailed usage
            detailed_cmd = build_ssh_command(host) + ["docker", "system", "df", "-v"]
            dproc = await asyncio.create_subprocess_exec(
                *detailed_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # nosec B603
            try:
                detailed_stdout, detailed_stderr = await asyncio.wait_for(
                    dproc.communicate(), timeout=120
                )
            except TimeoutError:
                dproc.kill()
                await dproc.wait()
                detailed_stdout = b""  # fall back to no details

            # Parse results
            summary = self._parse_disk_usage_summary(summary_stdout.decode())
            detailed = (
                self._parse_disk_usage_detailed(detailed_stdout.decode())
                if dproc.returncode == 0
                else {}
            )

            # Generate cleanup recommendations
            cleanup_potential = self._analyze_cleanup_potential(summary_stdout.decode())
            recommendations = self._generate_cleanup_recommendations(summary, detailed)

            # Base response with essential information
            response = {
                "success": True,
                "host_id": host_id,
                "summary": summary,
                "cleanup_potential": cleanup_potential,
                "recommendations": recommendations,
            }

            # Only include detailed information if requested (reduces token count)
            if include_details:
                response["top_consumers"] = detailed

            return response

        except Exception as e:
            self.logger.error("Docker disk usage check failed", host_id=host_id, error=str(e))
            return {"success": False, "error": str(e)}

    async def _check_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """Show detailed summary of what would be cleaned without actually cleaning."""

        # Get comprehensive disk usage data
        disk_usage_data = await self.docker_disk_usage(host_id, include_details=True)

        if not disk_usage_data.get("success", False):
            return {
                "success": False,
                "error": f"Failed to analyze disk usage: {disk_usage_data.get('error', 'Unknown error')}",
            }

        summary = disk_usage_data.get("summary", {})

        # Get additional specific cleanup data
        cleanup_details = await self._get_cleanup_details(host, host_id)

        # Format cleanup summary
        cleanup_summary = self._format_cleanup_summary(summary, cleanup_details)

        # Calculate cleanup level space estimates
        cleanup_levels = self._calculate_cleanup_levels(summary)

        return {
            "success": True,
            "host_id": host_id,
            "cleanup_type": "check",
            "mode": "check",
            "summary": cleanup_summary,
            "cleanup_levels": cleanup_levels,
            "total_reclaimable": summary.get("totals", {}).get("total_reclaimable", "0B"),
            "reclaimable_percentage": summary.get("totals", {}).get("reclaimable_percentage", 0),
            "recommendations": disk_usage_data.get("recommendations", []),
            "message": "📊 Cleanup (check) analysis complete - no actual cleanup was performed",
            "formatted_output": self._build_formatted_output(
                host_id,
                "check",
                {
                    "summary": cleanup_summary,
                    "total_reclaimable": summary.get("totals", {}).get("total_reclaimable", "0B"),
                    "reclaimable_percentage": summary.get("totals", {}).get("reclaimable_percentage", 0),
                    "recommendations": disk_usage_data.get("recommendations", []),
                },
            ),
        }

    async def _safe_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """Perform safe cleanup: containers, networks, build cache."""
        results = []

        # Clean stopped containers
        container_cmd = build_ssh_command(host) + ["docker", "container", "prune", "-f"]
        container_result = await self._run_cleanup_command(container_cmd, "containers")
        results.append(container_result)

        # Clean unused networks
        network_cmd = build_ssh_command(host) + ["docker", "network", "prune", "-f"]
        network_result = await self._run_cleanup_command(network_cmd, "networks")
        results.append(network_result)

        # Clean build cache
        builder_cmd = build_ssh_command(host) + ["docker", "builder", "prune", "-f"]
        builder_result = await self._run_cleanup_command(builder_cmd, "build cache")
        results.append(builder_result)

        return {
            "success": True,
            "host_id": host_id,
            "cleanup_type": "safe",
            "mode": "safe",
            "results": results,
            "message": "Safe cleanup completed - removed stopped containers, networks, and cleaned build cache",
            "formatted_output": self._build_formatted_output(
                host_id,
                "safe",
                {"results": results},
            ),
        }

    async def _moderate_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """Perform moderate cleanup: safe cleanup + unused images."""
        # First do safe cleanup
        safe_result = await self._safe_cleanup(host, host_id)

        # Then clean unused images
        images_cmd = build_ssh_command(host) + ["docker", "image", "prune", "-a", "-f"]
        images_result = await self._run_cleanup_command(images_cmd, "unused images")

        safe_result["results"].append(images_result)
        safe_result["cleanup_type"] = "moderate"
        safe_result["mode"] = "moderate"
        safe_result["message"] = (
            "Moderate cleanup completed - removed unused containers, networks, build cache, and images"
        )

        safe_result["formatted_output"] = self._build_formatted_output(
            host_id,
            "moderate",
            {"results": safe_result["results"]},
        )
        return safe_result

    async def _aggressive_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """Perform aggressive cleanup: moderate cleanup + volumes."""
        # First do moderate cleanup
        moderate_result = await self._moderate_cleanup(host, host_id)

        # Then clean unused volumes (DANGEROUS)
        volumes_cmd = build_ssh_command(host) + ["docker", "volume", "prune", "-f"]
        volumes_result = await self._run_cleanup_command(volumes_cmd, "unused volumes")

        moderate_result["results"].append(volumes_result)
        moderate_result["cleanup_type"] = "aggressive"
        moderate_result["mode"] = "aggressive"
        moderate_result["message"] = (
            "⚠️ AGGRESSIVE cleanup completed - removed unused containers, networks, "
            "build cache, images, and volumes"
        )

        moderate_result["formatted_output"] = self._build_formatted_output(
            host_id,
            "aggressive",
            {"results": moderate_result["results"]},
        )
        return moderate_result

    def _build_formatted_output(
        self, host_id: str, cleanup_type: str, payload: dict[str, Any]
    ) -> str:
        lines = [f"Cleanup ({cleanup_type}) on {host_id}"]

        if cleanup_type == "check":
            self._format_check_output(lines, payload)
        else:
            self._format_execution_output(lines, payload)

        return "\n".join(lines)

    def _format_check_output(self, lines: list[str], payload: dict[str, Any]) -> None:
        """Format output for check cleanup type."""
        reclaimable = payload.get("total_reclaimable", "0B")
        percentage = payload.get("reclaimable_percentage", 0)
        lines.append(f"Reclaimable: {reclaimable} ({percentage}%)")

        summary = payload.get("summary", {})
        for resource, details in summary.items():
            if not isinstance(details, dict):
                continue

            resource_line = self._format_resource_details(resource, details)
            if resource_line:
                lines.append(resource_line)

        self._add_recommendations(lines, payload.get("recommendations", []))

    def _format_resource_details(self, resource: str, details: dict[str, Any]) -> str:
        """Format resource details for display."""
        parts: list[str] = []
        if "stopped" in details:
            parts.append(f"stopped {details.get('stopped')}")
        if "unused" in details:
            parts.append(f"unused {details.get('unused')}")
        if "reclaimable_space" in details:
            parts.append(f"reclaim {details.get('reclaimable_space')}")
        if "size" in details:
            parts.append(f"size {details.get('size')}")

        return f"{resource.title()}: {', '.join(parts)}" if parts else ""

    def _add_recommendations(self, lines: list[str], recommendations: list[str]) -> None:
        """Add recommendations to output lines."""
        if recommendations:
            lines.append("")
            lines.append("Recommendations:")
            for recommendation in recommendations:
                lines.append(f"  • {recommendation}")

    def _format_execution_output(self, lines: list[str], payload: dict[str, Any]) -> None:
        """Format output for execution cleanup types."""
        results = payload.get("results", [])
        for entry in results:
            resource = entry.get("resource_type", "resource")
            if entry.get("success"):
                lines.append(
                    f"• {resource}: reclaimed {entry.get('space_reclaimed', '0B')}"
                )
            else:
                lines.append(
                    f"• {resource}: failed ({entry.get('error', 'unknown error')})"
                )

    async def _run_cleanup_command(self, cmd: list[str], resource_type: str) -> dict[str, Any]:
        """Run a cleanup command and parse results."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # nosec B603
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "resource_type": resource_type,
                "success": False,
                "error": "Timeout executing cleanup command",
                "space_reclaimed": "0B",
            }

        if proc.returncode != 0:
            return {
                "resource_type": resource_type,
                "success": False,
                "error": stderr.decode(),
                "space_reclaimed": "0B",
            }

        # Parse space reclaimed from output
        out_str = stdout.decode().strip()
        space_reclaimed = self._parse_cleanup_output(out_str)

        return {
            "resource_type": resource_type,
            "success": True,
            "space_reclaimed": space_reclaimed,
            "output": out_str,
        }

    def _parse_disk_usage_summary(self, output: str) -> dict[str, Any]:
        """Parse docker system df output for summary with calculations."""
        lines = output.strip().split("\n")
        if len(lines) < 2:
            return self._create_empty_disk_usage_summary()

        summary = self._create_empty_disk_usage_summary()

        # Parse each line for different resource types
        for line in lines[1:]:
            if "Images" in line:
                self._parse_images_line(line, summary)
            elif "Containers" in line:
                self._parse_containers_line(line, summary)
            elif "Local Volumes" in line:
                self._parse_volumes_line(line, summary)
            elif "Build Cache" in line:
                self._parse_build_cache_line(line, summary)

        # Calculate and add totals
        self._calculate_totals(summary)

        return summary

    def _create_empty_disk_usage_summary(self) -> dict[str, Any]:
        """Create empty disk usage summary structure."""
        return {
            "images": {
                "count": 0,
                "size": "0B",
                "size_bytes": 0,
                "reclaimable": "0B",
                "reclaimable_bytes": 0,
            },
            "containers": {
                "count": 0,
                "size": "0B",
                "size_bytes": 0,
                "reclaimable": "0B",
                "reclaimable_bytes": 0,
            },
            "volumes": {
                "count": 0,
                "size": "0B",
                "size_bytes": 0,
                "reclaimable": "0B",
                "reclaimable_bytes": 0,
            },
            "build_cache": {
                "size": "0B",
                "size_bytes": 0,
                "reclaimable": "0B",
                "reclaimable_bytes": 0,
            },
            "totals": {
                "total_size": "0B",
                "total_size_bytes": 0,
                "total_reclaimable": "0B",
                "total_reclaimable_bytes": 0,
                "reclaimable_percentage": 0,
            },
        }

    def _parse_images_line(self, line: str, summary: dict[str, Any]) -> None:
        """Parse Images line from docker system df output."""
        parts = line.split()
        if len(parts) >= 5:
            total_count = int(parts[1]) if parts[1].isdigit() else 0
            active_count = int(parts[2]) if parts[2].isdigit() else 0
            size_str = parts[3]
            reclaimable_str = parts[4] if len(parts) > 4 else "0B"

            summary["images"] = {
                "count": total_count,
                "active": active_count,
                "size": size_str,
                "size_bytes": self._parse_docker_size(size_str),
                "reclaimable": reclaimable_str,
                "reclaimable_bytes": self._parse_docker_size(reclaimable_str),
            }

    def _parse_containers_line(self, line: str, summary: dict[str, Any]) -> None:
        """Parse Containers line from docker system df output."""
        parts = line.split()
        if len(parts) >= 5:
            total_count = int(parts[1]) if parts[1].isdigit() else 0
            active_count = int(parts[2]) if parts[2].isdigit() else 0
            size_str = parts[3]
            reclaimable_str = parts[4] if len(parts) > 4 else "0B"

            summary["containers"] = {
                "count": total_count,
                "active": active_count,
                "size": size_str,
                "size_bytes": self._parse_docker_size(size_str),
                "reclaimable": reclaimable_str,
                "reclaimable_bytes": self._parse_docker_size(reclaimable_str),
            }

    def _parse_volumes_line(self, line: str, summary: dict[str, Any]) -> None:
        """Parse Local Volumes line from docker system df output."""
        parts = line.split()
        if len(parts) >= 5:
            total_count = int(parts[2]) if parts[2].isdigit() else 0  # "Local" "Volumes" COUNT
            active_count = int(parts[3]) if parts[3].isdigit() else 0
            size_str = parts[4]
            reclaimable_str = parts[5] if len(parts) > 5 else "0B"

            summary["volumes"] = {
                "count": total_count,
                "active": active_count,
                "size": size_str,
                "size_bytes": self._parse_docker_size(size_str),
                "reclaimable": reclaimable_str,
                "reclaimable_bytes": self._parse_docker_size(reclaimable_str),
            }

    def _parse_build_cache_line(self, line: str, summary: dict[str, Any]) -> None:
        """Parse Build Cache line from docker system df output."""
        parts = line.split()
        if len(parts) >= 5:
            # Format: Build Cache TOTAL ACTIVE SIZE RECLAIMABLE
            size_str = parts[4]  # SIZE column
            reclaimable_str = parts[5] if len(parts) > 5 else parts[4]

            summary["build_cache"] = {
                "size": size_str,
                "size_bytes": self._parse_docker_size(size_str),
                "reclaimable": reclaimable_str,
                "reclaimable_bytes": self._parse_docker_size(reclaimable_str),
            }

    def _calculate_totals(self, summary: dict[str, Any]) -> None:
        """Calculate total size and reclaimable space across all resource types."""
        total_size_bytes = (
            summary["images"]["size_bytes"]
            + summary["containers"]["size_bytes"]
            + summary["volumes"]["size_bytes"]
            + summary["build_cache"]["size_bytes"]
        )

        total_reclaimable_bytes = (
            summary["images"]["reclaimable_bytes"]
            + summary["containers"]["reclaimable_bytes"]
            + summary["volumes"]["reclaimable_bytes"]
            + summary["build_cache"].get("reclaimable_bytes", summary["build_cache"]["size_bytes"])
        )

        reclaimable_percentage = (
            int((total_reclaimable_bytes / total_size_bytes) * 100) if total_size_bytes > 0 else 0
        )

        summary["totals"] = {
            "total_size": format_size(total_size_bytes),
            "total_size_bytes": total_size_bytes,
            "total_reclaimable": format_size(total_reclaimable_bytes),
            "total_reclaimable_bytes": total_reclaimable_bytes,
            "reclaimable_percentage": reclaimable_percentage,
        }

    def _parse_disk_usage_detailed(self, output: str) -> dict[str, Any]:
        """Parse docker system df -v output for TOP space consumers only."""
        result: dict[str, Any] = {
            "top_images": [],
            "top_volumes": [],
            "container_stats": {
                "running": 0,
                "stopped": 0,
                "total_size": "0B",
                "total_size_bytes": 0,
            },
            "cleanup_candidates": [],
        }

        if not output:
            return result

        lines = output.split("\n")
        images, volumes = self._parse_disk_usage_sections(lines, result)
        self._process_top_consumers(images, volumes, result)

        return result

    def _parse_disk_usage_sections(
        self, lines: list[str], result: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Parse different sections of docker system df output."""
        current_section = None
        images: list[dict[str, Any]] = []
        volumes: list[dict[str, Any]] = []

        for line in lines:
            stripped_line = line.strip()
            if not stripped_line:
                continue

            # Detect sections
            section_type = self._detect_section_type(stripped_line)
            if section_type:
                current_section = section_type
                continue

            # Skip header lines and parse data
            if current_section and not self._is_header_line(stripped_line):
                self._parse_section_line(stripped_line, current_section, images, volumes, result)

        return images, volumes

    def _detect_section_type(self, line: str) -> str | None:
        """Detect section type from line content."""
        if line.startswith("REPOSITORY"):
            return "images"
        elif line.startswith("CONTAINER ID"):
            return "containers"
        elif line.startswith("VOLUME NAME"):
            return "volumes"
        elif line.startswith("CACHE ID"):
            return "cache"
        return None

    def _is_header_line(self, line: str) -> bool:
        """Check if line is a header line."""
        return line.startswith(("REPOSITORY", "CONTAINER", "VOLUME", "CACHE"))

    def _parse_section_line(
        self,
        line: str,
        section: str,
        images: list[dict[str, Any]],
        volumes: list[dict[str, Any]],
        result: dict[str, Any],
    ) -> None:
        """Parse a data line based on section type."""
        # Use more robust parsing - split on whitespace but handle multiple spaces
        parts = [part for part in line.split() if part.strip()]
        if len(parts) < 3:
            return

        try:
            if section == "images":
                self._parse_images_list_line(parts, images)
            elif section == "volumes":
                self._parse_volumes_list_line(parts, volumes)
            elif section == "containers":
                self._parse_containers_list_line(parts, result)
        except (ValueError, IndexError) as e:
            self.logger.debug(
                "Skipping malformed docker df line", section=section, line=line, error=str(e)
            )
            pass

    def _parse_images_list_line(self, parts: list[str], images: list[dict[str, Any]]) -> None:
        """Parse images section line."""
        # Docker system df -v format: REPOSITORY TAG IMAGE_ID CREATED SIZE
        if len(parts) >= 5:
            repo = parts[0]
            tag = parts[1]
            # Size is typically the last column, but could be in position 4
            size_str = parts[-1] if len(parts) >= 5 else parts[4]

            # Ensure we have a valid size string
            if not size_str or size_str in ["<none>", "<missing>"]:
                size_str = "0B"

            size_bytes = self._parse_docker_size(size_str)

            # Handle repository name formatting
            name = f"{repo}:{tag}" if tag not in ["<none>", "<missing>"] else repo
            if name == "<none>:<none>":
                name = f"<none>@{parts[2][:12]}" if len(parts) > 2 else "<none>"

            images.append(
                {
                    "name": name,
                    "size": size_str,
                    "size_bytes": size_bytes,
                }
            )

    def _parse_volumes_list_line(self, parts: list[str], volumes: list[dict[str, Any]]) -> None:
        """Parse volumes section line."""
        if len(parts) >= 3:
            name = parts[0]
            size_str = parts[2]
            size_bytes = self._parse_docker_size(size_str)
            volumes.append({"name": name, "size": size_str, "size_bytes": size_bytes})

    def _parse_containers_list_line(self, parts: list[str], result: dict[str, Any]) -> None:
        """Parse containers section line."""
        if len(parts) >= 7:
            size_str = parts[4]
            size_bytes = self._parse_docker_size(size_str)
            container_name = parts[-1]

            # Parse container status
            is_running = self._parse_container_status(parts)

            if is_running:
                result["container_stats"]["running"] += 1
            else:
                result["container_stats"]["stopped"] += 1
                result["cleanup_candidates"].append(
                    {
                        "type": "container",
                        "name": container_name,
                        "size": size_str,
                        "size_bytes": size_bytes,
                    }
                )

            result["container_stats"]["total_size_bytes"] += size_bytes

    def _parse_container_status(self, parts: list[str]) -> bool:
        """Parse container status and return True if running."""
        # Look for status keywords starting from index after SIZE onward
        for i in range(CONTAINER_SIZE_COLUMN_INDEX, len(parts) - 1):  # -1 to exclude NAMES column
            word = parts[i].lower()
            if word in ["up", "exited", "restarting", "paused", "dead", "created"]:
                # Found status start, collect status text
                status_parts = parts[i:-1]  # From status start to before NAMES
                status_text = " ".join(status_parts).lower()
                return "up" in status_text

        # Fallback: assume everything after CREATED is STATUS
        if len(parts) > MIN_CONTAINER_COLUMNS:
            status_text = " ".join(parts[CONTAINER_CREATED_COLUMN_INDEX:-1]).lower()
            return "up" in status_text

        return False

    def _process_top_consumers(
        self, images: list[dict[str, Any]], volumes: list[dict[str, Any]], result: dict[str, Any]
    ) -> None:
        """Sort and select top consumers."""
        # Sort and get top consumers
        images.sort(key=lambda x: x["size_bytes"], reverse=True)
        volumes.sort(key=lambda x: x["size_bytes"], reverse=True)

        result["top_images"] = images[:TOP_CONSUMERS]  # Top largest images
        result["top_volumes"] = volumes[:TOP_CONSUMERS]  # Top largest volumes
        result["container_stats"]["total_size"] = format_size(
            result["container_stats"]["total_size_bytes"]
        )

        # Sort cleanup candidates by size and limit to top candidates
        result["cleanup_candidates"] = sorted(
            result["cleanup_candidates"], key=lambda x: x.get("size_bytes", 0), reverse=True
        )[:TOP_CANDIDATES]

    def _analyze_cleanup_potential(self, df_output: str) -> dict[str, str]:
        """Analyze potential cleanup from docker system df output."""
        # Parse the output to estimate what could be cleaned
        potential = {
            "stopped_containers": "Unknown",
            "unused_networks": "Unknown",
            "build_cache": "Unknown",
            "unused_images": "Unknown",
        }

        # Basic parsing - could be enhanced with more detailed analysis
        if "Containers" in df_output:
            potential["stopped_containers"] = "Available"
        if "Build Cache" in df_output and "0B" not in df_output:
            potential["build_cache"] = "Available"

        return potential

    def _parse_cleanup_output(self, output: str) -> str:
        """Parse cleanup command output to extract space reclaimed."""
        # Look for patterns like "Total reclaimed space: 1.2GB"
        patterns = [r"Total reclaimed space:\s+(\S+)", r"freed\s+(\S+)", r"reclaimed:\s+(\S+)"]

        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return match.group(1)

        return "Unknown"

    def _parse_docker_size(self, size_str: str) -> int:
        """Convert Docker size string (e.g., '1.2GB', '980.2MB (2%)') to bytes."""
        if not size_str or size_str == "0B":
            return 0

        # Handle Docker's size format: "1.2GB", "345MB", "2.1kB", "980.2MB (2%)"
        size_str = size_str.strip().upper()

        # Strip percentage information if present (e.g., "980.2MB (2%)" -> "980.2MB")
        size_str = re.sub(r"\s*\([^)]*\)\s*$", "", size_str)

        # Extract number and unit
        match = re.match(r"^(\d+(?:\.\d+)?)\s*([A-Z]*B?)$", size_str)
        if not match:
            return 0

        value = float(match.group(1))
        unit = match.group(2) or "B"

        # Convert to bytes - handle both SI (1000-based) and IEC (1024-based) units
        # IEC binary prefixes (KiB, MiB, GiB, TiB, PiB) use 1024 multipliers
        # SI decimal prefixes (KB, MB, GB, TB, PB) use 1000 multipliers
        unit_upper = unit.upper()

        if unit_upper.endswith("IB"):
            # Binary/IEC units (base-1024) - only explicit IEC suffixes
            multipliers = {
                "KIB": 1024,
                "MIB": 1024**2,
                "GIB": 1024**3,
                "TIB": 1024**4,
                "PIB": 1024**5,
                "B": 1,
            }
        else:
            # Decimal/SI units (base-1000) - KB/MB/GB/TB and variants use 1000-based
            multipliers = {
                "B": 1,
                "KB": 1000,
                "MB": 1000**2,
                "GB": 1000**3,
                "TB": 1000**4,
                "PB": 1000**5,
            }

        # Use case-insensitive lookup for IEC units, exact match for decimal variants
        if unit_upper.endswith("IB"):
            multiplier = multipliers.get(unit_upper, 1)
        else:
            multiplier = multipliers.get(unit, 1)

        return int(value * multiplier)

    def _generate_cleanup_recommendations(self, summary: dict, detailed: dict) -> list[str]:
        """Generate actionable cleanup recommendations based on disk usage."""
        recommendations = []

        # Check for reclaimable space in each category
        if summary.get("containers", {}).get("reclaimable_bytes", 0) > 100 * 1024 * 1024:  # >100MB
            reclaimable = summary["containers"]["reclaimable"]
            recommendations.append(f"Remove stopped containers to reclaim {reclaimable}")

        if summary.get("images", {}).get("reclaimable_bytes", 0) > 500 * 1024 * 1024:  # >500MB
            reclaimable = summary["images"]["reclaimable"]
            recommendations.append(f"Remove unused images to reclaim {reclaimable}")

        if summary.get("build_cache", {}).get("size_bytes", 0) > 1024 * 1024 * 1024:  # >1GB
            size = summary["build_cache"]["size"]
            recommendations.append(f"Clear build cache to reclaim {size}")

        if summary.get("volumes", {}).get("reclaimable_bytes", 0) > 1024 * 1024 * 1024:  # >1GB
            reclaimable = summary["volumes"]["reclaimable"]
            recommendations.append(
                f"⚠️  Remove unused volumes to reclaim {reclaimable} (CAUTION: May delete data!)"
            )

        # Check for excessive cleanup candidates
        cleanup_candidates = detailed.get("cleanup_candidates", [])
        if len(cleanup_candidates) > 5:
            recommendations.append(
                f"Found {len(cleanup_candidates)} stopped containers that can be removed"
            )

        # Add specific cleanup commands if there are recommendations
        if recommendations:
            recommendations.append("🔧 Recommended Actions:")
            recommendations.append(
                "• Run 'docker_hosts cleanup safe' to clean containers, networks, and build cache"
            )
            recommendations.append(
                "• Run 'docker_hosts cleanup moderate' to also remove unused images"
            )

            # Only suggest aggressive cleanup if there are volumes to clean
            if summary.get("volumes", {}).get("reclaimable_bytes", 0) > 0:
                recommendations.append(
                    "• Run 'docker_hosts cleanup aggressive' to also remove unused volumes (DANGEROUS!)"
                )
        else:
            # If no significant cleanup opportunities
            total_size = summary.get("totals", {}).get("total_size", "0B")
            recommendations.append(
                f"✅ System is relatively clean. Total Docker usage: {total_size}"
            )

        return recommendations

    def _calculate_cleanup_levels(self, summary: dict) -> dict[str, Any]:
        """Calculate cumulative space that each cleanup level would free up."""
        # Extract byte values from summary
        containers_bytes = summary.get("containers", {}).get("reclaimable_bytes", 0)
        build_cache_bytes = summary.get("build_cache", {}).get("reclaimable_bytes", 0)
        images_bytes = summary.get("images", {}).get("reclaimable_bytes", 0)
        volumes_bytes = summary.get("volumes", {}).get("reclaimable_bytes", 0)

        # Calculate total size for percentage calculations
        total_size_bytes = summary.get("totals", {}).get("total_size_bytes", 0)

        # Calculate cumulative space for each level
        safe_bytes = containers_bytes + build_cache_bytes
        moderate_bytes = safe_bytes + images_bytes
        aggressive_bytes = moderate_bytes + volumes_bytes

        # Calculate percentages
        safe_percentage = int((safe_bytes / total_size_bytes) * 100) if total_size_bytes > 0 else 0
        moderate_percentage = (
            int((moderate_bytes / total_size_bytes) * 100) if total_size_bytes > 0 else 0
        )
        aggressive_percentage = (
            int((aggressive_bytes / total_size_bytes) * 100) if total_size_bytes > 0 else 0
        )

        return {
            "safe": {
                "size": format_size(safe_bytes),
                "size_bytes": safe_bytes,
                "percentage": safe_percentage,
                "description": "Stopped containers, networks, build cache",
                "components": {
                    "containers": format_size(containers_bytes),
                    "build_cache": format_size(build_cache_bytes),
                    "networks": "No size data (count-based cleanup)",
                },
            },
            "moderate": {
                "size": format_size(moderate_bytes),
                "size_bytes": moderate_bytes,
                "percentage": moderate_percentage,
                "description": "Safe cleanup + unused images",
                "additional_space": format_size(images_bytes),
            },
            "aggressive": {
                "size": format_size(aggressive_bytes),
                "size_bytes": aggressive_bytes,
                "percentage": aggressive_percentage,
                "description": "Moderate cleanup + unused volumes ⚠️  DATA LOSS RISK",
                "additional_space": format_size(volumes_bytes),
                "warning": "⚠️  Volume cleanup may permanently delete application data!",
            },
        }

    async def _get_cleanup_details(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """Get specific cleanup details beyond disk usage."""
        details = {
            "stopped_containers": {"count": 0, "names": []},
            "unused_networks": {"count": 0, "names": []},
            "dangling_images": {"count": 0, "size": "0B"},
        }

        try:
            # Get stopped containers
            containers_cmd = build_ssh_command(host) + [
                "docker",
                "ps",
                "-a",
                "--filter",
                "status=exited",
                "--format",
                "{{.Names}}",
            ]
            containers_proc = await asyncio.create_subprocess_exec(
                *containers_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # nosec B603
            containers_stdout, containers_stderr = await containers_proc.communicate()

            if containers_proc.returncode == 0 and containers_stdout.strip():
                stopped_containers = containers_stdout.decode().strip().split("\n")
                details["stopped_containers"] = {
                    "count": len(stopped_containers),
                    "names": stopped_containers[:5],  # Show first 5
                }

            # Get unused networks (custom networks with no containers)
            networks_cmd = build_ssh_command(host) + [
                "docker",
                "network",
                "ls",
                "--filter",
                "dangling=true",
                "--format",
                "{{.Name}}",
            ]
            networks_proc = await asyncio.create_subprocess_exec(
                *networks_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # nosec B603
            networks_stdout, networks_stderr = await networks_proc.communicate()

            if networks_proc.returncode == 0 and networks_stdout.strip():
                unused_networks = networks_stdout.decode().strip().split("\n")
                details["unused_networks"] = {
                    "count": len(unused_networks),
                    "names": unused_networks[:5],  # Show first 5
                }

            # Get dangling images
            images_cmd = build_ssh_command(host) + [
                "docker",
                "images",
                "-f",
                "dangling=true",
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ]
            images_proc = await asyncio.create_subprocess_exec(
                *images_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # nosec B603
            images_stdout, images_stderr = await images_proc.communicate()

            if images_proc.returncode == 0 and images_stdout.strip():
                dangling_images = images_stdout.decode().strip().split("\n")
                details["dangling_images"]["count"] = len(dangling_images)

        except Exception as e:
            self.logger.warning("Failed to get some cleanup details", host_id=host_id, error=str(e))

        return details

    def _format_cleanup_summary(self, summary: dict, cleanup_details: dict) -> dict[str, Any]:
        """Format a concise cleanup summary."""
        formatted = {
            "containers": {
                "stopped": cleanup_details["stopped_containers"]["count"],
                "reclaimable_space": summary.get("containers", {}).get("reclaimable", "0B"),
                "example_names": cleanup_details["stopped_containers"]["names"],
            },
            "images": {
                "unused": summary.get("images", {}).get("count", 0)
                - summary.get("images", {}).get("active", 0),
                "dangling": cleanup_details["dangling_images"]["count"],
                "reclaimable_space": summary.get("images", {}).get("reclaimable", "0B"),
            },
            "networks": {
                "unused": cleanup_details["unused_networks"]["count"],
                "example_names": cleanup_details["unused_networks"]["names"],
            },
            "build_cache": {
                "size": summary.get("build_cache", {}).get("size", "0B"),
                "reclaimable_space": summary.get("build_cache", {}).get("reclaimable", "0B"),
                "fully_reclaimable": True,
            },
            "volumes": {
                "unused": summary.get("volumes", {}).get("count", 0)
                - summary.get("volumes", {}).get("active", 0),
                "reclaimable_space": summary.get("volumes", {}).get("reclaimable", "0B"),
                "warning": "⚠️  Volume cleanup may delete data!",
            },
        }

        return formatted

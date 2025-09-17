"""
Docker Cleanup Service

Business logic for Docker cleanup and disk usage operations.
"""

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import structlog

from ..core.config_loader import DockerHost, DockerMCPConfig
from ..utils import build_ssh_command, format_size, validate_host

# Constants
TOP_CANDIDATES = 10


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
            "⚠️  AGGRESSIVE cleanup completed - removed unused containers, networks, "
            "build cache, images, and volumes"
        )

        return moderate_result

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
        parts = line.split()
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
        if len(parts) >= 5:
            repo = parts[0]
            tag = parts[1]
            size_str = parts[4]
            size_bytes = self._parse_docker_size(size_str)

            images.append(
                {
                    "name": f"{repo}:{tag}" if tag != "<none>" else repo,
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
        # Look for status keywords starting from index 5 onward (after SIZE)
        for i in range(5, len(parts) - 1):  # -1 to exclude NAMES column
            word = parts[i].lower()
            if word in ["up", "exited", "restarting", "paused", "dead", "created"]:
                # Found status start, collect status text
                status_parts = parts[i:-1]  # From status start to before NAMES
                status_text = " ".join(status_parts).lower()
                return "up" in status_text

        # Fallback: assume everything after CREATED is STATUS
        if len(parts) > 9:
            status_text = " ".join(parts[8:-1]).lower()
            return "up" in status_text

        return False

    def _process_top_consumers(
        self, images: list[dict[str, Any]], volumes: list[dict[str, Any]], result: dict[str, Any]
    ) -> None:
        """Sort and select top consumers."""
        # Sort and get top consumers
        images.sort(key=lambda x: x["size_bytes"], reverse=True)
        volumes.sort(key=lambda x: x["size_bytes"], reverse=True)

        result["top_images"] = images[:5]  # Top 5 largest images
        result["top_volumes"] = volumes[:5]  # Top 5 largest volumes
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

    # Schedule Management Methods (consolidated from ScheduleService)

    async def handle_schedule_action(
        self,
        schedule_action: str,
        host_id: str | None = None,
        cleanup_type: str | None = None,
        schedule_frequency: str | None = None,
        schedule_time: str | None = None,
        schedule_id: str | None = None,
    ) -> dict[str, Any]:
        """Handle schedule-related actions.

        Args:
            schedule_action: Action to perform (add, remove, list, enable, disable)
            host_id: Target Docker host identifier
            cleanup_type: Type of cleanup (safe, moderate only)
            schedule_frequency: Cleanup frequency (daily, weekly, monthly, custom)
            schedule_time: Time to run cleanup (e.g., '02:00')
            schedule_id: Schedule identifier for management

        Returns:
            Action result
        """
        try:
            if schedule_action == "add":
                return await self._add_schedule(
                    host_id, cleanup_type, schedule_frequency, schedule_time
                )
            elif schedule_action == "remove":
                return await self._remove_schedule(schedule_id)
            elif schedule_action == "list":
                return await self._list_schedules()
            elif schedule_action == "enable":
                return await self._toggle_schedule(schedule_id, True)
            elif schedule_action == "disable":
                return await self._toggle_schedule(schedule_id, False)
            else:
                return {
                    "success": False,
                    "error": f"Unknown schedule action: {schedule_action}",
                    "valid_actions": ["add", "remove", "list", "enable", "disable"],
                }

        except Exception as e:
            self.logger.error("Schedule action failed", action=schedule_action, error=str(e))
            return {"success": False, "error": str(e)}

    async def _add_schedule(
        self,
        host_id: str | None,
        cleanup_type: str | None,
        schedule_frequency: str | None,
        schedule_time: str | None,
    ) -> dict[str, Any]:
        """Add a new cleanup schedule."""
        # Validation
        if not host_id:
            return {"success": False, "error": "host_id is required"}
        if not cleanup_type or cleanup_type not in ["safe", "moderate"]:
            return {"success": False, "error": "cleanup_type must be 'safe' or 'moderate'"}
        if not schedule_frequency or schedule_frequency not in [
            "daily",
            "weekly",
            "monthly",
            "custom",
        ]:
            return {
                "success": False,
                "error": "schedule_frequency must be daily, weekly, monthly, or custom",
            }
        if not schedule_time or not self._validate_time_format(schedule_time):
            return {"success": False, "error": "schedule_time must be in HH:MM format (24-hour)"}

        is_valid, error_msg = validate_host(self.config, host_id)
        if not is_valid:
            return {"success": False, "error": error_msg}

        # Generate schedule ID including time to avoid collisions
        schedule_id = f"{host_id}_{cleanup_type}_{schedule_frequency}_{schedule_time}"

        # Check if schedule already exists
        if schedule_id in self.config.cleanup_schedules:
            return {
                "success": False,
                "error": f"Schedule already exists: {schedule_id}",
                "existing_schedule": self._format_schedule_display(
                    self.config.cleanup_schedules[schedule_id].model_dump()
                ),
            }

        # Create schedule configuration
        from typing import Literal, cast

        from ..core.config_loader import CleanupSchedule

        schedule_config = CleanupSchedule(
            host_id=host_id,
            cleanup_type=cast(Literal["safe", "moderate"], cleanup_type),
            frequency=cast(Literal["daily", "weekly", "monthly", "custom"], schedule_frequency),
            time=schedule_time,
            enabled=True,
            created_at=datetime.now(UTC),
        )

        # Add to configuration
        self.config.cleanup_schedules[schedule_id] = schedule_config

        # Generate cron expression
        cron_expression = self._generate_cron_expression(schedule_frequency, schedule_time)
        cleanup_command = self._generate_cleanup_command(schedule_config.model_dump())

        # Update crontab
        cron_entry = f"{cron_expression} {cleanup_command} # docker-mcp-{schedule_id}"
        cron_result = await self._update_crontab(schedule_id, "add", cron_entry)

        if not cron_result["success"]:
            # Remove from config if cron update failed
            del self.config.cleanup_schedules[schedule_id]
            return cron_result

        self.logger.info("Cleanup schedule added", schedule_id=schedule_id, host_id=host_id)

        return {
            "success": True,
            "message": f"Cleanup schedule added: {schedule_id}",
            "schedule_id": schedule_id,
            "schedule": self._format_schedule_display(schedule_config.model_dump()),
            "cron_expression": cron_expression,
        }

    async def _remove_schedule(self, schedule_id: str | None) -> dict[str, Any]:
        """Remove a cleanup schedule."""
        if not schedule_id:
            return {"success": False, "error": "schedule_id is required"}

        if schedule_id not in self.config.cleanup_schedules:
            return {"success": False, "error": f"Schedule not found: {schedule_id}"}

        # Remove from crontab
        cron_result = await self._update_crontab(schedule_id, "remove")
        if not cron_result["success"]:
            return cron_result

        # Remove from configuration
        removed_schedule = self.config.cleanup_schedules.pop(schedule_id)

        self.logger.info("Cleanup schedule removed", schedule_id=schedule_id)

        return {
            "success": True,
            "message": f"Cleanup schedule removed: {schedule_id}",
            "schedule_id": schedule_id,
            "removed_schedule": self._format_schedule_display(removed_schedule.model_dump()),
        }

    async def _toggle_schedule(self, schedule_id: str | None, enabled: bool) -> dict[str, Any]:
        """Enable or disable a cleanup schedule."""
        if not schedule_id:
            return {"success": False, "error": "schedule_id is required"}

        if schedule_id not in self.config.cleanup_schedules:
            return {"success": False, "error": f"Schedule not found: {schedule_id}"}

        schedule = self.config.cleanup_schedules[schedule_id]
        action = "enable" if enabled else "disable"

        if schedule.enabled == enabled:
            return {
                "success": False,
                "error": f"Schedule is already {action}d",
                "schedule": self._format_schedule_display(schedule.model_dump()),
            }

        # Update configuration
        schedule.enabled = enabled

        # Update crontab (comment/uncomment the entry)
        cron_result = await self._update_crontab(schedule_id, action)
        if not cron_result["success"]:
            schedule.enabled = not enabled  # Revert on failure
            return cron_result

        self.logger.info(f"Cleanup schedule {action}d", schedule_id=schedule_id)

        return {
            "success": True,
            "message": f"Cleanup schedule {action}d: {schedule_id}",
            "schedule_id": schedule_id,
            "schedule": self._format_schedule_display(schedule.model_dump()),
        }

    async def _list_schedules(self) -> dict[str, Any]:
        """List all cleanup schedules."""
        schedules = []
        for schedule_id, schedule_config in self.config.cleanup_schedules.items():
            schedule_data = self._format_schedule_display(schedule_config.model_dump())
            schedule_data["schedule_id"] = schedule_id
            schedules.append(schedule_data)

        return {
            "success": True,
            "schedules": schedules,
            "total_schedules": len(schedules),
            "active_schedules": len([s for s in schedules if s["enabled"]]),
        }

    def _validate_time_format(self, time_str: str) -> bool:
        """Validate time format (HH:MM)."""
        try:
            parts = time_str.split(":")
            if len(parts) != 2:
                return False

            hour, minute = int(parts[0]), int(parts[1])
            return 0 <= hour <= 23 and 0 <= minute <= 59
        except (ValueError, AttributeError):
            return False

    def _generate_cron_expression(self, frequency: str, time: str) -> str:
        """Generate cron expression from frequency and time."""
        hour, minute = time.split(":")

        if frequency == "daily":
            return f"{minute} {hour} * * *"
        elif frequency == "weekly":
            return f"{minute} {hour} * * 0"  # Sunday
        elif frequency == "monthly":
            return f"{minute} {hour} 1 * *"  # First day of month
        elif frequency == "custom":
            # For custom schedules, default to daily
            return f"{minute} {hour} * * *"
        else:
            raise ValueError(f"Unsupported frequency: {frequency}")

    async def _update_crontab(
        self, schedule_id: str, action: str, cron_entry: str = ""
    ) -> dict[str, Any]:
        """Update system crontab for cleanup schedule without invoking a shell."""
        try:
            # Read current crontab (empty if none)
            read_proc = await asyncio.create_subprocess_exec(
                "crontab",
                "-l",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # nosec B603
            out, err = await read_proc.communicate()
            current = out.decode() if read_proc.returncode == 0 else ""

            marker = f"docker-mcp-{schedule_id}"
            lines = [ln for ln in current.splitlines()]

            if action == "add":
                if not any(marker in ln for ln in lines):
                    lines.append(f"{cron_entry}")
            elif action == "remove":
                lines = [ln for ln in lines if marker not in ln]
            elif action in ("enable", "disable"):
                enable = action == "enable"
                new_lines = []
                for ln in lines:
                    if marker in ln:
                        stripped = ln.lstrip("#").lstrip()
                        new_lines.append(stripped if enable else f"# {stripped}")
                    else:
                        new_lines.append(ln)
                lines = new_lines
            else:
                return {"success": False, "error": f"Unsupported crontab action: {action}"}

            new_content = ("\n".join(lines) + "\n") if lines else "\n"
            write_proc = await asyncio.create_subprocess_exec(
                "crontab",
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # nosec B603
            w_out, w_err = await write_proc.communicate(input=new_content.encode())
            if write_proc.returncode == 0:
                return {"success": True, "message": f"Crontab updated for {action} action"}
            return {"success": False, "error": (w_err.decode() or "Crontab update failed").strip()}
        except Exception as e:
            return {"success": False, "error": f"Crontab update failed: {e}"}

    def _generate_cleanup_command(self, schedule_config: dict) -> str:
        """Generate the command to run for scheduled cleanup."""
        host_id = schedule_config["host_id"]
        cleanup_type = schedule_config["cleanup_type"]

        # This would typically call the Docker MCP tool
        return f"docker-mcp cleanup --host {host_id} --type {cleanup_type}"

    def _format_schedule_display(self, schedule_config: dict) -> dict[str, Any]:
        """Format schedule configuration for display."""
        cron_expr = self._generate_cron_expression(
            schedule_config["frequency"], schedule_config["time"]
        )

        return {
            "host_id": schedule_config["host_id"],
            "cleanup_type": schedule_config["cleanup_type"],
            "frequency": schedule_config["frequency"],
            "time": schedule_config["time"],
            "enabled": schedule_config["enabled"],
            "created_at": schedule_config["created_at"],
            "cron_expression": cron_expr,
            "description": f"Run {schedule_config['cleanup_type']} cleanup {schedule_config['frequency']} at {schedule_config['time']}",
        }

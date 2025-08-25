"""
Docker Cleanup Service

Business logic for Docker cleanup and disk usage operations.
"""

import asyncio
import re
import subprocess
from typing import Any

import structlog

from ..core.config_loader import DockerHost, DockerMCPConfig


class CleanupService:
    """Service for Docker cleanup and disk usage operations."""

    def __init__(self, config: DockerMCPConfig):
        """
        Initialize the CleanupService.
        
        Stores the provided DockerMCPConfig and creates a structlog logger used for structured, structured logging within the service.
        """
        self.config = config
        self.logger = structlog.get_logger()

    def _build_ssh_cmd(self, host: DockerHost) -> list[str]:
        """
        Build the base SSH command used to run remote Docker commands on a host.
        
        Returns a list of command arguments for subprocess/async execution. The command
        always disables strict host key checking; if the host provides an identity file
        it is added with -i, and a nondefault port is added with -p. The final element
        is the target user@hostname.
        """
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        if host.identity_file:
            ssh_cmd.extend(["-i", host.identity_file])
        if host.port != 22:
            ssh_cmd.extend(["-p", str(host.port)])
        ssh_cmd.append(f"{host.user}@{host.hostname}")
        return ssh_cmd

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """
        Check whether a host identifier exists in the configured hosts.
        
        Returns a tuple (is_valid, error_message). is_valid is True when the host_id is present in self.config.hosts; error_message is an empty string on success or a short description when not found.
        """
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

    async def docker_cleanup(self, host_id: str, cleanup_type: str) -> dict[str, Any]:
        """
        Perform Docker cleanup operations on a configured remote host over SSH.
        
        This dispatching entry point validates the requested host and runs one of four
        cleanup strategies determined by `cleanup_type`:
        
        - "check": non-destructive analysis that reports potential reclaimable space
          and recommendations.
        - "safe": prunes stopped containers, unused networks, and the build cache.
        - "moderate": performs "safe" actions then prunes unused images.
        - "aggressive": performs "moderate" actions then prunes unused volumes
          (may remove persistent data).
        
        Parameters:
            host_id (str): Identifier of the target host as defined in the service config.
            cleanup_type (str): One of "check", "safe", "moderate", or "aggressive".
        
        Returns:
            dict[str, Any]: A result object containing at minimum a boolean `success`
            key. On success the payload includes cleanup-specific information (summaries,
            per-resource results, reclaimed space estimates, and recommendations).
            On failure (invalid host, invalid cleanup_type, or runtime error) returns
            {"success": False, "error": "<message>"}.
        """
        try:
            # Validate host
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return {"success": False, "error": error_msg}
                
            host = self.config.hosts[host_id]
            
            self.logger.info(
                "Starting Docker cleanup",
                host_id=host_id,
                cleanup_type=cleanup_type,
                hostname=host.hostname
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
                return {
                    "success": False,
                    "error": f"Invalid cleanup_type: {cleanup_type}"
                }
                
        except Exception as e:
            self.logger.error(
                "Docker cleanup failed",
                host_id=host_id,
                cleanup_type=cleanup_type,
                error=str(e)
            )
            return {"success": False, "error": str(e)}

    async def docker_disk_usage(self, host_id: str, include_details: bool = False) -> dict[str, Any]:
        """
        Check Docker disk usage on a remote host over SSH and return a structured report.
        
        Performs `docker system df` on the target host and, if available, `docker system df -v` to collect a summary and optional detailed top-consumer information. The function validates the host id, runs the remote commands via SSH, parses their outputs, analyzes potential reclaimable space, and returns recommendations.
        
        Parameters:
            host_id (str): Identifier of the configured Docker host to query.
            include_details (bool): If True, include detailed top-consumer data from `docker system df -v` in the response (default False).
        
        Returns:
            dict: A result object with at least the following keys:
              - success (bool): True on success, False on error.
              - host_id (str): Echoes the requested host_id on success.
              - summary (dict): Parsed summary of disk usage (images, containers, volumes, build cache and totals).
              - cleanup_potential (dict): High-level indicators of available cleanup opportunities.
              - recommendations (list[str]): Actionable cleanup recommendations.
              - top_consumers (dict, optional): Detailed top-consumer data when include_details=True.
              - error (str, optional): Present when success is False and contains an error message.
        
        Notes:
            - Executes remote commands over SSH; failures of the summary command return a structured error.
            - Exceptions are caught and returned as {"success": False, "error": ...}.
        """
        try:
            # Validate host
            is_valid, error_msg = self._validate_host(host_id)
            if not is_valid:
                return {"success": False, "error": error_msg}
                
            host = self.config.hosts[host_id]
            
            self.logger.info(
                "Checking Docker disk usage",
                host_id=host_id,
                hostname=host.hostname
            )
            
            # Get disk usage summary
            summary_cmd = self._build_ssh_cmd(host) + ["docker", "system", "df"]
            summary_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    summary_cmd, check=False, capture_output=True, text=True
                ),
            )
            
            if summary_result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to get disk usage: {summary_result.stderr}"
                }
            
            # Get detailed usage
            detailed_cmd = self._build_ssh_cmd(host) + ["docker", "system", "df", "-v"]
            detailed_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    detailed_cmd, check=False, capture_output=True, text=True
                ),
            )
            
            # Parse results
            summary = self._parse_disk_usage_summary(summary_result.stdout)
            detailed = self._parse_disk_usage_detailed(detailed_result.stdout) if detailed_result.returncode == 0 else {}
            
            # Generate cleanup recommendations
            cleanup_potential = self._analyze_cleanup_potential(summary_result.stdout)
            recommendations = self._generate_cleanup_recommendations(summary, detailed)
            
            # Base response with essential information
            response = {
                "success": True,
                "host_id": host_id,
                "summary": summary,
                "cleanup_potential": cleanup_potential,
                "recommendations": recommendations
            }
            
            # Only include detailed information if requested (reduces token count)
            if include_details:
                response["top_consumers"] = detailed
            
            return response
            
        except Exception as e:
            self.logger.error(
                "Docker disk usage check failed",
                host_id=host_id,
                error=str(e)
            )
            return {"success": False, "error": str(e)}

    async def _check_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """
        Run a non-destructive analysis that reports what would be cleaned on the target host without performing any cleanup.
        
        This coroutine collects comprehensive disk-usage data (including detailed top-consumer information), gathers additional housekeeping details (stopped containers, dangling images, unused networks), and computes estimated space recoverable for three cleanup levels (safe, moderate, aggressive). It returns a structured result summarizing findings, recommended actions, and estimated reclaimable space.
        
        Parameters:
            host: DockerHost
                Target host object (used to run auxiliary inspection commands).
            host_id: str
                Identifier of the host in the service configuration.
        
        Returns:
            dict[str, Any]: A result dictionary with at least the following keys:
                - success (bool): True on successful analysis.
                - host_id (str): Echoed host identifier.
                - cleanup_type (str): Always "check".
                - summary (dict): Formatted cleanup summary (containers, images, volumes, build cache, networks).
                - cleanup_levels (dict): Estimated bytes/percentages for safe/moderate/aggressive levels.
                - total_reclaimable (str): Human-readable total reclaimable space (e.g., "1.2GB").
                - reclaimable_percentage (float|int): Percentage of total usage that is reclaimable.
                - recommendations (list[str]): Cleanup recommendations derived from disk usage.
                - message (str): Human-readable status message.
                - On failure, returns {"success": False, "error": "<message>"}.
        """
        
        # Get comprehensive disk usage data
        disk_usage_data = await self.docker_disk_usage(host_id, include_details=True)
        
        if not disk_usage_data.get("success", False):
            return {
                "success": False,
                "error": f"Failed to analyze disk usage: {disk_usage_data.get('error', 'Unknown error')}"
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
            "summary": cleanup_summary,
            "cleanup_levels": cleanup_levels,
            "total_reclaimable": summary.get("totals", {}).get("total_reclaimable", "0B"),
            "reclaimable_percentage": summary.get("totals", {}).get("reclaimable_percentage", 0),
            "recommendations": disk_usage_data.get("recommendations", []),
            "message": "📊 Cleanup Analysis Complete - No actual cleanup was performed"
        }

    async def _safe_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """
        Perform a non-destructive "safe" cleanup on the target host.
        
        This runs remote Docker prune commands for:
        - stopped containers (docker container prune -f)
        - unused networks (docker network prune -f)
        - builder cache (docker builder prune -f)
        
        The function executes each command over SSH, collects each command's result (as returned by _run_cleanup_command), and returns a summary object.
        
        Returns:
            dict: Summary with keys:
                - success (bool): True when the function completed and results were gathered.
                - host_id (str): The id of the host the cleanup was run against.
                - cleanup_type (str): Always "safe".
                - results (list[dict]): Per-resource results returned from _run_cleanup_command (contains resource_type, success, space_reclaimed, output, etc.).
                - message (str): Human-readable result message.
        """
        results = []
        
        # Clean stopped containers
        container_cmd = self._build_ssh_cmd(host) + [
            "docker", "container", "prune", "-f"
        ]
        container_result = await self._run_cleanup_command(container_cmd, "containers")
        results.append(container_result)
        
        # Clean unused networks
        network_cmd = self._build_ssh_cmd(host) + [
            "docker", "network", "prune", "-f"
        ]
        network_result = await self._run_cleanup_command(network_cmd, "networks")
        results.append(network_result)
        
        # Clean build cache
        builder_cmd = self._build_ssh_cmd(host) + [
            "docker", "builder", "prune", "-f"
        ]
        builder_result = await self._run_cleanup_command(builder_cmd, "build cache")
        results.append(builder_result)
        
        return {
            "success": True,
            "host_id": host_id,
            "cleanup_type": "safe",
            "results": results,
            "message": "Safe cleanup completed (containers, networks, build cache)"
        }

    async def _moderate_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """
        Perform a moderate cleanup on the target host.
        
        This runs a safe cleanup sequence (stopped containers, unused networks, build cache) and then prunes unused Docker images
        (`docker image prune -a -f`) over SSH. The returned dictionary is the safe-cleanup result extended with the images prune
        result appended to `results`; `cleanup_type` is set to `"moderate"` and `message` updated to describe the completed actions.
        
        Returns:
            dict[str, Any]: Aggregated cleanup result including per-resource results, `cleanup_type`, and a human-readable `message`.
        """
        # First do safe cleanup
        safe_result = await self._safe_cleanup(host, host_id)
        
        # Then clean unused images
        images_cmd = self._build_ssh_cmd(host) + [
            "docker", "image", "prune", "-a", "-f"
        ]
        images_result = await self._run_cleanup_command(images_cmd, "unused images")
        
        safe_result["results"].append(images_result)
        safe_result["cleanup_type"] = "moderate"
        safe_result["message"] = "Moderate cleanup completed (containers, networks, build cache, unused images)"
        
        return safe_result

    async def _aggressive_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """
        Perform an aggressive cleanup on the target host.
        
        This runs the full `moderate` cleanup (stopped containers, unused networks, build cache, and unused images) and then prunes unused Docker volumes on the remote host. The operation is destructive for volume data and may permanently remove data stored in volumes.
        
        Returns:
            A dictionary containing the aggregated cleanup results from the moderate run plus the volumes prune result. The returned structure includes keys such as "host_id", "cleanup_type" (set to "aggressive"), "results" (list of per-resource result objects), and "message".
        """
        # First do moderate cleanup
        moderate_result = await self._moderate_cleanup(host, host_id)
        
        # Then clean unused volumes (DANGEROUS)
        volumes_cmd = self._build_ssh_cmd(host) + [
            "docker", "volume", "prune", "-f"
        ]
        volumes_result = await self._run_cleanup_command(volumes_cmd, "unused volumes")
        
        moderate_result["results"].append(volumes_result)
        moderate_result["cleanup_type"] = "aggressive"
        moderate_result["message"] = "⚠️  AGGRESSIVE cleanup completed (containers, networks, build cache, unused images, volumes)"
        
        return moderate_result

    async def _run_cleanup_command(self, cmd: list[str], resource_type: str) -> dict[str, Any]:
        """
        Execute a cleanup command asynchronously and extract reclaimed disk space.
        
        Runs the provided command in a threadpool executor (non-blocking) and parses the subprocess output to determine how much space was reclaimed.
        
        Parameters:
            cmd (list[str]): The command and arguments to execute (e.g., SSH + remote docker prune invocation).
            resource_type (str): Logical name of the resource being cleaned (e.g., "containers", "images", "volumes").
        
        Returns:
            dict[str, Any]: Result object with keys:
                - resource_type (str): Echoes the provided resource_type.
                - success (bool): True if the command exited with code 0, False otherwise.
                - space_reclaimed (str): Human-readable reclaimed size parsed from output, or "0B" on failure.
                - output (str): Stripped stdout when successful.
                - error (str): stderr when the command failed (present only when success is False).
        """
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            return {
                "resource_type": resource_type,
                "success": False,
                "error": result.stderr,
                "space_reclaimed": "0B"
            }
        
        # Parse space reclaimed from output
        space_reclaimed = self._parse_cleanup_output(result.stdout)
        
        return {
            "resource_type": resource_type,
            "success": True,
            "space_reclaimed": space_reclaimed,
            "output": result.stdout.strip()
        }

    def _parse_disk_usage_summary(self, output: str) -> dict[str, Any]:
        """
        Parse the output of `docker system df` and return a structured summary with sizes converted to bytes and aggregated totals.
        
        Parses the summary lines for Images, Containers, Local Volumes and Build Cache, extracting counts, reported sizes and reclaimable sizes, converts size strings to byte counts, and computes aggregated totals and a reclaimable-percentage.
        
        Parameters:
            output (str): Raw stdout from `docker system df`.
        
        Returns:
            dict[str, Any]: A dictionary with the following top-level keys:
                - images: {count, active, size, size_bytes, reclaimable, reclaimable_bytes}
                - containers: {count, active, size, size_bytes, reclaimable, reclaimable_bytes}
                - volumes: {count, active, size, size_bytes, reclaimable, reclaimable_bytes}
                - build_cache: {size, size_bytes, reclaimable, reclaimable_bytes}
                - totals: {
                    total_size (str),
                    total_size_bytes (int),
                    total_reclaimable (str),
                    total_reclaimable_bytes (int),
                    reclaimable_percentage (int)  # percent of total size that is reclaimable
                }
        
        The function returns zeroed/empty structures when the input is empty or unparseable.
        """
        lines = output.strip().split('\n')
        if len(lines) < 2:
            return {
                "images": {"count": 0, "size": "0B", "size_bytes": 0, "reclaimable": "0B", "reclaimable_bytes": 0},
                "containers": {"count": 0, "size": "0B", "size_bytes": 0, "reclaimable": "0B", "reclaimable_bytes": 0},
                "volumes": {"count": 0, "size": "0B", "size_bytes": 0, "reclaimable": "0B", "reclaimable_bytes": 0},
                "build_cache": {"size": "0B", "size_bytes": 0, "reclaimable": "0B", "reclaimable_bytes": 0},
                "totals": {
                    "total_size": "0B",
                    "total_size_bytes": 0,
                    "total_reclaimable": "0B", 
                    "total_reclaimable_bytes": 0,
                    "reclaimable_percentage": 0
                }
            }
            
        # Initialize summary with enhanced structure
        summary = {
            "images": {"count": 0, "size": "0B", "size_bytes": 0, "reclaimable": "0B", "reclaimable_bytes": 0},
            "containers": {"count": 0, "size": "0B", "size_bytes": 0, "reclaimable": "0B", "reclaimable_bytes": 0},
            "volumes": {"count": 0, "size": "0B", "size_bytes": 0, "reclaimable": "0B", "reclaimable_bytes": 0},
            "build_cache": {"size": "0B", "size_bytes": 0, "reclaimable": "0B", "reclaimable_bytes": 0},
            "totals": {
                "total_size": "0B",
                "total_size_bytes": 0,
                "total_reclaimable": "0B",
                "total_reclaimable_bytes": 0,
                "reclaimable_percentage": 0
            }
        }
        
        for line in lines[1:]:
            if "Images" in line:
                parts = line.split()
                # Format: TYPE TOTAL ACTIVE SIZE RECLAIMABLE
                if len(parts) >= 5:
                    total_count = int(parts[1]) if parts[1].isdigit() else 0
                    active_count = int(parts[2]) if parts[2].isdigit() else 0
                    size_str = parts[3]
                    reclaimable_str = parts[4] if len(parts) > 4 else "0B"
                    
                    size_bytes = self._parse_docker_size(size_str)
                    reclaimable_bytes = self._parse_docker_size(reclaimable_str)
                    
                    summary["images"] = {
                        "count": total_count,
                        "active": active_count,
                        "size": size_str,
                        "size_bytes": size_bytes,
                        "reclaimable": reclaimable_str,
                        "reclaimable_bytes": reclaimable_bytes
                    }
                    
            elif "Containers" in line:
                parts = line.split()
                if len(parts) >= 5:
                    total_count = int(parts[1]) if parts[1].isdigit() else 0
                    active_count = int(parts[2]) if parts[2].isdigit() else 0
                    size_str = parts[3]
                    reclaimable_str = parts[4] if len(parts) > 4 else "0B"
                    
                    size_bytes = self._parse_docker_size(size_str)
                    reclaimable_bytes = self._parse_docker_size(reclaimable_str)
                    
                    summary["containers"] = {
                        "count": total_count,
                        "active": active_count,
                        "size": size_str,
                        "size_bytes": size_bytes,
                        "reclaimable": reclaimable_str,
                        "reclaimable_bytes": reclaimable_bytes
                    }
                    
            elif "Local Volumes" in line:
                parts = line.split()
                if len(parts) >= 5:
                    total_count = int(parts[2]) if parts[2].isdigit() else 0  # "Local" "Volumes" COUNT
                    active_count = int(parts[3]) if parts[3].isdigit() else 0
                    size_str = parts[4]
                    reclaimable_str = parts[5] if len(parts) > 5 else "0B"
                    
                    size_bytes = self._parse_docker_size(size_str)
                    reclaimable_bytes = self._parse_docker_size(reclaimable_str)
                    
                    summary["volumes"] = {
                        "count": total_count,
                        "active": active_count,
                        "size": size_str,
                        "size_bytes": size_bytes,
                        "reclaimable": reclaimable_str,
                        "reclaimable_bytes": reclaimable_bytes
                    }
                    
            elif "Build Cache" in line:
                parts = line.split()
                if len(parts) >= 5:
                    # Format: Build Cache TOTAL ACTIVE SIZE RECLAIMABLE
                    # parts[0]="Build", parts[1]="Cache", parts[2]=TOTAL, parts[3]=ACTIVE, parts[4]=SIZE, parts[5]=RECLAIMABLE
                    size_str = parts[4]  # SIZE column (was incorrectly using parts[2])
                    reclaimable_str = parts[5] if len(parts) > 5 else parts[4]  # RECLAIMABLE or fallback to SIZE
                    
                    size_bytes = self._parse_docker_size(size_str)
                    reclaimable_bytes = self._parse_docker_size(reclaimable_str)
                    
                    summary["build_cache"] = {
                        "size": size_str,
                        "size_bytes": size_bytes,
                        "reclaimable": reclaimable_str,
                        "reclaimable_bytes": reclaimable_bytes
                    }
        
        # Calculate totals
        total_size_bytes = (
            summary["images"]["size_bytes"] +
            summary["containers"]["size_bytes"] +
            summary["volumes"]["size_bytes"] +
            summary["build_cache"]["size_bytes"]
        )
        
        total_reclaimable_bytes = (
            summary["images"]["reclaimable_bytes"] +
            summary["containers"]["reclaimable_bytes"] +
            summary["volumes"]["reclaimable_bytes"] +
            summary["build_cache"].get("reclaimable_bytes", summary["build_cache"]["size_bytes"])  # Use reclaimable if available
        )
        
        reclaimable_percentage = (
            int((total_reclaimable_bytes / total_size_bytes) * 100)
            if total_size_bytes > 0 else 0
        )
        
        summary["totals"] = {
            "total_size": self._format_size(total_size_bytes),
            "total_size_bytes": total_size_bytes,
            "total_reclaimable": self._format_size(total_reclaimable_bytes),
            "total_reclaimable_bytes": total_reclaimable_bytes,
            "reclaimable_percentage": reclaimable_percentage
        }
        
        return summary

    def _parse_disk_usage_detailed(self, output: str) -> dict[str, Any]:
        """
        Parse the output of `docker system df -v` and extract top space consumers and basic container stats.
        
        Accepts the raw multiline output from `docker system df -v` and returns a structured summary focused on the largest images and volumes, container counts/sizes, and simple cleanup candidates. The parser tolerates missing or malformed lines and returns zeroed defaults when input is empty or unparsable.
        
        Returns:
            dict: {
                "top_images": list[{"name": str, "size": str, "size_bytes": int}],   # up to 5 largest images
                "top_volumes": list[{"name": str, "size": str, "size_bytes": int}],  # up to 5 largest volumes
                "container_stats": {
                    "running": int,            # number of containers with status indicating running
                    "stopped": int,            # number of non-running containers
                    "total_size": str,         # human-readable total size of listed containers
                    "total_size_bytes": int    # total size in bytes
                },
                "cleanup_candidates": list[{"type": "container", "name": str, "size": str}]  # stopped containers considered for cleanup
            }
        """
        result = {
            "top_images": [],
            "top_volumes": [],
            "container_stats": {
                "running": 0,
                "stopped": 0,
                "total_size": "0B",
                "total_size_bytes": 0
            },
            "cleanup_candidates": []
        }
        
        if not output:
            return result
        
        lines = output.split('\n')
        current_section = None
        
        images = []
        volumes = []
        containers = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Detect sections
            if line.startswith("REPOSITORY"):
                current_section = "images"
                continue
            elif line.startswith("CONTAINER ID"):
                current_section = "containers"  
                continue
            elif line.startswith("VOLUME NAME"):
                current_section = "volumes"
                continue
            elif line.startswith("CACHE ID"):
                current_section = "cache"
                continue
                
            # Skip header lines
            if current_section is None or line.startswith(("REPOSITORY", "CONTAINER", "VOLUME", "CACHE")):
                continue
                
            # Parse data lines
            parts = line.split()
            if len(parts) < 3:
                continue
                
            try:
                if current_section == "images":
                    # Format: REPOSITORY TAG IMAGE_ID CREATED SIZE SHARED_SIZE UNIQUE_SIZE CONTAINERS
                    if len(parts) >= 5:
                        repo = parts[0]
                        tag = parts[1] 
                        size_str = parts[4]
                        size_bytes = self._parse_docker_size(size_str)
                        
                        images.append({
                            "name": f"{repo}:{tag}" if tag != "<none>" else repo,
                            "size": size_str,
                            "size_bytes": size_bytes
                        })
                        
                elif current_section == "volumes":
                    # Format: VOLUME_NAME LINKS SIZE  
                    if len(parts) >= 3:
                        name = parts[0]
                        size_str = parts[2]
                        size_bytes = self._parse_docker_size(size_str)
                        
                        volumes.append({
                            "name": name,
                            "size": size_str,
                            "size_bytes": size_bytes
                        })
                        
                elif current_section == "containers":
                    # Format: CONTAINER_ID IMAGE COMMAND LOCAL_VOLUMES SIZE CREATED STATUS NAMES
                    # Note: CREATED and STATUS can be multiple words, so we need to identify STATUS by keywords
                    if len(parts) >= 7:
                        container_id = parts[0]
                        size_str = parts[4]  # Fixed: SIZE is at index 4
                        size_bytes = self._parse_docker_size(size_str)
                        container_name = parts[-1]  # NAMES is always the last column
                        
                        # Find STATUS by looking for keywords like "Up", "Exited", "Restarting", etc.
                        # STATUS typically starts after the SIZE and CREATED columns
                        status_found = False
                        status_text = ""
                        
                        # Look for status keywords starting from index 5 onward (after SIZE)
                        for i in range(5, len(parts) - 1):  # -1 to exclude NAMES column
                            word = parts[i].lower()
                            if word in ["up", "exited", "restarting", "paused", "dead", "created"]:
                                # Found status start, collect status text
                                status_parts = parts[i:-1]  # From status start to before NAMES
                                status_text = " ".join(status_parts).lower()
                                status_found = True
                                break
                        
                        if not status_found:
                            # Fallback: assume everything after CREATED is STATUS
                            # This is less reliable but better than wrong parsing
                            status_text = " ".join(parts[8:-1]).lower() if len(parts) > 9 else ""
                        
                        if "up" in status_text:
                            result["container_stats"]["running"] += 1
                        else:
                            result["container_stats"]["stopped"] += 1
                            # Stopped containers are cleanup candidates
                            result["cleanup_candidates"].append({
                                "type": "container",
                                "name": container_name,
                                "size": size_str
                            })
                        
                        result["container_stats"]["total_size_bytes"] += size_bytes
                        
            except (ValueError, IndexError):
                # Skip malformed lines
                continue
        
        # Sort and get top consumers
        images.sort(key=lambda x: x["size_bytes"], reverse=True)
        volumes.sort(key=lambda x: x["size_bytes"], reverse=True)
        
        result["top_images"] = images[:5]  # Top 5 largest images
        result["top_volumes"] = volumes[:5]  # Top 5 largest volumes
        result["container_stats"]["total_size"] = self._format_size(result["container_stats"]["total_size_bytes"])
        
        return result

    def _analyze_cleanup_potential(self, df_output: str) -> dict[str, str]:
        """
        Estimate which Docker cleanup categories are likely available based on the text output of `docker system df`.
        
        This performs a lightweight text scan (not a full parse) of the `docker system df` output to flag categories that appear to contain reclaimable resources.
        
        Parameters:
            df_output (str): Raw stdout from `docker system df` (or similar) to inspect.
        
        Returns:
            dict[str, str]: A mapping of cleanup categories to status strings. Keys:
                - "stopped_containers": "Available" if container section is present, otherwise "Unknown".
                - "unused_networks": currently always "Unknown" (placeholder for future detection).
                - "build_cache": "Available" if a Build Cache section is present and the output does not contain "0B".
                - "unused_images": currently always "Unknown" (placeholder for future detection).
        """
        # Parse the output to estimate what could be cleaned
        potential = {
            "stopped_containers": "Unknown",
            "unused_networks": "Unknown", 
            "build_cache": "Unknown",
            "unused_images": "Unknown"
        }
        
        # Basic parsing - could be enhanced with more detailed analysis
        if "Containers" in df_output:
            potential["stopped_containers"] = "Available"
        if "Build Cache" in df_output and "0B" not in df_output:
            potential["build_cache"] = "Available"
            
        return potential

    def _parse_cleanup_output(self, output: str) -> str:
        """
        Extract the amount of space reclaimed from a Docker cleanup command's output.
        
        Checks common textual patterns (case-insensitive) such as
        "Total reclaimed space: <value>", "freed <value>", and "reclaimed: <value>"
        and returns the first matched size token (e.g., "1.2GB", "45MB").
        If no known pattern is found, returns "Unknown".
        
        Returns:
            str: The reclaimed space value parsed from output or "Unknown" if not found.
        """
        # Look for patterns like "Total reclaimed space: 1.2GB"
        patterns = [
            r"Total reclaimed space:\s+(\S+)",
            r"freed\s+(\S+)",
            r"reclaimed:\s+(\S+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return "Unknown"

    def _format_size(self, size_bytes: int) -> str:
        """
        Return a human-readable representation of a byte count.
        
        Converts `size_bytes` into units B, KB, MB, GB, or TB. Returns "0B" for 0. For values less than 1 KB the result is an integer number of bytes (e.g., "512B"); for KB and larger the value is formatted with one decimal place (e.g., "1.2MB").
        
        Parameters:
            size_bytes (int): Number of bytes (expected non-negative).
        
        Returns:
            str: Human-readable size string with unit suffix.
        """
        if size_bytes == 0:
            return "0B"
        
        units = ["B", "KB", "MB", "GB", "TB"]
        unit_index = 0
        size = float(size_bytes)
        
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1
        
        if unit_index == 0:
            return f"{int(size)}B"
        else:
            return f"{size:.1f}{units[unit_index]}"

    def _parse_docker_size(self, size_str: str) -> int:
        """
        Parse a Docker-formatted size string and return its value in bytes.
        
        Accepts values like "1.2GB", "345MB", "2.1kB", or "980.2MB (2%)". The function is case-insensitive,
        automatically strips trailing parenthetical percentages, and supports units B, KB, MB, GB, and TB.
        Empty strings or "0B" yield 0. If the input cannot be parsed, the function returns 0.
        
        Parameters:
            size_str (str): Docker size string to parse.
        
        Returns:
            int: Size in bytes (rounded down to the nearest whole byte) or 0 for unparseable/empty values.
        """
        if not size_str or size_str == "0B":
            return 0
        
        # Handle Docker's size format: "1.2GB", "345MB", "2.1kB", "980.2MB (2%)"
        size_str = size_str.strip().upper()
        
        # Strip percentage information if present (e.g., "980.2MB (2%)" -> "980.2MB")
        size_str = re.sub(r'\s*\([^)]*\)\s*$', '', size_str)
        
        # Extract number and unit
        match = re.match(r'^(\d+(?:\.\d+)?)\s*([A-Z]*B?)$', size_str)
        if not match:
            return 0
        
        value = float(match.group(1))
        unit = match.group(2) or "B"
        
        # Convert to bytes
        multipliers = {
            "B": 1,
            "KB": 1024,
            "MB": 1024 ** 2,
            "GB": 1024 ** 3,
            "TB": 1024 ** 4
        }
        
        return int(value * multipliers.get(unit, 1))

    def _generate_cleanup_recommendations(self, summary: dict, detailed: dict) -> list[str]:
        """
        Build a prioritized list of human-readable cleanup recommendations based on parsed Docker disk-usage data.
        
        Given a disk-usage summary and optional detailed breakdown, returns actionable suggestions (including CLI commands and warnings) when reclaimable space or cleanup candidates exceed internal thresholds. Recommendations may include safe, moderate, and — when volumes are present — aggressive actions; volume-related recommendations include a caution about possible data loss.
        
        Parameters:
            summary (dict): Structured summary from _parse_disk_usage_summary containing keys like
                "containers", "images", "volumes", "build_cache", and "totals". Numeric byte fields
                (e.g., "reclaimable_bytes", "size_bytes") and human-readable size strings (e.g., "reclaimable", "size")
                are used to determine thresholds.
            detailed (dict): Optional detailed output (from _parse_disk_usage_detailed) containing lists such as
                "cleanup_candidates" and top consumers to influence recommendations.
        
        Returns:
            list[str]: Ordered list of recommendation lines. If no actionable opportunities are found, returns
            a single message indicating the system is relatively clean with the total Docker usage.
        """
        recommendations = []
        
        # Check for reclaimable space in each category
        if summary.get("containers", {}).get("reclaimable_bytes", 0) > 100 * 1024 * 1024:  # >100MB
            reclaimable = summary["containers"]["reclaimable"]
            recommendations.append(
                f"Remove stopped containers to reclaim {reclaimable}"
            )
        
        if summary.get("images", {}).get("reclaimable_bytes", 0) > 500 * 1024 * 1024:  # >500MB
            reclaimable = summary["images"]["reclaimable"]
            recommendations.append(
                f"Remove unused images to reclaim {reclaimable}"
            )
        
        if summary.get("build_cache", {}).get("size_bytes", 0) > 1024 * 1024 * 1024:  # >1GB
            size = summary["build_cache"]["size"]
            recommendations.append(
                f"Clear build cache to reclaim {size}"
            )
        
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
            recommendations.extend([
                "",  # Separator line
                "🔧 Recommended Actions:",
                "• Run 'docker_hosts cleanup safe' to clean containers, networks, and build cache",
                "• Run 'docker_hosts cleanup moderate' to also remove unused images"
            ])
            
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
        """
        Compute estimated space reclaimed at safe, moderate, and aggressive cleanup levels.
        
        Detailed behavior:
        - Reads reclaimable byte counts from the provided `summary` for containers, build cache,
          images, and volumes and computes cumulative totals for three cleanup tiers:
          - safe: stopped containers + build cache (networks are count-based and not sized)
          - moderate: safe + unused images
          - aggressive: moderate + unused volumes
        - Also computes each tier's percentage of the reported total disk usage and formats
          human-readable size strings via self._format_size.
        
        Parameters:
            summary (dict): Docker disk-usage summary produced by _parse_disk_usage_summary.
                Expected keys used:
                - "containers": {"reclaimable_bytes": int}
                - "build_cache": {"reclaimable_bytes": int}
                - "images": {"reclaimable_bytes": int}
                - "volumes": {"reclaimable_bytes": int}
                - "totals": {"total_size_bytes": int}
        
        Returns:
            dict[str, Any]: A mapping with keys "safe", "moderate", and "aggressive". Each entry
            contains:
              - size (str): human-readable total reclaimable size for the tier
              - size_bytes (int): total reclaimable bytes for the tier
              - percentage (int): integer percent of the reported total size
              - description (str): short description of what the tier includes
            Additional keys:
              - components (for "safe"): per-component formatted sizes and a note for networks
              - additional_space (for "moderate" and "aggressive"): formatted size contributed by images/volumes
              - warning (for "aggressive"): explicit data-loss warning for volume removal
        """
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
        moderate_percentage = int((moderate_bytes / total_size_bytes) * 100) if total_size_bytes > 0 else 0
        aggressive_percentage = int((aggressive_bytes / total_size_bytes) * 100) if total_size_bytes > 0 else 0
        
        return {
            "safe": {
                "size": self._format_size(safe_bytes),
                "size_bytes": safe_bytes,
                "percentage": safe_percentage,
                "description": "Stopped containers, networks, build cache",
                "components": {
                    "containers": self._format_size(containers_bytes),
                    "build_cache": self._format_size(build_cache_bytes),
                    "networks": "No size data (count-based cleanup)"
                }
            },
            "moderate": {
                "size": self._format_size(moderate_bytes),
                "size_bytes": moderate_bytes,
                "percentage": moderate_percentage,
                "description": "Safe cleanup + unused images",
                "additional_space": self._format_size(images_bytes)
            },
            "aggressive": {
                "size": self._format_size(aggressive_bytes),
                "size_bytes": aggressive_bytes,
                "percentage": aggressive_percentage,
                "description": "Moderate cleanup + unused volumes ⚠️  DATA LOSS RISK",
                "additional_space": self._format_size(volumes_bytes),
                "warning": "⚠️  Volume cleanup may permanently delete application data!"
            }
        }

    async def _get_cleanup_details(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """
        Retrieve additional, non-destructive cleanup details from the target host.
        
        Performs remote Docker queries over SSH to collect:
        - stopped containers (names and count; names truncated to first 5),
        - unused networks (names and count; names truncated to first 5),
        - dangling images (count).
        
        Parameters:
            host_id (str): Identifier of the host in the service configuration — used for logging/context.
        
        Returns:
            dict[str, Any]: A details dictionary with keys:
                - "stopped_containers": {"count": int, "names": list[str]}
                - "unused_networks": {"count": int, "names": list[str]}
                - "dangling_images": {"count": int, "size": str}
            Fields default to zero/empty values when queries fail or return nothing. Exceptions are caught and logged; the function returns whatever partial data was collected.
        """
        details = {
            "stopped_containers": {"count": 0, "names": []},
            "unused_networks": {"count": 0, "names": []},
            "dangling_images": {"count": 0, "size": "0B"}
        }
        
        try:
            # Get stopped containers
            containers_cmd = self._build_ssh_cmd(host) + [
                "docker", "ps", "-a", "--filter", "status=exited", "--format", "{{.Names}}"
            ]
            containers_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(containers_cmd, capture_output=True, text=True)  # nosec B603
            )
            
            if containers_result.returncode == 0 and containers_result.stdout.strip():
                stopped_containers = containers_result.stdout.strip().split('\n')
                details["stopped_containers"] = {
                    "count": len(stopped_containers),
                    "names": stopped_containers[:5]  # Show first 5
                }
            
            # Get unused networks (custom networks with no containers)
            networks_cmd = self._build_ssh_cmd(host) + [
                "docker", "network", "ls", "--filter", "dangling=true", "--format", "{{.Name}}"
            ]
            networks_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(networks_cmd, capture_output=True, text=True)  # nosec B603
            )
            
            if networks_result.returncode == 0 and networks_result.stdout.strip():
                unused_networks = networks_result.stdout.strip().split('\n')
                details["unused_networks"] = {
                    "count": len(unused_networks),
                    "names": unused_networks[:5]  # Show first 5
                }
            
            # Get dangling images
            images_cmd = self._build_ssh_cmd(host) + [
                "docker", "images", "-f", "dangling=true", "--format", "{{.Repository}}:{{.Tag}}"
            ]
            images_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(images_cmd, capture_output=True, text=True)  # nosec B603
            )
            
            if images_result.returncode == 0 and images_result.stdout.strip():
                dangling_images = images_result.stdout.strip().split('\n')
                details["dangling_images"]["count"] = len(dangling_images)
        
        except Exception as e:
            self.logger.warning(
                "Failed to get some cleanup details",
                host_id=host_id,
                error=str(e)
            )
        
        return details

    def _format_cleanup_summary(self, summary: dict, cleanup_details: dict) -> dict[str, Any]:
        """
        Builds a concise, human-friendly cleanup summary from parsed disk-usage and collected cleanup details.
        
        Parameters:
            summary (dict): Parsed output from _parse_disk_usage_summary containing sections for images, containers, volumes, and build_cache (counts, sizes, reclaimable values).
            cleanup_details (dict): Additional details gathered from the host (e.g., stopped_containers{name list/count}, unused_networks{name list/count}, dangling_images{count}).
        
        Returns:
            dict: A summary dictionary with the following structure:
                - containers:
                    - stopped (int): count of stopped containers
                    - reclaimable_space (str): human-readable reclaimable size for containers
                    - example_names (list[str]): example stopped container names
                - images:
                    - unused (int): estimated count of unused images (count - active)
                    - dangling (int): count of dangling images
                    - reclaimable_space (str): human-readable reclaimable size for images
                - networks:
                    - unused (int): count of unused networks
                    - example_names (list[str]): example unused network names
                - build_cache:
                    - size (str): total build cache size
                    - reclaimable_space (str): reclaimable portion of build cache
                    - fully_reclaimable (bool): whether build cache is considered fully reclaimable (always true here)
                - volumes:
                    - unused (int): estimated count of unused volumes (count - active)
                    - reclaimable_space (str): human-readable reclaimable size for volumes
                    - warning (str): user-facing warning about volume data loss risk
        """
        formatted = {
            "containers": {
                "stopped": cleanup_details["stopped_containers"]["count"],
                "reclaimable_space": summary.get("containers", {}).get("reclaimable", "0B"),
                "example_names": cleanup_details["stopped_containers"]["names"]
            },
            "images": {
                "unused": summary.get("images", {}).get("count", 0) - summary.get("images", {}).get("active", 0),
                "dangling": cleanup_details["dangling_images"]["count"],
                "reclaimable_space": summary.get("images", {}).get("reclaimable", "0B")
            },
            "networks": {
                "unused": cleanup_details["unused_networks"]["count"],
                "example_names": cleanup_details["unused_networks"]["names"]
            },
            "build_cache": {
                "size": summary.get("build_cache", {}).get("size", "0B"),
                "reclaimable_space": summary.get("build_cache", {}).get("reclaimable", "0B"),
                "fully_reclaimable": True
            },
            "volumes": {
                "unused": summary.get("volumes", {}).get("count", 0) - summary.get("volumes", {}).get("active", 0),
                "reclaimable_space": summary.get("volumes", {}).get("reclaimable", "0B"),
                "warning": "⚠️  Volume cleanup may delete data!"
            }
        }
        
        return formatted
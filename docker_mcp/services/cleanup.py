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
        self.config = config
        self.logger = structlog.get_logger()

    def _build_ssh_cmd(self, host: DockerHost) -> list[str]:
        """Build SSH command for a host."""
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        if host.identity_file:
            ssh_cmd.extend(["-i", host.identity_file])
        if host.port != 22:
            ssh_cmd.extend(["-p", str(host.port)])
        ssh_cmd.append(f"{host.user}@{host.hostname}")
        return ssh_cmd

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """Validate that a host exists in configuration."""
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

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

    async def docker_disk_usage(self, host_id: str) -> dict[str, Any]:
        """Check Docker disk usage on a host.
        
        Args:
            host_id: Target Docker host identifier
            
        Returns:
            Disk usage information and statistics
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
            
            return {
                "success": True,
                "host_id": host_id,
                "summary": summary,
                "detailed": detailed,
                "raw_output": {
                    "summary": summary_result.stdout,
                    "detailed": detailed_result.stdout if detailed_result.returncode == 0 else None
                }
            }
            
        except Exception as e:
            self.logger.error(
                "Docker disk usage check failed",
                host_id=host_id,
                error=str(e)
            )
            return {"success": False, "error": str(e)}

    async def _check_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """Show what would be cleaned without actually cleaning."""
        cmd = self._build_ssh_cmd(host) + ["docker", "system", "df"]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            return {
                "success": False,
                "error": f"Failed to check cleanup: {result.stderr}"
            }
        
        # Parse what could be cleaned
        cleanup_potential = self._analyze_cleanup_potential(result.stdout)
        
        return {
            "success": True,
            "host_id": host_id,
            "cleanup_type": "check",
            "would_clean": cleanup_potential,
            "message": "This is a dry run - no actual cleanup was performed"
        }

    async def _safe_cleanup(self, host: DockerHost, host_id: str) -> dict[str, Any]:
        """Perform safe cleanup: containers, networks, build cache."""
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
        """Perform moderate cleanup: safe cleanup + unused images."""
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
        """Perform aggressive cleanup: moderate cleanup + volumes."""
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
        """Run a cleanup command and parse results."""
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
        """Parse docker system df output for summary."""
        lines = output.strip().split('\n')
        if len(lines) < 2:
            return {}
            
        # Skip header line, parse data lines
        summary = {
            "images": {"active": 0, "size": "0B"},
            "containers": {"active": 0, "size": "0B"},
            "volumes": {"active": 0, "size": "0B"},
            "build_cache": {"size": "0B"}
        }
        
        for line in lines[1:]:
            if "Images" in line:
                parts = line.split()
                if len(parts) >= 7:
                    summary["images"] = {"active": parts[2], "size": parts[6]}
            elif "Containers" in line:
                parts = line.split()
                if len(parts) >= 7:
                    summary["containers"] = {"active": parts[2], "size": parts[6]}
            elif "Local Volumes" in line:
                parts = line.split()
                if len(parts) >= 7:
                    summary["volumes"] = {"active": parts[2], "size": parts[6]}
            elif "Build Cache" in line:
                parts = line.split()
                if len(parts) >= 3:
                    summary["build_cache"] = {"size": parts[2]}
        
        return summary

    def _parse_disk_usage_detailed(self, output: str) -> dict[str, Any]:
        """Parse docker system df -v output for detailed information."""
        # This would parse detailed output - implementation would depend on specific needs
        return {"note": "Detailed parsing not yet implemented"}

    def _analyze_cleanup_potential(self, df_output: str) -> dict[str, str]:
        """Analyze potential cleanup from docker system df output."""
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
        """Parse cleanup command output to extract space reclaimed."""
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
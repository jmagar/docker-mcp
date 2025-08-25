"""Volume parsing utilities for Docker Compose files."""

import asyncio
import shlex
import subprocess
from typing import Any

import structlog
import yaml  # type: ignore[import-untyped]

from ..exceptions import DockerMCPError

logger = structlog.get_logger()


class VolumeParsingError(DockerMCPError):
    """Volume parsing operation failed."""
    pass


class VolumeParser:
    """Parser for Docker Compose volume configurations."""
    
    def __init__(self):
        self.logger = logger.bind(component="volume_parser")
    
    async def parse_compose_volumes(self, compose_content: str, source_appdata_path: str = None) -> dict[str, Any]:
        """Parse Docker Compose file to extract volume information.
        
        Args:
            compose_content: Docker Compose YAML content
            source_appdata_path: Source host's appdata path from hosts.yml for expanding ${APPDATA_PATH}
            
        Returns:
            Dictionary with volume information:
            - named_volumes: List of named volume names
            - bind_mounts: List of bind mount paths
            - volume_definitions: Volume configuration from compose
        """
        try:
            compose_data = yaml.safe_load(compose_content)
            
            volumes_info = {
                "named_volumes": [],
                "bind_mounts": [],
                "volume_definitions": {},
            }
            
            # Extract top-level volume definitions
            if "volumes" in compose_data:
                volumes_info["volume_definitions"] = compose_data["volumes"]
            
            # Parse service volumes
            services = compose_data.get("services", {})
            for service_name, service_config in services.items():
                if "volumes" not in service_config:
                    continue
                
                for volume in service_config["volumes"]:
                    if isinstance(volume, str):
                        # Parse volume string format
                        volume_parsed = self._parse_volume_string(volume, source_appdata_path)
                        if volume_parsed["type"] == "named":
                            volumes_info["named_volumes"].append(volume_parsed["name"])
                        elif volume_parsed["type"] == "bind":
                            volumes_info["bind_mounts"].append(volume_parsed["source"])
                    elif isinstance(volume, dict):
                        # Parse volume dictionary format
                        if volume.get("type") == "volume":
                            volumes_info["named_volumes"].append(volume.get("source", ""))
                        elif volume.get("type") == "bind":
                            volumes_info["bind_mounts"].append(volume.get("source", ""))
            
            # Remove duplicates
            volumes_info["named_volumes"] = list(set(volumes_info["named_volumes"]))
            volumes_info["bind_mounts"] = list(set(volumes_info["bind_mounts"]))
            
            self.logger.info(
                "Parsed compose volumes",
                named_volumes=len(volumes_info["named_volumes"]),
                bind_mounts=len(volumes_info["bind_mounts"]),
            )
            
            return volumes_info
            
        except yaml.YAMLError as e:
            raise VolumeParsingError(f"Failed to parse compose file: {e}")
        except Exception as e:
            raise VolumeParsingError(f"Error extracting volumes: {e}")
    
    def _parse_volume_string(self, volume_str: str, source_appdata_path: str = None) -> dict[str, str]:
        """Parse Docker volume string format with environment variable expansion.
        
        Args:
            volume_str: Volume string like "data:/app/data" or "/host/path:/container/path" or "${APPDATA_PATH}/service:/data"
            source_appdata_path: Source host's appdata path from hosts.yml for expanding ${APPDATA_PATH}
            
        Returns:
            Dictionary with volume type and details
        """
        # Expand environment variables using host configuration
        expanded_volume_str = volume_str
        if source_appdata_path and "${APPDATA_PATH}" in volume_str:
            expanded_volume_str = volume_str.replace("${APPDATA_PATH}", source_appdata_path)
        
        # Split into at most [source, dest, mode]; don't explode extra colons
        parts = expanded_volume_str.split(":", 2)
        
        if len(parts) < 2:
            # Simple volume without destination
            return {"type": "named", "name": parts[0], "destination": ""}
        
        # Check if first part is absolute path (bind mount)
        if parts[0].startswith(("/", "./", "~")):
            return {
                "type": "bind",
                "source": parts[0],
                "destination": parts[1] if len(parts) > 1 else "",
                "mode": parts[2] if len(parts) > 2 else "rw",
                "original": volume_str,  # Keep original for path mapping
            }
        else:
            # Named volume
            return {
                "type": "named",
                "name": parts[0],
                "destination": parts[1] if len(parts) > 1 else "",
                "mode": parts[2] if len(parts) > 2 else "rw",
                "original": volume_str,
            }
    
    async def get_volume_locations(
        self,
        ssh_cmd: list[str],
        named_volumes: list[str],
    ) -> dict[str, str]:
        """Get actual filesystem paths for named Docker volumes.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            named_volumes: List of named volume names
            
        Returns:
            Dictionary mapping volume names to filesystem paths
        """
        volume_paths = {}
        
        for volume_name in named_volumes:
            # Docker volume inspect to get mount point (with shell injection protection)
            inspect_cmd = f"docker volume inspect {shlex.quote(volume_name)} --format '{{{{.Mountpoint}}}}'"
            full_cmd = ssh_cmd + [inspect_cmd]
            
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda cmd=full_cmd: subprocess.run(  # nosec B603
                    cmd, check=False, capture_output=True, text=True
                ),
            )
            
            if result.returncode == 0:
                mount_point = result.stdout.strip()
                volume_paths[volume_name] = mount_point
                self.logger.debug(
                    "Found volume mount point",
                    volume=volume_name,
                    path=mount_point,
                )
            else:
                self.logger.warning(
                    "Could not find volume mount point",
                    volume=volume_name,
                    error=result.stderr,
                )
        
        return volume_paths
    
    def update_compose_for_migration(
        self,
        compose_content: str,
        old_paths: dict[str, str],
        new_base_path: str,
        target_appdata_path: str = None,
    ) -> str:
        """Update compose file paths for target host.
        
        Args:
            compose_content: Original compose file content
            old_paths: Mapping of old volume paths
            new_base_path: New base path for volumes on target
            target_appdata_path: Target host's appdata path for environment variable replacement
            
        Returns:
            Updated compose file content
        """
        updated_content = compose_content
        
        # First, replace environment variables with actual target paths
        if target_appdata_path:
            updated_content = updated_content.replace("${APPDATA_PATH}", target_appdata_path)
            self.logger.debug(
                "Replaced APPDATA_PATH environment variable",
                target_path=target_appdata_path,
            )
        
        # Replace bind mount paths (for any remaining literal paths)
        for old_path in old_paths.values():
            if old_path in updated_content:
                # Extract relative path component
                path_parts = old_path.split("/")
                relative_name = path_parts[-1] if path_parts else "data"
                new_path = f"{new_base_path}/{relative_name}"
                updated_content = updated_content.replace(old_path, new_path)
                
                self.logger.debug(
                    "Updated compose path",
                    old=old_path,
                    new=new_path,
                )
        
        return updated_content
    
    def update_compose_for_migration(
        self,
        compose_content: str,
        old_paths: dict[str, str],
        new_base_path: str,
        target_appdata_path: str = None,
    ) -> str:
        """Update compose file paths for target host.
        
        Args:
            compose_content: Original compose file content
            old_paths: Mapping of old volume paths
            new_base_path: New base path for volumes on target
            target_appdata_path: Target host's appdata path for environment variable replacement
            
        Returns:
            Updated compose file content
        """
        updated_content = compose_content
        
        # First, replace environment variables with actual target paths
        if target_appdata_path:
            updated_content = updated_content.replace("${APPDATA_PATH}", target_appdata_path)
            self.logger.debug(
                "Replaced APPDATA_PATH environment variable",
                target_path=target_appdata_path,
            )
        
        # Replace bind mount paths (for any remaining literal paths)
        for old_path in old_paths.values():
            if old_path in updated_content:
                # Extract relative path component
                path_parts = old_path.split("/")
                relative_name = path_parts[-1] if path_parts else "data"
                new_path = f"{new_base_path}/{relative_name}"
                updated_content = updated_content.replace(old_path, new_path)
                
                self.logger.debug(
                    "Updated compose path",
                    old=old_path,
                    new=new_path,
                )
        
        return updated_content
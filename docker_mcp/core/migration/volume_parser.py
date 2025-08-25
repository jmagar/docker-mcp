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
        """
        Initialize a VolumeParser instance and bind a logger scoped to the "volume_parser" component.
        
        Sets self.logger to a context-bound logger instance used for component-specific logging within the class.
        """
        self.logger = logger.bind(component="volume_parser")
    
    async def parse_compose_volumes(self, compose_content: str, source_appdata_path: str = None) -> dict[str, Any]:
        """
        Parse a Docker Compose YAML string and extract named volumes, bind mounts, and top-level volume definitions.
        
        If `source_appdata_path` is provided, occurrences of `${APPDATA_PATH}` in volume strings are expanded before parsing.
        
        Returns:
            A dict with keys:
              - "named_volumes" (list[str]): unique named volume names referenced by services.
              - "bind_mounts" (list[str]): unique host paths used as bind mounts.
              - "volume_definitions" (dict): the top-level `volumes:` mapping from the compose content.
        
        Raises:
            VolumeParsingError: If the YAML cannot be parsed or volume extraction fails.
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
        """
        Parse a Docker Compose volume string and classify it as a named volume or a bind mount.
        
        If `source_appdata_path` is provided, `${APPDATA_PATH}` occurrences in the input are expanded first.
        The volume string is split into up to three parts (source/name, destination, mode). Behavior:
        - If the string has no ":" it is treated as a named volume with an empty destination.
        - If the first part looks like a host path (starts with "/", "./", or "~") it is treated as a bind mount.
        - Otherwise it is treated as a named volume.
        
        Returns a dict describing the volume. Keys:
        - type: "named" or "bind"
        - For named volumes: name, destination (may be ""), mode (defaults to "rw"), original (original input string)
        - For bind mounts: source, destination (may be ""), mode (defaults to "rw"), original (original input string)
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
        """
        Update Docker Compose content to point volume paths at a new host/base location.
        
        Performs two text-based transformations:
        1. If target_appdata_path is provided, replaces all occurrences of "${APPDATA_PATH}" with that path.
        2. For each value in old_paths, replaces literal occurrences of that old path with a new path built by joining new_base_path and the old path's last path component (the segment after the final '/'). If the old path has no segments, "data" is used as the relative name.
        
        Parameters:
            compose_content (str): Original Compose YAML/text content.
            old_paths (dict[str, str]): Mapping of identifiers to old absolute bind paths to be replaced (only the values are used).
            new_base_path (str): Base directory on the target host to which volume data should be migrated.
            target_appdata_path (str, optional): If provided, replaces `${APPDATA_PATH}` occurrences with this value.
        
        Returns:
            str: The updated compose content with replacements applied.
        
        Notes:
            - Replacements are plain string substitutions; this function does not parse or validate YAML structure.
            - The function uses the last path component of each old path to construct the new path (e.g., "/var/lib/foo" -> "{new_base_path}/foo").
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
        """
        Update Docker Compose content to point volume paths at a new host/base location.
        
        Performs two text-based transformations:
        1. If target_appdata_path is provided, replaces all occurrences of "${APPDATA_PATH}" with that path.
        2. For each value in old_paths, replaces literal occurrences of that old path with a new path built by joining new_base_path and the old path's last path component (the segment after the final '/'). If the old path has no segments, "data" is used as the relative name.
        
        Parameters:
            compose_content (str): Original Compose YAML/text content.
            old_paths (dict[str, str]): Mapping of identifiers to old absolute bind paths to be replaced (only the values are used).
            new_base_path (str): Base directory on the target host to which volume data should be migrated.
            target_appdata_path (str, optional): If provided, replaces `${APPDATA_PATH}` occurrences with this value.
        
        Returns:
            str: The updated compose content with replacements applied.
        
        Notes:
            - Replacements are plain string substitutions; this function does not parse or validate YAML structure.
            - The function uses the last path component of each old path to construct the new path (e.g., "/var/lib/foo" -> "{new_base_path}/foo").
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
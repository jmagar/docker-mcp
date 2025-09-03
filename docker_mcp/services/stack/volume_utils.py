"""
Stack Volume Utils Module

Volume and mount handling utilities for Docker Compose stacks.
Handles volume path normalization, mount extraction, and volume configuration.
"""

from pathlib import Path
from typing import Any

import structlog


class StackVolumeUtils:
    """Volume and mount handling utilities for stack operations."""

    def __init__(self):
        self.logger = structlog.get_logger()

    def normalize_volume_entry(
        self, volume: Any, target_appdata: str, stack_name: str
    ) -> str | None:
        """Normalize a single volume entry to source:destination format.

        Args:
            volume: Volume definition (string or dict)
            target_appdata: Target appdata path
            stack_name: Stack name for path resolution

        Returns:
            Normalized volume string in "source:destination" format or None
        """
        if isinstance(volume, str) and ":" in volume:
            parts = volume.split(":", 2)
            if len(parts) >= 2:
                source_path = parts[0]
                container_path = parts[1]

                # Convert relative paths to absolute
                if source_path.startswith("."):
                    source_path = f"{target_appdata}/{stack_name}/{source_path[2:]}"
                elif not source_path.startswith("/"):
                    # Named volume - needs resolution
                    source_path = f"{target_appdata}/{stack_name}"

                return f"{source_path}:{container_path}"

        elif isinstance(volume, dict) and volume.get("type") == "bind":
            source = volume.get("source", "")
            target = volume.get("target", "")
            if source and target:
                if not source.startswith("/"):
                    source = f"{target_appdata}/{stack_name}/{source}"
                return f"{source}:{target}"

        return None

    def extract_expected_mounts(
        self, compose_content: str, target_appdata: str, stack_name: str
    ) -> list[str]:
        """Extract expected volume mounts from compose file content.

        Args:
            compose_content: Docker Compose YAML content
            target_appdata: Target appdata path
            stack_name: Stack name

        Returns:
            List of expected mount strings in format "source:destination"
        """
        try:
            import yaml

            compose_data = yaml.safe_load(compose_content)
            expected_mounts = []

            # Parse services for volume mounts
            services = compose_data.get("services", {})
            for _service_name, service_config in services.items():
                volumes = service_config.get("volumes", [])
                for volume in volumes:
                    mount = self.normalize_volume_entry(volume, target_appdata, stack_name)
                    if mount and mount not in expected_mounts:
                        expected_mounts.append(mount)

            if expected_mounts:
                self.logger.info(
                    "Extracted expected mounts from compose file",
                    stack=stack_name,
                    mounts=expected_mounts,
                )
                return expected_mounts
            else:
                # No volumes found - return empty list (don't create fake mounts)
                self.logger.info(
                    "No volume mounts found in compose file - stack has no persistent data",
                    stack=stack_name,
                )
                return []

        except Exception as e:
            # Fallback on any parsing error - return empty list for safety
            self.logger.error(
                "Failed to parse compose file for mounts - assuming no volumes",
                stack=stack_name,
                error=str(e),
            )
            return []

    def resolve_volume_paths(self, volumes: list[str], base_path: str) -> list[str]:
        """Resolve volume paths to absolute paths.

        Args:
            volumes: List of volume definitions
            base_path: Base path for resolution

        Returns:
            List of resolved absolute paths
        """
        resolved_paths = []

        for volume in volumes:
            if ":" in volume:
                source_path = volume.split(":")[0]
                if not source_path.startswith("/"):
                    # Relative path - resolve against base_path
                    source_path = str(Path(base_path) / source_path)
                resolved_paths.append(source_path)
            else:
                # Named volume or single path
                if volume.startswith("/"):
                    resolved_paths.append(volume)
                else:
                    resolved_paths.append(str(Path(base_path) / volume))

        return resolved_paths

    def extract_named_volumes(self, compose_content: str) -> list[str]:
        """Extract named volumes from compose file.

        Args:
            compose_content: Docker Compose YAML content

        Returns:
            List of named volume names
        """
        try:
            import yaml

            compose_data = yaml.safe_load(compose_content)
            named_volumes = []

            # Extract from top-level volumes section
            if "volumes" in compose_data:
                named_volumes.extend(list(compose_data["volumes"].keys()))

            # Extract named volumes used in services
            services = compose_data.get("services", {})
            for _service_name, service_config in services.items():
                volumes = service_config.get("volumes", [])
                for volume in volumes:
                    if isinstance(volume, str) and ":" in volume:
                        source = volume.split(":")[0]
                        if not source.startswith("/") and not source.startswith("."):
                            # This is likely a named volume
                            if source not in named_volumes:
                                named_volumes.append(source)
                    elif isinstance(volume, dict) and volume.get("type") == "volume":
                        vol_name = volume.get("source")
                        if vol_name and vol_name not in named_volumes:
                            named_volumes.append(vol_name)

            return named_volumes

        except Exception as e:
            self.logger.warning("Failed to extract named volumes", error=str(e))
            return []

    def extract_bind_mounts(self, compose_content: str) -> list[dict]:
        """Extract bind mount configurations from compose file.

        Args:
            compose_content: Docker Compose YAML content

        Returns:
            List of bind mount dictionaries with source, target, and options
        """
        try:
            import yaml

            compose_data = yaml.safe_load(compose_content)
            bind_mounts = []

            services = compose_data.get("services", {})
            for service_name, service_config in services.items():
                volumes = service_config.get("volumes", [])
                for volume in volumes:
                    bind_mount = None

                    if isinstance(volume, str) and ":" in volume:
                        parts = volume.split(":", 2)
                        if len(parts) >= 2 and parts[0].startswith("/"):
                            # This is a bind mount (absolute path)
                            bind_mount = {
                                "service": service_name,
                                "source": parts[0],
                                "target": parts[1],
                                "options": parts[2] if len(parts) > 2 else None,
                                "type": "bind",
                            }
                    elif isinstance(volume, dict) and volume.get("type") == "bind":
                        bind_mount = {
                            "service": service_name,
                            "source": volume.get("source", ""),
                            "target": volume.get("target", ""),
                            "options": volume.get("bind", {}).get("propagation"),
                            "type": "bind",
                        }

                    if bind_mount:
                        bind_mounts.append(bind_mount)

            return bind_mounts

        except Exception as e:
            self.logger.warning("Failed to extract bind mounts", error=str(e))
            return []

    def get_volume_size_estimate(self, volumes: list[str]) -> int:
        """Estimate total size of volumes (placeholder for actual implementation).

        Args:
            volumes: List of volume paths

        Returns:
            Estimated size in bytes (placeholder value)
        """
        # In a real implementation, this would:
        # 1. Connect to the source host
        # 2. Use 'du' command to get actual sizes
        # 3. Sum up all volume sizes

        # For now, return a reasonable placeholder
        return len(volumes) * 1024 * 1024 * 100  # 100MB per volume

    def validate_volume_permissions(self, volume_paths: list[str], user: str = "docker") -> dict:
        """Validate volume path permissions (placeholder for actual implementation).

        Args:
            volume_paths: List of volume paths to validate
            user: User that needs access (default: docker)

        Returns:
            Dict with validation results per path
        """
        results = {}

        for path in volume_paths:
            # In a real implementation, this would:
            # 1. Check if path exists
            # 2. Check read/write permissions for the user
            # 3. Check if parent directory is writable

            results[path] = {
                "exists": True,  # Placeholder
                "readable": True,  # Placeholder
                "writable": True,  # Placeholder
                "owner": "docker",  # Placeholder
                "permissions": "755",  # Placeholder
            }

        return results

    def suggest_volume_optimizations(self, compose_content: str) -> list[str]:
        """Suggest optimizations for volume configuration.

        Args:
            compose_content: Docker Compose YAML content

        Returns:
            List of optimization suggestions
        """
        suggestions = []

        try:
            import yaml

            compose_data = yaml.safe_load(compose_content)
            bind_mounts = self.extract_bind_mounts(compose_content)
            named_volumes = self.extract_named_volumes(compose_content)

            # Check for excessive bind mounts
            if len(bind_mounts) > 5:
                suggestions.append(
                    f"Consider consolidating {len(bind_mounts)} bind mounts - "
                    "too many mounts can complicate deployment"
                )

            # Check for missing named volume definitions
            volume_section = compose_data.get("volumes", {})
            for vol_name in named_volumes:
                if vol_name not in volume_section:
                    suggestions.append(
                        f"Named volume '{vol_name}' is used but not defined in volumes section"
                    )

            # Check for potentially problematic paths
            for mount in bind_mounts:
                source = mount.get("source", "")
                if source.startswith("/home/"):
                    suggestions.append(
                        f"Bind mount from home directory ({source}) may have permission issues"
                    )
                elif source.startswith("/tmp/"):  # noqa: S108
                    suggestions.append(
                        f"Bind mount from temp directory ({source}) - data may not persist across reboots"
                    )

        except Exception as e:
            self.logger.warning("Failed to analyze volume configuration", error=str(e))

        return suggestions

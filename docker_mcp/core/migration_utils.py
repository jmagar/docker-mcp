"""Migration utilities for Docker stack and volume transfer."""

import asyncio
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml  # type: ignore[import-untyped]

from .config_loader import DockerHost
from .exceptions import DockerMCPError

logger = structlog.get_logger()


class MigrationError(DockerMCPError):
    """Migration operation failed."""
    pass


class MigrationUtils:
    """Utilities for migrating Docker stacks between hosts."""
    
    # Default exclusion patterns for archiving
    DEFAULT_EXCLUSIONS = [
        "node_modules/",
        ".git/",
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
        "*.log",
        "*.tmp",
        "*.temp",
        "cache/",
        "temp/",
        "tmp/",
        ".cache/",
        "*.swp",
        "*.swo",
        ".DS_Store",
        "Thumbs.db",
        "*.pid",
        "*.lock",
        ".venv/",
        "venv/",
        "env/",
        "dist/",
        "build/",
        ".next/",
        ".nuxt/",
        "coverage/",
        ".coverage",
        "*.bak",
        "*.backup",
        "*.old",
    ]
    
    def __init__(self):
        self.logger = logger.bind(component="migration_utils")
    
    async def parse_compose_volumes(self, compose_content: str) -> dict[str, Any]:
        """Parse Docker Compose file to extract volume information.
        
        Args:
            compose_content: Docker Compose YAML content
            
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
                        volume_parsed = self._parse_volume_string(volume)
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
            raise MigrationError(f"Failed to parse compose file: {e}")
        except Exception as e:
            raise MigrationError(f"Error extracting volumes: {e}")
    
    def _parse_volume_string(self, volume_str: str) -> dict[str, str]:
        """Parse Docker volume string format.
        
        Args:
            volume_str: Volume string like "data:/app/data" or "/host/path:/container/path"
            
        Returns:
            Dictionary with volume type and details
        """
        parts = volume_str.split(":")
        
        if len(parts) < 2:
            # Simple volume without destination
            return {"type": "named", "name": parts[0], "destination": ""}
        
        # Check if first part is absolute path (bind mount)
        if parts[0].startswith("/") or parts[0].startswith("./") or parts[0].startswith("~"):
            return {
                "type": "bind",
                "source": parts[0],
                "destination": parts[1] if len(parts) > 1 else "",
                "mode": parts[2] if len(parts) > 2 else "rw",
            }
        else:
            # Named volume
            return {
                "type": "named",
                "name": parts[0],
                "destination": parts[1] if len(parts) > 1 else "",
                "mode": parts[2] if len(parts) > 2 else "rw",
            }
    
    async def create_volume_archive(
        self,
        ssh_cmd: list[str],
        volume_paths: list[str],
        archive_name: str,
        temp_dir: str = "/tmp",
        exclusions: list[str] | None = None,
    ) -> str:
        """Create tar.gz archive of volume data on remote host.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            volume_paths: List of paths to archive
            archive_name: Name for the archive file
            temp_dir: Temporary directory for archive creation
            exclusions: Additional exclusion patterns
            
        Returns:
            Path to created archive on remote host
        """
        if not volume_paths:
            raise MigrationError("No volumes to archive")
        
        # Combine default and custom exclusions
        all_exclusions = self.DEFAULT_EXCLUSIONS.copy()
        if exclusions:
            all_exclusions.extend(exclusions)
        
        # Build exclusion flags for tar
        exclude_flags = []
        for pattern in all_exclusions:
            exclude_flags.extend(["--exclude", pattern])
        
        # Create timestamped archive name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_file = f"{temp_dir}/{archive_name}_{timestamp}.tar.gz"
        
        # Build tar command
        tar_cmd = ["tar", "czf", archive_file] + exclude_flags + volume_paths
        
        # Execute tar command on remote host
        remote_cmd = " ".join(tar_cmd)
        full_cmd = ssh_cmd + [remote_cmd]
        
        self.logger.info(
            "Creating volume archive",
            archive_file=archive_file,
            paths=volume_paths,
            exclusions=len(all_exclusions),
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                full_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            raise MigrationError(f"Failed to create archive: {result.stderr}")
        
        return archive_file
    
    async def transfer_with_rsync(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_path: str,
        target_path: str,
        compress: bool = True,
        delete: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Transfer files between hosts using rsync.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_path: Path on source host
            target_path: Path on target host
            compress: Use compression during transfer
            delete: Delete files on target not in source
            dry_run: Perform dry run only
            
        Returns:
            Transfer result with statistics
        """
        # Build rsync command
        rsync_cmd = ["rsync", "-avP"]
        
        if compress:
            rsync_cmd.append("-z")
        if delete:
            rsync_cmd.append("--delete")
        if dry_run:
            rsync_cmd.append("--dry-run")
        
        # Add SSH options for source
        if source_host.identity_file:
            ssh_opts = f"-e 'ssh -i {source_host.identity_file}'"
            rsync_cmd.append(ssh_opts)
        
        # Build source and target URLs
        source_url = f"{source_host.user}@{source_host.hostname}:{source_path}"
        target_url = f"{target_host.user}@{target_host.hostname}:{target_path}"
        
        rsync_cmd.extend([source_url, target_url])
        
        self.logger.info(
            "Starting rsync transfer",
            source=source_url,
            target=target_url,
            compress=compress,
            delete=delete,
            dry_run=dry_run,
        )
        
        # Execute rsync
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                rsync_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            raise MigrationError(f"Rsync failed: {result.stderr}")
        
        # Parse rsync output for statistics
        stats = self._parse_rsync_stats(result.stdout)
        
        return {
            "success": True,
            "source": source_url,
            "target": target_url,
            "stats": stats,
            "dry_run": dry_run,
            "output": result.stdout,
        }
    
    def _parse_rsync_stats(self, output: str) -> dict[str, Any]:
        """Parse rsync output for transfer statistics.
        
        Args:
            output: Rsync command output
            
        Returns:
            Dictionary with transfer statistics
        """
        stats = {
            "files_transferred": 0,
            "total_size": 0,
            "transfer_rate": "",
            "speedup": 1.0,
        }
        
        # Parse rsync summary statistics
        for line in output.split("\n"):
            if "Number of files transferred:" in line:
                match = re.search(r"(\d+)", line)
                if match:
                    stats["files_transferred"] = int(match.group(1))
            elif "Total transferred file size:" in line:
                match = re.search(r"([\d,]+) bytes", line)
                if match:
                    stats["total_size"] = int(match.group(1).replace(",", ""))
            elif "sent" in line and "received" in line:
                # Parse transfer rate from summary line
                match = re.search(r"(\d+\.?\d*) (\w+/sec)", line)
                if match:
                    stats["transfer_rate"] = f"{match.group(1)} {match.group(2)}"
            elif "speedup is" in line:
                match = re.search(r"speedup is (\d+\.?\d*)", line)
                if match:
                    stats["speedup"] = float(match.group(1))
        
        return stats
    
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
            # Docker volume inspect to get mount point
            inspect_cmd = f"docker volume inspect {volume_name} --format '{{{{.Mountpoint}}}}'"
            full_cmd = ssh_cmd + [inspect_cmd]
            
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    full_cmd, check=False, capture_output=True, text=True
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
    
    async def verify_containers_stopped(
        self,
        ssh_cmd: list[str],
        stack_name: str,
        force_stop: bool = False,
    ) -> tuple[bool, list[str]]:
        """Verify all containers in a stack are stopped.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            stack_name: Stack name to check
            force_stop: Force stop running containers
            
        Returns:
            Tuple of (all_stopped, list_of_running_containers)
        """
        # Check for running containers
        check_cmd = ssh_cmd + [
            f"docker ps --filter 'label=com.docker.compose.project={stack_name}' --format '{{{{.Names}}}}'"
        ]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                check_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            self.logger.warning(
                "Failed to check container status",
                stack=stack_name,
                error=result.stderr,
            )
            return False, []
        
        running_containers = [
            name.strip() for name in result.stdout.strip().split("\n") if name.strip()
        ]
        
        if not running_containers:
            return True, []
        
        if force_stop:
            self.logger.info(
                "Force stopping containers",
                stack=stack_name,
                containers=running_containers,
            )
            
            # Force stop each container
            for container in running_containers:
                stop_cmd = ssh_cmd + [f"docker kill {container}"]
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(  # nosec B603
                        stop_cmd, check=False, capture_output=True, text=True
                    ),
                )
            
            # Wait for containers to stop
            await asyncio.sleep(3)
            
            # Re-check
            return await self.verify_containers_stopped(ssh_cmd, stack_name, force_stop=False)
        
        return False, running_containers
    
    async def prepare_target_directories(
        self,
        ssh_cmd: list[str],
        appdata_path: str,
        stack_name: str,
    ) -> str:
        """Prepare target directories for migration.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            appdata_path: Base appdata path on target host
            stack_name: Stack name for directory organization
            
        Returns:
            Path to stack-specific appdata directory
        """
        # Create stack-specific directory
        stack_dir = f"{appdata_path}/{stack_name}"
        mkdir_cmd = f"mkdir -p {stack_dir}"
        full_cmd = ssh_cmd + [mkdir_cmd]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                full_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            raise MigrationError(f"Failed to create target directory: {result.stderr}")
        
        self.logger.info(
            "Prepared target directory",
            path=stack_dir,
        )
        
        return stack_dir
    
    def update_compose_for_migration(
        self,
        compose_content: str,
        old_paths: dict[str, str],
        new_base_path: str,
    ) -> str:
        """Update compose file paths for target host.
        
        Args:
            compose_content: Original compose file content
            old_paths: Mapping of old volume paths
            new_base_path: New base path for volumes on target
            
        Returns:
            Updated compose file content
        """
        updated_content = compose_content
        
        # Replace bind mount paths
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
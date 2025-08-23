"""Migration orchestration and Docker stack transfer coordination."""

import asyncio
import subprocess
from typing import Any

import structlog
import yaml  # type: ignore[import-untyped]

from .config_loader import DockerHost
from .exceptions import DockerMCPError
from .transfer import ArchiveUtils, RsyncTransfer, ZFSTransfer

logger = structlog.get_logger()


class MigrationError(DockerMCPError):
    """Migration operation failed."""
    pass


class MigrationManager:
    """Orchestrates Docker stack migrations between hosts."""
    
    def __init__(self):
        self.logger = logger.bind(component="migration_manager")
        
        # Initialize transfer methods
        self.archive_utils = ArchiveUtils()
        self.rsync_transfer = RsyncTransfer()
        self.zfs_transfer = ZFSTransfer()
    
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
    
    async def choose_transfer_method(
        self,
        source_host: DockerHost,
        target_host: DockerHost
    ) -> tuple[str, Any]:
        """Choose the optimal transfer method based on host capabilities.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            
        Returns:
            Tuple of (transfer_type: str, transfer_instance)
        """
        # Check if both hosts have ZFS capability configured
        if (source_host.zfs_capable and target_host.zfs_capable and 
            source_host.zfs_dataset and target_host.zfs_dataset):
            
            # Validate ZFS is actually available
            source_valid, _ = await self.zfs_transfer.validate_requirements(source_host)
            target_valid, _ = await self.zfs_transfer.validate_requirements(target_host)
            
            if source_valid and target_valid:
                self.logger.info("Using ZFS send/receive for optimal transfer")
                return "zfs", self.zfs_transfer
        
        # Fall back to rsync transfer
        self.logger.info("Using rsync transfer (ZFS not available on both hosts)")
        return "rsync", self.rsync_transfer
    
    async def transfer_data(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_paths: list[str],
        target_path: str,
        stack_name: str,
        dry_run: bool = False
    ) -> dict[str, Any]:
        """Transfer data between hosts using the optimal method.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_paths: List of paths to transfer from source
            target_path: Target path on destination
            stack_name: Stack name for organization
            dry_run: Whether this is a dry run
            
        Returns:
            Transfer result dictionary
        """
        if not source_paths:
            return {"success": True, "message": "No data to transfer", "transfer_type": "none"}
        
        # Choose transfer method
        transfer_type, transfer_instance = await self.choose_transfer_method(source_host, target_host)
        
        if transfer_type == "zfs":
            # ZFS transfer works on datasets, not individual paths
            # Use the configured datasets
            return await transfer_instance.transfer(
                source_host=source_host,
                target_host=target_host,
                source_path=source_host.appdata_path or "/opt/docker-appdata",
                target_path=target_host.appdata_path or "/opt/docker-appdata",
                source_dataset=source_host.zfs_dataset,
                target_dataset=target_host.zfs_dataset,
            )
        else:
            # Rsync transfer - need to create archive first
            if dry_run:
                return {"success": True, "message": "Dry run - would transfer via rsync", "transfer_type": "rsync"}
            
            # Create archive on source
            ssh_cmd_source = self._build_ssh_cmd(source_host)
            archive_path = await self.archive_utils.create_archive(
                ssh_cmd_source, source_paths, f"{stack_name}_migration"
            )
            
            # Transfer archive to target
            transfer_result = await transfer_instance.transfer(
                source_host=source_host,
                target_host=target_host,
                source_path=archive_path,
                target_path=f"/tmp/{stack_name}_migration.tar.gz"
            )
            
            if transfer_result["success"]:
                # Extract on target
                ssh_cmd_target = self._build_ssh_cmd(target_host)
                extracted = await self.archive_utils.extract_archive(
                    ssh_cmd_target, 
                    f"/tmp/{stack_name}_migration.tar.gz",
                    target_path
                )
                
                if extracted:
                    # Cleanup archive on target
                    await self.archive_utils.cleanup_archive(
                        ssh_cmd_target, f"/tmp/{stack_name}_migration.tar.gz"
                    )
            
            # Cleanup archive on source
            await self.archive_utils.cleanup_archive(ssh_cmd_source, archive_path)
            
            return transfer_result
    
    def _build_ssh_cmd(self, host: DockerHost) -> list[str]:
        """Build SSH command for a host."""
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        if host.identity_file:
            ssh_cmd.extend(["-i", host.identity_file])
        if host.port != 22:
            ssh_cmd.extend(["-p", str(host.port)])
        ssh_cmd.append(f"{host.user}@{host.hostname}")
        return ssh_cmd
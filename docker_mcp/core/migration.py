"""Migration orchestration and Docker stack transfer coordination."""

import asyncio
import os
import shlex
import subprocess
import time
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

    # Volume parsing methods have been moved to VolumeParser class
    # in docker_mcp.core.migration.volume_parser for better organization

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
            inspect_cmd = f"docker volume inspect {shlex.quote(volume_name)} --format '{{{{.Mountpoint}}}}'"
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
        label = shlex.quote(f"com.docker.compose.project={stack_name}")
        check_cmd = ssh_cmd + [f"docker ps --filter label={label} --format '{{{{.Names}}}}'"]

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
                stop_cmd = ssh_cmd + [f"docker kill {shlex.quote(container)}"]
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda cmd=stop_cmd: subprocess.run(  # nosec B603
                        cmd, check=False, capture_output=True, text=True
                    ),
                )

            # Wait for containers to stop and processes to fully terminate
            await asyncio.sleep(10)  # Increased from 3s to ensure complete shutdown

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
        mkdir_cmd = f"mkdir -p {shlex.quote(stack_dir)}"
        full_cmd = ssh_cmd + [mkdir_cmd]

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=full_cmd: subprocess.run(  # nosec B603
                cmd, check=False, capture_output=True, text=True
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
        target_appdata_path: str = None,
    ) -> str:
        """Update compose file paths for target host using YAML parsing.
        
        Args:
            compose_content: Original compose file content
            old_paths: Mapping of old volume paths
            new_base_path: New base path for volumes on target
            target_appdata_path: Target host's appdata path for environment variable replacement
            
        Returns:
            Updated compose file content
        """
        try:
            # Parse YAML to manipulate compose file structurally
            compose_data = yaml.safe_load(compose_content)

            if not isinstance(compose_data, dict):
                self.logger.warning("Invalid compose file structure, falling back to string replacement")
                return self._update_compose_string_fallback(
                    compose_content, old_paths, new_base_path, target_appdata_path
                )

            # Update services section
            if "services" in compose_data:
                for service_name, service_config in compose_data["services"].items():
                    if not isinstance(service_config, dict) or "volumes" not in service_config:
                        continue

                    # Update volume mounts
                    updated_volumes = []
                    for volume in service_config["volumes"]:
                        updated_volume = self._update_volume_definition(
                            volume, old_paths, new_base_path, target_appdata_path
                        )
                        updated_volumes.append(updated_volume)

                    compose_data["services"][service_name]["volumes"] = updated_volumes

            # Update top-level volumes section
            if "volumes" in compose_data and isinstance(compose_data["volumes"], dict):
                for volume_name, volume_config in compose_data["volumes"].items():
                    if isinstance(volume_config, dict) and "driver_opts" in volume_config:
                        driver_opts = volume_config["driver_opts"]
                        if "device" in driver_opts:
                            old_device = driver_opts["device"]
                            if old_device in old_paths.values():
                                # Extract relative path component
                                path_parts = old_device.split("/")
                                relative_name = path_parts[-1] if path_parts else "data"
                                new_device = f"{new_base_path}/{relative_name}"
                                driver_opts["device"] = new_device

                                self.logger.debug(
                                    "Updated volume driver device path",
                                    volume=volume_name,
                                    old_device=old_device,
                                    new_device=new_device,
                                )

            # Convert back to YAML with proper formatting
            return yaml.dump(compose_data, default_flow_style=False, sort_keys=False)

        except yaml.YAMLError as e:
            self.logger.warning(
                "Failed to parse compose file as YAML, falling back to string replacement",
                error=str(e)
            )
            return self._update_compose_string_fallback(
                compose_content, old_paths, new_base_path, target_appdata_path
            )
        except Exception as e:
            self.logger.error(
                "Error updating compose file with YAML parsing",
                error=str(e)
            )
            return self._update_compose_string_fallback(
                compose_content, old_paths, new_base_path, target_appdata_path
            )

    def _update_volume_definition(
        self,
        volume: str | dict,
        old_paths: dict[str, str],
        new_base_path: str,
        target_appdata_path: str = None,
    ) -> str | dict:
        """Update a single volume definition for migration.
        
        Args:
            volume: Volume definition (string or dict format)
            old_paths: Mapping of old volume paths
            new_base_path: New base path for volumes
            target_appdata_path: Target appdata path for environment variable replacement
            
        Returns:
            Updated volume definition
        """
        if isinstance(volume, str):
            # Handle string volume format
            updated_volume = volume

            # Replace environment variables
            if target_appdata_path and "${APPDATA_PATH}" in updated_volume:
                updated_volume = updated_volume.replace("${APPDATA_PATH}", target_appdata_path)

            # Replace old paths
            for old_path in old_paths.values():
                if old_path in updated_volume:
                    path_parts = old_path.split("/")
                    relative_name = path_parts[-1] if path_parts else "data"
                    new_path = f"{new_base_path}/{relative_name}"
                    updated_volume = updated_volume.replace(old_path, new_path)

                    self.logger.debug(
                        "Updated volume string",
                        old=old_path,
                        new=new_path,
                        volume=updated_volume,
                    )

            return updated_volume

        elif isinstance(volume, dict):
            # Handle dictionary volume format
            updated_volume = volume.copy()

            if "source" in updated_volume:
                source = updated_volume["source"]

                # Replace environment variables
                if target_appdata_path and "${APPDATA_PATH}" in source:
                    source = source.replace("${APPDATA_PATH}", target_appdata_path)

                # Replace old paths
                for old_path in old_paths.values():
                    if old_path in source:
                        path_parts = old_path.split("/")
                        relative_name = path_parts[-1] if path_parts else "data"
                        new_source = f"{new_base_path}/{relative_name}"
                        source = source.replace(old_path, new_source)

                        self.logger.debug(
                            "Updated volume dict source",
                            old=old_path,
                            new=new_source,
                            volume=updated_volume,
                        )

                updated_volume["source"] = source

            return updated_volume

        # Return unchanged if not string or dict
        return volume

    def _update_compose_string_fallback(
        self,
        compose_content: str,
        old_paths: dict[str, str],
        new_base_path: str,
        target_appdata_path: str = None,
    ) -> str:
        """Fallback method using string replacement for compose updates.
        
        This is used when YAML parsing fails for any reason.
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
                    "Updated compose path (fallback)",
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

    async def create_source_inventory(
        self,
        ssh_cmd: list[str],
        volume_paths: list[str],
    ) -> dict[str, Any]:
        """Create detailed inventory of source data before migration.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            volume_paths: List of source volume paths to inventory
            
        Returns:
            Dictionary containing complete source inventory
        """
        inventory = {
            "total_files": 0,
            "total_dirs": 0,
            "total_size": 0,
            "paths": {},
            "critical_files": {},
            "timestamp": time.time()
        }

        for path in volume_paths:
            qpath = shlex.quote(path)
            path_inventory = {}

            # Get file count
            file_count_cmd = ssh_cmd + [f"find {qpath} -type f 2>/dev/null | wc -l"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda cmd=file_count_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["file_count"] = int(result.stdout.strip()) if result.returncode == 0 else 0

            # Get directory count
            dir_count_cmd = ssh_cmd + [f"find {qpath} -type d 2>/dev/null | wc -l"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda cmd=dir_count_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["dir_count"] = int(result.stdout.strip()) if result.returncode == 0 else 0

            # Get total size in bytes
            size_cmd = ssh_cmd + [f"du -sb {qpath} 2>/dev/null | cut -f1"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda cmd=size_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["total_size"] = int(result.stdout.strip()) if result.returncode == 0 else 0

            # Get file listing for comparison
            file_list_cmd = ssh_cmd + [f"find {qpath} -type f -printf '%P\\n' 2>/dev/null | sort"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda cmd=file_list_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["file_list"] = result.stdout.strip().split("\n") if result.returncode == 0 else []

            # Find and checksum critical files (databases, configs)
            critical_cmd = ssh_cmd + [
                f"find {qpath} -type f \\( -name '*.db' -o -name '*.sqlite*' -o -name 'config.*' -o -name '*.conf' \\) "
                f"-exec md5sum {{}} + 2>/dev/null"
            ]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda cmd=critical_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
            )

            critical_files = {}
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    if line:
                        parts = line.strip().split(None, 1)
                        if len(parts) == 2:
                            checksum, filepath = parts
                            # Store relative path
                            rel_path = filepath.replace(f"{path}/", "")
                            critical_files[rel_path] = checksum

            path_inventory["critical_files"] = critical_files

            # Add to inventory
            inventory["paths"][path] = path_inventory
            inventory["total_files"] += path_inventory["file_count"]
            inventory["total_dirs"] += path_inventory["dir_count"]
            inventory["total_size"] += path_inventory["total_size"]
            inventory["critical_files"].update(critical_files)

        self.logger.info(
            "Created source inventory",
            total_files=inventory["total_files"],
            total_dirs=inventory["total_dirs"],
            total_size=inventory["total_size"],
            critical_files=len(inventory["critical_files"]),
        )

        return inventory

    async def verify_migration_completeness(
        self,
        ssh_cmd: list[str],
        source_inventory: dict[str, Any],
        target_appdata: str,
        stack_name: str,
    ) -> dict[str, Any]:
        """Verify all data was transferred correctly by comparing source inventory to target.
        
        Args:
            ssh_cmd: SSH command parts for target host execution
            source_inventory: Complete inventory created before migration
            target_appdata: Target appdata path
            stack_name: Stack name for path calculation
            
        Returns:
            Dictionary containing verification results
        """
        verification = {
            "data_transfer": {
                "success": True,
                "files_expected": source_inventory["total_files"],
                "files_found": 0,
                "dirs_expected": source_inventory["total_dirs"],
                "dirs_found": 0,
                "size_expected": source_inventory["total_size"],
                "size_found": 0,
                "missing_files": [],
                "critical_files_verified": {},
                "file_match_percentage": 0.0,
                "size_match_percentage": 0.0,
            },
            "issues": []
        }

        # Calculate expected target path
        target_path = f"{target_appdata}/{stack_name}"
        qtarget = shlex.quote(target_path)

        # Get target inventory using same methods as source
        # File count
        file_count_cmd = ssh_cmd + [f"find {qtarget} -type f 2>/dev/null | wc -l"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=file_count_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_files = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["files_found"] = target_files

        # Directory count
        dir_count_cmd = ssh_cmd + [f"find {qtarget} -type d 2>/dev/null | wc -l"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=dir_count_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_dirs = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["dirs_found"] = target_dirs

        # Total size
        size_cmd = ssh_cmd + [f"du -sb {qtarget} 2>/dev/null | cut -f1"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=size_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_size = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["size_found"] = target_size

        # Get target file listing
        file_list_cmd = ssh_cmd + [f"find {qtarget} -type f -printf '%P\\n' 2>/dev/null | sort"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=file_list_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_file_list = result.stdout.strip().split("\n") if result.returncode == 0 and result.stdout.strip() else []

        # Compare file listings to find missing files
        source_files = set()
        for path_data in source_inventory["paths"].values():
            source_files.update(path_data.get("file_list", []))

        target_file_set = set(target_file_list)
        missing_files = source_files - target_file_set
        verification["data_transfer"]["missing_files"] = list(missing_files)

        # Calculate match percentages
        if source_inventory["total_files"] > 0:
            verification["data_transfer"]["file_match_percentage"] = (
                target_files / source_inventory["total_files"] * 100
            )

        if source_inventory["total_size"] > 0:
            verification["data_transfer"]["size_match_percentage"] = (
                target_size / source_inventory["total_size"] * 100
            )

        # Verify critical files checksums
        critical_files_verified = {}
        for rel_path, source_checksum in source_inventory["critical_files"].items():
            target_file_path = f"{target_path.rstrip('/')}/{rel_path.lstrip('/')}"
            qfile = shlex.quote(target_file_path)
            # Try SHA256 first, fallback to MD5
            checksum_cmd = ssh_cmd + [
                f"if command -v sha256sum >/dev/null 2>&1; then "
                f"  sha256sum {qfile} 2>/dev/null | cut -d' ' -f1; "
                f"else "
                f"  md5sum {qfile} 2>/dev/null | cut -d' ' -f1; "
                f"fi"
            ]

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda cmd=checksum_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
            )

            if result.returncode == 0 and result.stdout.strip():
                target_checksum = result.stdout.strip()
                critical_files_verified[rel_path] = {
                    "verified": source_checksum == target_checksum,
                    "source_checksum": source_checksum,
                    "target_checksum": target_checksum
                }
            else:
                critical_files_verified[rel_path] = {
                    "verified": False,
                    "source_checksum": source_checksum,
                    "target_checksum": None,
                    "error": "File not found or inaccessible"
                }

        verification["data_transfer"]["critical_files_verified"] = critical_files_verified

        # Determine overall success and collect issues
        issues = []

        # File count mismatch
        if target_files != source_inventory["total_files"]:
            diff = target_files - source_inventory["total_files"]
            issues.append(f"File count mismatch: {diff:+d} files ({verification['data_transfer']['file_match_percentage']:.1f}% match)")

        # Size mismatch (allow 1% variance for filesystem overhead)
        size_variance = abs(target_size - source_inventory["total_size"]) / source_inventory["total_size"] * 100 if source_inventory["total_size"] > 0 else 0
        if size_variance > 1.0:
            issues.append(f"Size mismatch: {target_size - source_inventory['total_size']:+d} bytes ({verification['data_transfer']['size_match_percentage']:.1f}% match)")

        # Missing files
        if missing_files:
            issues.append(f"{len(missing_files)} files missing from target")

        # Critical file verification failures
        failed_critical = [f for f, v in critical_files_verified.items() if not v["verified"]]
        if failed_critical:
            issues.append(f"{len(failed_critical)} critical files failed verification")

        verification["issues"] = issues
        verification["data_transfer"]["success"] = len(issues) == 0

        self.logger.info(
            "Migration completeness verification",
            success=verification["data_transfer"]["success"],
            files_match=f"{verification['data_transfer']['file_match_percentage']:.1f}%",
            size_match=f"{verification['data_transfer']['size_match_percentage']:.1f}%",
            critical_files_ok=len(critical_files_verified) - len(failed_critical),
            issues=len(issues),
        )

        return verification

    async def verify_container_integration(
        self,
        ssh_cmd: list[str],
        stack_name: str,
        expected_appdata_path: str,
        expected_volumes: list[str],
    ) -> dict[str, Any]:
        """Verify container is properly integrated with migrated data.
        
        Args:
            ssh_cmd: SSH command parts for target host execution  
            stack_name: Stack/container name to check
            expected_appdata_path: Expected appdata path on target
            expected_volumes: List of expected volume mount strings
            
        Returns:
            Dictionary containing container integration verification results
        """
        verification = {
            "container_integration": {
                "success": True,
                "container_exists": False,
                "container_running": False,
                "container_healthy": False,
                "mount_paths_correct": False,
                "data_accessible": False,
                "expected_mounts": expected_volumes,
                "actual_mounts": [],
                "health_status": None,
                "startup_errors": [],
            },
            "issues": []
        }

        # Check if container exists and get its info
        qname = shlex.quote(stack_name)
        inspect_cmd = ssh_cmd + [f"docker inspect {qname} 2>/dev/null || echo 'NOT_FOUND'"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=inspect_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
        )

        if result.returncode != 0 or "NOT_FOUND" in result.stdout:
            verification["issues"].append(f"Container '{stack_name}' not found")
            verification["container_integration"]["success"] = False
            return verification

        verification["container_integration"]["container_exists"] = True

        try:
            import json
            container_info = json.loads(result.stdout)[0]

            # Check container state
            state = container_info.get("State", {})
            verification["container_integration"]["container_running"] = state.get("Running", False)

            # Check health status
            health = state.get("Health", {})
            health_status = health.get("Status")
            verification["container_integration"]["health_status"] = health_status
            verification["container_integration"]["container_healthy"] = health_status == "healthy"

            # Get mount information
            mounts = container_info.get("Mounts", [])
            actual_mounts = []
            for mount in mounts:
                if mount.get("Type") == "bind":  # Only check bind mounts
                    source = mount.get("Source", "")
                    destination = mount.get("Destination", "")
                    if source and destination:
                        actual_mounts.append(f"{source}:{destination}")

            verification["container_integration"]["actual_mounts"] = actual_mounts

            # Check if expected mounts are present
            mount_matches = 0
            for expected_mount in expected_volumes:
                if expected_mount in actual_mounts:
                    mount_matches += 1
                else:
                    # Check if mount points to expected appdata path
                    if ":" in expected_mount:
                        expected_source, expected_dest = expected_mount.split(":", 1)
                        # See if any actual mount has the same destination
                        for actual_mount in actual_mounts:
                            if ":" in actual_mount:
                                actual_source, actual_dest = actual_mount.split(":", 1)
                                if actual_dest == expected_dest and expected_appdata_path in actual_source:
                                    mount_matches += 1
                                    break

            verification["container_integration"]["mount_paths_correct"] = (
                mount_matches == len(expected_volumes) if expected_volumes else True
            )

            # Test data accessibility inside container if container is running
            if verification["container_integration"]["container_running"]:
                # Try to access a common path inside the container
                test_cmd = ssh_cmd + [f"docker exec {qname} ls /data 2>/dev/null || docker exec {qname} ls / 2>/dev/null"]
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda cmd=test_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                verification["container_integration"]["data_accessible"] = result.returncode == 0

                # Check for startup errors in logs
                logs_cmd = ssh_cmd + [f"docker logs {qname} --tail 50 2>&1 | grep -i error || true"]
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda cmd=logs_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                if result.stdout.strip():
                    error_lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
                    verification["container_integration"]["startup_errors"] = error_lines[:5]  # Limit to 5 errors

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            verification["issues"].append(f"Failed to parse container info: {str(e)}")
            verification["container_integration"]["success"] = False
            return verification

        # Collect integration issues
        issues = []

        if not verification["container_integration"]["container_running"]:
            issues.append("Container is not running")

        if not verification["container_integration"]["mount_paths_correct"]:
            issues.append("Container mount paths do not match expected")

        if verification["container_integration"]["container_running"] and not verification["container_integration"]["data_accessible"]:
            issues.append("Data not accessible inside container")

        if verification["container_integration"]["startup_errors"]:
            issues.append(f"Container has {len(verification['container_integration']['startup_errors'])} startup errors")

        if health_status and health_status not in ["healthy", "none"]:
            issues.append(f"Container health check failed: {health_status}")

        verification["issues"] = issues
        verification["container_integration"]["success"] = len(issues) == 0

        self.logger.info(
            "Container integration verification",
            success=verification["container_integration"]["success"],
            running=verification["container_integration"]["container_running"],
            healthy=verification["container_integration"]["container_healthy"],
            mounts_correct=verification["container_integration"]["mount_paths_correct"],
            data_accessible=verification["container_integration"]["data_accessible"],
            issues=len(issues),
        )

        return verification

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

            # Transfer archive to target with random suffix for security
            temp_suffix = os.urandom(8).hex()[:8]
            target_archive_path = f"/tmp/{stack_name}_migration_{temp_suffix}.tar.gz"

            transfer_result = await transfer_instance.transfer(
                source_host=source_host,
                target_host=target_host,
                source_path=archive_path,
                target_path=target_archive_path
            )

            if transfer_result["success"]:
                # Extract on target
                ssh_cmd_target = self._build_ssh_cmd(target_host)
                extracted = await self.archive_utils.extract_archive(
                    ssh_cmd_target,
                    target_archive_path,
                    target_path
                )

                if extracted:
                    # Cleanup archive on target
                    await self.archive_utils.cleanup_archive(
                        ssh_cmd_target, target_archive_path
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

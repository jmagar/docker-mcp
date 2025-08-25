"""Migration orchestration and Docker stack transfer coordination."""

import asyncio
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
        """
        Initialize the MigrationManager.
        
        Binds a component-scoped logger ("migration_manager") and constructs the transfer helpers used by migration workflows: ArchiveUtils, RsyncTransfer, and ZFSTransfer.
        """
        self.logger = logger.bind(component="migration_manager")
        
        # Initialize transfer methods
        self.archive_utils = ArchiveUtils()
        self.rsync_transfer = RsyncTransfer()
        self.zfs_transfer = ZFSTransfer()
    
    async def parse_compose_volumes(self, compose_content: str, source_appdata_path: str = None) -> dict[str, Any]:
        """
        Parse a Docker Compose YAML string and extract volume information.
        
        If present, top-level `volumes` definitions are captured and service-level volumes are analyzed. Service volumes may be specified as strings (e.g. "host_path:container_path:mode") or dictionary objects; string-style bind mounts will have `${APPDATA_PATH}` expanded using `source_appdata_path` before classification.
        
        Parameters:
            compose_content (str): Docker Compose YAML content.
            source_appdata_path (str | None): Optional path to expand `${APPDATA_PATH}` in volume strings.
        
        Returns:
            dict[str, Any]: {
                "named_volumes": list[str],        # unique named volume names referenced by services
                "bind_mounts": list[str],          # unique host paths used as bind mounts
                "volume_definitions": dict         # top-level `volumes` section from the compose file (may be empty)
            }
        
        Raises:
            MigrationError: If the YAML cannot be parsed or another extraction error occurs.
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
            raise MigrationError(f"Failed to parse compose file: {e}")
        except Exception as e:
            raise MigrationError(f"Error extracting volumes: {e}")
    
    def _parse_volume_string(self, volume_str: str, source_appdata_path: str = None) -> dict[str, str]:
        """
        Parse a single Docker volume specification string and return a normalized descriptor.
        
        Expands the `${APPDATA_PATH}` token using source_appdata_path if provided, then splits
        the volume string on ":" to classify it as a bind mount (host path) or a named Docker volume.
        
        Parameters:
            volume_str (str): Volume specification (e.g. "data:/app/data", "/host/path:/container/path",
                or "${APPDATA_PATH}/service:/data").
            source_appdata_path (str, optional): If provided, used to replace `${APPDATA_PATH}` before parsing.
        
        Returns:
            dict: Descriptor with keys depending on volume type:
                - For bind mounts:
                    {
                        "type": "bind",
                        "source": "<host_path>",
                        "destination": "<container_path>",
                        "mode": "<mode>" (defaults to "rw"),
                        "original": "<original_volume_str>"
                    }
                - For named volumes:
                    {
                        "type": "named",
                        "name": "<volume_name>",
                        "destination": "<container_path>",
                        "mode": "<mode>" (defaults to "rw")
                    }
                - If only a single token is provided, returns a named-volume descriptor with an empty destination.
        """
        # Expand environment variables using host configuration
        expanded_volume_str = volume_str
        if source_appdata_path and "${APPDATA_PATH}" in volume_str:
            expanded_volume_str = volume_str.replace("${APPDATA_PATH}", source_appdata_path)
        
        parts = expanded_volume_str.split(":")
        
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
                "original": volume_str,  # Keep original for path mapping
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
        target_appdata_path: str = None,
    ) -> str:
        """
        Update a Docker Compose file's volume paths for the target host.
        
        Replaces occurrences of the `${APPDATA_PATH}` variable with the provided
        target_appdata_path (if given), then updates any literal paths found in
        old_paths to point under new_base_path by taking the last path component
        of each old path (e.g., `/opt/app/data` -> `{new_base_path}/data`).
        
        Parameters:
            compose_content (str): Original compose YAML content.
            old_paths (dict[str, str]): Mapping of identifiers to old absolute paths to be replaced.
            new_base_path (str): Base directory on the target host where volumes should be placed.
            target_appdata_path (str, optional): If provided, replaces `${APPDATA_PATH}` occurrences.
        
        Returns:
            str: The updated compose content with substituted paths.
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
    
    async def choose_transfer_method(
        self,
        source_host: DockerHost,
        target_host: DockerHost
    ) -> tuple[str, Any]:
        """
        Selects the most suitable data transfer method between two hosts.
        
        If both hosts advertise ZFS capability and have a non-empty `zfs_dataset`, this method validates ZFS requirements on each host; if both validations succeed it returns the ZFS transfer implementation. Otherwise it falls back to the rsync transfer implementation.
        
        Parameters:
            source_host: Source host descriptor; ZFS is preferred when `zfs_capable` is true and `zfs_dataset` is set.
            target_host: Target host descriptor; ZFS is preferred when `zfs_capable` is true and `zfs_dataset` is set.
        
        Returns:
            A tuple (transfer_type, transfer_instance) where `transfer_type` is either `"zfs"` or `"rsync"` and `transfer_instance` is the corresponding transfer helper object.
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
        """
        Create a detailed inventory of files, directories, size, and checksums for each source volume path by running remote shell commands over the provided SSH command.
        
        For each path in volume_paths this coroutine collects:
        - file_count: number of regular files
        - dir_count: number of directories
        - total_size: size in bytes (du -sb)
        - file_list: newline-sorted list of files with paths relative to the volume path
        - critical_files: mapping of relative file path -> md5 checksum for files matching patterns (*.db, *.sqlite*, config.*, *.conf)
        
        The returned inventory also contains aggregated totals (total_files, total_dirs, total_size), a merged critical_files map, per-path details under "paths", and a "timestamp" (epoch seconds) when the inventory was created.
        
        Parameters:
            ssh_cmd (list[str]): Base SSH command (split into argv parts) used to execute remote shell commands; the function appends remote commands to this list.
            volume_paths (list[str]): List of absolute source filesystem paths on the remote host to include in the inventory.
        
        Returns:
            dict[str, Any]: Inventory dictionary with keys "total_files", "total_dirs", "total_size", "paths", "critical_files", and "timestamp".
        
        Notes:
        - If a remote command fails for a path, counts for that path default to zero or empty lists as appropriate; the function does not raise on remote command non-zero exit codes.
        - Critical file checksums are computed with md5sum and stored with paths relative to the scanned volume path.
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
            path_inventory = {}
            
            # Get file count
            file_count_cmd = ssh_cmd + [f"find {path} -type f 2>/dev/null | wc -l"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(file_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["file_count"] = int(result.stdout.strip()) if result.returncode == 0 else 0
            
            # Get directory count  
            dir_count_cmd = ssh_cmd + [f"find {path} -type d 2>/dev/null | wc -l"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(dir_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["dir_count"] = int(result.stdout.strip()) if result.returncode == 0 else 0
            
            # Get total size in bytes
            size_cmd = ssh_cmd + [f"du -sb {path} 2>/dev/null | cut -f1"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(size_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["total_size"] = int(result.stdout.strip()) if result.returncode == 0 else 0
            
            # Get file listing for comparison
            file_list_cmd = ssh_cmd + [f"find {path} -type f -printf '%P\\n' 2>/dev/null | sort"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(file_list_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["file_list"] = result.stdout.strip().split("\n") if result.returncode == 0 else []
            
            # Find and checksum critical files (databases, configs)
            critical_cmd = ssh_cmd + [
                f"find {path} -type f \\( -name '*.db' -o -name '*.sqlite*' -o -name 'config.*' -o -name '*.conf' \\) "
                f"-exec md5sum {{}} + 2>/dev/null"
            ]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(critical_cmd, capture_output=True, text=True, check=False)  # nosec B603
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
        """
        Compare a pre-migration source inventory with the target host data to verify transfer completeness.
        
        Performs file count, directory count, total size, and file-list comparisons for the migrated stack path (computed as f"{target_appdata}/{stack_name}"), and verifies checksums for critical files recorded in the source inventory. Allows ~1% size variance for filesystem overhead. Populates a verification dictionary with match percentages, missing files, per-critical-file verification results, and a list of human-readable issues.
        
        Parameters:
            ssh_cmd (list[str]): Base SSH command parts to execute remote commands on the target host.
            source_inventory (dict): Inventory produced by create_source_inventory; must include totals, per-path file lists, and `critical_files` mapping of relative path -> md5 checksum.
            target_appdata (str): Base appdata path on the target host.
            stack_name (str): Stack name used to derive the target path under target_appdata.
        
        Returns:
            dict: Verification report with at least the keys:
              - data_transfer: {
                  success (bool),
                  files_expected (int),
                  files_found (int),
                  dirs_expected (int),
                  dirs_found (int),
                  size_expected (int),
                  size_found (int),
                  missing_files (list[str]),
                  critical_files_verified (dict[str, dict]),
                  file_match_percentage (float),
                  size_match_percentage (float),
                }
              - issues (list[str]): List of detected problems (file/size mismatches, missing files, failed critical verifications).
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
        
        # Get target inventory using same methods as source
        # File count
        file_count_cmd = ssh_cmd + [f"find {target_path} -type f 2>/dev/null | wc -l"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(file_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_files = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["files_found"] = target_files
        
        # Directory count
        dir_count_cmd = ssh_cmd + [f"find {target_path} -type d 2>/dev/null | wc -l"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(dir_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_dirs = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["dirs_found"] = target_dirs
        
        # Total size
        size_cmd = ssh_cmd + [f"du -sb {target_path} 2>/dev/null | cut -f1"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(size_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_size = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["size_found"] = target_size
        
        # Get target file listing
        file_list_cmd = ssh_cmd + [f"find {target_path} -type f -printf '%P\\n' 2>/dev/null | sort"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(file_list_cmd, capture_output=True, text=True, check=False)  # nosec B603
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
            target_file_path = f"{target_path}/{rel_path}"
            checksum_cmd = ssh_cmd + [f"md5sum {target_file_path} 2>/dev/null | cut -d' ' -f1"]
            
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(checksum_cmd, capture_output=True, text=True, check=False)  # nosec B603
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
        """
        Verify that a deployed container from the given stack is correctly integrated with migrated application data on the target host.
        
        Performs a remote inspection and a series of checks to determine:
        - whether the container exists, is running, and is healthy;
        - whether bind mounts match the expected volume mappings (allowing matches where the destination is equal and the source contains the expected appdata path);
        - whether data inside the container is accessible (by attempting a simple directory listing);
        - whether recent startup logs contain error lines.
        
        Parameters:
            stack_name (str): Docker container name (typically the Compose service name or stack container identifier) to inspect.
            expected_appdata_path (str): Expected base appdata path on the target host used to validate mount source locations.
            expected_volumes (list[str]): Expected bind mount specifications in the form "source:destination" to verify presence.
        
        Note: The function executes Docker commands on the target host via the provided SSH command (ssh_cmd) and returns a structured verification report rather than raising on verification failures.
        
        Returns:
            dict[str, Any]: Verification report with keys:
              - "container_integration": dict with flags and details (success, container_exists, container_running,
                container_healthy, mount_paths_correct, data_accessible, expected_mounts, actual_mounts,
                health_status, startup_errors).
              - "issues": list of human-readable issue strings found during verification.
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
        inspect_cmd = ssh_cmd + [f"docker inspect {stack_name} 2>/dev/null || echo 'NOT_FOUND'"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(inspect_cmd, capture_output=True, text=True, check=False)  # nosec B603
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
                test_cmd = ssh_cmd + [f"docker exec {stack_name} ls /data 2>/dev/null || docker exec {stack_name} ls / 2>/dev/null"]
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(test_cmd, capture_output=True, text=True, check=False)  # nosec B603
                )
                verification["container_integration"]["data_accessible"] = result.returncode == 0
                
                # Check for startup errors in logs
                logs_cmd = ssh_cmd + [f"docker logs {stack_name} --tail 50 2>&1 | grep -i error || true"]
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(logs_cmd, capture_output=True, text=True, check=False)  # nosec B603
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
        """
        Transfer application data from a source host to a target host using the best available method (ZFS send/receive or rsync/archive).
        
        If both hosts support ZFS dataset transfers this will perform a ZFS dataset transfer; otherwise it creates an archive on the source, transfers it to the target and extracts it. Archives created on the source and temporary archives on the target are removed after transfer (success or failure). If no source_paths are provided the function returns immediately with success.
        
        Parameters:
            source_paths (list[str]): Filesystem paths on the source to include in the transfer.
            target_path (str): Destination directory on the target where data will be extracted (used by rsync/archive path).
            stack_name (str): Logical name used to build temporary archive filenames on both hosts.
            dry_run (bool): If True and rsync is selected, do not perform transfer; returns a successful dry-run result.
        
        Returns:
            dict: Result object with at minimum a "success" boolean and optional keys such as "message" and "transfer_type".
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
"""Migration verification utilities for Docker stack transfers."""

import asyncio
import json
import shlex
import subprocess
import time
from typing import Any

import structlog

from ..exceptions import DockerMCPError

logger = structlog.get_logger()


class VerificationError(DockerMCPError):
    """Verification operation failed."""
    pass


class MigrationVerifier:
    """Handles verification of Docker stack migrations."""
    
    def __init__(self):
        """
        Initialize the MigrationVerifier instance.
        
        Creates and stores a component-scoped logger bound to "migration_verifier" on self.logger.
        """
        self.logger = logger.bind(component="migration_verifier")
    
    async def create_source_inventory(
        self,
        ssh_cmd: list[str],
        volume_paths: list[str],
    ) -> dict[str, Any]:
        """
        Build a detailed inventory of files, directories, sizes, and critical-file checksums for each source volume path.
        
        This asynchronous method runs remote shell commands (via the provided SSH command parts) to collect, per path:
        - file_count: number of regular files
        - dir_count: number of directories
        - total_size: total size in bytes (du -sb)
        - file_list: newline-sorted list of file paths relative to the provided path
        - critical_files: mapping of relative file path -> md5 checksum for files matching common database/config patterns
        
        Parameters:
            ssh_cmd (list[str]): Base SSH command parts to execute on the remote host (e.g., ["ssh", "host"]). The method appends shell commands to this list.
            volume_paths (list[str]): List of absolute source paths to inventory.
        
        Returns:
            dict[str, Any]: Inventory dictionary containing per-path entries under "paths", aggregated totals ("total_files", "total_dirs", "total_size"), a combined "critical_files" map, and a "timestamp".
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
            file_count_cmd = ssh_cmd + [f"find {shlex.quote(path)} -type f 2>/dev/null | wc -l"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(file_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["file_count"] = int(result.stdout.strip()) if result.returncode == 0 else 0
            
            # Get directory count  
            dir_count_cmd = ssh_cmd + [f"find {shlex.quote(path)} -type d 2>/dev/null | wc -l"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(dir_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["dir_count"] = int(result.stdout.strip()) if result.returncode == 0 else 0
            
            # Get total size in bytes
            size_cmd = ssh_cmd + [f"du -sb {shlex.quote(path)} 2>/dev/null | cut -f1"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(size_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["total_size"] = int(result.stdout.strip()) if result.returncode == 0 else 0
            
            # Get file listing for comparison
            file_list_cmd = ssh_cmd + [f"find {shlex.quote(path)} -type f -printf '%P\\n' 2>/dev/null | sort"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(file_list_cmd, capture_output=True, text=True, check=False)  # nosec B603
            )
            path_inventory["file_list"] = result.stdout.strip().split("\n") if result.returncode == 0 else []
            
            # Find and checksum critical files (databases, configs)
            critical_cmd = ssh_cmd + [
                f"find {shlex.quote(path)} -type f \\( -name '*.db' -o -name '*.sqlite*' -o -name 'config.*' -o -name '*.conf' \\) "
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
        target_path: str,
    ) -> dict[str, Any]:
        """
        Verify that data at a target path matches a previously created source inventory.
        
        Performs remote checks on the target host (using the provided SSH command parts) to:
        - count files and directories and measure total size under target_path,
        - collect a relative file listing to determine missing files,
        - compute checksums for critical files (preferring sha256sum, falling back to md5sum) and compare them to source checksums,
        - compute file- and size-match percentages and assemble a list of issues.
        
        Parameters:
            ssh_cmd (list[str]): Base SSH command parts to run a shell command on the target host (e.g., ["ssh", "user@host"]). Individual shell commands are appended to this list internally.
            source_inventory (dict): Inventory produced by create_source_inventory. Expected keys used:
                - "total_files" (int), "total_dirs" (int), "total_size" (int)
                - "paths" (mapping) where each path entry may contain "file_list" (iterable of relative file paths)
                - "critical_files" (mapping of relative path -> checksum) for files that must be checksum-verified.
            target_path (str): Absolute path on the target host where the migrated data were extracted.
        
        Returns:
            dict: A verification report with the top-level keys:
                - "data_transfer": {
                    "success": bool,
                    "files_expected": int,
                    "files_found": int,
                    "dirs_expected": int,
                    "dirs_found": int,
                    "size_expected": int,
                    "size_found": int,
                    "missing_files": list[str],
                    "critical_files_verified": dict[str, dict],  # per-file verification details
                    "file_match_percentage": float,
                    "size_match_percentage": float
                  }
                - "issues": list[str]  # human-readable issue descriptions; empty when success is True
        
        Notes:
            - The function tolerates absence of checksum utilities on the target by attempting sha256sum then md5sum.
            - Size comparison allows for typical filesystem overhead; the function flags size mismatches when variance exceeds 1%.
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
        
        # Use provided target path
        # Get target inventory using same methods as source
        # File count
        file_count_cmd = ssh_cmd + [f"find {shlex.quote(target_path)} -type f 2>/dev/null | wc -l"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(file_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_files = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["files_found"] = target_files
        
        # Directory count
        dir_count_cmd = ssh_cmd + [f"find {shlex.quote(target_path)} -type d 2>/dev/null | wc -l"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(dir_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_dirs = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["dirs_found"] = target_dirs
        
        # Total size
        size_cmd = ssh_cmd + [f"du -sb {shlex.quote(target_path)} 2>/dev/null | cut -f1"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(size_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        target_size = int(result.stdout.strip()) if result.returncode == 0 else 0
        verification["data_transfer"]["size_found"] = target_size
        
        # Get target file listing
        file_list_cmd = ssh_cmd + [f"find {shlex.quote(target_path)} -type f -printf '%P\\n' 2>/dev/null | sort"]
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
    
    async def _inspect_container(self, ssh_cmd: list[str], stack_name: str) -> dict[str, Any] | None:
        """
        Run `docker inspect` on the remote host and return the parsed container information.
        
        This executes the provided SSH command parts with `docker inspect <stack_name>` on the remote side and attempts to parse the first JSON object from the command output. Returns the parsed inspect dict on success. Returns None if the container/stack is not found, the command indicates absence, or the output cannot be parsed as the expected JSON structure.
        """
        inspect_cmd = ssh_cmd + [f"docker inspect {shlex.quote(stack_name)} 2>/dev/null || echo 'NOT_FOUND'"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=inspect_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if result.returncode != 0 or "NOT_FOUND" in result.stdout:
            return None
        
        try:
            return json.loads(result.stdout)[0]
        except (json.JSONDecodeError, KeyError, IndexError):
            return None
    
    def _collect_mounts(self, container_info: dict[str, Any]) -> list[str]:
        """
        Return a list of bind mount mappings from Docker inspect output as "source:destination" strings.
        
        Only mounts with Type == "bind" and both Source and Destination present are included.
        
        Returns:
            list[str]: Bind mount mappings formatted as "source:destination".
        """
        actual_mounts = []
        mounts = container_info.get("Mounts", [])
        for mount in mounts:
            if mount.get("Type") == "bind":  # Only check bind mounts
                source = mount.get("Source", "")
                destination = mount.get("Destination", "")
                if source and destination:
                    actual_mounts.append(f"{source}:{destination}")
        return actual_mounts
    
    async def _check_in_container_access(self, ssh_cmd: list[str], stack_name: str) -> bool:
        """
        Check whether the container identified by `stack_name` can list expected data paths via SSH.
        
        Attempts to run `ls /data` inside the named container and falls back to `ls /` if `/data` is absent; the commands are executed through the provided `ssh_cmd` command parts. Returns True when the remote `docker exec` command succeeds (exit code 0), otherwise False.
        
        Parameters:
            ssh_cmd (list[str]): SSH command parts to invoke on the remote host (e.g., ['ssh', 'host']).
            stack_name (str): Container or stack name passed to `docker exec`.
        
        Returns:
            bool: True if the container responded successfully to the list command, False otherwise.
        """
        test_cmd = ssh_cmd + [f"docker exec {shlex.quote(stack_name)} ls /data 2>/dev/null || docker exec {shlex.quote(stack_name)} ls / 2>/dev/null"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=test_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        return result.returncode == 0
    
    async def _collect_startup_errors(self, ssh_cmd: list[str], stack_name: str) -> list[str]:
        """
        Return up to five startup-related error lines extracted from the container's recent logs.
        
        This runs `docker logs <stack_name> --tail 50` on the remote host (using the provided SSH command prefix),
        filters lines that contain "error" (case-insensitive), and returns at most the first five non-empty matching lines.
        If no matching lines are found the function returns an empty list.
        
        Parameters:
            ssh_cmd (list[str]): SSH command prefix (as list of argv parts) used to run the remote docker logs command.
            stack_name (str): Container or stack name passed to `docker logs`.
        
        Returns:
            list[str]: Up to five log lines containing the string "error" (case-insensitive); empty if none found.
        """
        logs_cmd = ssh_cmd + [f"docker logs {shlex.quote(stack_name)} --tail 50 2>&1 | grep -i error || true"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=logs_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if result.stdout.strip():
            error_lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
            return error_lines[:5]  # Limit to 5 errors
        return []
    
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
        
        # Use helper to inspect container
        container_info = await self._inspect_container(ssh_cmd, stack_name)
        
        if not container_info:
            verification["issues"].append(f"Container '{stack_name}' not found")
            verification["container_integration"]["success"] = False
            return verification
            
        verification["container_integration"]["container_exists"] = True
        
        # Check container state
        state = container_info.get("State", {})
        verification["container_integration"]["container_running"] = state.get("Running", False)
        
        # Check health status
        health = state.get("Health", {})
        health_status = health.get("Status")
        verification["container_integration"]["health_status"] = health_status
        verification["container_integration"]["container_healthy"] = health_status == "healthy"
        
        # Get mount information using helper
        actual_mounts = self._collect_mounts(container_info)
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
            verification["container_integration"]["data_accessible"] = await self._check_in_container_access(ssh_cmd, stack_name)
            verification["container_integration"]["startup_errors"] = await self._collect_startup_errors(ssh_cmd, stack_name)
        
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
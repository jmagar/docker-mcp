"""Migration verification utilities for Docker stack transfers."""

import asyncio
import datetime
import json
import shlex
import subprocess
from subprocess import CompletedProcess
from typing import Any

import structlog

logger = structlog.get_logger()


# Removed unused VerificationError; verifier reports issues via structured results.


class MigrationVerifier:
    """Handles verification of Docker stack migrations."""

    def __init__(self):
        self.logger = logger.bind(component="migration_verifier")

    async def _run_remote(
        self, cmd: list[str], description: str = "", timeout: int = 60
    ) -> CompletedProcess[str]:
        """Run a remote SSH/Docker command with timeout and consistent annotation."""
        self.logger.debug("exec_remote", description=description, cmd=cmd)
        return await asyncio.to_thread(
            subprocess.run,  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

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
        inventory = self._create_inventory_template()

        # Validate all paths exist before processing
        await self._validate_source_paths(ssh_cmd, volume_paths)

        # Process each path to build complete inventory
        for path in volume_paths:
            path_inventory = await self._process_single_path(ssh_cmd, path)
            self._add_path_to_inventory(inventory, path, path_inventory)

        self._log_inventory_summary(inventory)
        return inventory

    def _create_inventory_template(self) -> dict[str, Any]:
        """Create the initial inventory structure."""
        return {
            "total_files": 0,
            "total_dirs": 0,
            "total_size": 0,
            "paths": {},
            "critical_files": {},
            "timestamp": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    async def _validate_source_paths(self, ssh_cmd: list[str], volume_paths: list[str]) -> None:
        """Validate that all source paths exist before gathering inventory."""
        for path in volume_paths:
            path_exists_cmd = ssh_cmd + [f"test -e {shlex.quote(path)} && echo 'EXISTS' || echo 'NOT_FOUND'"]
            result = await self._run_remote(path_exists_cmd, "path_existence_check", timeout=30)

            if result.returncode != 0 or "NOT_FOUND" in result.stdout:
                raise ValueError(f"Source path does not exist: {path}")

            if "EXISTS" not in result.stdout:
                raise ValueError(f"Unable to verify source path existence: {path}")

    async def _process_single_path(self, ssh_cmd: list[str], path: str) -> dict[str, Any]:
        """Process a single path to gather its complete inventory."""
        path_inventory: dict[str, Any] = {}

        # Gather basic metrics
        path_inventory.update(await self._gather_path_metrics(ssh_cmd, path))

        # Get file listing
        path_inventory["file_list"] = await self._get_file_listing(ssh_cmd, path)

        # Process critical files with checksums
        critical_files, algorithm = await self._process_critical_files(ssh_cmd, path)
        path_inventory["critical_files"] = critical_files
        path_inventory["checksum_algorithm"] = algorithm

        return path_inventory

    async def _gather_path_metrics(self, ssh_cmd: list[str], path: str) -> dict[str, Any]:
        """Gather basic metrics (file count, dir count, size) for a path."""
        metrics = {}

        # Get file count
        file_count_cmd = ssh_cmd + [f"find {shlex.quote(path)} -type f 2>/dev/null | wc -l"]
        result = await self._run_remote(file_count_cmd, "file_count", timeout=60)
        metrics["file_count"] = int(result.stdout.strip()) if result.returncode == 0 else 0

        # Get directory count
        dir_count_cmd = ssh_cmd + [f"find {shlex.quote(path)} -type d 2>/dev/null | wc -l"]
        result = await self._run_remote(dir_count_cmd, "dir_count", timeout=60)
        metrics["dir_count"] = int(result.stdout.strip()) if result.returncode == 0 else 0

        # Get total size in bytes
        size_cmd = ssh_cmd + [f"du -sb {shlex.quote(path)} 2>/dev/null | cut -f1"]
        result = await self._run_remote(size_cmd, "size_check", timeout=60)
        metrics["total_size"] = int(result.stdout.strip()) if result.returncode == 0 else 0

        return metrics

    async def _get_file_listing(self, ssh_cmd: list[str], path: str) -> list[str]:
        """Get sorted file listing for a path."""
        file_list_cmd = ssh_cmd + [
            f"find {shlex.quote(path)} -type f -printf '%P\\n' 2>/dev/null | sort"
        ]
        result = await self._run_remote(file_list_cmd, "file_listing", timeout=60)
        return result.stdout.strip().split("\n") if result.returncode == 0 else []

    async def _process_critical_files(self, ssh_cmd: list[str], path: str) -> tuple[dict[str, Any], str]:
        """Find and checksum critical files, with SHA256 preferred over MD5."""
        critical_files: dict[str, Any] = {}

        # Try SHA256 first for better integrity verification
        algorithm, result = await self._try_checksum_algorithm(ssh_cmd, path, "sha256")

        # Fallback to MD5 if SHA256 fails
        if result.returncode != 0 or not result.stdout.strip():
            algorithm, result = await self._try_checksum_algorithm(ssh_cmd, path, "md5")

        # Parse checksum results
        if result.returncode == 0 and result.stdout.strip():
            critical_files = self._parse_checksum_output(result.stdout, path)
            # Update algorithm in each critical file entry
            for file_info in critical_files.values():
                file_info["algorithm"] = algorithm

        return critical_files, algorithm

    async def _try_checksum_algorithm(self, ssh_cmd: list[str], path: str, algorithm: str) -> tuple[str, Any]:
        """Try to run checksum command with specified algorithm."""
        checksum_cmd = f"{algorithm}sum" if algorithm == "sha256" else "md5sum"
        cmd = ssh_cmd + [
            f"find {shlex.quote(path)} -type f \\( -name '*.db' -o -name '*.sqlite*' -o -name 'config.*' -o -name '*.conf' -o -name '*.json' -o -name '*.xml' -o -name '*.yml' -o -name '*.yaml' \\) -exec {checksum_cmd} {{}} + 2>/dev/null"
        ]
        result = await self._run_remote(cmd, f"critical_files_{algorithm}", timeout=300)
        return algorithm, result

    def _parse_checksum_output(self, stdout: str, base_path: str) -> dict[str, Any]:
        """Parse checksum command output into critical files dictionary."""
        critical_files: dict[str, Any] = {}
        path_normalized = base_path.rstrip("/")

        for line in stdout.strip().split("\n"):
            if line:
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    checksum, filepath = parts
                    # Store relative path (handle path normalization properly)
                    if filepath.startswith(path_normalized + "/"):
                        rel_path = filepath[len(path_normalized) + 1 :]
                    else:
                        rel_path = filepath
                    critical_files[rel_path] = {
                        "checksum": checksum,
                        "algorithm": "",  # Will be updated by caller
                        "full_path": filepath
                    }
        return critical_files

    def _add_path_to_inventory(self, inventory: dict[str, Any], path: str, path_inventory: dict[str, Any]) -> None:
        """Add a single path's inventory to the overall inventory."""
        inventory["paths"][path] = path_inventory
        inventory["total_files"] += path_inventory["file_count"]
        inventory["total_dirs"] += path_inventory["dir_count"]
        inventory["total_size"] += path_inventory["total_size"]
        inventory["critical_files"].update(path_inventory["critical_files"])

    def _log_inventory_summary(self, inventory: dict[str, Any]) -> None:
        """Log summary of created inventory."""
        self.logger.info(
            "Created source inventory",
            total_files=inventory["total_files"],
            total_dirs=inventory["total_dirs"],
            total_size=inventory["total_size"],
            critical_files=len(inventory["critical_files"]),
        )

    async def verify_migration_completeness(
        self,
        ssh_cmd: list[str],
        source_inventory: dict[str, Any],
        target_path: str,
    ) -> dict[str, Any]:
        """Verify all data was transferred correctly by comparing source inventory to target.

        Args:
            ssh_cmd: SSH command parts for target host execution
            source_inventory: Complete inventory created before migration
            target_path: Full target path where data was extracted

        Returns:
            Dictionary containing verification results
        """
        verification = self._create_migration_verification_template(source_inventory)

        # Gather target metrics and file listing
        await self._gather_target_metrics(ssh_cmd, target_path, verification)

        # Compare source and target to find discrepancies
        await self._compare_file_listings(ssh_cmd, target_path, source_inventory, verification)
        self._calculate_match_percentages(source_inventory, verification)

        # Verify critical files with checksums
        await self._verify_critical_files(ssh_cmd, target_path, source_inventory, verification)

        # Analyze results and collect issues
        self._analyze_verification_results(source_inventory, verification)

        self._log_verification_summary(verification)
        return verification

    def _create_migration_verification_template(self, source_inventory: dict[str, Any]) -> dict[str, Any]:
        """Create the initial verification result structure."""
        return {
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
            "issues": [],
        }

    async def _gather_target_metrics(self, ssh_cmd: list[str], target_path: str, verification: dict[str, Any]) -> None:
        """Gather basic metrics from the target path."""
        # File count
        file_count_cmd = ssh_cmd + [f"find {shlex.quote(target_path)} -type f 2>/dev/null | wc -l"]
        result = await self._run_remote(file_count_cmd, "file_count", timeout=300)
        verification["data_transfer"]["files_found"] = int(result.stdout.strip()) if result.returncode == 0 else 0

        # Directory count
        dir_count_cmd = ssh_cmd + [f"find {shlex.quote(target_path)} -type d 2>/dev/null | wc -l"]
        result = await self._run_remote(dir_count_cmd, "dir_count", timeout=300)
        verification["data_transfer"]["dirs_found"] = int(result.stdout.strip()) if result.returncode == 0 else 0

        # Total size
        size_cmd = ssh_cmd + [f"du -sb {shlex.quote(target_path)} 2>/dev/null | cut -f1"]
        result = await self._run_remote(size_cmd, "size_check", timeout=300)
        verification["data_transfer"]["size_found"] = int(result.stdout.strip()) if result.returncode == 0 else 0

    async def _compare_file_listings(self, ssh_cmd: list[str], target_path: str, source_inventory: dict[str, Any], verification: dict[str, Any]) -> None:
        """Compare source and target file listings to find missing files."""
        # Get target file listing
        file_list_cmd = ssh_cmd + [
            f"find {shlex.quote(target_path)} -type f -printf '%P\\n' 2>/dev/null | sort"
        ]
        result = await self._run_remote(file_list_cmd, "file_listing", timeout=300)
        target_file_list = (
            result.stdout.strip().split("\n")
            if result.returncode == 0 and result.stdout.strip()
            else []
        )

        # Build source file set from all paths
        source_files = set()
        for path_data in source_inventory["paths"].values():
            source_files.update(path_data.get("file_list", []))

        # Find missing files
        target_file_set = set(target_file_list)
        missing_files = source_files - target_file_set
        verification["data_transfer"]["missing_files"] = sorted(missing_files)

    def _calculate_match_percentages(self, source_inventory: dict[str, Any], verification: dict[str, Any]) -> None:
        """Calculate file and size match percentages."""
        target_files = verification["data_transfer"]["files_found"]
        target_size = verification["data_transfer"]["size_found"]

        if source_inventory["total_files"] > 0:
            verification["data_transfer"]["file_match_percentage"] = (
                target_files / source_inventory["total_files"] * 100
            )

        if source_inventory["total_size"] > 0:
            verification["data_transfer"]["size_match_percentage"] = (
                target_size / source_inventory["total_size"] * 100
            )

    async def _verify_critical_files(self, ssh_cmd: list[str], target_path: str, source_inventory: dict[str, Any], verification: dict[str, Any]) -> None:
        """Verify critical files using checksums."""
        critical_files_verified: dict[str, Any] = {}

        for rel_path, file_info in source_inventory["critical_files"].items():
            verification_result = await self._verify_single_critical_file(
                ssh_cmd, target_path, rel_path, file_info
            )
            critical_files_verified[rel_path] = verification_result

        verification["data_transfer"]["critical_files_verified"] = critical_files_verified

    async def _verify_single_critical_file(self, ssh_cmd: list[str], target_path: str, rel_path: str, file_info: dict[str, Any] | str) -> dict[str, Any]:
        """Verify a single critical file's checksum."""
        target_file_path = f"{target_path.rstrip('/')}/{rel_path.lstrip('/')}"
        qfile = shlex.quote(target_file_path)

        # Handle both old (string) and new (dict) checksum formats for backward compatibility
        if isinstance(file_info, str):
            source_checksum = file_info
            algorithm = "md5"  # Default for legacy format
        else:
            source_checksum = file_info["checksum"]
            algorithm = file_info["algorithm"]

        # Use the same algorithm that was used for source checksums
        if algorithm == "sha256":
            checksum_cmd = ssh_cmd + [f"sha256sum {qfile} 2>/dev/null | cut -d' ' -f1"]
        else:
            checksum_cmd = ssh_cmd + [f"md5sum {qfile} 2>/dev/null | cut -d' ' -f1"]

        result = await self._run_remote(checksum_cmd, f"checksum_{algorithm}", timeout=300)

        if result.returncode == 0 and result.stdout.strip():
            target_checksum = result.stdout.strip()
            return {
                "verified": source_checksum == target_checksum,
                "source_checksum": source_checksum,
                "target_checksum": target_checksum,
                "algorithm": algorithm,
            }
        else:
            return {
                "verified": False,
                "source_checksum": source_checksum,
                "target_checksum": None,
                "algorithm": algorithm,
                "error": "File not found or inaccessible",
            }

    def _analyze_verification_results(self, source_inventory: dict[str, Any], verification: dict[str, Any]) -> None:
        """Analyze verification results and collect issues."""
        issues: list[str] = []
        data_transfer = verification["data_transfer"]
        critical_files_verified = data_transfer["critical_files_verified"]

        # Check file count mismatch
        target_files = data_transfer["files_found"]
        if target_files != source_inventory["total_files"]:
            diff = target_files - source_inventory["total_files"]
            issues.append(
                f"File count mismatch: {diff:+d} files ({data_transfer['file_match_percentage']:.1f}% match)"
            )

        # Check size mismatch (allow 1% variance for filesystem overhead)
        target_size = data_transfer["size_found"]
        size_variance = (
            abs(target_size - source_inventory["total_size"]) / source_inventory["total_size"] * 100
            if source_inventory["total_size"] > 0
            else 0
        )
        if size_variance > 1.0:
            issues.append(
                f"Size mismatch: {target_size - source_inventory['total_size']:+d} bytes ({data_transfer['size_match_percentage']:.1f}% match)"
            )

        # Check missing files
        missing_files = data_transfer["missing_files"]
        if missing_files:
            issues.append(f"{len(missing_files)} files missing from target")

        # Check critical file verification failures
        failed_critical = [f for f, v in critical_files_verified.items() if not v["verified"]]
        if failed_critical:
            issues.append(f"{len(failed_critical)} critical files failed verification")

        # Update verification results
        verification["issues"] = issues
        verification["data_transfer"]["success"] = len(issues) == 0
        verification["success"] = len(issues) == 0  # Top-level success flag

    def _log_verification_summary(self, verification: dict[str, Any]) -> None:
        """Log verification summary."""
        data_transfer = verification["data_transfer"]
        critical_files_verified = data_transfer["critical_files_verified"]
        failed_critical = [f for f, v in critical_files_verified.items() if not v["verified"]]

        self.logger.info(
            "Migration completeness verification",
            success=verification["success"],
            files_match=f"{data_transfer['file_match_percentage']:.1f}%",
            size_match=f"{data_transfer['size_match_percentage']:.1f}%",
            critical_files_ok=len(critical_files_verified) - len(failed_critical),
            issues=len(verification["issues"]),
        )

    async def _inspect_container(
        self, ssh_cmd: list[str], stack_name: str
    ) -> dict[str, Any] | None:
        """Run docker inspect and return parsed container info."""
        # First, find the actual container name by project label (Docker Compose containers are named like stack-service-N)
        filter_arg = f"label=com.docker.compose.project={shlex.quote(stack_name)}"
        find_cmd = ssh_cmd + [
            f"docker ps --filter {filter_arg} --format '{{{{.Names}}}}' | head -1"
        ]
        find_result = await self._run_remote(find_cmd, "find_container", timeout=60)

        if find_result.returncode != 0 or not find_result.stdout.strip():
            return None

        container_name = find_result.stdout.strip()

        # Now inspect the actual container
        inspect_cmd = ssh_cmd + [f"docker inspect {shlex.quote(container_name)} 2>/dev/null"]
        result = await self._run_remote(inspect_cmd, "inspect_container", timeout=60)

        if result.returncode != 0:
            return None

        try:
            return json.loads(result.stdout)[0]
        except (json.JSONDecodeError, KeyError, IndexError):
            return None

    def _collect_mounts(self, container_info: dict[str, Any]) -> list[str]:
        """Extract actual mount strings from container inspect output."""
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
        """Check if data is accessible inside the container."""
        # Find the actual container name first
        filter_arg = f"label=com.docker.compose.project={shlex.quote(stack_name)}"
        find_cmd = ssh_cmd + [
            f"docker ps --filter {filter_arg} --format '{{{{.Names}}}}' | head -1"
        ]
        find_result = await self._run_remote(find_cmd, "find_container", timeout=60)

        if find_result.returncode != 0 or not find_result.stdout.strip():
            return False

        container_name = find_result.stdout.strip()

        test_cmd = ssh_cmd + [
            f"docker exec {shlex.quote(container_name)} ls /usr/share/nginx/html 2>/dev/null || docker exec {shlex.quote(container_name)} ls / 2>/dev/null"
        ]
        result = await self._run_remote(test_cmd, "test_access", timeout=60)
        return result.returncode == 0

    async def _collect_startup_errors(self, ssh_cmd: list[str], stack_name: str) -> list[str]:
        """Collect startup errors from container logs."""
        # Find the actual container name first
        filter_arg = f"label=com.docker.compose.project={shlex.quote(stack_name)}"
        find_cmd = ssh_cmd + [
            f"docker ps --filter {filter_arg} --format '{{{{.Names}}}}' | head -1"
        ]
        find_result = await self._run_remote(find_cmd, "find_container", timeout=60)

        if find_result.returncode != 0 or not find_result.stdout.strip():
            return [f"No container found for stack '{stack_name}'"]

        container_name = find_result.stdout.strip()

        logs_cmd = ssh_cmd + [
            f"docker logs {shlex.quote(container_name)} --tail 50 2>&1 | grep -i error || true"
        ]
        result = await self._run_remote(logs_cmd, "collect_logs", timeout=60)

        if result.stdout.strip():
            error_lines = [
                line.strip() for line in result.stdout.strip().split("\n") if line.strip()
            ]
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
        verification = self._create_verification_template(expected_volumes)

        # Get container info and check if container exists
        container_info = await self._inspect_container(ssh_cmd, stack_name)
        if not container_info:
            verification["issues"].append(f"Container '{stack_name}' not found")
            verification["container_integration"]["success"] = False
            return verification

        verification["container_integration"]["container_exists"] = True

        # Verify container state and health
        self._verify_container_state(verification, container_info)

        # Verify mount configuration
        self._verify_container_mounts(
            verification, container_info, expected_volumes, expected_appdata_path
        )

        # Test runtime accessibility if container is running
        if verification["container_integration"]["container_running"]:
            await self._verify_runtime_accessibility(verification, ssh_cmd, stack_name)

        # Collect all issues and determine overall success
        self._collect_verification_issues(verification)

        self._log_verification_results(verification)

        return verification

    def _create_verification_template(self, expected_volumes: list[str]) -> dict[str, Any]:
        """Create the initial verification result structure."""
        return {
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
            "issues": [],
        }

    def _verify_container_state(
        self, verification: dict[str, Any], container_info: dict[str, Any]
    ) -> None:
        """Verify container running state and health status."""
        state = container_info.get("State", {})
        verification["container_integration"]["container_running"] = state.get("Running", False)

        health = state.get("Health", {})
        health_status = health.get("Status")
        verification["container_integration"]["health_status"] = health_status
        verification["container_integration"]["container_healthy"] = health_status == "healthy"

    def _verify_container_mounts(
        self,
        verification: dict[str, Any],
        container_info: dict[str, Any],
        expected_volumes: list[str],
        expected_appdata_path: str,
    ) -> None:
        """Verify container mount configuration matches expectations."""
        actual_mounts = self._collect_mounts(container_info)
        verification["container_integration"]["actual_mounts"] = actual_mounts

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
                            if (
                                actual_dest == expected_dest
                                and expected_appdata_path in actual_source
                            ):
                                mount_matches += 1
                                break

        verification["container_integration"]["mount_paths_correct"] = (
            mount_matches == len(expected_volumes) if expected_volumes else True
        )

    async def _verify_runtime_accessibility(
        self, verification: dict[str, Any], ssh_cmd: list[str], stack_name: str
    ) -> None:
        """Test data accessibility and collect startup errors for running containers."""
        verification["container_integration"][
            "data_accessible"
        ] = await self._check_in_container_access(ssh_cmd, stack_name)
        verification["container_integration"][
            "startup_errors"
        ] = await self._collect_startup_errors(ssh_cmd, stack_name)

    def _collect_verification_issues(self, verification: dict[str, Any]) -> None:
        """Collect all verification issues and set overall success status."""
        issues = []
        integration = verification["container_integration"]

        if not integration["container_running"]:
            issues.append("Container is not running")

        if not integration["mount_paths_correct"]:
            issues.append("Container mount paths do not match expected")

        if integration["container_running"] and not integration["data_accessible"]:
            issues.append("Data not accessible inside container")

        if integration["startup_errors"]:
            issues.append(f"Container has {len(integration['startup_errors'])} startup errors")

        health_status = integration["health_status"]
        if health_status and health_status not in ["healthy", "none"]:
            issues.append(f"Container health check failed: {health_status}")

        verification["issues"] = issues
        verification["container_integration"]["success"] = len(issues) == 0

    def _log_verification_results(self, verification: dict[str, Any]) -> None:
        """Log the container integration verification results."""
        integration = verification["container_integration"]
        self.logger.info(
            "Container integration verification",
            success=integration["success"],
            running=integration["container_running"],
            healthy=integration["container_healthy"],
            mounts_correct=integration["mount_paths_correct"],
            data_accessible=integration["data_accessible"],
            issues=len(verification["issues"]),
        )

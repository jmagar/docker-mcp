"""Safety guards and validation for destructive operations."""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import structlog

from .exceptions import DockerMCPError

logger = structlog.get_logger()


class SafetyError(DockerMCPError):
    """Safety validation failed."""

    pass


class MigrationSafety:
    """Safety guards for migration operations to prevent accidental data loss."""

    # Allowed paths for safe deletion operations
    SAFE_DELETE_PATHS = [
        "/tmp",
        "/var/tmp",
        "/opt/migration_temp",
        # Add more safe temporary directories as needed
    ]

    # Paths that should NEVER be deleted
    FORBIDDEN_PATHS = [
        "/",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/proc",
        "/root",
        "/sbin",
        "/sys",
        "/usr",
        "/var/log",
        "/var/lib",
        "/home",
        "/mnt",
        "/opt",  # Top level - subdirs might be ok
    ]

    def __init__(self):
        self.logger = logger.bind(component="migration_safety")
        self.deletion_manifest: list[dict[str, Any]] = []

    def validate_deletion_path(self, file_path: str) -> tuple[bool, str]:
        """Validate that a path is safe to delete.

        Args:
            file_path: Path to validate for deletion

        Returns:
            Tuple of (is_safe: bool, reason: str)
        """
        try:
            # Resolve path to handle symlinks and relative paths
            resolved_path = str(Path(file_path).resolve())

            # Check for forbidden paths
            for forbidden in self.FORBIDDEN_PATHS:
                if resolved_path == forbidden or resolved_path.startswith(forbidden + "/"):
                    return False, f"Path '{resolved_path}' is in forbidden directory '{forbidden}'"

            # Check for parent directory traversal attempts
            if ".." in file_path:
                return False, f"Path '{file_path}' contains parent directory traversal"

            # Check if path is in safe deletion areas
            is_in_safe_area = False
            for safe_path in self.SAFE_DELETE_PATHS:
                if resolved_path.startswith(safe_path + "/"):
                    is_in_safe_area = True
                    break

            # For files outside safe areas, require more validation
            if not is_in_safe_area:
                # Allow specific file extensions in any directory
                if file_path.endswith((".tar.gz", ".tar", ".zip", ".tmp", ".temp", ".migration")):
                    return True, f"File type allowed: {file_path}"

                # Allow specific filenames
                filename = Path(file_path).name
                if filename in ("docker-compose.yml", "docker-compose.yaml"):
                    return True, f"Docker compose file allowed: {file_path}"

                return False, f"Path '{resolved_path}' is not in safe deletion area"

            return True, f"Path validated: {resolved_path}"

        except Exception as e:
            return False, f"Path validation error: {str(e)}"

    def add_to_deletion_manifest(self, file_path: str, operation: str, reason: str) -> None:
        """Add a deletion operation to the manifest for audit trail.

        Args:
            file_path: Path to be deleted
            operation: Type of operation (rm, rm -f, rm -rf, etc.)
            reason: Reason for deletion
        """
        manifest_entry = {
            "path": file_path,
            "operation": operation,
            "reason": reason,
            "timestamp": asyncio.get_event_loop().time(),
            "validated": False,
        }

        # Validate the path
        is_safe, validation_reason = self.validate_deletion_path(file_path)
        manifest_entry["validated"] = is_safe
        manifest_entry["validation_reason"] = validation_reason

        self.deletion_manifest.append(manifest_entry)

        self.logger.info(
            "Added deletion to manifest",
            path=file_path,
            operation=operation,
            safe=is_safe,
            reason=validation_reason,
        )

    def get_deletion_manifest(self) -> list[dict[str, Any]]:
        """Get the current deletion manifest."""
        return self.deletion_manifest.copy()

    def clear_deletion_manifest(self) -> None:
        """Clear the deletion manifest."""
        self.deletion_manifest.clear()

    async def safe_delete_file(
        self, ssh_cmd: list[str], file_path: str, reason: str = "Migration cleanup"
    ) -> tuple[bool, str]:
        """Safely delete a file with validation and audit trail.

        Args:
            ssh_cmd: SSH command parts for remote execution
            file_path: Path to file to delete
            reason: Reason for deletion

        Returns:
            Tuple of (success: bool, message: str)
        """
        # Add to manifest
        self.add_to_deletion_manifest(file_path, "rm -f", reason)

        # Validate path
        is_safe, validation_reason = self.validate_deletion_path(file_path)
        if not is_safe:
            error_msg = f"SAFETY BLOCK: {validation_reason}"
            self.logger.error(
                "File deletion blocked by safety check", path=file_path, reason=validation_reason
            )
            raise SafetyError(error_msg)

        # Proceed with deletion
        delete_cmd = ssh_cmd + [f"rm -f {file_path}"]

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    delete_cmd, check=False, capture_output=True, text=True
                ),
            )

            if result.returncode == 0:
                self.logger.info("File deleted safely", path=file_path, reason=reason)
                return True, f"File deleted: {file_path}"
            else:
                error_msg = f"Deletion failed: {result.stderr}"
                self.logger.error("File deletion failed", path=file_path, error=result.stderr)
                return False, error_msg

        except Exception as e:
            error_msg = f"Deletion error: {str(e)}"
            self.logger.error("File deletion exception", path=file_path, error=str(e))
            return False, error_msg

    def validate_zfs_snapshot_deletion(self, snapshot_name: str) -> tuple[bool, str]:
        """Validate ZFS snapshot deletion to prevent accidental deletion of production snapshots.

        Args:
            snapshot_name: Full ZFS snapshot name (dataset@snapshot)

        Returns:
            Tuple of (is_safe: bool, reason: str)
        """
        if "@" not in snapshot_name:
            return False, "Invalid snapshot format - must contain '@'"

        dataset, snap_name = snapshot_name.split("@", 1)

        # Only allow deletion of migration-specific snapshots
        migration_prefixes = ["migrate_", "migration_", "backup_", "temp_"]
        if not any(snap_name.startswith(prefix) for prefix in migration_prefixes):
            return False, f"Snapshot '{snap_name}' does not appear to be migration-related"

        # Check for suspicious patterns
        if len(snap_name) < 10:  # Migration snapshots should have timestamps
            return False, f"Snapshot name '{snap_name}' is too short - may not be migration-related"

        return True, f"ZFS snapshot deletion validated: {snapshot_name}"

    async def safe_cleanup_archive(
        self, ssh_cmd: list[str], archive_path: str, reason: str = "Migration cleanup"
    ) -> tuple[bool, str]:
        """Safely cleanup archive files with validation.

        Args:
            ssh_cmd: SSH command parts for remote execution
            archive_path: Path to archive file
            reason: Reason for cleanup

        Returns:
            Tuple of (success: bool, message: str)
        """
        # Validate archive path
        if not archive_path.endswith((".tar.gz", ".tar", ".zip")):
            return False, f"Not an archive file: {archive_path}"

        # Use safe delete
        return await self.safe_delete_file(ssh_cmd, archive_path, reason)

    def create_safety_report(self) -> dict[str, Any]:
        """Create a safety report of all deletion operations."""
        total_deletions = len(self.deletion_manifest)
        validated_deletions = len([m for m in self.deletion_manifest if m["validated"]])
        blocked_deletions = total_deletions - validated_deletions

        return {
            "total_deletion_attempts": total_deletions,
            "validated_deletions": validated_deletions,
            "blocked_deletions": blocked_deletions,
            "safety_rate": validated_deletions / total_deletions * 100
            if total_deletions > 0
            else 100,
            "manifest": self.deletion_manifest,
            "safe_paths": self.SAFE_DELETE_PATHS,
            "forbidden_paths": self.FORBIDDEN_PATHS,
        }

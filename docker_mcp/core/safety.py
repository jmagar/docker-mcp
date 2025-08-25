"""Safety guards and validation for destructive operations."""

import asyncio
import os
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
        """
        Initialize MigrationSafety.
        
        Binds a component-scoped logger for migration safety operations and creates an empty
        deletion_manifest used to record attempted deletions and their validation metadata.
        """
        self.logger = logger.bind(component="migration_safety")
        self.deletion_manifest: list[dict[str, Any]] = []
    
    def validate_deletion_path(self, file_path: str) -> tuple[bool, str]:
        """
        Validate whether a filesystem path is safe to delete.
        
        Performs several safety checks and returns (is_safe, reason):
        - Resolves the path (follows symlinks and expands relative segments) before checks.
        - Rejects any path that equals or is nested under entries in self.FORBIDDEN_PATHS.
        - Rejects paths containing parent-directory traversal ("..").
        - Considers a path safe if it resides under one of self.SAFE_DELETE_PATHS.
        - For paths outside safe areas, allows only certain archive/temp file extensions ('.tar.gz', '.tar', '.zip', '.tmp', '.temp', '.migration') or filenames 'docker-compose.yml' / 'docker-compose.yaml'.
        - On success returns (True, explanatory message); on failure returns (False, explanatory reason).
        - Catches and reports unexpected errors as a failed validation.
        
        Parameters:
            file_path (str): The filesystem path to validate.
        
        Returns:
            tuple[bool, str]: (is_safe, reason) where `is_safe` indicates whether deletion is allowed and `reason` explains the decision.
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
                if file_path.endswith(('.tar.gz', '.tar', '.zip', '.tmp', '.temp', '.migration')):
                    return True, f"File type allowed: {file_path}"
                
                # Allow specific filenames
                filename = Path(file_path).name
                if filename in ('docker-compose.yml', 'docker-compose.yaml'):
                    return True, f"Docker compose file allowed: {file_path}"
                
                return False, f"Path '{resolved_path}' is not in safe deletion area"
            
            return True, f"Path validated: {resolved_path}"
            
        except Exception as e:
            return False, f"Path validation error: {str(e)}"
    
    def add_to_deletion_manifest(self, file_path: str, operation: str, reason: str) -> None:
        """
        Record a planned deletion in the migration deletion manifest and validate its path.
        
        Adds an entry to the instance's deletion_manifest for auditing, including the provided
        path, operation, reason, an event-loop timestamp, and the result of validate_deletion_path.
        The manifest entry will contain these keys: "path", "operation", "reason", "timestamp",
        "validated" (bool) and "validation_reason" (str).
        
        Parameters:
            file_path (str): Filesystem path targeted for deletion; will be validated before recording.
            operation (str): Deletion command/operation label (e.g., "rm -f", "rm -rf").
            reason (str): Human-readable rationale for the deletion.
        """
        manifest_entry = {
            "path": file_path,
            "operation": operation,
            "reason": reason,
            "timestamp": asyncio.get_event_loop().time(),
            "validated": False
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
            reason=validation_reason
        )
    
    def get_deletion_manifest(self) -> list[dict[str, Any]]:
        """Get the current deletion manifest."""
        return self.deletion_manifest.copy()
    
    def clear_deletion_manifest(self) -> None:
        """Clear the deletion manifest."""
        self.deletion_manifest.clear()
    
    async def safe_delete_file(self, ssh_cmd: list[str], file_path: str, reason: str = "Migration cleanup") -> tuple[bool, str]:
        """
        Delete a remote file after validating its path and recording the attempt in the deletion manifest.
        
        This asynchronous method adds a manifest entry for the requested deletion, validates the target path with MigrationSafety.validate_deletion_path, and — if validated — executes the deletion by appending an `rm -f <file_path>` command to the provided SSH command and running it in an executor. If the validation fails, a SafetyError is raised and the deletion is blocked. Returns a (success, message) tuple describing the outcome.
        
        Parameters:
            file_path (str): Filesystem path to delete on the remote host.
            reason (str, optional): Human-readable reason stored in the manifest and used in logs. Defaults to "Migration cleanup".
        
        Returns:
            tuple[bool, str]: (True, success_message) if deletion succeeded; (False, error_message) on failure.
        
        Raises:
            SafetyError: If validate_deletion_path determines the path is unsafe and blocks deletion.
        """
        # Add to manifest
        self.add_to_deletion_manifest(file_path, "rm -f", reason)
        
        # Validate path
        is_safe, validation_reason = self.validate_deletion_path(file_path)
        if not is_safe:
            error_msg = f"SAFETY BLOCK: {validation_reason}"
            self.logger.error("File deletion blocked by safety check", path=file_path, reason=validation_reason)
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
        """
        Return whether a ZFS snapshot name is safe to delete and a reason.
        
        Validates that `snapshot_name` is in the `dataset@snapshot` form, that the snapshot portion
        appears migration-related (must start with one of: "migrate_", "migration_", "backup_", "temp_")
        and that the snapshot name is sufficiently long (minimum 10 characters) to imply a timestamped
        migration snapshot. Returns (True, success_message) when deletion is allowed, otherwise
        (False, explanatory_reason).
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
    
    async def safe_cleanup_archive(self, ssh_cmd: list[str], archive_path: str, reason: str = "Migration cleanup") -> tuple[bool, str]:
        """
        Safely remove an archive file after validating its extension and delegating to safe_delete_file.
        
        Validates that archive_path ends with one of the allowed archive extensions ('.tar.gz', '.tar', '.zip'); if validation fails, returns (False, error_message). Otherwise delegates deletion to self.safe_delete_file.
        
        Parameters:
            archive_path (str): Remote path to the archive file to remove; must end with a supported archive extension.
            reason (str): Human-readable reason for the cleanup (used in the deletion manifest and logs).
        
        Returns:
            tuple[bool, str]: (success, message) where success is True when the archive was deleted, False otherwise.
        """
        # Validate archive path
        if not archive_path.endswith(('.tar.gz', '.tar', '.zip')):
            return False, f"Not an archive file: {archive_path}"
        
        # Use safe delete
        return await self.safe_delete_file(ssh_cmd, archive_path, reason)
    
    def create_safety_report(self) -> dict[str, Any]:
        """
        Return an aggregate safety report summarizing deletion attempts tracked in the manifest.
        
        The report includes counts of total, validated, and blocked deletion attempts, a safety rate
        (as a percentage of validated over total attempts; 100 when no attempts), the full manifest,
        and the configured safe and forbidden base paths.
        
        Returns:
            dict[str, Any]: {
                "total_deletion_attempts": int,
                "validated_deletions": int,
                "blocked_deletions": int,
                "safety_rate": float,         # percentage (0-100)
                "manifest": list[dict],       # the deletion_manifest as stored
                "safe_paths": list[str],      # SAFE_DELETE_PATHS
                "forbidden_paths": list[str]  # FORBIDDEN_PATHS
            }
        """
        total_deletions = len(self.deletion_manifest)
        validated_deletions = len([m for m in self.deletion_manifest if m["validated"]])
        blocked_deletions = total_deletions - validated_deletions
        
        return {
            "total_deletion_attempts": total_deletions,
            "validated_deletions": validated_deletions,
            "blocked_deletions": blocked_deletions,
            "safety_rate": validated_deletions / total_deletions * 100 if total_deletions > 0 else 100,
            "manifest": self.deletion_manifest,
            "safe_paths": self.SAFE_DELETE_PATHS,
            "forbidden_paths": self.FORBIDDEN_PATHS
        }
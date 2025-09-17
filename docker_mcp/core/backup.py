"""Backup and restore operations for migration rollback capability."""

import asyncio
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from ..utils import build_ssh_command, format_size
from .config_loader import DockerHost
from .exceptions import DockerMCPError
from .safety import MigrationSafety

logger = structlog.get_logger()

# Timeout constants for backup operations
BACKUP_TIMEOUT_SECONDS = 300  # 5 minutes for backup operations
CHECK_TIMEOUT_SECONDS = 30  # 30 seconds for status checks


class BackupInfo(BaseModel):
    """Backup information with validation and schema guarantees."""

    success: bool = Field(description="Whether backup was successful")
    type: str = Field(description="Type of backup (directory)")
    host_id: str = Field(description="Host identifier where backup was created")
    source_path: str | None = Field(
        default=None, description="Source directory path for directory backups"
    )
    backup_path: str | None = Field(
        default=None, description="Path to backup file (for directory backups)"
    )
    backup_size: int = Field(description="Size of backup in bytes")
    backup_size_human: str = Field(description="Human-readable backup size")
    timestamp: str = Field(description="Timestamp when backup was created")
    reason: str = Field(description="Reason for creating the backup")
    stack_name: str = Field(description="Stack name associated with the backup")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class BackupError(DockerMCPError):
    """Backup operation failed."""

    pass


class BackupManager:
    """Manager for creating and managing backups during migrations."""

    def __init__(self):
        self.logger = logger.bind(component="backup_manager")
        self.safety = MigrationSafety()
        self.backups: list[BackupInfo] = []

    async def backup_directory(
        self,
        host: DockerHost,
        source_path: str,
        stack_name: str,
        backup_reason: str = "Pre-migration backup",
    ) -> BackupInfo:
        """Create a backup of a directory using tar.

        Args:
            host: Host configuration
            source_path: Directory path to backup
            stack_name: Stack name for backup naming
            backup_reason: Reason for backup (for audit trail)

        Returns:
            Backup information dictionary
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backup_{stack_name}_{timestamp}.tar.gz"
        remote_tmp_dir = "/tmp/docker_mcp_backups"  # noqa: S108 - Remote temp dir, not local
        backup_path = f"{remote_tmp_dir}/{backup_filename}"

        ssh_cmd = build_ssh_command(host)

        # Check if source path exists
        check_cmd = ssh_cmd + [
            "sh",
            "-lc",
            f"test -d {shlex.quote(source_path)} && echo 'EXISTS' || echo 'NOT_FOUND'",
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                check_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=CHECK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.error(
                "Source path check timed out",
                host_id=host.hostname,
                source_path=source_path,
                timeout_seconds=CHECK_TIMEOUT_SECONDS,
            )
            raise BackupError(
                f"Source path check timed out after {CHECK_TIMEOUT_SECONDS} seconds"
            ) from None

        if "NOT_FOUND" in result.stdout:
            self.logger.info("Source path does not exist, no backup needed", path=source_path)
            return BackupInfo(
                success=True,
                type="directory",
                host_id=host.hostname,
                source_path=source_path,
                backup_path=None,
                backup_size=0,
                backup_size_human="0 B",
                timestamp=datetime.now(UTC).strftime("%Y%m%d_%H%M%S"),
                reason=backup_reason,
                stack_name=stack_name,
                created_at=datetime.now(UTC).isoformat(),
            )

        # Create backup using tar
        backup_cmd = ssh_cmd + [
            "sh",
            "-lc",
            (
                f"mkdir -p {shlex.quote(remote_tmp_dir)} && "
                f"cd {shlex.quote(str(Path(source_path).parent))} && "
                f"tar czf {shlex.quote(backup_path)} {shlex.quote(Path(source_path).name)} "
                "2>/dev/null && echo 'BACKUP_SUCCESS' || echo 'BACKUP_FAILED'"
            ),
        ]

        self.logger.info(
            "Creating directory backup",
            source=source_path,
            backup=backup_path,
            host=host.hostname,
            reason=backup_reason,
        )

        try:
            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                backup_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=BACKUP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.error(
                "Backup operation timed out",
                host_id=host.hostname,
                source_path=source_path,
                backup_path=backup_path,
                timeout_seconds=BACKUP_TIMEOUT_SECONDS,
            )
            # Try to clean up partial backup
            cleanup_cmd = ssh_cmd + ["rm", "-f", shlex.quote(backup_path)]
            try:
                await asyncio.to_thread(
                    subprocess.run,  # nosec B603
                    cleanup_cmd,
                    capture_output=True,
                    check=False,
                    timeout=CHECK_TIMEOUT_SECONDS,
                )
            except Exception as cleanup_err:
                logger.warning("Failed to cleanup partial backup", error=str(cleanup_err))
            raise BackupError(
                f"Backup operation timed out after {BACKUP_TIMEOUT_SECONDS} seconds"
            ) from None

        if "BACKUP_FAILED" in result.stdout or result.returncode != 0:
            raise BackupError(f"Failed to create backup: {result.stderr}")

        # Get backup size
        size_cmd = ssh_cmd + [
            "sh",
            "-lc",
            f"stat -c%s {shlex.quote(backup_path)} 2>/dev/null || echo '0'",
        ]
        backup_size = 0  # Initialize to prevent UnboundLocalError
        try:
            size_result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                size_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=CHECK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Backup size check timed out",
                host_id=host.hostname,
                backup_path=backup_path,
                timeout_seconds=CHECK_TIMEOUT_SECONDS,
            )
            backup_size = 0  # Default to 0 if we can't check size
        except Exception as e:
            logger.warning(
                "Backup size check failed",
                host_id=host.hostname,
                backup_path=backup_path,
                error=str(e),
            )
            backup_size = 0  # Default to 0 on any other error
        else:
            backup_size = (
                int(size_result.stdout.strip()) if size_result.stdout.strip().isdigit() else 0
            )

        # Create backup record
        backup_info = BackupInfo(
            success=True,
            type="directory",
            host_id=host.hostname,
            source_path=source_path,
            backup_path=backup_path,
            backup_size=backup_size,
            backup_size_human=format_size(backup_size),
            timestamp=timestamp,
            reason=backup_reason,
            stack_name=stack_name,
            created_at=datetime.now(UTC).isoformat(),
        )

        self.backups.append(backup_info)

        self.logger.info(
            "Directory backup created successfully",
            backup=backup_path,
            size=backup_info.backup_size_human,
            host=host.hostname,
        )

        return backup_info


    async def restore_directory_backup(
        self, host: DockerHost, backup_info: BackupInfo
    ) -> tuple[bool, str]:
        """Restore a directory from backup.

        Args:
            host: Host configuration
            backup_info: Backup information from backup_directory()

        Returns:
            Tuple of (success: bool, message: str)
        """
        if backup_info.type != "directory":
            return False, f"Not a directory backup: {backup_info.type}"

        backup_path = backup_info.backup_path
        source_path = backup_info.source_path

        if not backup_path:
            return True, "No backup to restore (directory didn't exist)"

        if not source_path:
            return False, "No source path specified in backup info"

        ssh_cmd = build_ssh_command(host)

        # Remove current directory and restore from backup
        restore_cmd = ssh_cmd + [
            "sh",
            "-c",
            (
                f"rm -rf {shlex.quote(source_path)} && "
                f"cd {shlex.quote(str(Path(source_path).parent))} && "
                f"tar xzf {shlex.quote(backup_path)} && "
                f"echo 'RESTORE_SUCCESS' || echo 'RESTORE_FAILED'"
            ),
        ]

        self.logger.info(
            "Restoring directory from backup",
            backup=backup_path,
            target=source_path,
            host=host.hostname,
        )

        try:
            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603
                restore_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=BACKUP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.error(
                "Backup restore operation timed out",
                host_id=host.hostname,
                backup_path=backup_path,
                source_path=source_path,
                timeout_seconds=BACKUP_TIMEOUT_SECONDS,
            )
            raise BackupError(
                f"Backup restore operation timed out after {BACKUP_TIMEOUT_SECONDS} seconds"
            ) from None

        if "RESTORE_FAILED" in result.stdout or result.returncode != 0:
            return False, f"Failed to restore backup: {result.stderr}"

        return True, f"Directory restored from backup: {backup_path}"


    async def cleanup_backup(self, host: DockerHost, backup_info: BackupInfo) -> tuple[bool, str]:
        """Clean up a backup after successful migration.

        Args:
            host: Host configuration
            backup_info: Backup information

        Returns:
            Tuple of (success: bool, message: str)
        """
        if backup_info.type == "directory":
            if not backup_info.backup_path:
                return True, "No backup file to clean up"

            success, message = await self.safety.safe_delete_file(
                build_ssh_command(host),
                backup_info.backup_path,
                f"Cleanup backup for {backup_info.stack_name}",
            )
            return success, message


        return False, f"Unknown backup type: {backup_info.type}"


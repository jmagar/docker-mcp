"""Backup and restore operations for migration rollback capability."""

import asyncio
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from ..utils import build_ssh_command, format_size
from .config_loader import DockerHost
from .exceptions import DockerMCPError
from .safety import MigrationSafety

logger = structlog.get_logger()


class BackupError(DockerMCPError):
    """Backup operation failed."""

    pass


class BackupManager:
    """Manager for creating and managing backups during migrations."""

    def __init__(self):
        self.logger = logger.bind(component="backup_manager")
        self.safety = MigrationSafety()
        self.backups: list[dict[str, Any]] = []

    async def backup_directory(
        self,
        host: DockerHost,
        source_path: str,
        stack_name: str,
        backup_reason: str = "Pre-migration backup",
    ) -> dict[str, Any]:
        """Create a backup of a directory using tar.

        Args:
            host: Host configuration
            source_path: Directory path to backup
            stack_name: Stack name for backup naming
            backup_reason: Reason for backup (for audit trail)

        Returns:
            Backup information dictionary
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backup_{stack_name}_{timestamp}.tar.gz"
        temp_dir = tempfile.mkdtemp(prefix="docker_mcp_backup_")
        backup_path = f"{temp_dir}/{backup_filename}"

        ssh_cmd = build_ssh_command(host)

        # Check if source path exists
        check_cmd = ssh_cmd + [f"test -d {shlex.quote(source_path)} && echo 'EXISTS' || echo 'NOT_FOUND'"]
        result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            check_cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if "NOT_FOUND" in result.stdout:
            self.logger.info("Source path does not exist, no backup needed", path=source_path)
            return {
                "success": True,
                "backup_path": None,
                "backup_size": 0,
                "message": f"No existing data at {source_path} - backup skipped",
            }

        # Create backup using tar
        backup_cmd = ssh_cmd + [
            f"cd {Path(source_path).parent} && "
            f"tar czf {backup_path} {Path(source_path).name} 2>/dev/null && "
            f"echo 'BACKUP_SUCCESS' || echo 'BACKUP_FAILED'"
        ]

        self.logger.info(
            "Creating directory backup",
            source=source_path,
            backup=backup_path,
            host=host.hostname,
            reason=backup_reason,
        )

        result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            backup_cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if "BACKUP_FAILED" in result.stdout or result.returncode != 0:
            raise BackupError(f"Failed to create backup: {result.stderr}")

        # Get backup size
        size_cmd = ssh_cmd + [f"stat -c%s {shlex.quote(backup_path)} 2>/dev/null || echo '0'"]
        size_result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            size_cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        backup_size = int(size_result.stdout.strip()) if size_result.stdout.strip().isdigit() else 0

        # Create backup record
        backup_info = {
            "success": True,
            "type": "directory",
            "host_id": host.hostname,
            "source_path": source_path,
            "backup_path": backup_path,
            "backup_size": backup_size,
            "backup_size_human": format_size(backup_size),
            "timestamp": timestamp,
            "reason": backup_reason,
            "stack_name": stack_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self.backups.append(backup_info)

        self.logger.info(
            "Directory backup created successfully",
            backup=backup_path,
            size=backup_info["backup_size_human"],
            host=host.hostname,
        )

        return backup_info

    async def backup_zfs_dataset(
        self,
        host: DockerHost,
        dataset: str,
        stack_name: str,
        backup_reason: str = "Pre-migration ZFS backup",
    ) -> dict[str, Any]:
        """Create a ZFS snapshot backup.

        Args:
            host: Host configuration
            dataset: ZFS dataset to backup
            stack_name: Stack name for backup naming
            backup_reason: Reason for backup

        Returns:
            Backup information dictionary
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"backup_{stack_name}_{timestamp}"
        full_snapshot = f"{dataset}@{snapshot_name}"

        ssh_cmd = build_ssh_command(host)

        # Create ZFS snapshot
        snap_cmd = ssh_cmd + [f"zfs snapshot {full_snapshot}"]

        self.logger.info(
            "Creating ZFS backup snapshot",
            dataset=dataset,
            snapshot=full_snapshot,
            host=host.hostname,
            reason=backup_reason,
        )

        result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            snap_cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise BackupError(f"Failed to create ZFS backup snapshot: {result.stderr}")

        # Get snapshot size
        size_cmd = ssh_cmd + [f"zfs list -H -o used {full_snapshot} 2>/dev/null || echo '0'"]
        size_result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            size_cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        # Parse ZFS size format (e.g., "1.2G", "512M", "4K")
        size_str = size_result.stdout.strip()
        backup_size = self._parse_zfs_size(size_str)

        # Create backup record
        backup_info = {
            "success": True,
            "type": "zfs_snapshot",
            "host_id": host.hostname,
            "dataset": dataset,
            "snapshot_name": full_snapshot,
            "backup_size": backup_size,
            "backup_size_human": size_str,
            "timestamp": timestamp,
            "reason": backup_reason,
            "stack_name": stack_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self.backups.append(backup_info)

        self.logger.info(
            "ZFS backup snapshot created successfully",
            snapshot=full_snapshot,
            size=size_str,
            host=host.hostname,
        )

        return backup_info

    async def restore_directory_backup(
        self, host: DockerHost, backup_info: dict[str, Any]
    ) -> tuple[bool, str]:
        """Restore a directory from backup.

        Args:
            host: Host configuration
            backup_info: Backup information from backup_directory()

        Returns:
            Tuple of (success: bool, message: str)
        """
        if backup_info["type"] != "directory":
            return False, f"Not a directory backup: {backup_info['type']}"

        backup_path = backup_info["backup_path"]
        source_path = backup_info["source_path"]

        if not backup_path:
            return True, "No backup to restore (directory didn't exist)"

        ssh_cmd = build_ssh_command(host)

        # Remove current directory and restore from backup
        restore_cmd = ssh_cmd + [
            f"rm -rf {source_path} && "
            f"cd {Path(source_path).parent} && "
            f"tar xzf {backup_path} && "
            f"echo 'RESTORE_SUCCESS' || echo 'RESTORE_FAILED'"
        ]

        self.logger.info(
            "Restoring directory from backup",
            backup=backup_path,
            target=source_path,
            host=host.hostname,
        )

        result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            restore_cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if "RESTORE_FAILED" in result.stdout or result.returncode != 0:
            return False, f"Failed to restore backup: {result.stderr}"

        return True, f"Directory restored from backup: {backup_path}"

    async def restore_zfs_backup(
        self, host: DockerHost, backup_info: dict[str, Any]
    ) -> tuple[bool, str]:
        """Restore a ZFS dataset from snapshot backup.

        Args:
            host: Host configuration
            backup_info: Backup information from backup_zfs_dataset()

        Returns:
            Tuple of (success: bool, message: str)
        """
        if backup_info["type"] != "zfs_snapshot":
            return False, f"Not a ZFS backup: {backup_info['type']}"

        snapshot_name = backup_info["snapshot_name"]
        dataset = backup_info["dataset"]

        ssh_cmd = build_ssh_command(host)

        # Rollback to snapshot
        rollback_cmd = ssh_cmd + [f"zfs rollback {snapshot_name}"]

        self.logger.info(
            "Rolling back ZFS dataset to backup snapshot",
            snapshot=snapshot_name,
            dataset=dataset,
            host=host.hostname,
        )

        result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            rollback_cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return False, f"Failed to rollback ZFS dataset: {result.stderr}"

        return True, f"ZFS dataset rolled back to snapshot: {snapshot_name}"

    async def cleanup_backup(
        self, host: DockerHost, backup_info: dict[str, Any]
    ) -> tuple[bool, str]:
        """Clean up a backup after successful migration.

        Args:
            host: Host configuration
            backup_info: Backup information

        Returns:
            Tuple of (success: bool, message: str)
        """
        if backup_info["type"] == "directory":
            if not backup_info["backup_path"]:
                return True, "No backup file to clean up"

            success, message = await self.safety.safe_delete_file(
                build_ssh_command(host),
                backup_info["backup_path"],
                f"Cleanup backup for {backup_info['stack_name']}",
            )
            return success, message

        elif backup_info["type"] == "zfs_snapshot":
            # For ZFS snapshots, we might want to keep them longer
            # Or provide option to delete
            return True, f"ZFS backup snapshot retained: {backup_info['snapshot_name']}"

        return False, f"Unknown backup type: {backup_info['type']}"

    def _parse_zfs_size(self, size_str: str) -> int:
        """Parse ZFS size string to bytes.

        Args:
            size_str: Size string like "1.2G", "512M", "4K"

        Returns:
            Size in bytes
        """
        size_str = size_str.strip().upper()
        if not size_str or size_str == "0":
            return 0

        # Extract number and unit
        import re

        match = re.match(r"([0-9.]+)([KMGTPE]?)", size_str)
        if not match:
            return 0

        number = float(match.group(1))
        unit = match.group(2)

        # Convert to bytes
        multipliers = {
            "": 1,
            "K": 1024,
            "M": 1024**2,
            "G": 1024**3,
            "T": 1024**4,
            "P": 1024**5,
            "E": 1024**6,
        }

        return int(number * multipliers.get(unit, 1))

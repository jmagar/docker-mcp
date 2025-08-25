"""Backup and restore operations for migration rollback capability."""

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

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
        """
        Initialize a BackupManager.
        
        Sets up a component-scoped logger, a MigrationSafety helper for safe removals, and an empty in-memory list to record backup metadata:
        - self.logger: structlog-bound logger with component="backup_manager"
        - self.safety: MigrationSafety instance used for safe delete operations
        - self.backups: list to store backup records (each is a dict with backup metadata)
        """
        self.logger = logger.bind(component="backup_manager")
        self.safety = MigrationSafety()
        self.backups: list[dict[str, Any]] = []
    
    def _build_ssh_cmd(self, host: DockerHost) -> list[str]:
        """
        Build the SSH command arguments for connecting to a remote DockerHost.
        
        Returns a list of command parts suitable for subprocess execution (e.g. ["ssh", "-o", "StrictHostKeyChecking=no", ...]).
        The command disables strict host key checking, adds an identity file if host.identity_file is set, adds a non-default port if host.port != 22, and appends the user@hostname target.
        """
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        if host.identity_file:
            ssh_cmd.extend(["-i", host.identity_file])
        if host.port != 22:
            ssh_cmd.extend(["-p", str(host.port)])
        ssh_cmd.append(f"{host.user}@{host.hostname}")
        return ssh_cmd
    
    async def backup_directory(
        self,
        host: DockerHost,
        source_path: str,
        stack_name: str,
        backup_reason: str = "Pre-migration backup"
    ) -> dict[str, Any]:
        """
        Create a gzipped tar backup of a remote directory and record metadata.
        
        If the remote source directory does not exist this returns a success payload with
        "backup_path" set to None and "backup_size" 0 (no backup created). On success,
        a backup archive is written on the remote host (under /tmp as `backup_<stack>_<timestamp>.tar.gz`),
        a backup record is appended to self.backups, and a dictionary with metadata is returned.
        
        Parameters:
            source_path (str): Absolute path of the directory to back up on the remote host.
            stack_name (str): Logical stack name used to form the backup filename.
            backup_reason (str): Short audit reason for the backup (defaults to "Pre-migration backup").
        
        Returns:
            dict: Backup information with keys including:
                - success (bool)
                - type (str) — "directory"
                - host_id (str)
                - source_path (str)
                - backup_path (str|None)
                - backup_size (int, bytes)
                - backup_size_human (str)
                - timestamp (str)
                - reason (str)
                - stack_name (str)
                - created_at (ISO timestamp)
        
        Raises:
            BackupError: If creating the remote tar archive fails.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backup_{stack_name}_{timestamp}.tar.gz"
        backup_path = f"/tmp/{backup_filename}"
        
        ssh_cmd = self._build_ssh_cmd(host)
        
        # Check if source path exists
        check_cmd = ssh_cmd + [f"test -d {source_path} && echo 'EXISTS' || echo 'NOT_FOUND'"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(check_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if "NOT_FOUND" in result.stdout:
            self.logger.info("Source path does not exist, no backup needed", path=source_path)
            return {
                "success": True,
                "backup_path": None,
                "backup_size": 0,
                "message": f"No existing data at {source_path} - backup skipped"
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
            reason=backup_reason
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(backup_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if "BACKUP_FAILED" in result.stdout or result.returncode != 0:
            raise BackupError(f"Failed to create backup: {result.stderr}")
        
        # Get backup size
        size_cmd = ssh_cmd + [f"stat -c%s {backup_path} 2>/dev/null || echo '0'"]
        size_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(size_cmd, capture_output=True, text=True, check=False)  # nosec B603
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
            "backup_size_human": self._format_size(backup_size),
            "timestamp": timestamp,
            "reason": backup_reason,
            "stack_name": stack_name,
            "created_at": datetime.now().isoformat()
        }
        
        self.backups.append(backup_info)
        
        self.logger.info(
            "Directory backup created successfully",
            backup=backup_path,
            size=backup_info["backup_size_human"],
            host=host.hostname
        )
        
        return backup_info
    
    async def backup_zfs_dataset(
        self,
        host: DockerHost,
        dataset: str,
        stack_name: str,
        backup_reason: str = "Pre-migration ZFS backup"
    ) -> dict[str, Any]:
        """
        Create a ZFS snapshot on the remote host and record its metadata.
        
        This asynchronously creates a snapshot named `backup_<stack_name>_<timestamp>` for the given ZFS
        dataset on the provided host, captures the snapshot's used size, stores a backup record in the
        manager's history, and returns that record.
        
        Parameters:
            host (DockerHost): Target host where the ZFS dataset resides.
            dataset (str): Full ZFS dataset name to snapshot (e.g., `pool/dataset`).
            stack_name (str): Stack identifier used when naming the snapshot.
            backup_reason (str): Short human-readable reason for creating the backup.
        
        Returns:
            dict: A backup information dictionary containing at least:
                - success (bool): True when snapshot creation succeeded.
                - type (str): "zfs_snapshot".
                - host_id (str): Host identifier (hostname).
                - dataset (str): The dataset that was snapshotted.
                - snapshot_name (str): Full snapshot reference `dataset@snapshot`.
                - backup_size (int): Parsed size of the snapshot in bytes.
                - backup_size_human (str): Original ZFS size string (e.g., "1.2G").
                - timestamp (str): Short timestamp used in the snapshot name (YYYYmmdd_HHMMSS).
                - reason (str): backup_reason as provided.
                - stack_name (str): stack_name as provided.
                - created_at (str): ISO8601 creation time.
        
        Raises:
            BackupError: If creating the ZFS snapshot on the remote host fails.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"backup_{stack_name}_{timestamp}"
        full_snapshot = f"{dataset}@{snapshot_name}"
        
        ssh_cmd = self._build_ssh_cmd(host)
        
        # Create ZFS snapshot
        snap_cmd = ssh_cmd + [f"zfs snapshot {full_snapshot}"]
        
        self.logger.info(
            "Creating ZFS backup snapshot",
            dataset=dataset,
            snapshot=full_snapshot,
            host=host.hostname,
            reason=backup_reason
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(snap_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if result.returncode != 0:
            raise BackupError(f"Failed to create ZFS backup snapshot: {result.stderr}")
        
        # Get snapshot size
        size_cmd = ssh_cmd + [f"zfs list -H -o used {full_snapshot} 2>/dev/null || echo '0'"]
        size_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(size_cmd, capture_output=True, text=True, check=False)  # nosec B603
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
            "created_at": datetime.now().isoformat()
        }
        
        self.backups.append(backup_info)
        
        self.logger.info(
            "ZFS backup snapshot created successfully",
            snapshot=full_snapshot,
            size=size_str,
            host=host.hostname
        )
        
        return backup_info
    
    async def restore_directory_backup(
        self,
        host: DockerHost,
        backup_info: dict[str, Any]
    ) -> tuple[bool, str]:
        """
        Restore a directory on a remote host from a previously created directory backup.
        
        Restores the filesystem at backup_info["source_path"] by removing the current target
        and extracting the tar.gz archive located at backup_info["backup_path"] on the remote host.
        If backup_info["backup_path"] is None the function treats this as "nothing to restore"
        and returns success.
        
        Important: this operation will remove the existing target path (uses `rm -rf` on the host)
        before extraction.
        
        Parameters:
            backup_info (dict): Backup metadata produced by backup_directory().
                Required keys:
                  - "type": must be "directory"
                  - "backup_path": remote path to the tar.gz archive (or None)
                  - "source_path": path that will be restored
        
        Returns:
            tuple[bool, str]: (success, message). On failure the message contains a short error.
        """
        if backup_info["type"] != "directory":
            return False, f"Not a directory backup: {backup_info['type']}"
        
        backup_path = backup_info["backup_path"]
        source_path = backup_info["source_path"]
        
        if not backup_path:
            return True, "No backup to restore (directory didn't exist)"
        
        ssh_cmd = self._build_ssh_cmd(host)
        
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
            host=host.hostname
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(restore_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if "RESTORE_FAILED" in result.stdout or result.returncode != 0:
            return False, f"Failed to restore backup: {result.stderr}"
        
        return True, f"Directory restored from backup: {backup_path}"
    
    async def restore_zfs_backup(
        self,
        host: DockerHost,
        backup_info: dict[str, Any]
    ) -> tuple[bool, str]:
        """
        Restore a ZFS dataset by rolling it back to a previously taken snapshot.
        
        Validates that `backup_info` describes a ZFS snapshot, then runs a remote `zfs rollback`
        to the snapshot specified in `backup_info`. If `backup_info["snapshot_name"]` or
        `backup_info["dataset"]` is present, they are used to perform and report the rollback.
        
        Parameters:
            backup_info (dict): Backup record produced by `backup_zfs_dataset()`. Must include
                the keys `"type"` (value "zfs_snapshot"), `"snapshot_name"`, and `"dataset"`.
        
        Returns:
            tuple[bool, str]: (success, message). On success `success` is True and the message
            confirms the snapshot applied. On failure `success` is False and the message
            contains the failure reason (e.g., command stderr or type mismatch).
        """
        if backup_info["type"] != "zfs_snapshot":
            return False, f"Not a ZFS backup: {backup_info['type']}"
        
        snapshot_name = backup_info["snapshot_name"]
        dataset = backup_info["dataset"]
        
        ssh_cmd = self._build_ssh_cmd(host)
        
        # Rollback to snapshot
        rollback_cmd = ssh_cmd + [f"zfs rollback {snapshot_name}"]
        
        self.logger.info(
            "Rolling back ZFS dataset to backup snapshot",
            snapshot=snapshot_name,
            dataset=dataset,
            host=host.hostname
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(rollback_cmd, capture_output=True, text=True, check=False)  # nosec B603
        )
        
        if result.returncode != 0:
            return False, f"Failed to rollback ZFS dataset: {result.stderr}"
        
        return True, f"ZFS dataset rolled back to snapshot: {snapshot_name}"
    
    async def cleanup_backup(
        self,
        host: DockerHost,
        backup_info: dict[str, Any]
    ) -> tuple[bool, str]:
        """
        Remove or retain a created backup after migration and return the outcome.
        
        Performs cleanup based on backup_info["type"]:
        - "directory": If backup_info["backup_path"] is falsy, no action is needed and (True, message) is returned.
          Otherwise the remote backup file is removed via MigrationSafety.safe_delete_file using an SSH command built for `host`.
        - "zfs_snapshot": Snapshot backups are retained by default; returns (True, message) indicating retention.
        - Any other type: returns (False, error_message).
        
        Parameters documented only where meaning is not obvious:
        - backup_info (dict): Backup metadata required by this method. Expected keys:
            - "type" (str): Either "directory" or "zfs_snapshot".
            - For "directory": "backup_path" (str|None) and "stack_name" (str) are used.
            - For "zfs_snapshot": "snapshot_name" (str) and "stack_name" (str) are used.
        
        Returns:
            tuple[bool, str]: (success, human-readable message) describing the cleanup result.
        """
        if backup_info["type"] == "directory":
            if not backup_info["backup_path"]:
                return True, "No backup file to clean up"
            
            success, message = await self.safety.safe_delete_file(
                self._build_ssh_cmd(host),
                backup_info["backup_path"],
                f"Cleanup backup for {backup_info['stack_name']}"
            )
            return success, message
            
        elif backup_info["type"] == "zfs_snapshot":
            # For ZFS snapshots, we might want to keep them longer
            # Or provide option to delete
            return True, f"ZFS backup snapshot retained: {backup_info['snapshot_name']}"
        
        return False, f"Unknown backup type: {backup_info['type']}"
    
    def _parse_zfs_size(self, size_str: str) -> int:
        """
        Convert a ZFS-style size string (e.g., "1.2G", "512M", "4K") into a byte count.
        
        Accepts an empty string or "0" and returns 0. Parsing is case-insensitive and accepts a decimal numeric value
        followed by an optional unit: K, M, G, T, P, E. Units are interpreted using binary multiples (1K = 1024 bytes).
        If the input cannot be parsed, 0 is returned.
        
        Parameters:
            size_str (str): size string to parse
        
        Returns:
            int: size in bytes
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
            "M": 1024 ** 2,
            "G": 1024 ** 3,
            "T": 1024 ** 4,
            "P": 1024 ** 5,
            "E": 1024 ** 6
        }
        
        return int(number * multipliers.get(unit, 1))
    
    def _format_size(self, size_bytes: int) -> str:
        """
        Return a human-readable representation of a byte count.
        
        Converts an integer number of bytes into a string using binary step units (B, KB, MB, GB, TB),
        choosing the largest unit where the value is less than 1024. For bytes the value is shown as an
        integer; for larger units the value is formatted with one decimal place. Zero bytes return "0 B".
        Values larger than TB are represented up to PB (formatted with one decimal place).
        
        Parameters:
            size_bytes (int): Number of bytes to format.
        
        Returns:
            str: Human-readable size string (e.g., "512 B", "1.2 KB", "3.4 GB").
        """
        if size_bytes == 0:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                if unit == 'B':
                    return f"{int(size_bytes)} {unit}"
                else:
                    return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"
    
    def get_backups(self) -> list[dict[str, Any]]:
        """
        Return a shallow copy of the list of backup records created by this BackupManager.
        
        The returned list contains dict entries representing backups (the same dict objects stored internally).
        A shallow copy of the list is returned to prevent callers from mutating the internal list structure; modifying the dicts themselves will affect the manager's stored records.
        """
        return self.backups.copy()
    
    def get_backups_for_stack(self, stack_name: str) -> list[dict[str, Any]]:
        """Get backups for a specific stack."""
        return [backup for backup in self.backups if backup["stack_name"] == stack_name]
    
    def clear_backup_history(self) -> None:
        """Clear the backup history."""
        self.backups.clear()
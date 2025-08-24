"""Filesystem sync verification for safe container data operations."""

import asyncio
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from .exceptions import DockerMCPError

logger = structlog.get_logger()


class FilesystemSyncError(DockerMCPError):
    """Filesystem sync operation failed."""

    pass


@dataclass
class SyncVerificationResult:
    """Result of filesystem sync verification."""

    success: bool
    duration: float
    attempts: int
    method: str
    details: dict[str, Any]


class FilesystemSync:
    """Verify filesystem changes are complete before proceeding with operations."""

    def __init__(
        self,
        max_wait_time: float = 30.0,
        initial_delay: float = 0.5,
        max_delay: float = 5.0,
        backoff_factor: float = 1.5,
    ):
        """Initialize filesystem sync verifier.

        Args:
            max_wait_time: Maximum time to wait for sync (seconds)
            initial_delay: Initial polling delay (seconds)
            max_delay: Maximum polling delay (seconds)
            backoff_factor: Exponential backoff multiplier
        """
        self.max_wait_time = max_wait_time
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.logger = logger.bind(component="filesystem_sync")

    async def wait_for_sync(
        self,
        ssh_cmd: list[str],
        paths: list[str],
        method: str = "auto",
    ) -> SyncVerificationResult:
        """Wait for filesystem changes to be fully synced.

        Args:
            ssh_cmd: SSH command parts for remote execution
            paths: List of paths to verify sync for
            method: Sync verification method (auto, sync, checksum, size)

        Returns:
            SyncVerificationResult with sync status

        Raises:
            FilesystemSyncError: If sync verification fails or times out
        """
        if not paths:
            return SyncVerificationResult(
                success=True,
                duration=0.0,
                attempts=0,
                method="none",
                details={"message": "No paths to sync"},
            )

        start_time = datetime.now()

        # Choose verification method
        if method == "auto":
            method = await self._detect_best_method(ssh_cmd)

        self.logger.info(
            "Starting filesystem sync verification",
            method=method,
            paths=len(paths),
            max_wait=self.max_wait_time,
        )

        # Perform sync verification based on method
        if method == "sync":
            result = await self._verify_with_sync_command(ssh_cmd, paths, start_time)
        elif method == "checksum":
            result = await self._verify_with_checksums(ssh_cmd, paths, start_time)
        elif method == "size":
            result = await self._verify_with_file_sizes(ssh_cmd, paths, start_time)
        else:
            # Fallback to simple delay with sync command
            result = await self._simple_sync_wait(ssh_cmd, start_time)

        duration = (datetime.now() - start_time).total_seconds()
        self.logger.info(
            "Filesystem sync verification complete",
            success=result.success,
            duration=duration,
            attempts=result.attempts,
            method=result.method,
        )

        if not result.success:
            raise FilesystemSyncError(
                f"Filesystem sync verification failed after {duration:.1f}s: {result.details}"
            )

        return result

    async def _detect_best_method(self, ssh_cmd: list[str]) -> str:
        """Detect the best sync verification method for the system.

        Args:
            ssh_cmd: SSH command parts for remote execution

        Returns:
            Best method name (sync, checksum, size)
        """
        # Check if sync command is available
        check_sync = ssh_cmd + ["which sync"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                check_sync, check=False, capture_output=True, text=True
            ),
        )

        if result.returncode == 0:
            return "sync"

        # Check if md5sum/sha256sum is available for checksums
        check_md5 = ssh_cmd + ["which md5sum"]
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                check_md5, check=False, capture_output=True, text=True
            ),
        )

        if result.returncode == 0:
            return "checksum"

        # Fallback to file size verification
        return "size"

    async def _verify_with_sync_command(
        self,
        ssh_cmd: list[str],
        paths: list[str],
        start_time: datetime,
    ) -> SyncVerificationResult:
        """Verify sync using system sync command with polling.

        Args:
            ssh_cmd: SSH command parts for remote execution
            paths: Paths to verify
            start_time: Start time for timeout calculation

        Returns:
            SyncVerificationResult
        """
        delay = self.initial_delay
        attempts = 0

        while (datetime.now() - start_time).total_seconds() < self.max_wait_time:
            attempts += 1

            # Call sync command
            sync_cmd = ssh_cmd + ["sync"]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    sync_cmd, check=False, capture_output=True, text=True
                ),
            )

            if result.returncode == 0:
                # Verify paths still exist and are accessible
                verify_cmd = ssh_cmd + [
                    f"ls -la {' '.join(paths[:3])} > /dev/null 2>&1 && echo 'OK'"
                ]
                verify_result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(  # nosec B603
                        verify_cmd, check=False, capture_output=True, text=True
                    ),
                )

                if "OK" in verify_result.stdout:
                    return SyncVerificationResult(
                        success=True,
                        duration=(datetime.now() - start_time).total_seconds(),
                        attempts=attempts,
                        method="sync",
                        details={"message": "Sync command successful"},
                    )

            # Wait with exponential backoff
            await asyncio.sleep(delay)
            delay = min(delay * self.backoff_factor, self.max_delay)

        return SyncVerificationResult(
            success=False,
            duration=(datetime.now() - start_time).total_seconds(),
            attempts=attempts,
            method="sync",
            details={"error": "Sync verification timed out"},
        )

    async def _verify_with_checksums(
        self,
        ssh_cmd: list[str],
        paths: list[str],
        start_time: datetime,
    ) -> SyncVerificationResult:
        """Verify sync by checking file checksums remain stable.

        Args:
            ssh_cmd: SSH command parts for remote execution
            paths: Paths to verify
            start_time: Start time for timeout calculation

        Returns:
            SyncVerificationResult
        """
        delay = self.initial_delay
        attempts = 0
        previous_checksums: dict[str, str] = {}
        stable_count = 0
        required_stable_checks = 2

        # Limit to first 10 paths for performance
        check_paths = paths[:10]

        while (datetime.now() - start_time).total_seconds() < self.max_wait_time:
            attempts += 1
            current_checksums = {}

            # Calculate checksums for each path
            for path in check_paths:
                # Use find to get first few files and calculate checksum
                checksum_cmd = ssh_cmd + [
                    f"find {path} -type f -name '*' ! -name '*.log' ! -name '*.tmp' "
                    f"2>/dev/null | head -5 | xargs -r md5sum 2>/dev/null | md5sum | cut -d' ' -f1"
                ]

                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(  # nosec B603
                        checksum_cmd, check=False, capture_output=True, text=True
                    ),
                )

                if result.returncode == 0 and result.stdout.strip():
                    current_checksums[path] = result.stdout.strip()

            # Check if checksums are stable
            if previous_checksums and current_checksums == previous_checksums:
                stable_count += 1
                if stable_count >= required_stable_checks:
                    return SyncVerificationResult(
                        success=True,
                        duration=(datetime.now() - start_time).total_seconds(),
                        attempts=attempts,
                        method="checksum",
                        details={
                            "message": "Checksums stable",
                            "paths_checked": len(check_paths),
                        },
                    )
            else:
                stable_count = 0

            previous_checksums = current_checksums

            # Wait with exponential backoff
            await asyncio.sleep(delay)
            delay = min(delay * self.backoff_factor, self.max_delay)

        return SyncVerificationResult(
            success=False,
            duration=(datetime.now() - start_time).total_seconds(),
            attempts=attempts,
            method="checksum",
            details={"error": "Checksums did not stabilize"},
        )

    async def _verify_with_file_sizes(
        self,
        ssh_cmd: list[str],
        paths: list[str],
        start_time: datetime,
    ) -> SyncVerificationResult:
        """Verify sync by checking file sizes remain stable.

        Args:
            ssh_cmd: SSH command parts for remote execution
            paths: Paths to verify
            start_time: Start time for timeout calculation

        Returns:
            SyncVerificationResult
        """
        delay = self.initial_delay
        attempts = 0
        previous_sizes: dict[str, int] = {}
        stable_count = 0
        required_stable_checks = 3

        while (datetime.now() - start_time).total_seconds() < self.max_wait_time:
            attempts += 1
            current_sizes = {}

            # Get total size for each path
            for path in paths[:10]:  # Limit for performance
                size_cmd = ssh_cmd + [f"du -sb {path} 2>/dev/null | cut -f1"]

                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(  # nosec B603
                        size_cmd, check=False, capture_output=True, text=True
                    ),
                )

                if result.returncode == 0 and result.stdout.strip().isdigit():
                    current_sizes[path] = int(result.stdout.strip())

            # Check if sizes are stable
            if previous_sizes and current_sizes == previous_sizes:
                stable_count += 1
                if stable_count >= required_stable_checks:
                    # Final sync command if available
                    sync_cmd = ssh_cmd + ["sync"]
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: subprocess.run(  # nosec B603
                            sync_cmd, check=False, capture_output=True, text=True
                        ),
                    )

                    return SyncVerificationResult(
                        success=True,
                        duration=(datetime.now() - start_time).total_seconds(),
                        attempts=attempts,
                        method="size",
                        details={
                            "message": "File sizes stable",
                            "paths_checked": len(current_sizes),
                        },
                    )
            else:
                stable_count = 0

            previous_sizes = current_sizes

            # Wait with exponential backoff
            await asyncio.sleep(delay)
            delay = min(delay * self.backoff_factor, self.max_delay)

        return SyncVerificationResult(
            success=False,
            duration=(datetime.now() - start_time).total_seconds(),
            attempts=attempts,
            method="size",
            details={"error": "File sizes did not stabilize"},
        )

    async def _simple_sync_wait(
        self,
        ssh_cmd: list[str],
        start_time: datetime,
    ) -> SyncVerificationResult:
        """Simple sync wait with sync command calls.

        Args:
            ssh_cmd: SSH command parts for remote execution
            start_time: Start time

        Returns:
            SyncVerificationResult
        """
        # Try to call sync command multiple times
        for _ in range(3):
            sync_cmd = ssh_cmd + ["sync"]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    sync_cmd, check=False, capture_output=True, text=True
                ),
            )
            await asyncio.sleep(0.5)

        # Final wait
        await asyncio.sleep(1.0)

        return SyncVerificationResult(
            success=True,
            duration=(datetime.now() - start_time).total_seconds(),
            attempts=3,
            method="simple",
            details={"message": "Simple sync wait completed"},
        )

    async def verify_path_accessibility(
        self,
        ssh_cmd: list[str],
        paths: list[str],
    ) -> dict[str, bool]:
        """Verify paths are accessible after sync.

        Args:
            ssh_cmd: SSH command parts for remote execution
            paths: Paths to verify

        Returns:
            Dictionary mapping paths to accessibility status
        """
        results = {}

        for path in paths:
            check_cmd = ssh_cmd + [f"test -e {path} && echo 'EXISTS' || echo 'MISSING'"]

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(  # nosec B603
                    check_cmd, check=False, capture_output=True, text=True
                ),
            )

            results[path] = "EXISTS" in result.stdout

        return results

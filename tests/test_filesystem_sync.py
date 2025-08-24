"""Tests for filesystem sync verification module."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docker_mcp.core.filesystem_sync import (
    FilesystemSync,
    FilesystemSyncError,
    SyncVerificationResult,
)


@pytest.fixture
def filesystem_sync():
    """Create a FilesystemSync instance with test configuration."""
    return FilesystemSync(
        max_wait_time=5.0,
        initial_delay=0.1,
        max_delay=1.0,
        backoff_factor=2.0,
    )


@pytest.fixture
def ssh_cmd():
    """Standard SSH command for testing."""
    return ["ssh", "-o", "StrictHostKeyChecking=no", "user@host"]


@pytest.fixture
def test_paths():
    """Test paths for sync verification."""
    return [
        "/opt/appdata/app1",
        "/opt/appdata/app2",
        "/var/lib/docker/volumes/test_vol",
    ]


class TestFilesystemSync:
    """Test filesystem sync verification functionality."""

    @pytest.mark.asyncio
    async def test_wait_for_sync_no_paths(self, filesystem_sync):
        """Test sync with no paths returns immediately."""
        result = await filesystem_sync.wait_for_sync([], [])

        assert result.success is True
        assert result.duration == 0.0
        assert result.attempts == 0
        assert result.method == "none"

    @pytest.mark.asyncio
    async def test_detect_best_method_sync_available(self, filesystem_sync, ssh_cmd):
        """Test detection when sync command is available."""
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)

            method = await filesystem_sync._detect_best_method(ssh_cmd)
            assert method == "sync"

    @pytest.mark.asyncio
    async def test_detect_best_method_checksum_available(self, filesystem_sync, ssh_cmd):
        """Test detection when only md5sum is available."""
        with patch("asyncio.get_event_loop") as mock_loop:
            # First call (sync check) fails
            sync_result = MagicMock()
            sync_result.returncode = 1

            # Second call (md5sum check) succeeds
            md5_result = MagicMock()
            md5_result.returncode = 0

            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=[sync_result, md5_result]
            )

            method = await filesystem_sync._detect_best_method(ssh_cmd)
            assert method == "checksum"

    @pytest.mark.asyncio
    async def test_detect_best_method_fallback_to_size(self, filesystem_sync, ssh_cmd):
        """Test fallback to size method when others unavailable."""
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)

            method = await filesystem_sync._detect_best_method(ssh_cmd)
            assert method == "size"

    @pytest.mark.asyncio
    async def test_verify_with_sync_command_success(self, filesystem_sync, ssh_cmd, test_paths):
        """Test successful sync verification using sync command."""
        with patch("asyncio.get_event_loop") as mock_loop:
            # Sync command succeeds
            sync_result = MagicMock()
            sync_result.returncode = 0

            # Verify command succeeds
            verify_result = MagicMock()
            verify_result.returncode = 0
            verify_result.stdout = "OK"

            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=[sync_result, verify_result]
            )

            start_time = datetime.now()
            result = await filesystem_sync._verify_with_sync_command(
                ssh_cmd, test_paths, start_time
            )

            assert result.success is True
            assert result.method == "sync"
            assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_verify_with_sync_command_timeout(self, filesystem_sync, ssh_cmd, test_paths):
        """Test sync command timeout."""
        # Create a filesystem sync with very short timeout
        fs_sync = FilesystemSync(max_wait_time=0.1, initial_delay=0.05)

        with patch("asyncio.get_event_loop") as mock_loop:
            # Sync always fails
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)

            start_time = datetime.now()
            result = await fs_sync._verify_with_sync_command(ssh_cmd, test_paths, start_time)

            assert result.success is False
            assert "timed out" in result.details.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_verify_with_checksums_stable(self, filesystem_sync, ssh_cmd, test_paths):
        """Test checksum verification with stable checksums."""
        with patch("asyncio.get_event_loop") as mock_loop:
            # Return same checksum multiple times
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "abc123def456"

            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)

            start_time = datetime.now()
            result = await filesystem_sync._verify_with_checksums(ssh_cmd, test_paths, start_time)

            assert result.success is True
            assert result.method == "checksum"
            assert "stable" in result.details.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_verify_with_checksums_changing(self, filesystem_sync, ssh_cmd, test_paths):
        """Test checksum verification with changing checksums."""
        # Create a filesystem sync with very short timeout
        fs_sync = FilesystemSync(max_wait_time=0.2, initial_delay=0.05)

        with patch("asyncio.get_event_loop") as mock_loop:
            # Return different checksums each time - need more results since we check multiple paths
            checksums = ["abc123", "def456", "ghi789", "jkl012", "mno345", "pqr678"]
            results = []
            # Each iteration checks up to 10 paths, multiply results
            for _ in range(20):  # Enough results for the timeout duration
                for checksum in checksums:
                    mock_result = MagicMock()
                    mock_result.returncode = 0
                    mock_result.stdout = checksum
                    results.append(mock_result)

            mock_loop.return_value.run_in_executor = AsyncMock(side_effect=results)

            start_time = datetime.now()
            result = await fs_sync._verify_with_checksums(ssh_cmd, test_paths, start_time)

            assert result.success is False
            assert "stabilize" in result.details.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_verify_with_file_sizes_stable(self, filesystem_sync, ssh_cmd, test_paths):
        """Test file size verification with stable sizes."""
        with patch("asyncio.get_event_loop") as mock_loop:
            # Return same size multiple times, then sync succeeds
            size_result = MagicMock()
            size_result.returncode = 0
            size_result.stdout = "1024000"

            sync_result = MagicMock()
            sync_result.returncode = 0

            # Need enough results for multiple stability checks
            results = [size_result] * 20 + [sync_result]

            mock_loop.return_value.run_in_executor = AsyncMock(side_effect=results)

            start_time = datetime.now()
            result = await filesystem_sync._verify_with_file_sizes(ssh_cmd, test_paths, start_time)

            assert result.success is True
            assert result.method == "size"
            assert "stable" in result.details.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_verify_with_file_sizes_growing(self, filesystem_sync, ssh_cmd, test_paths):
        """Test file size verification with growing files."""
        # Create a filesystem sync with very short timeout
        fs_sync = FilesystemSync(max_wait_time=0.2, initial_delay=0.05)

        with patch("asyncio.get_event_loop") as mock_loop:
            # Return increasing sizes - need more results since we check multiple paths
            sizes = ["1000", "2000", "3000", "4000", "5000", "6000"]
            results = []
            # Each iteration checks up to 10 paths, multiply results
            for _ in range(20):  # Enough results for timeout duration
                for size in sizes:
                    mock_result = MagicMock()
                    mock_result.returncode = 0
                    mock_result.stdout = size
                    results.append(mock_result)

            mock_loop.return_value.run_in_executor = AsyncMock(side_effect=results)

            start_time = datetime.now()
            result = await fs_sync._verify_with_file_sizes(ssh_cmd, test_paths, start_time)

            assert result.success is False
            assert "stabilize" in result.details.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_simple_sync_wait(self, filesystem_sync, ssh_cmd):
        """Test simple sync wait fallback."""
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)

            start_time = datetime.now()
            result = await filesystem_sync._simple_sync_wait(ssh_cmd, start_time)

            assert result.success is True
            assert result.method == "simple"
            assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_wait_for_sync_auto_method(self, filesystem_sync, ssh_cmd, test_paths):
        """Test automatic method selection."""
        with patch.object(filesystem_sync, "_detect_best_method") as mock_detect:
            mock_detect.return_value = "sync"

            with patch.object(filesystem_sync, "_verify_with_sync_command") as mock_verify:
                mock_verify.return_value = SyncVerificationResult(
                    success=True,
                    duration=1.0,
                    attempts=2,
                    method="sync",
                    details={"message": "Test sync"},
                )

                result = await filesystem_sync.wait_for_sync(ssh_cmd, test_paths, method="auto")

                assert result.success is True
                assert result.method == "sync"
                mock_detect.assert_called_once_with(ssh_cmd)

    @pytest.mark.asyncio
    async def test_wait_for_sync_specific_method(self, filesystem_sync, ssh_cmd, test_paths):
        """Test specific method selection."""
        with patch.object(filesystem_sync, "_verify_with_checksums") as mock_verify:
            mock_verify.return_value = SyncVerificationResult(
                success=True,
                duration=2.0,
                attempts=3,
                method="checksum",
                details={"message": "Test checksum"},
            )

            result = await filesystem_sync.wait_for_sync(ssh_cmd, test_paths, method="checksum")

            assert result.success is True
            assert result.method == "checksum"
            mock_verify.assert_called_once()

    @pytest.mark.asyncio
    async def test_wait_for_sync_failure_raises_error(self, filesystem_sync, ssh_cmd, test_paths):
        """Test that sync failure raises FilesystemSyncError."""
        with patch.object(filesystem_sync, "_detect_best_method") as mock_detect:
            mock_detect.return_value = "sync"

            with patch.object(filesystem_sync, "_verify_with_sync_command") as mock_verify:
                mock_verify.return_value = SyncVerificationResult(
                    success=False,
                    duration=5.0,
                    attempts=10,
                    method="sync",
                    details={"error": "Test failure"},
                )

                with pytest.raises(FilesystemSyncError) as exc_info:
                    await filesystem_sync.wait_for_sync(ssh_cmd, test_paths, method="auto")

                assert "Test failure" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_verify_path_accessibility(self, filesystem_sync, ssh_cmd, test_paths):
        """Test path accessibility verification."""
        with patch("asyncio.get_event_loop") as mock_loop:
            # First path exists
            result1 = MagicMock()
            result1.stdout = "EXISTS"

            # Second path missing
            result2 = MagicMock()
            result2.stdout = "MISSING"

            # Third path exists
            result3 = MagicMock()
            result3.stdout = "EXISTS"

            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=[result1, result2, result3]
            )

            results = await filesystem_sync.verify_path_accessibility(ssh_cmd, test_paths)

            assert results[test_paths[0]] is True
            assert results[test_paths[1]] is False
            assert results[test_paths[2]] is True

    @pytest.mark.asyncio
    async def test_exponential_backoff(self, filesystem_sync, ssh_cmd, test_paths):
        """Test exponential backoff behavior."""
        # Track delays
        delays = []
        original_sleep = asyncio.sleep

        async def track_sleep(delay):
            delays.append(delay)
            await original_sleep(0.01)  # Very short actual delay for testing

        with patch("asyncio.sleep", side_effect=track_sleep):
            with patch("asyncio.get_event_loop") as mock_loop:
                # Always return failure to force retries
                mock_result = MagicMock()
                mock_result.returncode = 1
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)

                fs_sync = FilesystemSync(
                    max_wait_time=0.5,
                    initial_delay=0.1,
                    max_delay=0.4,
                    backoff_factor=2.0,
                )

                start_time = datetime.now()
                result = await fs_sync._verify_with_sync_command(ssh_cmd, test_paths, start_time)

                assert result.success is False
                assert len(delays) > 0

                # Verify exponential backoff pattern
                if len(delays) > 1:
                    assert delays[0] == 0.1  # Initial delay
                    if len(delays) > 2:
                        assert delays[1] == 0.2  # Doubled
                        if len(delays) > 3:
                            assert delays[2] == 0.4  # Doubled again (hits max)
                            if len(delays) > 4:
                                assert delays[3] == 0.4  # Stays at max


class TestIntegrationWithStack:
    """Test integration with stack service."""

    @pytest.mark.asyncio
    async def test_stack_migration_uses_filesystem_sync(self):
        """Test that stack migration properly uses filesystem sync."""
        from docker_mcp.core.config_loader import DockerMCPConfig
        from docker_mcp.core.docker_context import DockerContextManager
        from docker_mcp.services.stack import StackService

        # Create mock config and context manager
        mock_config = MagicMock(spec=DockerMCPConfig)
        mock_config.hosts = {
            "source": MagicMock(
                hostname="source.example.com",
                user="user",
                identity_file=None,
                appdata_path="/opt/appdata",
                compose_path="/opt/compose",
            ),
            "target": MagicMock(
                hostname="target.example.com",
                user="user",
                identity_file=None,
                appdata_path="/opt/appdata",
                compose_path="/opt/compose",
            ),
        }

        mock_context_manager = MagicMock(spec=DockerContextManager)

        # Create stack service
        stack_service = StackService(mock_config, mock_context_manager)

        # Verify filesystem_sync is initialized
        assert hasattr(stack_service, "filesystem_sync")
        assert isinstance(stack_service.filesystem_sync, FilesystemSync)

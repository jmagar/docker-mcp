"""Tests for Docker stack migration functionality."""

from unittest.mock import MagicMock, patch

import pytest

from docker_mcp.core.config_loader import DockerHost
from docker_mcp.core.exceptions import DockerMCPError as MigrationError
from docker_mcp.core.migration.manager import MigrationManager


class TestMigrationManager:
    """Test suite for MigrationManager class."""

    @pytest.fixture
    def migration_manager(self):
        """Create MigrationManager instance for testing."""
        return MigrationManager()

    @pytest.mark.asyncio
    async def test_parse_compose_volumes_simple(self, migration_manager):
        """Test parsing simple compose file with volumes."""
        compose_content = """
version: '3.8'
services:
  app:
    image: nginx
    volumes:
      - data:/var/www/html
      - ./config:/etc/nginx
volumes:
  data:
"""
        result = await migration_manager.parse_compose_volumes(compose_content)

        assert "data" in result["named_volumes"]
        assert "./config" in result["bind_mounts"]
        assert "data" in result["volume_definitions"]

    @pytest.mark.asyncio
    async def test_parse_compose_volumes_complex(self, migration_manager):
        """Test parsing complex compose file with various volume formats."""
        compose_content = """
version: '3.8'
services:
  db:
    image: postgres
    volumes:
      - type: volume
        source: postgres_data
        target: /var/lib/postgresql/data
      - type: bind
        source: /host/backup
        target: /backup
  cache:
    image: redis
    volumes:
      - redis_data:/data
volumes:
  postgres_data:
    driver: local
  redis_data:
"""
        result = await migration_manager.parse_compose_volumes(compose_content)

        assert "postgres_data" in result["named_volumes"]
        assert "redis_data" in result["named_volumes"]
        assert "/host/backup" in result["bind_mounts"]
        assert len(result["volume_definitions"]) == 2

    @pytest.mark.asyncio
    async def test_parse_compose_volumes_invalid_yaml(self, migration_manager):
        """Test parsing invalid YAML raises MigrationError."""
        compose_content = """
invalid: yaml: content
  - this is not valid
"""
        with pytest.raises(MigrationError, match="Failed to parse compose file"):
            await migration_manager.parse_compose_volumes(compose_content)

    def test_parse_volume_string_named(self, migration_manager):
        """Test parsing named volume string."""
        result = migration_manager._parse_volume_string("data:/app/data")

        assert result["type"] == "named"
        assert result["name"] == "data"
        assert result["destination"] == "/app/data"

    def test_parse_volume_string_bind_absolute(self, migration_manager):
        """Test parsing bind mount with absolute path."""
        result = migration_manager._parse_volume_string("/host/path:/container/path:ro")

        assert result["type"] == "bind"
        assert result["source"] == "/host/path"
        assert result["destination"] == "/container/path"
        assert result["mode"] == "ro"

    def test_parse_volume_string_bind_relative(self, migration_manager):
        """Test parsing bind mount with relative path."""
        result = migration_manager._parse_volume_string("./config:/etc/app")

        assert result["type"] == "bind"
        assert result["source"] == "./config"
        assert result["destination"] == "/etc/app"

    @pytest.mark.asyncio
    async def test_create_volume_archive(self, migration_manager):
        """Test creating volume archive with exclusions."""
        ssh_cmd = ["ssh", "user@host"]
        volume_paths = ["/data/app", "/data/db"]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            archive_path = await migration_manager.archive_utils.create_archive(
                ssh_cmd, volume_paths, "test_archive"
            )

            assert "test_archive" in archive_path
            assert ".tar.gz" in archive_path

            # Check that exclusions were applied
            call_args = mock_run.call_args[0][0]
            command_str = " ".join(call_args) if isinstance(call_args, list) else str(call_args)
            assert "--exclude" in command_str
            assert "node_modules/" in command_str

    @pytest.mark.asyncio
    async def test_create_volume_archive_empty_paths(self, migration_manager):
        """Test creating archive with no paths raises error."""
        ssh_cmd = ["ssh", "user@host"]

        with pytest.raises(Exception, match="No volumes to archive"):
            await migration_manager.archive_utils.create_archive(ssh_cmd, [], "test")

    @pytest.mark.asyncio
    async def test_transfer_with_rsync(self, migration_manager):
        """Test rsync transfer between hosts."""
        source_host = DockerHost(
            hostname="source.example.com",
            user="user1",
            identity_file="/path/to/key"
        )
        target_host = DockerHost(
            hostname="target.example.com",
            user="user2"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="sent 1000 bytes  received 35 bytes  2070.00 bytes/sec\ntotal size is 1000  speedup is 0.97",
                stderr=""
            )

            result = await migration_manager.rsync_transfer.transfer(
                source_host, target_host,
                "/source/path", "/target/path",
                compress=True, delete=False, dry_run=False
            )

            assert result["success"] is True
            assert "source" in result
            assert "target" in result
            assert "stats" in result

    @pytest.mark.asyncio
    async def test_transfer_with_rsync_failure(self, migration_manager):
        """Test rsync transfer failure handling."""
        source_host = DockerHost(hostname="source.example.com", user="user1")
        target_host = DockerHost(hostname="target.example.com", user="user2")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Connection refused"
            )

            with pytest.raises(Exception, match="Rsync failed"):
                await migration_manager.rsync_transfer.transfer(
                    source_host, target_host,
                    "/source/path", "/target/path"
                )

    def test_parse_rsync_stats(self, migration_manager):
        """Test parsing rsync output statistics."""
        output = """
sending incremental file list
file1.txt
file2.txt

Number of files transferred: 2
Total transferred file size: 1,024 bytes
sent 1,234 bytes  received 56 bytes  2,580.00 bytes/sec
total size is 1,024  speedup is 0.79
"""
        stats = migration_manager.rsync_transfer._parse_stats(output)

        assert stats["files_transferred"] == 2
        assert stats["total_size"] == 1024
        assert "580.00 bytes/sec" in stats["transfer_rate"]
        assert stats["speedup"] == 0.79

    @pytest.mark.asyncio
    async def test_get_volume_locations(self, migration_manager):
        """Test getting Docker volume mount points."""
        ssh_cmd = ["ssh", "user@host"]
        named_volumes = ["app_data", "db_data"]

        with patch("subprocess.run") as mock_run:
            # Mock successful volume inspect commands
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="/var/lib/docker/volumes/app_data/_data\n"),
                MagicMock(returncode=0, stdout="/var/lib/docker/volumes/db_data/_data\n"),
            ]

            result = await migration_manager.get_volume_locations(ssh_cmd, named_volumes)

            assert result["app_data"] == "/var/lib/docker/volumes/app_data/_data"
            assert result["db_data"] == "/var/lib/docker/volumes/db_data/_data"

    @pytest.mark.asyncio
    async def test_verify_containers_stopped_none_running(self, migration_manager):
        """Test verifying no containers are running."""
        ssh_cmd = ["ssh", "user@host"]
        stack_name = "test_stack"

        with patch("subprocess.run") as mock_run:
            # No containers running
            mock_run.return_value = MagicMock(returncode=0, stdout="")

            all_stopped, running = await migration_manager.verify_containers_stopped(
                ssh_cmd, stack_name
            )

            assert all_stopped is True
            assert running == []

    @pytest.mark.asyncio
    async def test_verify_containers_stopped_with_running(self, migration_manager):
        """Test verifying with running containers."""
        ssh_cmd = ["ssh", "user@host"]
        stack_name = "test_stack"

        with patch("subprocess.run") as mock_run:
            # Containers are running
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="test_stack_web_1\ntest_stack_db_1\n"
            )

            all_stopped, running = await migration_manager.verify_containers_stopped(
                ssh_cmd, stack_name
            )

            assert all_stopped is False
            assert running == ["test_stack_web_1", "test_stack_db_1"]

    @pytest.mark.asyncio
    async def test_verify_containers_stopped_with_force(self, migration_manager):
        """Test force stopping running containers."""
        ssh_cmd = ["ssh", "user@host"]
        stack_name = "test_stack"

        with patch("subprocess.run") as mock_run:
            # First check: containers running
            # Kill commands for each container
            # Second check: no containers running
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="test_stack_web_1\ntest_stack_db_1\n"),
                MagicMock(returncode=0, stdout=""),  # kill web
                MagicMock(returncode=0, stdout=""),  # kill db
                MagicMock(returncode=0, stdout=""),  # re-check: all stopped
            ]

            all_stopped, running = await migration_manager.verify_containers_stopped(
                ssh_cmd, stack_name, force_stop=True
            )

            assert all_stopped is True
            assert running == []
            assert mock_run.call_count == 4  # check, 2 kills, re-check

    @pytest.mark.asyncio
    async def test_prepare_target_directories(self, migration_manager):
        """Test preparing target directories for migration."""
        ssh_cmd = ["ssh", "user@host"]
        appdata_path = "/opt/appdata"
        stack_name = "myapp"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            result = await migration_manager.prepare_target_directories(
                ssh_cmd, appdata_path, stack_name
            )

            assert result == f"{appdata_path}/{stack_name}"
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "mkdir -p" in " ".join(call_args)

    def test_update_compose_for_migration(self, migration_manager):
        """Test updating compose file paths for target host."""
        compose_content = """
version: '3.8'
services:
  app:
    volumes:
      - /old/path/data:/app/data
      - ./config:/etc/app
"""
        old_paths = {
            "data": "/old/path/data"
        }
        new_base_path = "/new/appdata/myapp"

        result = migration_manager.update_compose_for_migration(
            compose_content, old_paths, new_base_path
        )

        assert "/old/path/data" not in result
        assert "/new/appdata/myapp/data" in result
        assert "./config" in result  # Relative paths should remain unchanged


@pytest.mark.integration
class TestMigrationIntegration:
    """Integration tests for migration functionality."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        True,  # Skip integration tests by default
        reason="Integration tests disabled by default"
    )
    async def test_end_to_end_migration(self):
        """Test complete migration workflow (requires test environment)."""
        # This would test the full migration process with real Docker hosts
        # Skipped by default as it requires specific test infrastructure
        pass

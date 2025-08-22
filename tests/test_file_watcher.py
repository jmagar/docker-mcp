"""
Comprehensive tests for file watching and hot reload functionality.

Tests configuration file watching, hot reload management, and server integration.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docker_mcp.core.config import DockerHost, DockerMCPConfig
from docker_mcp.core.exceptions import ConfigurationError
from docker_mcp.core.file_watcher import ConfigFileWatcher, HotReloadManager


class TestConfigFileWatcher:
    """Test configuration file watching functionality."""

    @pytest.fixture
    def mock_reload_callback(self):
        """Create mock reload callback for testing."""
        return AsyncMock()

    @pytest.fixture
    def temp_config_file(self):
        """Create temporary configuration file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("""
hosts:
  test-host:
    hostname: test.example.com
    user: testuser
    description: Test host
    enabled: true

server:
  host: 127.0.0.1
  port: 8000
""")
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        Path(temp_path).unlink(missing_ok=True)

    def test_config_file_watcher_creation(self, temp_config_file, mock_reload_callback):
        """Test creating ConfigFileWatcher with valid parameters."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        assert watcher.config_path == Path(temp_config_file)
        assert watcher.reload_callback == mock_reload_callback
        assert watcher._watch_task is None
        assert watcher._is_watching is False
        assert watcher._last_config_hash is None

    @pytest.mark.asyncio
    async def test_start_watching_valid_file(self, temp_config_file, mock_reload_callback):
        """Test starting to watch an existing configuration file."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        with patch.object(watcher, '_watch_files') as mock_watch:
            mock_watch.return_value = AsyncMock()
            
            await watcher.start_watching()
            
            assert watcher._is_watching is True
            assert watcher._watch_task is not None
            # Clean up the task
            await watcher.stop_watching()

    @pytest.mark.asyncio
    async def test_start_watching_already_watching(self, temp_config_file, mock_reload_callback):
        """Test starting to watch when already watching."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        watcher._is_watching = True  # Simulate already watching
        
        await watcher.start_watching()
        
        # Should remain watching but not create new task
        assert watcher._is_watching is True
        assert watcher._watch_task is None

    @pytest.mark.asyncio
    async def test_start_watching_nonexistent_file(self, mock_reload_callback):
        """Test starting to watch a nonexistent file."""
        nonexistent_path = "/tmp/definitely_does_not_exist.yml"
        watcher = ConfigFileWatcher(nonexistent_path, mock_reload_callback)
        
        await watcher.start_watching()
        
        # Should not start watching for nonexistent file
        assert watcher._is_watching is False
        assert watcher._watch_task is None

    @pytest.mark.asyncio
    async def test_stop_watching_when_not_watching(self, temp_config_file, mock_reload_callback):
        """Test stopping watcher when not currently watching."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        await watcher.stop_watching()
        
        # Should handle gracefully
        assert watcher._is_watching is False

    @pytest.mark.asyncio
    async def test_stop_watching_with_active_task(self, temp_config_file, mock_reload_callback):
        """Test stopping watcher with active watch task."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        # Create a custom mock task class that can be awaited
        class MockTask:
            def __init__(self):
                self.cancel = MagicMock()
                
            def done(self):
                return False
                
            def __await__(self):
                # Return a generator that raises CancelledError when awaited
                async def coro():
                    raise asyncio.CancelledError()
                return coro().__await__()
        
        mock_task = MockTask()
        watcher._watch_task = mock_task
        watcher._is_watching = True
        
        await watcher.stop_watching()
        
        assert watcher._is_watching is False
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_config_change_success(self, temp_config_file, mock_reload_callback):
        """Test successful configuration change handling."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        with patch('docker_mcp.core.file_watcher.load_config') as mock_load_config:
            # Mock successful config loading
            mock_config = DockerMCPConfig(hosts={
                "new-host": DockerHost(hostname="new.example.com", user="newuser")
            })
            mock_load_config.return_value = mock_config
            
            await watcher._handle_config_change()
            
            # Verify reload callback was called
            mock_reload_callback.assert_called_once_with(mock_config)
            assert watcher._last_config_hash is not None

    @pytest.mark.asyncio
    async def test_handle_config_change_skip_duplicate(self, temp_config_file, mock_reload_callback):
        """Test skipping duplicate configuration changes."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        with patch('docker_mcp.core.file_watcher.load_config') as mock_load_config:
            mock_config = DockerMCPConfig(hosts={
                "test-host": DockerHost(hostname="test.example.com", user="testuser")
            })
            mock_load_config.return_value = mock_config
            
            # Handle change once
            await watcher._handle_config_change()
            first_call_count = mock_reload_callback.call_count
            
            # Handle same change again
            await watcher._handle_config_change()
            
            # Should only be called once due to hash check
            assert mock_reload_callback.call_count == first_call_count

    @pytest.mark.asyncio
    async def test_handle_config_change_load_error(self, temp_config_file, mock_reload_callback):
        """Test handling configuration load errors gracefully."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        with patch('docker_mcp.core.file_watcher.load_config') as mock_load_config:
            mock_load_config.side_effect = Exception("Invalid YAML")
            
            # Should not raise exception
            await watcher._handle_config_change()
            
            # Callback should not be called due to error
            mock_reload_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_config_change_callback_error(self, temp_config_file, mock_reload_callback):
        """Test handling callback errors gracefully."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        with patch('docker_mcp.core.file_watcher.load_config') as mock_load_config:
            mock_config = DockerMCPConfig()
            mock_load_config.return_value = mock_config
            mock_reload_callback.side_effect = Exception("Callback failed")
            
            # Should not raise exception
            await watcher._handle_config_change()
            
            # Callback should have been called despite error
            mock_reload_callback.assert_called_once()

    def test_calculate_config_hash_different_configs(self, temp_config_file, mock_reload_callback):
        """Test configuration hash calculation for different configs."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        config1 = DockerMCPConfig(hosts={
            "host1": DockerHost(hostname="host1.example.com", user="user1", enabled=True)
        })
        
        config2 = DockerMCPConfig(hosts={
            "host1": DockerHost(hostname="host1.example.com", user="user1", enabled=False)
        })
        
        config3 = DockerMCPConfig(hosts={
            "host2": DockerHost(hostname="host2.example.com", user="user2", enabled=True)
        })
        
        hash1 = watcher._calculate_config_hash(config1)
        hash2 = watcher._calculate_config_hash(config2)
        hash3 = watcher._calculate_config_hash(config3)
        
        # All hashes should be different
        assert hash1 != hash2
        assert hash1 != hash3
        assert hash2 != hash3

    def test_calculate_config_hash_same_config(self, temp_config_file, mock_reload_callback):
        """Test configuration hash calculation for identical configs."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        config1 = DockerMCPConfig(hosts={
            "host1": DockerHost(hostname="host1.example.com", user="user1"),
            "host2": DockerHost(hostname="host2.example.com", user="user2")
        })
        
        config2 = DockerMCPConfig(hosts={
            "host2": DockerHost(hostname="host2.example.com", user="user2"),
            "host1": DockerHost(hostname="host1.example.com", user="user1")
        })
        
        hash1 = watcher._calculate_config_hash(config1)
        hash2 = watcher._calculate_config_hash(config2)
        
        # Should be the same despite different order
        assert hash1 == hash2

    def test_calculate_config_hash_empty_config(self, temp_config_file, mock_reload_callback):
        """Test configuration hash calculation for empty config."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        empty_config = DockerMCPConfig()
        hash_value = watcher._calculate_config_hash(empty_config)
        
        assert isinstance(hash_value, str)
        assert len(hash_value) > 0

    @pytest.mark.asyncio
    async def test_watch_files_cancellation(self, temp_config_file, mock_reload_callback):
        """Test file watching handles cancellation gracefully."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        
        with patch('docker_mcp.core.file_watcher.awatch') as mock_awatch:
            # Simulate cancellation
            mock_awatch.side_effect = asyncio.CancelledError()
            
            with pytest.raises(asyncio.CancelledError):
                await watcher._watch_files()

    @pytest.mark.asyncio
    async def test_watch_files_general_exception_restart(self, temp_config_file, mock_reload_callback):
        """Test file watching handles general exceptions and restarts."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        watcher._is_watching = True
        
        call_count = 0
        def mock_awatch_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Simulated error")
            else:
                # Stop watching to prevent infinite recursion
                watcher._is_watching = False
                raise asyncio.CancelledError()
        
        with patch('docker_mcp.core.file_watcher.awatch') as mock_awatch, \
             patch('asyncio.sleep') as mock_sleep:
            mock_awatch.side_effect = mock_awatch_side_effect
            
            with pytest.raises(asyncio.CancelledError):
                await watcher._watch_files()
            
            # Should have tried to restart after error
            assert call_count == 2
            mock_sleep.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_watch_files_stop_during_sleep(self, temp_config_file, mock_reload_callback):
        """Test file watching stops if _is_watching becomes false during error recovery."""
        watcher = ConfigFileWatcher(temp_config_file, mock_reload_callback)
        watcher._is_watching = True
        
        async def mock_sleep(duration):
            # Simulate stopping during sleep
            watcher._is_watching = False
        
        with patch('docker_mcp.core.file_watcher.awatch') as mock_awatch, \
             patch('asyncio.sleep', side_effect=mock_sleep):
            mock_awatch.side_effect = Exception("Simulated error")
            
            await watcher._watch_files()
            
            # Should have stopped watching and not restarted


class TestHotReloadManager:
    """Test hot reload management functionality."""

    @pytest.fixture
    def mock_server(self):
        """Create mock server instance for testing."""
        mock_server = MagicMock()
        mock_server.config = DockerMCPConfig(hosts={
            "existing-host": DockerHost(
                hostname="existing.example.com",
                user="existing",
                enabled=True
            )
        })
        
        # Mock context manager
        mock_context_manager = MagicMock()
        mock_context_manager._context_cache = {"existing-host": "existing-context"}
        mock_server.context_manager = mock_context_manager
        
        return mock_server

    @pytest.fixture
    def temp_config_file(self):
        """Create temporary configuration file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("""
hosts:
  test-host:
    hostname: test.example.com
    user: testuser

server:
  host: 127.0.0.1
  port: 8000
""")
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        Path(temp_path).unlink(missing_ok=True)

    def test_hot_reload_manager_creation(self):
        """Test creating HotReloadManager."""
        manager = HotReloadManager()
        
        assert manager.config_watcher is None
        assert manager._server_instance is None

    def test_setup_hot_reload(self, temp_config_file, mock_server):
        """Test setting up hot reload with config file and server."""
        manager = HotReloadManager()
        
        manager.setup_hot_reload(temp_config_file, mock_server)
        
        assert manager._server_instance == mock_server
        assert manager.config_watcher is not None
        assert manager.config_watcher.config_path == Path(temp_config_file)

    @pytest.mark.asyncio
    async def test_start_hot_reload_with_watcher(self, temp_config_file, mock_server):
        """Test starting hot reload when watcher is configured."""
        manager = HotReloadManager()
        manager.setup_hot_reload(temp_config_file, mock_server)
        
        with patch.object(manager.config_watcher, 'start_watching') as mock_start:
            await manager.start_hot_reload()
            mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_hot_reload_without_watcher(self):
        """Test starting hot reload when no watcher is configured."""
        manager = HotReloadManager()
        
        # Should handle gracefully
        await manager.start_hot_reload()

    @pytest.mark.asyncio
    async def test_stop_hot_reload_with_watcher(self, temp_config_file, mock_server):
        """Test stopping hot reload when watcher is configured."""
        manager = HotReloadManager()
        manager.setup_hot_reload(temp_config_file, mock_server)
        
        with patch.object(manager.config_watcher, 'stop_watching') as mock_stop:
            await manager.stop_hot_reload()
            mock_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_hot_reload_without_watcher(self):
        """Test stopping hot reload when no watcher is configured."""
        manager = HotReloadManager()
        
        # Should handle gracefully
        await manager.stop_hot_reload()

    @pytest.mark.asyncio
    async def test_reload_server_config_success(self, mock_server):
        """Test successful server configuration reload."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        new_config = DockerMCPConfig(hosts={
            "new-host": DockerHost(hostname="new.example.com", user="new")
        })
        
        await manager._reload_server_config(new_config)
        
        # Should have called update_configuration on server
        mock_server.update_configuration.assert_called_once_with(new_config)

    @pytest.mark.asyncio
    async def test_reload_server_config_no_server(self):
        """Test server configuration reload without server instance."""
        manager = HotReloadManager()
        
        new_config = DockerMCPConfig()
        
        # Should handle gracefully
        await manager._reload_server_config(new_config)

    @pytest.mark.asyncio
    async def test_reload_server_config_server_error(self, mock_server):
        """Test server configuration reload with server error."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        # Make server update_configuration raise an error
        mock_server.update_configuration.side_effect = Exception("Server error")
        
        new_config = DockerMCPConfig()
        
        with pytest.raises(ConfigurationError, match="Hot reload failed"):
            await manager._reload_server_config(new_config)

    def test_detect_host_changes_no_server(self):
        """Test detecting host changes without server instance."""
        manager = HotReloadManager()
        
        new_config = DockerMCPConfig()
        changes = manager._detect_host_changes(new_config)
        
        expected = {"added": set(), "removed": set(), "updated": set()}
        assert changes == expected

    def test_detect_host_changes_added_hosts(self, mock_server):
        """Test detecting added hosts."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        new_config = DockerMCPConfig(hosts={
            "existing-host": DockerHost(hostname="existing.example.com", user="existing"),
            "new-host": DockerHost(hostname="new.example.com", user="new")
        })
        
        changes = manager._detect_host_changes(new_config)
        
        assert changes["added"] == {"new-host"}
        assert changes["removed"] == set()
        assert changes["updated"] == set()

    def test_detect_host_changes_removed_hosts(self, mock_server):
        """Test detecting removed hosts."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        new_config = DockerMCPConfig(hosts={})
        
        changes = manager._detect_host_changes(new_config)
        
        assert changes["added"] == set()
        assert changes["removed"] == {"existing-host"}
        assert changes["updated"] == set()

    def test_detect_host_changes_updated_hosts(self, mock_server):
        """Test detecting updated hosts."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        # Same host ID but different configuration
        new_config = DockerMCPConfig(hosts={
            "existing-host": DockerHost(
                hostname="updated.example.com",  # Changed hostname
                user="existing",
                enabled=True
            )
        })
        
        changes = manager._detect_host_changes(new_config)
        
        assert changes["added"] == set()
        assert changes["removed"] == set()
        assert changes["updated"] == {"existing-host"}

    def test_is_host_updated_no_server(self):
        """Test checking if host is updated without server instance."""
        manager = HotReloadManager()
        
        new_config = DockerMCPConfig()
        is_updated = manager._is_host_updated("any-host", new_config)
        
        assert is_updated is False

    def test_is_host_updated_hostname_changed(self, mock_server):
        """Test detecting hostname changes."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        new_config = DockerMCPConfig(hosts={
            "existing-host": DockerHost(
                hostname="changed.example.com",  # Different hostname
                user="existing",
                enabled=True
            )
        })
        
        is_updated = manager._is_host_updated("existing-host", new_config)
        assert is_updated is True

    def test_is_host_updated_user_changed(self, mock_server):
        """Test detecting user changes."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        new_config = DockerMCPConfig(hosts={
            "existing-host": DockerHost(
                hostname="existing.example.com",
                user="changed-user",  # Different user
                enabled=True
            )
        })
        
        is_updated = manager._is_host_updated("existing-host", new_config)
        assert is_updated is True

    def test_is_host_updated_enabled_changed(self, mock_server):
        """Test detecting enabled status changes."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        new_config = DockerMCPConfig(hosts={
            "existing-host": DockerHost(
                hostname="existing.example.com",
                user="existing",
                enabled=False  # Different enabled status
            )
        })
        
        is_updated = manager._is_host_updated("existing-host", new_config)
        assert is_updated is True

    def test_is_host_updated_no_changes(self, mock_server):
        """Test detecting no changes in host."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        # Identical configuration
        new_config = DockerMCPConfig(hosts={
            "existing-host": DockerHost(
                hostname="existing.example.com",
                user="existing",
                enabled=True
            )
        })
        
        is_updated = manager._is_host_updated("existing-host", new_config)
        assert is_updated is False

    def test_log_host_changes_all_types(self):
        """Test logging all types of host changes."""
        manager = HotReloadManager()
        
        host_changes = {
            "added": {"new-host1", "new-host2"},
            "removed": {"old-host1"},
            "updated": {"updated-host1", "updated-host2"}
        }
        
        # Should not raise exceptions
        manager._log_host_changes(host_changes)

    def test_log_host_changes_empty(self):
        """Test logging with no host changes."""
        manager = HotReloadManager()
        
        host_changes = {
            "added": set(),
            "removed": set(),
            "updated": set()
        }
        
        # Should not raise exceptions
        manager._log_host_changes(host_changes)

    def test_clear_context_cache_no_server(self):
        """Test clearing context cache without server instance."""
        manager = HotReloadManager()
        
        host_changes = {
            "added": set(),
            "removed": {"removed-host"},
            "updated": {"updated-host"}
        }
        
        # Should handle gracefully
        manager._clear_context_cache(host_changes)

    def test_clear_context_cache_no_context_manager(self, mock_server):
        """Test clearing context cache when server has no context manager."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        del mock_server.context_manager  # Remove context manager
        
        host_changes = {
            "added": set(),
            "removed": {"removed-host"},
            "updated": {"updated-host"}
        }
        
        # Should handle gracefully
        manager._clear_context_cache(host_changes)

    def test_clear_context_cache_success(self, mock_server):
        """Test successful context cache clearing."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        # Add more entries to context cache
        mock_server.context_manager._context_cache["updated-host"] = "updated-context"
        mock_server.context_manager._context_cache["removed-host"] = "removed-context"
        
        host_changes = {
            "added": {"new-host"},
            "removed": {"removed-host"},
            "updated": {"updated-host"}
        }
        
        manager._clear_context_cache(host_changes)
        
        # Should have removed contexts for updated and removed hosts
        assert "updated-host" not in mock_server.context_manager._context_cache
        assert "removed-host" not in mock_server.context_manager._context_cache
        assert "existing-host" in mock_server.context_manager._context_cache  # Should remain

    def test_clear_context_cache_host_not_in_cache(self, mock_server):
        """Test clearing context cache for hosts not in cache."""
        manager = HotReloadManager()
        manager._server_instance = mock_server
        
        host_changes = {
            "added": set(),
            "removed": {"non-cached-host"},
            "updated": {"another-non-cached-host"}
        }
        
        # Should handle gracefully
        manager._clear_context_cache(host_changes)
        
        # Original cache should remain unchanged
        assert "existing-host" in mock_server.context_manager._context_cache


class TestFileWatcherIntegration:
    """Integration tests for file watcher and hot reload functionality."""

    @pytest.fixture
    def temp_config_file(self):
        """Create temporary configuration file for integration testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("""
hosts:
  integration-host:
    hostname: integration.example.com
    user: integration
    enabled: true

server:
  host: 127.0.0.1
  port: 8000
""")
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        Path(temp_path).unlink(missing_ok=True)

    @pytest.fixture
    def mock_server_with_context(self):
        """Create mock server with context manager for integration testing."""
        mock_server = MagicMock()
        mock_server.config = DockerMCPConfig(hosts={
            "integration-host": DockerHost(
                hostname="integration.example.com",
                user="integration",
                enabled=True
            )
        })
        
        # Mock context manager with cache
        mock_context_manager = MagicMock()
        mock_context_manager._context_cache = {"integration-host": "integration-context"}
        mock_server.context_manager = mock_context_manager
        
        return mock_server

    @pytest.mark.asyncio
    async def test_hot_reload_complete_workflow(self, temp_config_file, mock_server_with_context):
        """Test complete hot reload workflow from setup to execution."""
        manager = HotReloadManager()
        
        # Setup hot reload
        manager.setup_hot_reload(temp_config_file, mock_server_with_context)
        
        # Start watching
        with patch.object(manager.config_watcher, 'start_watching') as mock_start:
            await manager.start_hot_reload()
            mock_start.assert_called_once()
        
        # Simulate configuration change
        new_config = DockerMCPConfig(hosts={
            "integration-host": DockerHost(
                hostname="updated-integration.example.com",  # Changed hostname
                user="integration",
                enabled=True
            ),
            "new-integration-host": DockerHost(
                hostname="new.example.com",
                user="new"
            )
        })
        
        # Trigger reload
        await manager._reload_server_config(new_config)
        
        # Verify server was updated
        mock_server_with_context.update_configuration.assert_called_once_with(new_config)
        
        # Verify context cache was cleared for updated host
        assert "integration-host" not in mock_server_with_context.context_manager._context_cache
        
        # Stop watching
        with patch.object(manager.config_watcher, 'stop_watching') as mock_stop:
            await manager.stop_hot_reload()
            mock_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_file_watcher_integration_with_real_changes(self, temp_config_file):
        """Test ConfigFileWatcher with simulated real file changes."""
        reload_calls = []
        
        async def capture_reload(config):
            reload_calls.append(config)
        
        watcher = ConfigFileWatcher(temp_config_file, capture_reload)
        
        # Test configuration change handling
        await watcher._handle_config_change()
        
        # Should have captured one reload call
        assert len(reload_calls) == 1
        assert isinstance(reload_calls[0], DockerMCPConfig)
        assert "integration-host" in reload_calls[0].hosts

    @pytest.mark.asyncio
    async def test_error_recovery_and_resilience(self, temp_config_file, mock_server_with_context):
        """Test error recovery and system resilience."""
        manager = HotReloadManager()
        manager.setup_hot_reload(temp_config_file, mock_server_with_context)
        
        # Test server error during reload
        mock_server_with_context.update_configuration.side_effect = Exception("Server busy")
        
        new_config = DockerMCPConfig()
        
        with pytest.raises(ConfigurationError):
            await manager._reload_server_config(new_config)
        
        # Reset server to working state
        mock_server_with_context.update_configuration.side_effect = None
        
        # Should work again after error
        await manager._reload_server_config(new_config)
        
        # Should have been called twice (once failed, once succeeded)
        assert mock_server_with_context.update_configuration.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_config_changes(self, temp_config_file):
        """Test handling concurrent configuration changes."""
        reload_call_count = 0
        
        async def count_reloads(config):
            nonlocal reload_call_count
            reload_call_count += 1
            # Simulate some processing time
            await asyncio.sleep(0.01)
        
        watcher = ConfigFileWatcher(temp_config_file, count_reloads)
        
        # Simulate multiple rapid changes
        tasks = []
        for _ in range(5):
            tasks.append(asyncio.create_task(watcher._handle_config_change()))
        
        await asyncio.gather(*tasks)
        
        # All changes should have been processed
        assert reload_call_count == 5

    def test_config_hash_consistency_across_instances(self, temp_config_file):
        """Test that config hash is consistent across different watcher instances."""
        callback1 = AsyncMock()
        callback2 = AsyncMock()
        
        watcher1 = ConfigFileWatcher(temp_config_file, callback1)
        watcher2 = ConfigFileWatcher(temp_config_file, callback2)
        
        config = DockerMCPConfig(hosts={
            "consistent-host": DockerHost(hostname="consistent.example.com", user="user")
        })
        
        hash1 = watcher1._calculate_config_hash(config)
        hash2 = watcher2._calculate_config_hash(config)
        
        assert hash1 == hash2
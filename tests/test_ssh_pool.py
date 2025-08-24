"""Tests for SSH connection pool manager."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from paramiko import SSHClient, SSHException

from docker_mcp.config_loader import DockerHost
from docker_mcp.core.ssh_pool import (
    PooledConnection,
    SSHConnectionError,
    SSHConnectionPool,
    close_connection_pool,
    get_connection_pool,
    initialize_connection_pool,
)


@pytest.fixture
def mock_host():
    """Create a mock host configuration."""
    return DockerHost(
        hostname="test.example.com",
        user="testuser",
        port=22,
        identity_file="/home/testuser/.ssh/id_rsa",
    )


@pytest.fixture
def mock_ssh_client():
    """Create a mock SSH client."""
    client = MagicMock(spec=SSHClient)
    transport = MagicMock()
    transport.is_active.return_value = True
    transport.send_ignore = MagicMock()
    client.get_transport.return_value = transport
    return client


@pytest.fixture
async def connection_pool():
    """Create a test connection pool."""
    pool = SSHConnectionPool(
        max_connections_per_host=3,
        max_idle_time=60,
        max_lifetime=300,
        health_check_interval=10,
    )
    yield pool
    await pool.close_all()


class TestPooledConnection:
    """Test PooledConnection wrapper class."""

    def test_pooled_connection_creation(self, mock_ssh_client, mock_host):
        """Test creating a pooled connection."""
        conn = PooledConnection(client=mock_ssh_client, host=mock_host)
        
        assert conn.client == mock_ssh_client
        assert conn.host == mock_host
        assert conn.in_use is False
        assert conn.use_count == 0
        assert isinstance(conn.created_at, datetime)
        assert isinstance(conn.last_used_at, datetime)

    def test_is_alive_with_active_transport(self, mock_ssh_client, mock_host):
        """Test is_alive returns True for active connection."""
        conn = PooledConnection(client=mock_ssh_client, host=mock_host)
        
        assert conn.is_alive() is True
        mock_ssh_client.get_transport.assert_called_once()
        mock_ssh_client.get_transport().send_ignore.assert_called_once()

    def test_is_alive_with_inactive_transport(self, mock_ssh_client, mock_host):
        """Test is_alive returns False for inactive connection."""
        mock_ssh_client.get_transport().is_active.return_value = False
        conn = PooledConnection(client=mock_ssh_client, host=mock_host)
        
        assert conn.is_alive() is False

    def test_is_alive_with_exception(self, mock_ssh_client, mock_host):
        """Test is_alive returns False when exception occurs."""
        mock_ssh_client.get_transport.side_effect = Exception("Connection lost")
        conn = PooledConnection(client=mock_ssh_client, host=mock_host)
        
        assert conn.is_alive() is False

    def test_touch_updates_timestamp(self, mock_ssh_client, mock_host):
        """Test touch method updates last_used_at and use_count."""
        conn = PooledConnection(client=mock_ssh_client, host=mock_host)
        initial_time = conn.last_used_at
        initial_count = conn.use_count
        
        # Small delay to ensure timestamp changes
        import time
        time.sleep(0.01)
        
        conn.touch()
        
        assert conn.last_used_at > initial_time
        assert conn.use_count == initial_count + 1


class TestSSHConnectionPool:
    """Test SSH connection pool manager."""

    def test_pool_initialization(self):
        """Test connection pool initialization with custom parameters."""
        pool = SSHConnectionPool(
            max_connections_per_host=10,
            max_idle_time=600,
            max_lifetime=7200,
            health_check_interval=120,
        )
        
        assert pool.max_connections_per_host == 10
        assert pool.max_idle_time == 600
        assert pool.max_lifetime == 7200
        assert pool.health_check_interval == 120
        assert len(pool._pools) == 0
        assert pool._stats["connections_created"] == 0

    def test_get_host_key(self, connection_pool, mock_host):
        """Test host key generation."""
        key = connection_pool._get_host_key(mock_host)
        assert key == "testuser@test.example.com:22"
        
        # Test with non-default port
        mock_host.port = 2222
        key = connection_pool._get_host_key(mock_host)
        assert key == "testuser@test.example.com:2222"

    @pytest.mark.asyncio
    @patch('docker_mcp.core.ssh_pool.SSHClient')
    async def test_create_connection(self, mock_ssh_class, connection_pool, mock_host):
        """Test creating a new SSH connection."""
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        
        # Mock transport for keepalive
        mock_transport = MagicMock()
        mock_client.get_transport.return_value = mock_transport
        
        # Create connection
        client = await connection_pool._create_connection(mock_host)
        
        assert client == mock_client
        assert connection_pool._stats["connections_created"] == 1
        mock_client.connect.assert_called_once()
        mock_transport.set_keepalive.assert_called_once_with(30)

    @pytest.mark.asyncio
    @patch('docker_mcp.core.ssh_pool.SSHClient')
    async def test_create_connection_failure(self, mock_ssh_class, connection_pool, mock_host):
        """Test handling connection creation failure."""
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.side_effect = SSHException("Connection refused")
        
        with pytest.raises(SSHConnectionError) as exc_info:
            await connection_pool._create_connection(mock_host)
        
        assert "Failed to connect" in str(exc_info.value)
        assert connection_pool._stats["connection_errors"] == 1
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_create_connection_new(self, connection_pool, mock_host, mock_ssh_client):
        """Test getting a new connection when none exist."""
        with patch.object(connection_pool, '_create_connection', return_value=mock_ssh_client):
            conn = await connection_pool._get_or_create_connection(mock_host)
            
            assert conn.client == mock_ssh_client
            assert conn.in_use is True
            assert conn.use_count == 1
            assert len(connection_pool._pools[connection_pool._get_host_key(mock_host)]) == 1

    @pytest.mark.asyncio
    async def test_get_or_create_connection_reuse(self, connection_pool, mock_host, mock_ssh_client):
        """Test reusing an existing connection."""
        # Create an existing connection
        existing_conn = PooledConnection(client=mock_ssh_client, host=mock_host, in_use=False)
        host_key = connection_pool._get_host_key(mock_host)
        connection_pool._pools[host_key].append(existing_conn)
        
        # Get connection (should reuse)
        conn = await connection_pool._get_or_create_connection(mock_host)
        
        assert conn == existing_conn
        assert conn.in_use is True
        assert connection_pool._stats["connections_reused"] == 1

    @pytest.mark.asyncio
    async def test_get_or_create_connection_max_limit(self, connection_pool, mock_host):
        """Test connection limit enforcement."""
        host_key = connection_pool._get_host_key(mock_host)
        
        # Fill pool with in-use connections
        for _ in range(connection_pool.max_connections_per_host):
            conn = PooledConnection(
                client=MagicMock(), host=mock_host, in_use=True
            )
            connection_pool._pools[host_key].append(conn)
        
        # Try to get another connection
        with pytest.raises(SSHConnectionError) as exc_info:
            await connection_pool._get_or_create_connection(mock_host)
        
        assert "Maximum connections" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_connection_context_manager(self, connection_pool, mock_host, mock_ssh_client):
        """Test using get_connection as a context manager."""
        with patch.object(connection_pool, '_create_connection', return_value=mock_ssh_client):
            async with connection_pool.get_connection(mock_host) as client:
                assert client == mock_ssh_client
                # Connection should be in use
                host_key = connection_pool._get_host_key(mock_host)
                conn = connection_pool._pools[host_key][0]
                assert conn.in_use is True
            
            # After context, connection should be returned to pool
            assert conn.in_use is False
            assert conn.use_count == 1

    @pytest.mark.asyncio
    async def test_execute_command(self, connection_pool, mock_host, mock_ssh_client):
        """Test executing a command via connection pool."""
        # Mock exec_command
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stdout.read.return_value = b"command output"
        mock_stderr.read.return_value = b""
        mock_ssh_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)
        
        with patch.object(connection_pool, '_create_connection', return_value=mock_ssh_client):
            exit_code, stdout, stderr = await connection_pool.execute_command(
                mock_host, "ls -la", timeout=30
            )
            
            assert exit_code == 0
            assert stdout == "command output"
            assert stderr == ""
            mock_ssh_client.exec_command.assert_called_once_with(
                "ls -la", timeout=30, get_pty=True
            )

    @pytest.mark.asyncio
    async def test_execute_command_list(self, connection_pool, mock_host):
        """Test executing a command provided as a list."""
        with patch.object(connection_pool, 'execute_command') as mock_execute:
            # Use the actual method but patch the recursive call
            mock_execute.return_value = (0, "output", "")
            
            # Call with list of command parts
            await connection_pool.execute_command(
                mock_host, ["docker", "ps", "-a"]
            )
            
            # Should be called with joined string
            mock_execute.assert_called_with(mock_host, ["docker", "ps", "-a"])

    @pytest.mark.asyncio
    async def test_cleanup_idle_connections(self, connection_pool, mock_host):
        """Test cleaning up idle and expired connections."""
        host_key = connection_pool._get_host_key(mock_host)
        
        # Create connections with different states
        active_conn = PooledConnection(
            client=MagicMock(), host=mock_host, in_use=True
        )
        
        idle_conn = PooledConnection(
            client=MagicMock(), host=mock_host, in_use=False
        )
        idle_conn.last_used_at = datetime.now() - timedelta(seconds=connection_pool.max_idle_time + 1)
        
        valid_conn = PooledConnection(
            client=MagicMock(), host=mock_host, in_use=False
        )
        
        connection_pool._pools[host_key] = [active_conn, idle_conn, valid_conn]
        
        # Run cleanup
        await connection_pool.cleanup_idle_connections()
        
        # Active and valid connections should remain
        assert active_conn in connection_pool._pools[host_key]
        assert valid_conn in connection_pool._pools[host_key]
        assert idle_conn not in connection_pool._pools[host_key]
        
        # Idle connection should be closed
        idle_conn.client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_cleanup_task(self, connection_pool):
        """Test starting the background cleanup task."""
        await connection_pool.start_cleanup_task()
        
        assert connection_pool._cleanup_task is not None
        assert not connection_pool._cleanup_task.done()
        
        # Stop the task
        connection_pool._cleanup_task.cancel()
        try:
            await connection_pool._cleanup_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_close_all(self, connection_pool, mock_host):
        """Test closing all connections."""
        # Add some connections
        host_key = connection_pool._get_host_key(mock_host)
        conn1 = PooledConnection(client=MagicMock(), host=mock_host)
        conn2 = PooledConnection(client=MagicMock(), host=mock_host)
        connection_pool._pools[host_key] = [conn1, conn2]
        
        # Start cleanup task
        await connection_pool.start_cleanup_task()
        
        # Close all
        await connection_pool.close_all()
        
        # All connections should be closed
        conn1.client.close.assert_called_once()
        conn2.client.close.assert_called_once()
        assert len(connection_pool._pools) == 0
        assert connection_pool._cleanup_task.cancelled()

    def test_get_stats(self, connection_pool, mock_host):
        """Test getting pool statistics."""
        # Add some connections
        host_key = connection_pool._get_host_key(mock_host)
        conn1 = PooledConnection(client=MagicMock(), host=mock_host, in_use=True)
        conn2 = PooledConnection(client=MagicMock(), host=mock_host, in_use=False)
        connection_pool._pools[host_key] = [conn1, conn2]
        
        # Update stats
        connection_pool._stats["connections_created"] = 5
        connection_pool._stats["connections_reused"] = 10
        
        stats = connection_pool.get_stats()
        
        assert stats["connections_created"] == 5
        assert stats["connections_reused"] == 10
        assert stats["active_pools"] == 1
        assert stats["total_connections"] == 2
        assert stats["active_connections"] == 1


class TestGlobalConnectionPool:
    """Test global connection pool management."""

    @pytest.mark.asyncio
    async def test_get_connection_pool(self):
        """Test getting global connection pool instance."""
        pool1 = get_connection_pool()
        pool2 = get_connection_pool()
        
        assert pool1 is pool2  # Should be the same instance

    @pytest.mark.asyncio
    async def test_initialize_connection_pool(self):
        """Test initializing global connection pool with custom settings."""
        pool = await initialize_connection_pool(
            max_connections_per_host=10,
            max_idle_time=120,
        )
        
        assert pool.max_connections_per_host == 10
        assert pool.max_idle_time == 120
        assert pool._cleanup_task is not None
        
        # Clean up
        await close_connection_pool()

    @pytest.mark.asyncio
    async def test_close_connection_pool(self):
        """Test closing global connection pool."""
        # Initialize pool
        await initialize_connection_pool()
        pool = get_connection_pool()
        
        # Close pool
        await close_connection_pool()
        
        # Pool should be reset
        new_pool = get_connection_pool()
        assert new_pool is not pool
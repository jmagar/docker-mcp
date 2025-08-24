"""SSH connection pool manager for optimized remote operations."""

import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Optional

import paramiko
import structlog
from paramiko import AutoAddPolicy, SSHClient
from paramiko.ssh_exception import SSHException

from ..config_loader import DockerHost
from ..core.exceptions import DockerMCPError

logger = structlog.get_logger()


class SSHConnectionError(DockerMCPError):
    """SSH connection related errors."""
    pass


@dataclass
class PooledConnection:
    """Wrapper for a pooled SSH connection."""
    
    client: SSHClient
    host: DockerHost
    created_at: datetime = field(default_factory=datetime.now)
    last_used_at: datetime = field(default_factory=datetime.now)
    in_use: bool = False
    use_count: int = 0
    
    def is_alive(self) -> bool:
        """Check if the connection is still alive."""
        try:
            # Send a keepalive packet
            transport = self.client.get_transport()
            if transport and transport.is_active():
                transport.send_ignore()
                return True
        except Exception:
            pass
        return False
    
    def touch(self):
        """Update last used timestamp."""
        self.last_used_at = datetime.now()
        self.use_count += 1


class SSHConnectionPool:
    """Manages a pool of SSH connections for efficient reuse."""
    
    def __init__(
        self,
        max_connections_per_host: int = 5,
        max_idle_time: int = 300,  # 5 minutes
        max_lifetime: int = 3600,  # 1 hour
        health_check_interval: int = 60,  # 1 minute
    ):
        """Initialize SSH connection pool.
        
        Args:
            max_connections_per_host: Maximum connections per host
            max_idle_time: Maximum idle time in seconds before closing
            max_lifetime: Maximum connection lifetime in seconds
            health_check_interval: Interval for health checks in seconds
        """
        self.max_connections_per_host = max_connections_per_host
        self.max_idle_time = max_idle_time
        self.max_lifetime = max_lifetime
        self.health_check_interval = health_check_interval
        
        # Pool storage: host_key -> list of connections
        self._pools: dict[str, list[PooledConnection]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._stats = {
            "connections_created": 0,
            "connections_reused": 0,
            "connections_closed": 0,
            "connection_errors": 0,
        }
        
        logger.info(
            "SSH connection pool initialized",
            max_connections_per_host=max_connections_per_host,
            max_idle_time=max_idle_time,
            max_lifetime=max_lifetime,
        )
    
    def _get_host_key(self, host: DockerHost) -> str:
        """Generate a unique key for a host configuration."""
        return f"{host.user}@{host.hostname}:{host.port}"
    
    async def _create_connection(self, host: DockerHost) -> SSHClient:
        """Create a new SSH connection to the host."""
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        
        connect_kwargs = {
            "hostname": host.hostname,
            "port": host.port,
            "username": host.user,
            "timeout": 30,
            "banner_timeout": 30,
            "auth_timeout": 30,
        }
        
        # Add authentication parameters
        if host.identity_file:
            connect_kwargs["key_filename"] = host.identity_file
        elif host.password:
            connect_kwargs["password"] = host.password
        
        # Use thread pool for blocking connect operation
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: client.connect(**connect_kwargs)
            )
            
            # Configure keepalive
            transport = client.get_transport()
            if transport:
                transport.set_keepalive(30)  # Send keepalive every 30 seconds
            
            self._stats["connections_created"] += 1
            logger.debug(
                "Created new SSH connection",
                host=self._get_host_key(host),
                total_created=self._stats["connections_created"],
            )
            
            return client
            
        except Exception as e:
            self._stats["connection_errors"] += 1
            client.close()
            raise SSHConnectionError(f"Failed to connect to {host.hostname}: {str(e)}")
    
    async def _get_or_create_connection(self, host: DockerHost) -> PooledConnection:
        """Get an existing connection or create a new one."""
        host_key = self._get_host_key(host)
        pool = self._pools[host_key]
        
        # Find an available connection
        for conn in pool:
            if not conn.in_use and conn.is_alive():
                idle_time = (datetime.now() - conn.last_used_at).total_seconds()
                lifetime = (datetime.now() - conn.created_at).total_seconds()
                
                # Check if connection is still valid
                if idle_time < self.max_idle_time and lifetime < self.max_lifetime:
                    conn.in_use = True
                    conn.touch()
                    self._stats["connections_reused"] += 1
                    logger.debug(
                        "Reusing existing connection",
                        host=host_key,
                        use_count=conn.use_count,
                        idle_time=idle_time,
                    )
                    return conn
                else:
                    # Connection expired, remove it
                    await self._close_connection(conn)
                    pool.remove(conn)
        
        # Check if we can create a new connection
        active_connections = sum(1 for c in pool if c.in_use)
        if active_connections >= self.max_connections_per_host:
            raise SSHConnectionError(
                f"Maximum connections ({self.max_connections_per_host}) reached for {host_key}"
            )
        
        # Create new connection
        client = await self._create_connection(host)
        conn = PooledConnection(
            client=client,
            host=host,
            in_use=True,
        )
        conn.touch()
        pool.append(conn)
        
        return conn
    
    async def _close_connection(self, conn: PooledConnection):
        """Close an SSH connection."""
        try:
            conn.client.close()
            self._stats["connections_closed"] += 1
            logger.debug(
                "Closed SSH connection",
                host=self._get_host_key(conn.host),
                use_count=conn.use_count,
                lifetime=(datetime.now() - conn.created_at).total_seconds(),
            )
        except Exception as e:
            logger.warning(
                "Error closing SSH connection",
                host=self._get_host_key(conn.host),
                error=str(e),
            )
    
    @asynccontextmanager
    async def get_connection(self, host: DockerHost) -> AsyncGenerator[SSHClient, None]:
        """Get an SSH connection from the pool.
        
        Args:
            host: Host configuration
            
        Yields:
            SSHClient instance
        """
        async with self._lock:
            conn = await self._get_or_create_connection(host)
        
        try:
            yield conn.client
        finally:
            # Return connection to pool
            async with self._lock:
                conn.in_use = False
                conn.touch()
    
    async def execute_command(
        self,
        host: DockerHost,
        command: str | list[str],
        timeout: int = 300,
    ) -> tuple[int, str, str]:
        """Execute a command on a remote host using a pooled connection.
        
        Args:
            host: Host configuration
            command: Command to execute (string or list)
            timeout: Command timeout in seconds
            
        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if isinstance(command, list):
            command = " ".join(command)
        
        async with self.get_connection(host) as client:
            try:
                # Use thread pool for blocking exec_command
                loop = asyncio.get_event_loop()
                
                def _execute():
                    stdin, stdout, stderr = client.exec_command(
                        command,
                        timeout=timeout,
                        get_pty=True,  # Get pseudo-terminal for better output
                    )
                    
                    # Read output
                    exit_code = stdout.channel.recv_exit_status()
                    stdout_data = stdout.read().decode('utf-8', errors='ignore')
                    stderr_data = stderr.read().decode('utf-8', errors='ignore')
                    
                    return exit_code, stdout_data, stderr_data
                
                result = await loop.run_in_executor(None, _execute)
                
                logger.debug(
                    "Executed SSH command",
                    host=self._get_host_key(host),
                    command=command[:100],  # Log first 100 chars
                    exit_code=result[0],
                )
                
                return result
                
            except Exception as e:
                logger.error(
                    "Failed to execute SSH command",
                    host=self._get_host_key(host),
                    command=command[:100],
                    error=str(e),
                )
                raise SSHConnectionError(f"Command execution failed: {str(e)}")
    
    async def cleanup_idle_connections(self):
        """Clean up idle and expired connections."""
        async with self._lock:
            now = datetime.now()
            
            for host_key, pool in self._pools.items():
                connections_to_remove = []
                
                for conn in pool:
                    if conn.in_use:
                        continue
                    
                    idle_time = (now - conn.last_used_at).total_seconds()
                    lifetime = (now - conn.created_at).total_seconds()
                    
                    # Check if connection should be closed
                    should_close = (
                        idle_time > self.max_idle_time or
                        lifetime > self.max_lifetime or
                        not conn.is_alive()
                    )
                    
                    if should_close:
                        await self._close_connection(conn)
                        connections_to_remove.append(conn)
                
                # Remove closed connections
                for conn in connections_to_remove:
                    pool.remove(conn)
                
                # Clean up empty pools
                if not pool:
                    del self._pools[host_key]
            
            logger.debug(
                "Cleaned up idle connections",
                pools=len(self._pools),
                total_connections=sum(len(p) for p in self._pools.values()),
            )
    
    async def start_cleanup_task(self):
        """Start the background cleanup task."""
        async def _cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(self.health_check_interval)
                    await self.cleanup_idle_connections()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Error in cleanup task", error=str(e))
        
        self._cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info("Started connection pool cleanup task")
    
    async def close_all(self):
        """Close all connections and stop cleanup task."""
        # Stop cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Close all connections
        async with self._lock:
            for pool in self._pools.values():
                for conn in pool:
                    await self._close_connection(conn)
            
            self._pools.clear()
        
        logger.info(
            "SSH connection pool closed",
            stats=self._stats,
        )
    
    def get_stats(self) -> dict[str, Any]:
        """Get connection pool statistics."""
        return {
            **self._stats,
            "active_pools": len(self._pools),
            "total_connections": sum(len(p) for p in self._pools.values()),
            "active_connections": sum(
                sum(1 for c in p if c.in_use)
                for p in self._pools.values()
            ),
        }


# Global connection pool instance
_connection_pool: Optional[SSHConnectionPool] = None


def get_connection_pool() -> SSHConnectionPool:
    """Get the global SSH connection pool instance."""
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = SSHConnectionPool()
    return _connection_pool


async def initialize_connection_pool(**kwargs):
    """Initialize the global connection pool with custom settings."""
    global _connection_pool
    if _connection_pool:
        await _connection_pool.close_all()
    
    _connection_pool = SSHConnectionPool(**kwargs)
    await _connection_pool.start_cleanup_task()
    return _connection_pool


async def close_connection_pool():
    """Close the global connection pool."""
    global _connection_pool
    if _connection_pool:
        await _connection_pool.close_all()
        _connection_pool = None
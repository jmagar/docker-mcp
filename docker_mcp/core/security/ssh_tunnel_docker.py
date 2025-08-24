"""Docker API over SSH tunnel implementation for enhanced security."""

import asyncio
import contextlib
import json
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import aiohttp
import structlog

from docker_mcp.core.exceptions import DockerMCPError

logger = structlog.get_logger()


class SSHTunnelError(DockerMCPError):
    """SSH tunnel error."""
    pass


class DockerAPITunnel:
    """Manages Docker API access through SSH tunnel.
    
    This provides an alternative to executing Docker commands via SSH by
    establishing a secure tunnel to the Docker API socket on the remote host.
    """
    
    def __init__(
        self,
        host_id: str,
        hostname: str,
        username: str,
        identity_file: str,
        remote_socket: str = "/var/run/docker.sock",
        local_port: Optional[int] = None,
        ssh_port: int = 22
    ):
        """Initialize Docker API tunnel.
        
        Args:
            host_id: Host identifier
            hostname: Remote hostname
            username: SSH username
            identity_file: Path to SSH identity file
            remote_socket: Path to Docker socket on remote host
            local_port: Local port for tunnel (auto-assigned if None)
            ssh_port: SSH port on remote host
        """
        self.host_id = host_id
        self.hostname = hostname
        self.username = username
        self.identity_file = identity_file
        self.remote_socket = remote_socket
        self.local_port = local_port or self._find_free_port()
        self.ssh_port = ssh_port
        
        self._tunnel_process: Optional[subprocess.Popen] = None
        self._tunnel_ready = False
        self._api_url = f"http://localhost:{self.local_port}"
        self._session: Optional[aiohttp.ClientSession] = None
    
    def _find_free_port(self) -> int:
        """Find a free local port for the tunnel.
        
        Returns:
            Available port number
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port
    
    async def __aenter__(self) -> "DockerAPITunnel":
        """Async context manager entry - establish tunnel."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - close tunnel."""
        await self.disconnect()
    
    async def connect(self) -> None:
        """Establish SSH tunnel to Docker API.
        
        Raises:
            SSHTunnelError: If tunnel cannot be established
        """
        if self._tunnel_process:
            return  # Already connected
        
        # Build SSH tunnel command with security options
        ssh_cmd = [
            "ssh",
            "-N",  # No command execution
            "-L", f"{self.local_port}:{self.remote_socket}",  # Local forward
            "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=/etc/ssh/ssh_known_hosts",
            "-o", "PasswordAuthentication=no",
            "-o", "PreferredAuthentications=publickey",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",  # Exit if tunnel fails
            "-o", "ServerAliveInterval=60",
            "-o", "ServerAliveCountMax=3",
            "-o", "LogLevel=ERROR",
            "-i", self.identity_file,
            "-p", str(self.ssh_port),
            f"{self.username}@{self.hostname}"
        ]
        
        try:
            # Start SSH tunnel process
            self._tunnel_process = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.Popen(
                    ssh_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
            )
            
            # Wait for tunnel to be ready
            await self._wait_for_tunnel()
            
            # Create HTTP session for API calls
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                connector=aiohttp.TCPConnector(limit=10)
            )
            
            logger.info(
                "SSH tunnel established",
                host_id=self.host_id,
                local_port=self.local_port
            )
            
        except Exception as e:
            await self.disconnect()
            raise SSHTunnelError(f"Failed to establish tunnel: {e}") from e
    
    async def _wait_for_tunnel(self, timeout: float = 10.0) -> None:
        """Wait for SSH tunnel to be ready.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Raises:
            SSHTunnelError: If tunnel doesn't become ready
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Check if process has failed
            if self._tunnel_process and self._tunnel_process.poll() is not None:
                stderr = self._tunnel_process.stderr.read() if self._tunnel_process.stderr else ""
                raise SSHTunnelError(f"SSH tunnel process failed: {stderr}")
            
            # Try to connect to local port
            try:
                reader, writer = await asyncio.open_connection('localhost', self.local_port)
                writer.close()
                await writer.wait_closed()
                self._tunnel_ready = True
                return
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.5)
        
        raise SSHTunnelError(f"Tunnel not ready after {timeout} seconds")
    
    async def disconnect(self) -> None:
        """Close SSH tunnel and cleanup resources."""
        if self._session:
            await self._session.close()
            self._session = None
        
        if self._tunnel_process:
            try:
                self._tunnel_process.terminate()
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._tunnel_process.wait(timeout=5)
                )
            except subprocess.TimeoutExpired:
                self._tunnel_process.kill()
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._tunnel_process.wait
                )
            finally:
                self._tunnel_process = None
                self._tunnel_ready = False
        
        logger.info(
            "SSH tunnel closed",
            host_id=self.host_id
        )
    
    async def api_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> dict[str, Any]:
        """Make a request to the Docker API through the tunnel.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            **kwargs: Additional request parameters
            
        Returns:
            JSON response from the API
            
        Raises:
            SSHTunnelError: If API request fails
        """
        if not self._session or not self._tunnel_ready:
            raise SSHTunnelError("Tunnel not connected")
        
        url = f"{self._api_url}{endpoint}"
        
        try:
            async with self._session.request(method, url, **kwargs) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            raise SSHTunnelError(f"API request failed: {e}") from e
    
    async def stream_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream response from Docker API.
        
        Args:
            method: HTTP method
            endpoint: API endpoint path
            **kwargs: Additional request parameters
            
        Yields:
            Response lines
            
        Raises:
            SSHTunnelError: If streaming fails
        """
        if not self._session or not self._tunnel_ready:
            raise SSHTunnelError("Tunnel not connected")
        
        url = f"{self._api_url}{endpoint}"
        
        try:
            async with self._session.request(method, url, **kwargs) as response:
                response.raise_for_status()
                async for line in response.content:
                    if line:
                        yield line.decode('utf-8').strip()
        except aiohttp.ClientError as e:
            raise SSHTunnelError(f"Stream request failed: {e}") from e
    
    # Docker API convenience methods
    
    async def list_containers(self, all: bool = False) -> list[dict[str, Any]]:
        """List containers through the API.
        
        Args:
            all: Include stopped containers
            
        Returns:
            List of container information
        """
        params = {"all": "true"} if all else {}
        return await self.api_request("GET", "/containers/json", params=params)
    
    async def inspect_container(self, container_id: str) -> dict[str, Any]:
        """Inspect a container.
        
        Args:
            container_id: Container ID or name
            
        Returns:
            Container details
        """
        return await self.api_request("GET", f"/containers/{container_id}/json")
    
    async def container_logs(
        self,
        container_id: str,
        follow: bool = False,
        tail: Optional[int] = None
    ) -> AsyncIterator[str]:
        """Get container logs.
        
        Args:
            container_id: Container ID or name
            follow: Follow log output
            tail: Number of lines from the end
            
        Yields:
            Log lines
        """
        params = {
            "stdout": "true",
            "stderr": "true",
            "follow": str(follow).lower()
        }
        if tail is not None:
            params["tail"] = str(tail)
        
        async for line in self.stream_request(
            "GET",
            f"/containers/{container_id}/logs",
            params=params
        ):
            yield line
    
    async def start_container(self, container_id: str) -> None:
        """Start a container.
        
        Args:
            container_id: Container ID or name
        """
        await self.api_request("POST", f"/containers/{container_id}/start")
    
    async def stop_container(self, container_id: str, timeout: int = 10) -> None:
        """Stop a container.
        
        Args:
            container_id: Container ID or name
            timeout: Seconds to wait before killing
        """
        await self.api_request(
            "POST",
            f"/containers/{container_id}/stop",
            params={"t": str(timeout)}
        )
    
    async def restart_container(self, container_id: str, timeout: int = 10) -> None:
        """Restart a container.
        
        Args:
            container_id: Container ID or name
            timeout: Seconds to wait before killing
        """
        await self.api_request(
            "POST",
            f"/containers/{container_id}/restart",
            params={"t": str(timeout)}
        )
    
    async def list_images(self) -> list[dict[str, Any]]:
        """List Docker images.
        
        Returns:
            List of image information
        """
        return await self.api_request("GET", "/images/json")
    
    async def pull_image(self, image: str, tag: str = "latest") -> AsyncIterator[dict]:
        """Pull a Docker image.
        
        Args:
            image: Image name
            tag: Image tag
            
        Yields:
            Pull progress updates
        """
        params = {"fromImage": image, "tag": tag}
        
        async for line in self.stream_request(
            "POST",
            "/images/create",
            params=params
        ):
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    
    async def version(self) -> dict[str, Any]:
        """Get Docker version information.
        
        Returns:
            Version information
        """
        return await self.api_request("GET", "/version")
    
    async def info(self) -> dict[str, Any]:
        """Get Docker system information.
        
        Returns:
            System information
        """
        return await self.api_request("GET", "/info")
    
    async def ping(self) -> bool:
        """Ping the Docker API.
        
        Returns:
            True if API is responsive
        """
        try:
            await self.api_request("GET", "/_ping")
            return True
        except Exception:
            return False


class SecureDockerAPIManager:
    """Manages multiple Docker API tunnels with security controls."""
    
    def __init__(self, max_tunnels: int = 10):
        """Initialize API manager.
        
        Args:
            max_tunnels: Maximum concurrent tunnels
        """
        self.max_tunnels = max_tunnels
        self._tunnels: dict[str, DockerAPITunnel] = {}
        self._lock = asyncio.Lock()
    
    @contextlib.asynccontextmanager
    async def get_tunnel(
        self,
        host_id: str,
        hostname: str,
        username: str,
        identity_file: str,
        **kwargs
    ) -> AsyncIterator[DockerAPITunnel]:
        """Get or create a tunnel for a host.
        
        Args:
            host_id: Host identifier
            hostname: Remote hostname
            username: SSH username
            identity_file: SSH key path
            **kwargs: Additional tunnel parameters
            
        Yields:
            Connected tunnel instance
            
        Raises:
            SSHTunnelError: If tunnel cannot be established
        """
        async with self._lock:
            # Check if tunnel exists
            if host_id in self._tunnels:
                tunnel = self._tunnels[host_id]
                # Verify tunnel is still alive
                if await tunnel.ping():
                    yield tunnel
                    return
                else:
                    # Tunnel is dead, remove it
                    await tunnel.disconnect()
                    del self._tunnels[host_id]
            
            # Check tunnel limit
            if len(self._tunnels) >= self.max_tunnels:
                # Close least recently used tunnel
                oldest_id = next(iter(self._tunnels))
                await self._tunnels[oldest_id].disconnect()
                del self._tunnels[oldest_id]
            
            # Create new tunnel
            tunnel = DockerAPITunnel(
                host_id=host_id,
                hostname=hostname,
                username=username,
                identity_file=identity_file,
                **kwargs
            )
            
            try:
                await tunnel.connect()
                self._tunnels[host_id] = tunnel
                yield tunnel
            except Exception:
                await tunnel.disconnect()
                raise
    
    async def close_all(self) -> None:
        """Close all active tunnels."""
        async with self._lock:
            for tunnel in self._tunnels.values():
                await tunnel.disconnect()
            self._tunnels.clear()
    
    async def close_tunnel(self, host_id: str) -> None:
        """Close a specific tunnel.
        
        Args:
            host_id: Host identifier
        """
        async with self._lock:
            if host_id in self._tunnels:
                await self._tunnels[host_id].disconnect()
                del self._tunnels[host_id]
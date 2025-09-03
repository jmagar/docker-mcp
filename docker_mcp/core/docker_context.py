"""Docker context management for FastMCP Docker SSH Manager.

This module handles Docker context creation and management for SSH-based connections.
It uses Docker's built-in context system for connection pooling and management.
"""

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any

import docker
import structlog

from .config_loader import DockerHost, DockerMCPConfig
from .exceptions import DockerContextError

logger = structlog.get_logger()

# Docker SDK client timeout - configurable via environment variable
DOCKER_CLIENT_TIMEOUT = int(os.getenv("DOCKER_CLIENT_TIMEOUT", "30"))


def _normalize_hostname(hostname: str) -> str:
    """Normalize hostname for SSH connections to handle case sensitivity issues.

    SSH known_hosts files are case-sensitive, but hostnames should be treated
    case-insensitively for better compatibility.

    Args:
        hostname: The hostname to normalize

    Returns:
        Normalized hostname (lowercase)
    """
    return hostname.lower().strip()


def _build_ssh_url_with_fallback(host_config: DockerHost) -> list[tuple[str, str]]:
    """Build SSH URLs with fallback options for known_hosts compatibility.

    Returns a list of (ssh_url, description) tuples to try in order.
    """
    base_user_host = f"{host_config.user}@{host_config.hostname}"
    normalized_host = f"{host_config.user}@{_normalize_hostname(host_config.hostname)}"

    port_suffix = f":{host_config.port}" if host_config.port != 22 else ""

    urls = []

    # Try original hostname first
    original_url = f"ssh://{base_user_host}{port_suffix}"
    urls.append((original_url, f"original hostname ({host_config.hostname})"))

    # Try normalized hostname if different from original
    if _normalize_hostname(host_config.hostname) != host_config.hostname:
        normalized_url = f"ssh://{normalized_host}{port_suffix}"
        urls.append(
            (normalized_url, f"normalized hostname ({_normalize_hostname(host_config.hostname)})")
        )

    return urls


class DockerContextManager:
    """Manages Docker contexts for SSH connections."""

    def __init__(self, config: DockerMCPConfig):
        self.config = config
        self._context_cache: dict[str, str] = {}
        self._client_cache: dict[str, docker.DockerClient] = {}
        self._docker_bin = shutil.which("docker") or "docker"

    async def _run_docker_command(
        self, args: list[str], timeout: int = 30
    ) -> subprocess.CompletedProcess:
        """Safely execute docker command."""
        cmd = [self._docker_bin] + args
        return await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            ),
        )

    async def ensure_context(self, host_id: str) -> str:
        """Ensure Docker context exists for host."""
        if host_id not in self.config.hosts:
            raise DockerContextError(f"Host {host_id} not configured")

        # Check cache first
        if host_id in self._context_cache:
            context_name = self._context_cache[host_id]
            if await self._context_exists(context_name):
                return context_name
            else:
                # Context was deleted, remove from cache
                del self._context_cache[host_id]

        host_config = self.config.hosts[host_id]
        context_name = host_config.docker_context or f"docker-mcp-{host_id}"

        # Check if context already exists
        if await self._context_exists(context_name):
            logger.debug("Docker context exists", context_name=context_name)
            self._context_cache[host_id] = context_name
            return context_name

        # Create new context
        await self._create_context(context_name, host_config)
        logger.info("Docker context created", context_name=context_name, host_id=host_id)
        self._context_cache[host_id] = context_name
        return context_name

    async def _context_exists(self, context_name: str) -> bool:
        """Check if Docker context exists."""
        try:
            result = await self._run_docker_command(
                ["context", "inspect", context_name], timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False

    async def _create_context(self, context_name: str, host_config: DockerHost) -> None:
        """Create a new Docker context."""
        # Build SSH URL
        ssh_url = f"ssh://{host_config.user}@{host_config.hostname}"
        if host_config.port != 22:
            ssh_url += f":{host_config.port}"

        cmd_args = [
            "context",
            "create",
            context_name,
            "--docker",
            f"host={ssh_url}",
        ]

        if host_config.description:
            cmd_args.extend(["--description", host_config.description])

        try:
            result = await self._run_docker_command(cmd_args, timeout=30)

            if result.returncode != 0:
                raise DockerContextError(f"Failed to create context: {result.stderr}")

        except subprocess.TimeoutExpired as e:
            raise DockerContextError(f"Context creation timed out: {e}") from e
        except Exception as e:
            raise DockerContextError(f"Failed to create context: {e}") from e

    async def execute_docker_command(self, host_id: str, command: str) -> dict[str, Any]:
        """Execute Docker command using context."""
        context_name = await self.ensure_context(host_id)

        # Validate command for security
        self._validate_docker_command(command)

        cmd_args = ["--context", context_name] + command.split()

        try:
            result = await self._run_docker_command(cmd_args, timeout=60)

            if result.returncode != 0:
                logger.error(
                    "Docker command failed", host_id=host_id, command=command, error=result.stderr
                )
                raise DockerContextError(f"Docker command failed: {result.stderr}")

            # Try to parse JSON output for commands that return JSON
            json_commands = ["inspect", "version", "info"]
            command_parts = command.strip().split()

            if command_parts and command_parts[0] in json_commands:
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    logger.warning(
                        "Expected JSON output but got non-JSON",
                        command=command,
                        output_preview=result.stdout[:200],
                    )
                    return {"output": result.stdout.strip()}
            else:
                # For non-JSON commands, return wrapped output
                return {"output": result.stdout.strip()}

        except subprocess.TimeoutExpired as e:
            raise DockerContextError(f"Docker command timed out: {e}") from e
        except Exception as e:
            if isinstance(e, DockerContextError):
                raise
            raise DockerContextError(f"Failed to execute Docker command: {e}") from e

    def _validate_docker_command(self, command: str) -> None:
        """Validate Docker command for security."""
        allowed_commands = {
            "ps",
            "logs",
            "start",
            "stop",
            "restart",
            "stats",
            "compose",
            "pull",
            "build",
            "inspect",
            "images",
            "volume",
            "network",
            "system",
            "info",
            "version",
            "rm",  # Added for test cleanup
        }

        parts = command.strip().split()
        if not parts:
            raise ValueError("Empty command")

        main_command = parts[0]
        if main_command not in allowed_commands:
            raise ValueError(f"Command not allowed: {main_command}")

    async def list_contexts(self) -> list[dict[str, Any]]:
        """List all Docker contexts."""
        try:
            result = await self._run_docker_command(
                ["context", "ls", "--format", "json"], timeout=10
            )

            if result.returncode != 0:
                raise DockerContextError(f"Failed to list contexts: {result.stderr}")

            contexts = []
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        context_data = json.loads(line)
                        contexts.append(context_data)
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse context JSON", line=line)

            return contexts

        except subprocess.TimeoutExpired as e:
            raise DockerContextError(f"Context listing timed out: {e}") from e
        except Exception as e:
            if isinstance(e, DockerContextError):
                raise
            raise DockerContextError(f"Failed to list contexts: {e}") from e

    async def remove_context(self, context_name: str) -> None:
        """Remove a Docker context."""
        try:
            result = await self._run_docker_command(["context", "rm", context_name], timeout=10)

            if result.returncode != 0:
                raise DockerContextError(f"Failed to remove context: {result.stderr}")

            # Remove from cache if present
            for host_id, cached_context in list(self._context_cache.items()):
                if cached_context == context_name:
                    del self._context_cache[host_id]
                    break

            logger.info("Docker context removed", context_name=context_name)

        except subprocess.TimeoutExpired as e:
            raise DockerContextError(f"Context removal timed out: {e}") from e
        except Exception as e:
            if isinstance(e, DockerContextError):
                raise
            raise DockerContextError(f"Failed to remove context: {e}") from e

    async def test_context_connection(self, host_id: str) -> bool:
        """Test Docker connection using context."""
        try:
            context_name = await self.ensure_context(host_id)

            result = await self._run_docker_command(
                ["--context", context_name, "version", "--format", "json"], timeout=15
            )

            if result.returncode == 0:
                try:
                    # Parse version info to verify connection
                    version_data = json.loads(result.stdout)
                    logger.debug(
                        "Docker context test successful",
                        host_id=host_id,
                        context_name=context_name,
                        docker_version=version_data.get("Client", {}).get("Version"),
                    )
                    return True
                except json.JSONDecodeError:
                    logger.warning("Docker version output not JSON", host_id=host_id)
                    return result.returncode == 0
            else:
                logger.warning(
                    "Docker context test failed",
                    host_id=host_id,
                    context_name=context_name,
                    error=result.stderr,
                )
                return False

        except Exception as e:
            logger.error("Docker context test error", host_id=host_id, error=str(e))
            return False

    async def get_client(self, host_id: str) -> docker.DockerClient | None:
        """Get Docker SDK client for a host.

        Creates a Docker SDK client that can connect to the host via SSH.
        Uses Docker contexts to establish the connection.
        """
        try:
            # Check cache first
            if host_id in self._client_cache:
                client = self._client_cache[host_id]
                # Test if client is still alive
                try:
                    client.ping()
                    return client
                except Exception:
                    # Client is dead, remove from cache
                    self._client_cache.pop(host_id, None)

            if host_id not in self.config.hosts:
                raise DockerContextError(f"Host {host_id} not configured")

            # Ensure context exists (for potential fallback use)
            await self.ensure_context(host_id)

            # Create Docker SDK client with paramiko SSH support and hostname fallback
            host_config = self.config.hosts[host_id]
            ssh_urls = _build_ssh_url_with_fallback(host_config)

            # Try each SSH URL variant
            for ssh_url, description in ssh_urls:
                try:
                    # Docker SDK with use_ssh_client=False uses paramiko directly for SSH connections.
                    # This is faster and more reliable than use_ssh_client=True which shells out
                    # to the system SSH command and can have timeout issues.
                    client = docker.DockerClient(base_url=ssh_url, use_ssh_client=False, timeout=DOCKER_CLIENT_TIMEOUT)
                    # Test the connection to ensure it's actually connected to the remote host
                    client.ping()

                    # Validate we're connected to the right host by checking version endpoint
                    version_info = client.version()
                    if not version_info:
                        raise Exception(
                            "Unable to retrieve Docker version - connection may be invalid"
                        )

                    # Cache the working client
                    self._client_cache[host_id] = client

                    if description != f"original hostname ({host_config.hostname})":
                        logger.info(
                            f"Connected to {host_id} using {description} (hostname case fallback)"
                        )
                    else:
                        logger.debug(f"Created Docker SDK client for host {host_id}")
                    return client

                except Exception as e:
                    logger.debug(
                        f"Failed to create Docker SDK client for {host_id} with {description}: {e}"
                    )
                    continue

            # If all direct SSH attempts failed, log final error but don't try docker.from_env()
            # as that would create a localhost client which causes confusion
            logger.warning(
                f"Failed to create Docker SDK client for {host_id}: all SSH connection attempts failed"
            )
            return None

        except Exception as e:
            logger.error(f"Error getting Docker client for {host_id}: {e}")
            return None

"""File watching and hot reload functionality for configuration files."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from watchfiles import awatch

from .config import DockerMCPConfig, load_config
from .exceptions import ConfigurationError

if TYPE_CHECKING:
    from ..server import DockerMCPServer

logger = structlog.get_logger()


class ConfigFileWatcher:
    """Watches configuration files for changes and triggers hot reload."""

    def __init__(
        self, config_path: str, reload_callback: Callable[[DockerMCPConfig], Awaitable[None]]
    ):
        self.config_path = Path(config_path)
        self.reload_callback = reload_callback
        self._watch_task: asyncio.Task | None = None
        self._is_watching = False
        self._last_config_hash: str | None = None

    async def start_watching(self) -> None:
        """Start watching the configuration file for changes."""
        if self._is_watching:
            logger.warning("File watcher is already running")
            return

        if not self.config_path.exists():
            logger.warning("Configuration file does not exist", path=str(self.config_path))
            return

        self._is_watching = True
        self._watch_task = asyncio.create_task(self._watch_files())
        logger.info("Started configuration file watcher", path=str(self.config_path))

    async def stop_watching(self) -> None:
        """Stop watching the configuration file."""
        if not self._is_watching:
            return

        self._is_watching = False
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass

        logger.info("Stopped configuration file watcher")

    async def _watch_files(self) -> None:
        """Watch for file changes and trigger reloads."""
        try:
            # Watch the specific config file
            watch_path = str(self.config_path)

            logger.debug("Starting file watcher", path=watch_path)

            async for changes in awatch(watch_path):
                if not self._is_watching:
                    logger.debug("File watcher stopping - not watching")
                    break

                # Process any changes to the config file
                for change_type, file_path in changes:
                    logger.debug("File change detected", path=file_path, change_type=change_type)
                    # Always trigger reload since we're watching the specific file
                    await self._handle_config_change()
                    break  # Only process one change at a time

        except asyncio.CancelledError:
            logger.debug("File watcher cancelled")
            raise
        except Exception as e:
            logger.error("File watcher error", error=str(e))
            # Try to restart watching after a delay
            if self._is_watching:
                await asyncio.sleep(5)
                if self._is_watching:  # Check again in case we were stopped during sleep
                    logger.info("Restarting file watcher after error")
                    await self._watch_files()

    async def _handle_config_change(self) -> None:
        """Handle configuration file changes."""
        try:
            # Add a small delay to avoid processing partial writes
            await asyncio.sleep(0.1)

            # Load the new configuration
            logger.info("Reloading configuration", path=str(self.config_path))
            new_config = load_config(str(self.config_path))

            # Calculate a simple hash to avoid unnecessary reloads
            config_hash = self._calculate_config_hash(new_config)
            if config_hash == self._last_config_hash:
                logger.debug("Configuration unchanged, skipping reload")
                return

            # Call the reload callback
            await self.reload_callback(new_config)
            self._last_config_hash = config_hash

            logger.info("Configuration reloaded successfully", hosts=list(new_config.hosts.keys()))

        except Exception as e:
            logger.error("Failed to reload configuration", error=str(e), path=str(self.config_path))
            # Don't re-raise - we want to continue watching

    def _calculate_config_hash(self, config: DockerMCPConfig) -> str:
        """Calculate a simple hash of the configuration for change detection."""
        # Create a string representation of key configuration elements
        host_data = []
        for host_id, host_config in config.hosts.items():
            host_data.append(
                f"{host_id}:{host_config.hostname}:{host_config.user}:{host_config.enabled}"
            )

        config_str = "|".join(sorted(host_data))
        return str(hash(config_str))


class HotReloadManager:
    """Manages hot reload functionality for the FastMCP server."""

    def __init__(self) -> None:
        self.config_watcher: ConfigFileWatcher | None = None
        self._server_instance: DockerMCPServer | None = None

    def setup_hot_reload(self, config_path: str, server_instance: "DockerMCPServer") -> None:
        """Setup hot reload for the given configuration file and server instance."""
        self._server_instance = server_instance
        self.config_watcher = ConfigFileWatcher(config_path, self._reload_server_config)

    async def start_hot_reload(self) -> None:
        """Start the hot reload watcher."""
        if self.config_watcher:
            await self.config_watcher.start_watching()

    async def stop_hot_reload(self) -> None:
        """Stop the hot reload watcher."""
        if self.config_watcher:
            await self.config_watcher.stop_watching()

    async def _reload_server_config(self, new_config: DockerMCPConfig) -> None:
        """Reload server configuration while preserving active connections."""
        try:
            if not self._server_instance:
                logger.error("No server instance available for hot reload")
                return

            logger.info("Applying hot configuration reload")

            # Detect configuration changes
            host_changes = self._detect_host_changes(new_config)

            # Log changes
            self._log_host_changes(host_changes)

            # Update the server configuration using the proper method
            self._server_instance.update_configuration(new_config)

            # Clear Docker context cache for updated/removed hosts
            self._clear_context_cache(host_changes)

            logger.info("Hot reload completed successfully")

        except Exception as e:
            logger.error("Hot reload failed", error=str(e))
            raise ConfigurationError(f"Hot reload failed: {e}") from e

    def _detect_host_changes(self, new_config: DockerMCPConfig) -> dict[str, set[str]]:
        """Detect added, removed, and updated hosts."""
        if not self._server_instance:
            return {"added": set(), "removed": set(), "updated": set()}

        old_hosts = set(self._server_instance.config.hosts.keys())
        new_hosts = set(new_config.hosts.keys())

        added_hosts = new_hosts - old_hosts
        removed_hosts = old_hosts - new_hosts
        updated_hosts = set()

        # Check for updated hosts
        for host_id in old_hosts & new_hosts:
            if self._is_host_updated(host_id, new_config):
                updated_hosts.add(host_id)

        return {
            "added": added_hosts,
            "removed": removed_hosts,
            "updated": updated_hosts,
        }

    def _is_host_updated(self, host_id: str, new_config: DockerMCPConfig) -> bool:
        """Check if a host configuration has changed."""
        if not self._server_instance:
            return False

        old_host = self._server_instance.config.hosts[host_id]
        new_host = new_config.hosts[host_id]

        return (
            old_host.hostname != new_host.hostname
            or old_host.user != new_host.user
            or old_host.enabled != new_host.enabled
        )

    def _log_host_changes(self, host_changes: dict[str, set[str]]) -> None:
        """Log host configuration changes."""
        if host_changes["added"]:
            logger.info("Added hosts during hot reload", hosts=list(host_changes["added"]))
        if host_changes["removed"]:
            logger.info("Removed hosts during hot reload", hosts=list(host_changes["removed"]))
        if host_changes["updated"]:
            logger.info("Updated hosts during hot reload", hosts=list(host_changes["updated"]))

    def _clear_context_cache(self, host_changes: dict[str, set[str]]) -> None:
        """Clear Docker context cache for updated/removed hosts."""
        if not self._server_instance or not hasattr(self._server_instance, "context_manager"):
            return

        context_manager = self._server_instance.context_manager
        hosts_to_clear = host_changes["removed"] | host_changes["updated"]

        for host_id in hosts_to_clear:
            if host_id in context_manager._context_cache:
                del context_manager._context_cache[host_id]
                logger.debug("Cleared context cache for host", host_id=host_id)

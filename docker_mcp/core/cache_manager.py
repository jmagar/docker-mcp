#!/usr/bin/env python3
"""
Docker Cache Manager for MCP Server
Runs background inspection with intelligent caching
"""

import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from ..constants import (
    DOCKER_COMPOSE_CONFIG_FILES,
    DOCKER_COMPOSE_PROJECT,
    DOCKER_COMPOSE_SERVICE,
    DOCKER_COMPOSE_WORKING_DIR,
)
from .config_loader import DockerMCPConfig
from .docker_context import DockerContextManager


@dataclass
class CacheConfig:
    """Configuration for cache TTL and update intervals"""

    # Different TTLs for different data types
    STATS_TTL: int = 120  # 2 minutes for CPU/memory stats
    STATUS_TTL: int = 300  # 5 minutes for container status
    CONFIG_TTL: int = 3600  # 1 hour for configuration
    COMPOSE_TTL: int = 7200  # 2 hours for compose data

    # Update intervals
    STATS_UPDATE: int = 30  # Update stats every 30 seconds
    STATUS_UPDATE: int = 60  # Update status every minute
    CONFIG_UPDATE: int = 600  # Update config every 10 minutes

    # Tiered memory retention (different periods for different data types)
    STATS_RETENTION: int = 900  # 15 minutes for CPU/memory stats
    STATUS_RETENTION: int = 1800  # 30 minutes for container status
    CONFIG_RETENTION: int = 3600  # 1 hour for configuration data
    DISK_RETENTION: int = 604800  # Keep 7 days on disk

    # Cleanup intervals
    MEMORY_CLEANUP_INTERVAL: int = 300  # Every 5 minutes
    DISK_CLEANUP_INTERVAL: int = 3600  # Every hour

    # Stats collection performance
    MAX_STATS_CONTAINERS: int = 20  # Max containers to collect stats for
    STATS_SEMAPHORE_LIMIT: int = 5  # Concurrent stats operations


@dataclass
class ContainerCache:
    """Cached container information"""

    host_id: str
    container_id: str
    name: str
    status: str
    image: str
    created: str
    started: str
    uptime: str | None

    # Resource info
    cpu_percent: float
    memory_usage: int
    memory_percent: float
    memory_limit: int

    # Network info
    networks: list[dict[str, str]]
    ports: list[str]
    ip_address: str

    # Compose info
    compose_project: str | None
    compose_service: str | None
    compose_stack_containers: list[str]
    compose_config_files: str | None  # Path to docker-compose.yml
    compose_working_dir: str | None  # Working directory from labels

    # Enhanced Storage info
    bind_mounts: list[str]
    volumes: list[dict[str, str]]  # All volume mounts, not just bind
    volume_drivers: dict[str, str]  # Volume driver info
    working_dir: str | None

    # Network aliases
    network_aliases: dict[str, list[str]]  # Network-specific aliases

    # Labels (for better discovery)
    labels: dict[str, str]  # All container labels

    # Command info
    command: str  # Container command
    entrypoint: str  # Container entrypoint

    # Health info
    health_status: str | None
    restart_count: int
    exit_code: int
    restart_policy: str  # Restart policy

    # Metadata
    cached_at: float
    last_updated: float

    def to_service_dict(self) -> dict[str, Any]:
        """Convert to dict format expected by services."""
        return {
            "id": self.container_id,
            "name": self.name,
            "image": self.image,
            "status": self.status,
            "state": "running" if self.status.lower() in ["running", "up"] else "stopped",
            "ports": self.ports,
            "created": self.created,
            "labels": self.labels,
            "compose_project": self.compose_project,
            "compose_service": self.compose_service,
        }


class DockerCacheManager:
    """Manages cached Docker inspection data across multiple hosts"""

    def __init__(
        self,
        config: DockerMCPConfig,
        context_manager: DockerContextManager,
        cache_dir: Path | None = None,
    ):
        self.config = config
        self.context_manager = context_manager

        self.cache_dir = cache_dir or Path("~/.docker-mcp/cache").expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Cache configuration
        self.cache_config = CacheConfig()

        # Cache storage
        self.containers_cache: dict[
            str, dict[str, ContainerCache]
        ] = {}  # host_id -> container_id -> cache
        self.host_stats_cache: dict[str, dict] = {}  # host_id -> host_stats
        self.cache_timestamps: dict[str, float] = {}  # host_id -> timestamp

        # Use intelligent update intervals and TTLs
        self.update_interval = self.cache_config.STATUS_UPDATE
        self.cache_ttl = self.cache_config.STATUS_TTL
        self.running = False

        # Background task tracking
        self._tasks: list[asyncio.Task] = []

        self.logger = structlog.get_logger()

    async def start(self):
        """Enhanced start with cache warmup and event streaming"""
        self.running = True
        self.logger.info("Starting Docker cache manager...")

        # Load persisted cache for fast startup
        await self._load_cache_from_disk()

        # Start background tasks (don't await - they're infinite loops)
        self._tasks = [
            asyncio.create_task(self._cache_update_loop()),
            asyncio.create_task(self._event_stream_loop()),
            asyncio.create_task(self._cleanup_loop()),
        ]

        self.logger.info(f"Started {len(self._tasks)} background cache tasks")

        # Do initial cache population
        await self._initial_cache_warmup()

    async def stop(self):
        """Stop the cache manager"""
        self.running = False
        self.logger.info("Stopping Docker cache manager...")

        # Cancel all background tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to cleanup
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self.logger.info(f"Stopped {len(self._tasks)} background tasks")

        self._tasks.clear()

    async def _initial_cache_warmup(self):
        """Initial cache population on startup"""
        try:
            self.logger.info("Starting initial cache warmup...")
            hosts = await self._get_configured_hosts()

            if not hosts:
                self.logger.warning("No configured hosts found for cache warmup")
                return

            # Update cache for all hosts in parallel
            warmup_tasks = []
            for host_id, host_config in hosts.items():
                task = asyncio.create_task(self._update_host_cache(host_id, host_config))
                warmup_tasks.append(task)

            # Wait for all warmup tasks with timeout
            if warmup_tasks:
                await asyncio.wait_for(
                    asyncio.gather(*warmup_tasks, return_exceptions=True),
                    timeout=60,  # 1 minute timeout for warmup
                )

            total_containers = sum(len(containers) for containers in self.containers_cache.values())
            self.logger.info(
                f"Cache warmup completed: {total_containers} containers across {len(hosts)} hosts"
            )

        except asyncio.TimeoutError:
            self.logger.warning("Cache warmup timed out after 60 seconds")
        except Exception as e:
            self.logger.error(f"Error during cache warmup: {e}")

    async def _cache_update_loop(self):
        """Main cache update loop"""
        self.logger.info("Cache update loop started")
        while self.running:
            try:
                loop_start = time.time()
                # Get all configured hosts from your MCP config
                hosts = await self._get_configured_hosts()

                if not hosts:
                    self.logger.warning("No configured hosts found in cache update loop")
                    await asyncio.sleep(self.update_interval)
                    continue

                self.logger.debug(f"Updating cache for {len(hosts)} hosts")

                # Update cache for each host
                update_tasks = []
                for host_id, host_config in hosts.items():
                    task = asyncio.create_task(self._update_host_cache(host_id, host_config))
                    update_tasks.append(task)

                # Wait for all updates with timeout
                if update_tasks:
                    results = await asyncio.wait_for(
                        asyncio.gather(*update_tasks, return_exceptions=True),
                        timeout=self.update_interval - 0.5,  # Leave buffer time
                    )

                    # Log results
                    successful_updates = sum(1 for r in results if not isinstance(r, Exception))
                    failed_updates = len(results) - successful_updates

                    loop_time = time.time() - loop_start
                    total_containers = sum(
                        len(containers) for containers in self.containers_cache.values()
                    )

                    self.logger.info(
                        f"Cache update completed: {successful_updates} successful, {failed_updates} failed, "
                        f"{total_containers} total containers, took {loop_time:.2f}s"
                    )

            except asyncio.TimeoutError:
                self.logger.warning("Cache update cycle timed out")
            except Exception as e:
                self.logger.error(f"Error in cache update loop: {e}")

            # Wait before next update
            await asyncio.sleep(self.update_interval)

    async def _event_stream_loop(self):
        """Subscribe to Docker events for real-time updates"""
        while self.running:
            for host_id in list(self.config.hosts.keys()):
                if not self.config.hosts[host_id].enabled:
                    continue

                try:
                    client = await self._get_docker_client(host_id, {})
                    if not client:
                        continue

                    # Subscribe to events with more specific filtering
                    events = client.events(
                        decode=True,
                        filters={
                            "type": ["container"],
                            "event": ["start", "stop", "die", "restart", "destroy"],
                        },
                    )

                    # Process events with timeout to prevent blocking
                    try:
                        # Use asyncio.wait_for to timeout event processing
                        event_iter = iter(events)
                        while self.running:
                            try:
                                # Wait max 5 seconds for next event
                                event = await asyncio.wait_for(
                                    asyncio.get_event_loop().run_in_executor(
                                        None, next, event_iter
                                    ),
                                    timeout=5.0,
                                )
                                await self._handle_docker_event(host_id, event)
                            except asyncio.TimeoutError:
                                # No event in 5 seconds, continue to next host
                                self.logger.debug(
                                    f"No events from {host_id} in 5 seconds, continuing"
                                )
                                break
                            except StopIteration:
                                # Event stream ended
                                self.logger.info(f"Event stream ended for {host_id}")
                                break
                    except Exception as event_error:
                        self.logger.error(f"Event processing error for {host_id}: {event_error}")
                        # Continue to next host

                except Exception as e:
                    self.logger.error(f"Event stream error for {host_id}: {e}")
                    # Brief delay before retrying this host
                    await asyncio.sleep(2)

            # No delay - immediately retry host iteration for responsiveness

    async def _handle_docker_event(self, host_id: str, event: dict):
        """Handle individual Docker events"""
        try:
            event_type = event.get("Type")
            event_action = event.get("Action")

            if event_type == "container":
                container_id = event["Actor"]["ID"]

                if event_action in ["start", "stop", "die", "restart"]:
                    # Update single container immediately
                    self.logger.debug(f"Container {event_action} event for {container_id[:12]}")
                    await self._update_single_container(host_id, container_id)

                elif event_action == "destroy":
                    # Remove from cache
                    if host_id in self.containers_cache:
                        self.containers_cache[host_id].pop(container_id, None)
                        self.logger.debug(f"Container {container_id[:12]} removed from cache")

            elif event_type in ["network", "volume"]:
                # Network/volume changes might affect containers, trigger partial refresh
                self.logger.debug(f"{event_type} {event_action} event, triggering cache refresh")
                # Could implement more granular updates here

        except Exception as e:
            self.logger.warning(f"Error handling Docker event: {e}")

    async def _update_single_container(self, host_id: str, container_id: str):
        """Update cache for a single container"""
        try:
            client = await self._get_docker_client(host_id, {})
            if not client:
                return

            # Get the specific container
            container = client.containers.get(container_id)

            # Get stats if running
            stats = {}
            if container.status == "running":
                try:
                    stats_data = container.stats(stream=False)
                    stats = self._parse_stats(stats_data)
                except Exception as e:
                    self.logger.warning(f"Failed to get stats for {container.name}: {e}")

            # Build cache entry
            container_cache = await self._build_container_cache(host_id, container, stats)

            # Update cache
            if host_id not in self.containers_cache:
                self.containers_cache[host_id] = {}
            self.containers_cache[host_id][container_id] = container_cache

            self.logger.debug(f"Updated cache for container {container.name}")

        except Exception as e:
            self.logger.warning(f"Failed to update single container {container_id[:12]}: {e}")

    async def _update_host_cache(self, host_id: str, host_config: dict):
        """Update cache for a specific host"""
        host_start = time.time()
        try:
            self.logger.debug(f"Starting cache update for host {host_id}")

            # Create Docker client for this host (using your existing SSH/Docker logic)
            client = await self._get_docker_client(host_id, host_config)

            if not client:
                self.logger.warning(
                    f"Could not connect to host {host_id} - no Docker client available"
                )
                return

            # Get all containers (running and stopped)
            containers = await self._get_containers_async(client)

            # Get stats for running containers only (expensive operation)
            stats_data = {}
            running_containers = [c for c in containers if c.status == "running"]
            if running_containers:
                stats_data = await self._get_containers_stats_async(running_containers)

            # Update cache for each container
            host_cache = {}
            for container in containers:
                try:
                    container_cache = await self._build_container_cache(
                        host_id, container, stats_data.get(container.name, {})
                    )
                    host_cache[container.id] = container_cache
                except Exception as e:
                    self.logger.error(f"Error caching container {container.name}: {e}")

            # Store in memory cache
            self.containers_cache[host_id] = host_cache
            self.cache_timestamps[host_id] = time.time()

            host_time = time.time() - host_start
            self.logger.debug(
                f"Host {host_id} cache updated: {len(host_cache)} containers in {host_time:.2f}s"
            )

            # Optionally persist to disk for startup speed
            await self._persist_cache(host_id, host_cache)

        except Exception as e:
            host_time = time.time() - host_start
            self.logger.error(
                f"Error updating cache for host {host_id} after {host_time:.2f}s: {e}",
                exc_info=True,
            )

    async def _build_container_cache(self, host_id: str, container, stats: dict) -> ContainerCache:
        """Build cache entry for a single container"""
        # Parse container attributes (using logic from our previous Python script)
        attrs = container.attrs
        config = attrs["Config"]
        host_config = attrs["HostConfig"]
        network_settings = attrs["NetworkSettings"]
        state = attrs["State"]

        # Calculate uptime
        uptime = None
        if state["Status"] == "running" and state["StartedAt"] != "0001-01-01T00:00:00Z":
            uptime = self._calculate_uptime(state["StartedAt"])

        # Network info
        networks = []
        for name, network_config in network_settings.get("Networks", {}).items():
            networks.append(
                {
                    "name": name,
                    "ip": network_config.get("IPAddress", ""),
                    "gateway": network_config.get("Gateway", ""),
                    "mac": network_config.get("MacAddress", ""),
                }
            )

        # Port mappings
        ports = []
        for container_port, host_bindings in network_settings.get("Ports", {}).items():
            if host_bindings:
                for binding in host_bindings:
                    ports.append(f"{container_port}->{binding['HostIp']}:{binding['HostPort']}")
            else:
                ports.append(container_port)

        # Compose info
        labels = container.labels
        compose_project = labels.get(DOCKER_COMPOSE_PROJECT)
        compose_service = labels.get(DOCKER_COMPOSE_SERVICE)

        # Get stack containers (if compose project exists)
        stack_containers = []
        if compose_project:
            stack_containers = await self._get_compose_stack_containers(container, compose_project)

        # Enhanced mount info
        bind_mounts = []
        volumes = []
        volume_drivers = {}

        for mount in attrs.get("Mounts", []):
            if mount.get("Type") == "bind":
                mode = f"({mount.get('Mode', 'rw')})" if mount.get("Mode") else ""
                bind_mounts.append(f"{mount['Source']}:{mount['Destination']}{mode}")
            elif mount.get("Type") == "volume":
                volumes.append(
                    {
                        "name": mount.get("Name", ""),
                        "source": mount.get("Source", ""),
                        "destination": mount.get("Destination", ""),
                        "mode": mount.get("Mode", "rw"),
                    }
                )
                if mount.get("Driver"):
                    volume_drivers[mount.get("Name", "")] = mount.get("Driver")

        # Enhanced compose info
        compose_config_files = labels.get(DOCKER_COMPOSE_CONFIG_FILES)
        compose_working_dir = labels.get(DOCKER_COMPOSE_WORKING_DIR)

        # Network aliases
        network_aliases = {}
        for name, network_config in network_settings.get("Networks", {}).items():
            aliases = network_config.get("Aliases", [])
            if aliases:
                network_aliases[name] = aliases

        # Command and entrypoint
        command = " ".join(config.get("Cmd", [])) if config.get("Cmd") else ""
        entrypoint = " ".join(config.get("Entrypoint", [])) if config.get("Entrypoint") else ""

        # Restart policy
        restart_policy = host_config.get("RestartPolicy", {}).get("Name", "no")

        return ContainerCache(
            host_id=host_id,
            container_id=container.id,
            name=container.name,
            status=state["Status"],
            image=container.image.tags[0] if container.image.tags else container.image.id[:12],
            created=attrs["Created"],
            started=state["StartedAt"],
            uptime=uptime,
            # Resource info from stats
            cpu_percent=stats.get("cpu_percent", 0.0),
            memory_usage=stats.get("memory_usage", 0),
            memory_percent=stats.get("memory_percent", 0.0),
            memory_limit=host_config.get("Memory", 0),
            # Network info
            networks=networks,
            ports=ports,
            ip_address=network_settings.get("IPAddress", ""),
            # Enhanced Compose info
            compose_project=compose_project,
            compose_service=compose_service,
            compose_stack_containers=stack_containers,
            compose_config_files=compose_config_files,
            compose_working_dir=compose_working_dir,
            # Enhanced Storage info
            bind_mounts=bind_mounts,
            volumes=volumes,
            volume_drivers=volume_drivers,
            working_dir=config.get("WorkingDir"),
            # Network aliases
            network_aliases=network_aliases,
            # Labels (all container labels)
            labels=labels or {},
            # Command info
            command=command,
            entrypoint=entrypoint,
            # Health info
            health_status=state.get("Health", {}).get("Status"),
            restart_count=attrs.get("RestartCount", 0),
            exit_code=state.get("ExitCode", 0),
            restart_policy=restart_policy,
            # Metadata
            cached_at=time.time(),
            last_updated=time.time(),
        )

    async def get_containers(
        self, host_id: str, force_refresh: bool = False
    ) -> list[ContainerCache]:
        """Get cached containers for a host"""
        if force_refresh or not self._is_cache_fresh(host_id):
            self.logger.info(f"Cache miss for host {host_id}, triggering refresh")
            # Trigger immediate update
            hosts = await self._get_configured_hosts()
            if host_id in hosts:
                await self._update_host_cache(host_id, hosts[host_id])

        return list(self.containers_cache.get(host_id, {}).values())

    async def get_container(
        self, host_id: str, container_id: str, force_refresh: bool = False
    ) -> ContainerCache | None:
        """Get specific cached container"""
        containers = await self.get_containers(host_id, force_refresh)

        for container in containers:
            if container.container_id == container_id or container.name == container_id:
                return container

        return None

    # Advanced Query Methods

    async def get_stack_members(self, project_name: str) -> list[ContainerCache]:
        """Get all containers in a compose stack across all hosts"""
        members = []
        for _host_id, containers in self.containers_cache.items():
            for container in containers.values():
                if container.compose_project == project_name:
                    members.append(container)
        return members

    async def find_by_label(
        self, label_key: str, label_value: str | None = None
    ) -> list[ContainerCache]:
        """Find containers by label across all hosts"""
        results = []
        for _host_id, containers in self.containers_cache.items():
            for container in containers.values():
                if label_key in container.labels:
                    if label_value is None or container.labels[label_key] == label_value:
                        results.append(container)
        return results

    async def get_by_network(self, network_name: str) -> list[ContainerCache]:
        """Get all containers on a specific network"""
        results = []
        for _host_id, containers in self.containers_cache.items():
            for container in containers.values():
                for network in container.networks:
                    if network["name"] == network_name:
                        results.append(container)
                        break
        return results

    async def cross_host_search(self, query: str) -> list[ContainerCache]:
        """Search containers by name/image/id across all hosts"""
        query_lower = query.lower()
        results = []

        for _host_id, containers in self.containers_cache.items():
            for container in containers.values():
                if (
                    query_lower in container.name.lower()
                    or query_lower in container.image.lower()
                    or container.container_id.startswith(query)
                ):
                    results.append(container)

        return results

    async def get_resource_usage(self, top_n: int = 10) -> dict[str, list[ContainerCache]]:
        """Get top resource consumers"""
        all_containers = []
        for _host_id, containers in self.containers_cache.items():
            all_containers.extend(containers.values())

        # Sort by CPU and memory
        by_cpu = sorted(all_containers, key=lambda c: c.cpu_percent, reverse=True)[:top_n]
        by_memory = sorted(all_containers, key=lambda c: c.memory_usage, reverse=True)[:top_n]

        return {"top_cpu": by_cpu, "top_memory": by_memory}

    async def get_containers_by_status(self, status: str) -> list[ContainerCache]:
        """Get all containers with a specific status across all hosts"""
        results = []
        for _host_id, containers in self.containers_cache.items():
            for container in containers.values():
                if container.status.lower() == status.lower():
                    results.append(container)
        return results

    async def find_containers_with_mounts(self, mount_path: str) -> list[ContainerCache]:
        """Find containers that have volumes mounted from a specific path"""
        results = []
        for _host_id, containers in self.containers_cache.items():
            for container in containers.values():
                # Check bind mounts
                for mount in container.bind_mounts:
                    if mount_path in mount:
                        results.append(container)
                        break
        return results

    async def get_health_summary(self) -> dict[str, list[ContainerCache]]:
        """Get containers grouped by health status"""
        healthy = []
        unhealthy = []
        starting = []
        no_health = []

        for _host_id, containers in self.containers_cache.items():
            for container in containers.values():
                health_status = container.health_status
                if health_status == "healthy":
                    healthy.append(container)
                elif health_status == "unhealthy":
                    unhealthy.append(container)
                elif health_status == "starting":
                    starting.append(container)
                else:
                    no_health.append(container)

        return {
            "healthy": healthy,
            "unhealthy": unhealthy,
            "starting": starting,
            "no_health_check": no_health,
        }

    def _is_cache_fresh(self, host_id: str, cache_type: str = "status") -> bool:
        """Check if cache is still fresh based on data type"""
        timestamp = self.cache_timestamps.get(host_id, 0)
        age = time.time() - timestamp

        # Use appropriate TTL based on cache type
        ttl = {
            "stats": self.cache_config.STATS_TTL,
            "status": self.cache_config.STATUS_TTL,
            "config": self.cache_config.CONFIG_TTL,
            "compose": self.cache_config.COMPOSE_TTL,
        }.get(cache_type, self.cache_config.STATUS_TTL)

        return age < ttl

    def _should_update_stats(self, host_id: str) -> bool:
        """Check if stats need updating (more frequent than general cache)"""
        timestamp = self.cache_timestamps.get(host_id, 0)
        age = time.time() - timestamp
        return age > self.cache_config.STATS_UPDATE

    def _should_update_config(self, host_id: str) -> bool:
        """Check if config data needs updating (less frequent)"""
        timestamp = self.cache_timestamps.get(host_id, 0)
        age = time.time() - timestamp
        return age > self.cache_config.CONFIG_UPDATE

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics"""
        stats: dict[str, Any] = {
            "hosts_cached": len(self.containers_cache),
            "total_containers": sum(
                len(containers) for containers in self.containers_cache.values()
            ),
            "cache_ages": {},
            "update_interval": self.update_interval,
            "cache_ttl": self.cache_ttl,
        }

        current_time = time.time()
        for host_id, timestamp in self.cache_timestamps.items():
            stats["cache_ages"][host_id] = current_time - timestamp

        return stats

    async def _cleanup_loop(self):
        """Periodic cleanup of stale cache entries"""
        while self.running:
            try:
                current_time = time.time()

                # Remove stale entries (use CONFIG_RETENTION as base retention period)
                for host_id in list(self.cache_timestamps.keys()):
                    age = current_time - self.cache_timestamps[host_id]
                    if age > self.cache_config.CONFIG_RETENTION:
                        self.logger.info(
                            f"Cleaning up stale cache for host {host_id} (age: {age:.0f}s)"
                        )
                        self.containers_cache.pop(host_id, None)
                        self.cache_timestamps.pop(host_id, None)

            except Exception as e:
                self.logger.error(f"Error in cleanup loop: {e}")

            # Use configured cleanup interval
            await asyncio.sleep(self.cache_config.MEMORY_CLEANUP_INTERVAL)

    # Helper methods (implement based on your existing MCP server structure)
    async def _get_configured_hosts(self) -> dict[str, dict]:
        """Get configured hosts from your MCP config"""
        all_hosts = list(self.config.hosts.keys())
        enabled_hosts = {
            host_id: {
                "hostname": host.hostname,
                "user": host.user,
                "port": host.port,
                "identity_file": host.identity_file,
                "compose_path": host.compose_path,
                "appdata_path": host.appdata_path,
            }
            for host_id, host in self.config.hosts.items()
            if host.enabled
        }

        self.logger.debug(
            f"Configured hosts: {len(all_hosts)} total, {len(enabled_hosts)} enabled ({list(enabled_hosts.keys())})"
        )

        return enabled_hosts

    async def _get_docker_client(self, host_id: str, host_config: dict):
        """Get Docker client for host (reuse your existing SSH/Docker logic)"""
        try:
            client = await self.context_manager.get_client(host_id)
            return client
        except Exception as e:
            self.logger.error(f"Failed to get Docker client for {host_id}: {e}")
            return None

    async def _get_containers_async(self, client):
        """Get containers asynchronously"""
        # Wrap synchronous Docker API calls
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: client.containers.list(all=True))

    async def _get_containers_stats_async(self, containers) -> dict[str, dict]:
        """Get container stats asynchronously"""
        # This is expensive, so we limit it and run async
        loop = asyncio.get_event_loop()

        async def get_single_stat(container):
            try:
                stats = await loop.run_in_executor(None, lambda: container.stats(stream=False))
                return container.name, self._parse_stats(stats)
            except Exception as e:
                self.logger.warning(f"Failed to get stats for {container.name}: {e}")
                return container.name, {}

        # Limit concurrent stat collection to avoid overwhelming Docker daemon
        semaphore = asyncio.Semaphore(self.cache_config.STATS_SEMAPHORE_LIMIT)

        async def get_stat_with_semaphore(container):
            async with semaphore:
                return await get_single_stat(container)

        # Limit stats collection based on configuration
        max_stats = min(len(containers), self.cache_config.MAX_STATS_CONTAINERS)
        tasks = [get_stat_with_semaphore(c) for c in containers[:max_stats]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        stats_dict = {}
        for result in results:
            if isinstance(result, tuple):
                name, stats = result
                stats_dict[name] = stats

        return stats_dict

    def _parse_stats(self, stats: dict) -> dict:
        """Parse Docker stats for CPU and memory usage"""
        try:
            # Calculate CPU percentage
            cpu_delta = (
                stats["cpu_stats"]["cpu_usage"]["total_usage"]
                - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
            )

            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta > 0:
                cpu_count = len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
                cpu_percent = (cpu_delta / system_delta) * cpu_count * 100.0

            # Memory statistics
            memory_usage = stats["memory_stats"].get("usage", 0)
            memory_limit = stats["memory_stats"].get("limit", 0)
            memory_percent = 0.0
            if memory_limit > 0:
                memory_percent = (memory_usage / memory_limit) * 100.0

            return {
                "cpu_percent": round(cpu_percent, 2),
                "memory_usage": memory_usage,
                "memory_percent": round(memory_percent, 2),
                "memory_limit": memory_limit,
            }
        except (KeyError, ZeroDivisionError, TypeError) as e:
            self.logger.warning(f"Failed to parse stats: {e}")
            return {"cpu_percent": 0.0, "memory_usage": 0, "memory_percent": 0.0, "memory_limit": 0}

    def _calculate_uptime(self, started_at: str) -> str:
        """Calculate uptime from container start time"""
        try:
            if started_at == "0001-01-01T00:00:00Z":
                return "Not started"

            # Parse the ISO format timestamp
            start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = now - start_time

            days = delta.days
            hours = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60

            if days > 0:
                return f"{days}d {hours}h"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"

        except (ValueError, TypeError) as e:
            self.logger.warning(f"Failed to calculate uptime from {started_at}: {e}")
            return "Unknown"

    async def _get_compose_stack_containers(self, container, project: str) -> list[str]:
        """Get other containers in compose stack"""
        try:
            # Get the Docker client to list other containers with same project label
            client = container.client if hasattr(container, "client") else None
            if not client:
                return []

            # Find all containers with the same compose project
            stack_containers = []
            all_containers = client.containers.list(all=True)

            for c in all_containers:
                if c.id != container.id:  # Don't include self
                    labels = c.labels or {}
                    if labels.get(DOCKER_COMPOSE_PROJECT) == project:
                        stack_containers.append(c.name)

            return stack_containers

        except Exception as e:
            self.logger.warning(f"Failed to get stack containers for project {project}: {e}")
            return []

    async def _persist_cache(self, host_id: str, cache_data: dict):
        """Enhanced persistence with compression and metadata"""
        try:
            import gzip
            import pickle

            cache_file = self.cache_dir / f"{host_id}_cache.pkl.gz"

            # Convert to serializable format with metadata
            cache_bundle = {
                "version": "1.0",
                "timestamp": time.time(),
                "host_id": host_id,
                "containers": {cid: asdict(container) for cid, container in cache_data.items()},
            }

            # Compress and save
            with gzip.open(cache_file, "wb") as f:
                pickle.dump(cache_bundle, f)

            self.logger.debug(f"Persisted cache for {host_id} with {len(cache_data)} containers")

        except Exception as e:
            self.logger.warning(f"Failed to persist cache for {host_id}: {e}")

    async def _load_cache_from_disk(self):
        """Load cache from disk on startup for fast warmup"""
        try:
            import gzip
            import pickle

            loaded_hosts = 0
            total_containers = 0

            for cache_file in self.cache_dir.glob("*_cache.pkl.gz"):
                try:
                    with gzip.open(cache_file, "rb") as f:
                        cache_bundle = pickle.load(f)

                    # Check if cache is recent enough (within 1 hour)
                    cache_age = time.time() - cache_bundle["timestamp"]
                    if cache_age < 3600:  # 1 hour
                        host_id = cache_bundle["host_id"]

                        # Reconstruct ContainerCache objects
                        self.containers_cache[host_id] = {}
                        for cid, container_dict in cache_bundle["containers"].items():
                            self.containers_cache[host_id][cid] = ContainerCache(**container_dict)
                            total_containers += 1

                        self.cache_timestamps[host_id] = cache_bundle["timestamp"]
                        loaded_hosts += 1

                        self.logger.debug(
                            f"Loaded cache for {host_id} with {len(cache_bundle['containers'])} containers"
                        )
                    else:
                        self.logger.debug(
                            f"Cache for {cache_file.stem} too old ({cache_age:.0f}s), skipping"
                        )

                except Exception as e:
                    self.logger.warning(f"Failed to load cache from {cache_file}: {e}")

            if loaded_hosts > 0:
                self.logger.info(
                    f"Cache warmup completed: loaded {total_containers} containers from {loaded_hosts} hosts"
                )
            else:
                self.logger.info("No valid cache files found for warmup")

        except Exception as e:
            self.logger.error(f"Failed to load cache from disk: {e}")


# Global cache manager instance
_cache_manager: DockerCacheManager | None = None


async def get_cache_manager(
    config: DockerMCPConfig, context_manager: DockerContextManager
) -> DockerCacheManager:
    """Get the global cache manager instance"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = DockerCacheManager(config, context_manager)
        # Start in background task
        asyncio.create_task(_cache_manager.start())
    return _cache_manager

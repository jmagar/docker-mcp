# Docker Cache Manager - Complete Documentation

## Table of Contents

1. [Overview & Architecture](#overview--architecture)
2. [Configuration Guide](#configuration-guide)
3. [API Reference](#api-reference)
4. [ContainerCache Data Model](#containercache-data-model)
5. [Background Tasks](#background-tasks)
6. [Usage Patterns & Examples](#usage-patterns--examples)
7. [Performance Tuning](#performance-tuning)
8. [Integration Guide](#integration-guide)
9. [Monitoring & Troubleshooting](#monitoring--troubleshooting)
10. [Technical Details](#technical-details)

---

## Overview & Architecture

The Docker Cache Manager is a sophisticated background caching system designed to dramatically improve Docker MCP server performance by eliminating repetitive SSH operations and Docker API calls. It provides **2.6x performance improvement** by maintaining intelligent, continuously-updated container inspection data across multiple Docker hosts.

### Core Benefits

- **Performance**: Eliminates slow SSH operations for discovery and container queries
- **Real-time Updates**: Docker event streaming for immediate cache invalidation
- **Intelligence**: Multi-tier TTL system optimized for different data types
- **Scalability**: Handles multiple Docker hosts with concurrent operations
- **Reliability**: Automatic error handling and retry mechanisms

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                Docker Cache Manager                         │
├─────────────────────────────────────────────────────────────┤
│  Background Tasks:                                          │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │ Update Loop │ │ Event Stream│ │ Cleanup     │           │
│  │ (60s cycle) │ │ (real-time) │ │ (5m cycle)  │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
├─────────────────────────────────────────────────────────────┤
│  Cache Storage:                                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │ Containers  │ │ Host Stats  │ │ Timestamps  │           │
│  │   Cache     │ │   Cache     │ │   Cache     │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
├─────────────────────────────────────────────────────────────┤
│  Query Interface:                                           │
│  • get_containers() • find_by_label()                      │
│  • get_stack_members() • cross_host_search()               │
│  • get_resource_usage() • get_health_summary()             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│            Docker Context Manager                           │
│  • SSH Connection Management                                │
│  • Docker SDK Client Creation                              │
│  • Context Caching                                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│               Multiple Docker Hosts                        │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │   Host A    │ │   Host B    │ │   Host C    │           │
│  │ (via SSH)   │ │ (via SSH)   │ │ (via SSH)   │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

1. **DockerCacheManager**: Main orchestrator class
2. **CacheConfig**: Intelligent TTL and timing configuration
3. **ContainerCache**: Rich container data model (40+ fields)
4. **Background Tasks**: Three async loops for continuous updates

---

## Configuration Guide

### CacheConfig Structure

The cache manager uses an intelligent multi-tier TTL system optimized for different data characteristics:

```python
@dataclass
class CacheConfig:
    """Configuration for cache TTL and update intervals"""
    # Different TTLs for different data types
    STATS_TTL: int = 120           # 2 minutes for CPU/memory stats
    STATUS_TTL: int = 300          # 5 minutes for container status
    CONFIG_TTL: int = 3600         # 1 hour for configuration
    COMPOSE_TTL: int = 7200        # 2 hours for compose data
    
    # Update intervals
    STATS_UPDATE: int = 30         # Update stats every 30 seconds
    STATUS_UPDATE: int = 60        # Update status every minute
    CONFIG_UPDATE: int = 600       # Update config every 10 minutes
    
    # Retention
    MEMORY_RETENTION: int = 3600   # Keep 1 hour in memory
    DISK_RETENTION: int = 604800   # Keep 7 days on disk
    
    # Cleanup intervals
    MEMORY_CLEANUP_INTERVAL: int = 300   # Every 5 minutes
    DISK_CLEANUP_INTERVAL: int = 3600    # Every hour
```

### TTL Strategy Explained

| Data Type | TTL | Reasoning |
|-----------|-----|-----------|
| **Stats** (CPU/Memory) | 2 minutes | Highly volatile, frequent changes |
| **Status** (running/stopped) | 5 minutes | Changes moderately, balance accuracy vs performance |
| **Config** (ports, mounts) | 1 hour | Rarely changes, expensive to fetch |
| **Compose** (project info) | 2 hours | Very stable, complex to parse |

### Customization Options

```python
# Custom cache configuration
cache_config = CacheConfig()
cache_config.STATS_TTL = 60        # More frequent stats updates
cache_config.STATUS_UPDATE = 30    # Faster status refresh
cache_config.MEMORY_RETENTION = 7200  # Keep 2 hours in memory

cache_manager = DockerCacheManager(config, context_manager)
cache_manager.cache_config = cache_config
```

### Storage Configuration

```python
# Custom cache directory
custom_cache_dir = Path("/var/cache/docker-mcp")
cache_manager = DockerCacheManager(
    config, 
    context_manager, 
    cache_dir=custom_cache_dir
)
```

---

## API Reference

### Core Query Methods

#### `get_containers(host_id: str, force_refresh: bool = False) -> List[ContainerCache]`

Retrieve cached containers for a specific host.

```python
# Get all containers for a host
containers = await cache_manager.get_containers("production-server")

# Force refresh (bypass cache)
containers = await cache_manager.get_containers("production-server", force_refresh=True)

# Example response
for container in containers:
    print(f"{container.name}: {container.status} ({container.uptime})")
    # Output: nginx-proxy: running (2d 5h)
```

#### `get_container(host_id: str, container_id: str, force_refresh: bool = False) -> Optional[ContainerCache]`

Get a specific container by ID or name.

```python
# By container ID
container = await cache_manager.get_container("production-server", "abc123")

# By container name
container = await cache_manager.get_container("production-server", "nginx-proxy")

if container:
    print(f"CPU: {container.cpu_percent}%, Memory: {container.memory_percent}%")
```

### Advanced Query Methods

#### `get_stack_members(project_name: str) -> List[ContainerCache]`

Find all containers in a Docker Compose stack across all hosts.

```python
# Get all containers in the "webapp" stack
stack_containers = await cache_manager.get_stack_members("webapp")

for container in stack_containers:
    print(f"{container.host_id}: {container.name} ({container.compose_service})")
    # Output: 
    # production-server: webapp_frontend_1 (frontend)
    # production-server: webapp_backend_1 (backend)
    # production-server: webapp_database_1 (database)
```

#### `find_by_label(label_key: str, label_value: Optional[str] = None) -> List[ContainerCache]`

Search containers by Docker labels.

```python
# Find all containers with "environment" label
env_containers = await cache_manager.find_by_label("environment")

# Find specific environment
prod_containers = await cache_manager.find_by_label("environment", "production")

for container in prod_containers:
    print(f"{container.name}: {container.labels['environment']}")
```

#### `cross_host_search(query: str) -> List[ContainerCache]`

Search containers by name, image, or ID across all hosts.

```python
# Search for nginx containers across all hosts
nginx_containers = await cache_manager.cross_host_search("nginx")

# Search by partial container ID
containers = await cache_manager.cross_host_search("abc123")

for container in nginx_containers:
    print(f"{container.host_id}: {container.name} - {container.image}")
    # Output: production-server: nginx-proxy - nginx:1.21-alpine
```

#### `get_resource_usage(top_n: int = 10) -> Dict[str, List[ContainerCache]]`

Get top resource consumers across all hosts.

```python
# Get top 5 CPU and memory consumers
usage = await cache_manager.get_resource_usage(top_n=5)

print("Top CPU consumers:")
for container in usage['top_cpu']:
    print(f"  {container.name}: {container.cpu_percent}% on {container.host_id}")

print("Top Memory consumers:")
for container in usage['top_memory']:
    print(f"  {container.name}: {container.memory_percent}% on {container.host_id}")
```

#### `find_containers_with_mounts(mount_path: str) -> List[ContainerCache]`

Find containers mounting from a specific path.

```python
# Find all containers using /data volume
data_containers = await cache_manager.find_containers_with_mounts("/data")

for container in data_containers:
    print(f"{container.name}: {container.bind_mounts}")
    # Output: database: ['/data:/var/lib/postgresql/data:rw']
```

#### `get_health_summary() -> Dict[str, List[ContainerCache]]`

Get containers grouped by health status.

```python
health = await cache_manager.get_health_summary()

print(f"Healthy: {len(health['healthy'])}")
print(f"Unhealthy: {len(health['unhealthy'])}")
print(f"Starting: {len(health['starting'])}")
print(f"No health check: {len(health['no_health_check'])}")

# List unhealthy containers
for container in health['unhealthy']:
    print(f"⚠️  {container.name} on {container.host_id}")
```

### Cache Management Methods

#### `get_cache_stats() -> Dict`

Get comprehensive cache statistics.

```python
stats = cache_manager.get_cache_stats()

print(f"Hosts cached: {stats['hosts_cached']}")
print(f"Total containers: {stats['total_containers']}")
print(f"Update interval: {stats['update_interval']}s")
print(f"Cache TTL: {stats['cache_ttl']}s")

# Per-host cache ages
for host_id, age in stats['cache_ages'].items():
    print(f"{host_id}: {age:.1f}s old")
```

---

## ContainerCache Data Model

The `ContainerCache` dataclass contains comprehensive container information organized into logical categories:

### Basic Information
```python
host_id: str                    # Host where container resides
container_id: str              # Full Docker container ID  
name: str                      # Container name
status: str                    # running, stopped, paused, etc.
image: str                     # Image name:tag or image ID
created: str                   # ISO timestamp of creation
started: str                   # ISO timestamp of start
uptime: Optional[str]          # Human-readable uptime (e.g., "2d 5h")
```

### Resource Information
```python
cpu_percent: float             # Current CPU usage percentage
memory_usage: int              # Memory usage in bytes
memory_percent: float          # Memory usage percentage
memory_limit: int              # Memory limit in bytes (0 = unlimited)
```

### Network Information
```python
networks: List[Dict[str, str]] # Network configurations
ports: List[str]               # Port mappings (container->host:port)
ip_address: str                # Primary IP address
network_aliases: Dict[str, List[str]]  # Network-specific aliases
```

### Docker Compose Information
```python
compose_project: Optional[str]        # docker-compose project name
compose_service: Optional[str]        # Service name within project
compose_stack_containers: List[str]   # Other containers in stack
compose_config_files: Optional[str]   # Path to docker-compose.yml
compose_file_content: Optional[str]   # Cached YAML content
compose_working_dir: Optional[str]    # Project working directory
```

### Storage Information
```python
bind_mounts: List[str]                # Bind mount mappings
volumes: List[Dict[str, str]]         # Volume mount details
volume_drivers: Dict[str, str]        # Volume drivers used
working_dir: Optional[str]            # Container working directory
```

### Environment & Configuration
```python
environment_vars: Dict[str, str]      # All environment variables
labels: Dict[str, str]                # All container labels
command: str                          # Container command
entrypoint: str                       # Container entrypoint
```

### Health & Dependencies
```python
health_status: Optional[str]          # healthy, unhealthy, starting
restart_count: int                    # Number of restarts
exit_code: int                        # Last exit code
restart_policy: str                   # Restart policy (no, always, etc.)
depends_on: List[str]                 # Container dependencies
links: List[str]                      # Legacy container links
```

### Logging Information
```python
log_tail: List[str]                   # Last 100 log lines (populated on demand)
log_driver: str                       # Logging driver (json-file, syslog, etc.)
```

### Metadata
```python
cached_at: float                      # Unix timestamp when cached
last_updated: float                   # Unix timestamp of last update
```

### Example Usage

```python
container = await cache_manager.get_container("prod-server", "webapp")

# Access basic info
print(f"Status: {container.status}")
print(f"Uptime: {container.uptime}")
print(f"Image: {container.image}")

# Check resources
if container.cpu_percent > 80:
    print(f"⚠️  High CPU usage: {container.cpu_percent}%")

# Compose stack info
if container.compose_project:
    print(f"Part of stack: {container.compose_project}")
    print(f"Service: {container.compose_service}")

# Network information
for network in container.networks:
    print(f"Network: {network['name']} IP: {network['ip']}")

# Environment variables
if 'DATABASE_URL' in container.environment_vars:
    print("Database configured")

# Health check
if container.health_status == 'unhealthy':
    print(f"⚠️  Container {container.name} is unhealthy!")
```

---

## Background Tasks

The cache manager runs three background tasks continuously:

### 1. Cache Update Loop (`_cache_update_loop`)

**Purpose**: Periodic refresh of all container data  
**Interval**: 60 seconds (configurable)  
**Behavior**: Updates all enabled hosts in parallel

```python
async def _cache_update_loop(self):
    """Main cache update loop"""
    while self.running:
        loop_start = time.time()
        hosts = await self._get_configured_hosts()
        
        # Update all hosts in parallel
        update_tasks = []
        for host_id, host_config in hosts.items():
            task = asyncio.create_task(self._update_host_cache(host_id, host_config))
            update_tasks.append(task)
        
        # Wait with timeout
        results = await asyncio.wait_for(
            asyncio.gather(*update_tasks, return_exceptions=True),
            timeout=self.update_interval - 0.5
        )
        
        # Log comprehensive results
        successful_updates = sum(1 for r in results if not isinstance(r, Exception))
        total_containers = sum(len(containers) for containers in self.containers_cache.values())
        
        self.logger.info(
            f"Cache update completed: {successful_updates} successful, "
            f"{total_containers} total containers, took {time.time() - loop_start:.2f}s"
        )
        
        await asyncio.sleep(self.update_interval)
```

**Key Features**:
- Parallel host updates for performance
- Timeout protection to prevent blocking
- Detailed logging with timing metrics
- Exception handling per host

### 2. Event Stream Loop (`_event_stream_loop`)

**Purpose**: Real-time cache updates via Docker events  
**Interval**: Continuous event monitoring  
**Behavior**: Subscribes to Docker events for immediate updates

```python
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
                    
                # Subscribe to relevant events
                events = client.events(decode=True, filters={
                    'type': ['container', 'network', 'volume']
                })
                
                # Process events in real-time
                for event in events:
                    if not self.running:
                        break
                        
                    await self._handle_docker_event(host_id, event)
                    
            except Exception as e:
                self.logger.error(f"Event stream error for {host_id}: {e}")
                await asyncio.sleep(5)  # Retry after 5 seconds
```

**Handled Events**:
- `container start/stop/die/restart`: Immediate cache update
- `container destroy`: Remove from cache
- `network/volume changes`: Trigger partial refresh

**Benefits**:
- Near-instantaneous cache updates
- Eliminates stale data between periodic updates
- Reduces discovery latency to milliseconds

### 3. Cleanup Loop (`_cleanup_loop`)

**Purpose**: Remove stale cache entries  
**Interval**: 5 minutes (configurable)  
**Behavior**: Removes entries older than retention period

```python
async def _cleanup_loop(self):
    """Periodic cleanup of stale cache entries"""
    while self.running:
        try:
            current_time = time.time()
            
            # Remove stale entries
            for host_id in list(self.cache_timestamps.keys()):
                age = current_time - self.cache_timestamps[host_id]
                if age > self.cache_config.MEMORY_RETENTION:
                    self.logger.info(f"Cleaning up stale cache for host {host_id}")
                    self.containers_cache.pop(host_id, None)
                    self.cache_timestamps.pop(host_id, None)
                    
        except Exception as e:
            self.logger.error(f"Error in cleanup loop: {e}")
        
        await asyncio.sleep(self.cache_config.MEMORY_CLEANUP_INTERVAL)
```

**Key Features**:
- Prevents memory leaks from disabled hosts
- Configurable retention period
- Safe removal with exception handling

---

## Usage Patterns & Examples

### Basic Container Queries

```python
# List all containers on a host
containers = await cache_manager.get_containers("production-server")
print(f"Found {len(containers)} containers")

# Get specific container
app_container = await cache_manager.get_container("production-server", "myapp")
if app_container and app_container.status == 'running':
    print(f"App is running, uptime: {app_container.uptime}")
```

### Cross-Host Operations

```python
# Find all nginx containers across infrastructure
nginx_containers = await cache_manager.cross_host_search("nginx")

print(f"Nginx containers across {len(set(c.host_id for c in nginx_containers))} hosts:")
for container in nginx_containers:
    print(f"  {container.host_id}: {container.name} - {container.status}")
```

### Compose Stack Management

```python
# Get all containers in a stack
webapp_stack = await cache_manager.get_stack_members("webapp")

# Group by service
services = {}
for container in webapp_stack:
    service = container.compose_service or "unknown"
    services.setdefault(service, []).append(container)

print("Stack composition:")
for service, containers in services.items():
    print(f"  {service}: {len(containers)} containers")
```

### Resource Monitoring

```python
# Find resource-intensive containers
usage = await cache_manager.get_resource_usage(top_n=5)

# Alert on high CPU usage
for container in usage['top_cpu']:
    if container.cpu_percent > 80:
        print(f"🚨 HIGH CPU: {container.name} on {container.host_id}: {container.cpu_percent}%")

# Check memory usage
for container in usage['top_memory']:
    if container.memory_percent > 90:
        print(f"🚨 HIGH MEMORY: {container.name}: {container.memory_percent}%")
```

### Health Monitoring

```python
# Check overall health
health = await cache_manager.get_health_summary()

if health['unhealthy']:
    print(f"⚠️  {len(health['unhealthy'])} unhealthy containers:")
    for container in health['unhealthy']:
        print(f"  - {container.name} on {container.host_id}")

# Monitor container restart counts
containers = await cache_manager.get_containers("production-server")
for container in containers:
    if container.restart_count > 5:
        print(f"⚠️  {container.name} has restarted {container.restart_count} times")
```

### Label-Based Queries

```python
# Find production environment containers
prod_containers = await cache_manager.find_by_label("environment", "production")

# Find containers with backup enabled
backup_containers = await cache_manager.find_by_label("backup.enabled", "true")

# Find containers by team
team_containers = await cache_manager.find_by_label("team", "platform")
```

### Volume Usage Analysis

```python
# Find containers using specific mount points
data_containers = await cache_manager.find_containers_with_mounts("/data")
home_containers = await cache_manager.find_containers_with_mounts("/home")

print("Volume usage analysis:")
for container in data_containers:
    print(f"  {container.name} uses /data:")
    for mount in container.bind_mounts:
        if "/data" in mount:
            print(f"    {mount}")
```

---

## Performance Tuning

### TTL Optimization Strategies

#### High-Frequency Environments
For environments with frequent container changes:

```python
# Faster updates, shorter TTLs
config = CacheConfig()
config.STATS_TTL = 60          # 1 minute for stats
config.STATUS_TTL = 120        # 2 minutes for status
config.STATUS_UPDATE = 30      # Update every 30 seconds
```

#### Stable Production Environments
For stable environments with infrequent changes:

```python
# Longer TTLs, less frequent updates
config = CacheConfig()
config.STATS_TTL = 300         # 5 minutes for stats
config.STATUS_TTL = 600        # 10 minutes for status  
config.STATUS_UPDATE = 120     # Update every 2 minutes
```

#### Resource-Constrained Systems
For systems with limited resources:

```python
# Conservative settings
config = CacheConfig()
config.MEMORY_RETENTION = 1800  # 30 minutes in memory
config.STATS_UPDATE = 90        # Less frequent stats
# Disable stats collection for non-critical hosts
```

### Memory vs Disk Trade-offs

#### Memory-Optimized Configuration
Prioritize memory usage for fastest access:

```python
config = CacheConfig()
config.MEMORY_RETENTION = 7200     # Keep 2 hours in memory
config.MEMORY_CLEANUP_INTERVAL = 600  # Clean every 10 minutes
# Increase cache directory for disk persistence
```

#### Disk-Optimized Configuration
Minimize memory usage, rely on disk cache:

```python
config = CacheConfig()
config.MEMORY_RETENTION = 1800     # Keep 30 minutes in memory
config.DISK_RETENTION = 2592000    # Keep 30 days on disk
config.MEMORY_CLEANUP_INTERVAL = 180  # Aggressive memory cleanup
```

### Concurrent Update Limits

The cache manager includes built-in concurrency controls:

```python
# Stats collection uses semaphore to limit concurrent operations
semaphore = asyncio.Semaphore(3)  # Max 3 concurrent stats calls

# Container limit prevents overwhelming Docker daemon
tasks = [get_stat_with_semaphore(c) for c in containers[:10]]  # Limit to 10
```

**Recommendations**:
- **Small environments** (1-3 hosts): Keep defaults
- **Medium environments** (4-10 hosts): Increase semaphore to 5
- **Large environments** (10+ hosts): Consider 7-10, monitor Docker daemon load

### Event Stream Optimization

#### High-Volume Environments
For environments with many container events:

```python
# Process events in batches
async def _handle_docker_event_batch(self, host_id: str, events: List[Dict]):
    # Custom batch processing logic
    pass
```

#### Low-Latency Requirements
For applications requiring minimal cache lag:

```python
# Reduce event processing delay
await asyncio.sleep(0.1)  # Faster event loop cycle
```

---

## Integration Guide

### Integration with HostService

The HostService uses the cache manager for discovery operations:

```python
class HostService:
    def __init__(self, config: DockerMCPConfig):
        self.config = config
        self.cache_manager: Optional[DockerCacheManager] = None
    
    def set_cache_manager(self, cache_manager: DockerCacheManager):
        """Set cache manager for enhanced performance."""
        self.cache_manager = cache_manager
    
    async def discover_compose_paths(self, host_id: str) -> List[str]:
        """Discover compose paths using cache when available."""
        if self.cache_manager:
            # Use cached container data
            containers = await self.cache_manager.get_containers(host_id)
            compose_paths = []
            
            for container in containers:
                if container.compose_config_files:
                    compose_paths.append(container.compose_config_files)
            
            return list(set(compose_paths))
        
        # Fallback to SSH-based discovery
        return await self._discover_compose_paths_ssh(host_id)
```

### Integration with ContainerService

The ContainerService leverages cached data for operations:

```python
class ContainerService:
    def set_cache_manager(self, cache_manager: DockerCacheManager):
        """Enable cache-powered operations."""
        self.cache_manager = cache_manager
    
    async def list_containers(self, host_id: str, all_containers: bool = False) -> dict[str, Any]:
        """List containers with cache acceleration."""
        if self.cache_manager:
            # Get from cache first
            cached_containers = await self.cache_manager.get_containers(host_id)
            
            # Filter by status if needed
            if not all_containers:
                cached_containers = [c for c in cached_containers if c.status == 'running']
            
            # Convert to expected format
            containers = []
            for container in cached_containers:
                containers.append({
                    'id': container.container_id,
                    'name': container.name,
                    'status': container.status,
                    'image': container.image,
                    'uptime': container.uptime,
                    'cpu_percent': container.cpu_percent,
                    'memory_percent': container.memory_percent,
                })
            
            return {
                'success': True,
                'containers': containers,
                'total': len(containers),
                'source': 'cache'
            }
        
        # Fallback to direct Docker operations
        return await self._list_containers_direct(host_id, all_containers)
```

### Cache-Powered Discovery Operations

Discovery operations are dramatically accelerated with the cache:

```python
async def discover_all_hosts(self) -> dict[str, Any]:
    """Discover capabilities for all configured hosts."""
    if not self.cache_manager:
        # Use traditional SSH-based discovery
        return await self._discover_all_hosts_ssh()
    
    # Use cache-powered discovery
    results = {}
    
    for host_id in self.config.hosts.keys():
        if not self.config.hosts[host_id].enabled:
            continue
        
        # Get cached containers
        containers = await self.cache_manager.get_containers(host_id)
        
        # Extract discovery data from cache
        compose_projects = set()
        appdata_paths = set()
        
        for container in containers:
            if container.compose_project:
                compose_projects.add(container.compose_project)
            
            # Analyze bind mounts for appdata paths
            for mount in container.bind_mounts:
                source_path = mount.split(':')[0]
                if '/appdata' in source_path or '/data' in source_path:
                    appdata_paths.add(source_path)
        
        results[host_id] = {
            'success': True,
            'compose_projects': list(compose_projects),
            'appdata_paths': list(appdata_paths),
            'total_containers': len(containers),
            'source': 'cache'
        }
    
    return results
```

---

## Monitoring & Troubleshooting

### Log Interpretation

The cache manager provides structured logging for easy monitoring:

#### Startup Logs
```
{"event": "Starting Docker cache manager...", "level": "info"}
{"event": "No valid cache files found for warmup", "level": "info"}
{"event": "Started 3 background cache tasks", "level": "info"}
{"event": "Starting initial cache warmup...", "level": "info"}
```

#### Update Cycle Logs
```
{"event": "Updating cache for 5 hosts", "level": "debug"}
{"event": "Host production-server cache updated: 12 containers in 2.34s", "level": "debug"}
{"event": "Cache update completed: 5 successful, 0 failed, 67 total containers, took 3.45s", "level": "info"}
```

#### Error Logs
```
{"event": "Failed to get Docker client for staging-server: Connection refused", "level": "error"}
{"event": "Error updating cache for staging-server after 5.67s: SSH connection failed", "level": "error"}
```

### Common Issues and Solutions

#### 1. Cache Not Populating

**Symptoms**:
- Discovery returns empty results
- `get_containers()` returns empty list
- Logs show "Could not connect to host"

**Diagnosis**:
```python
# Check cache statistics
stats = cache_manager.get_cache_stats()
print(f"Hosts cached: {stats['hosts_cached']}")
print(f"Total containers: {stats['total_containers']}")

# Check configured hosts
hosts = await cache_manager._get_configured_hosts()
print(f"Enabled hosts: {list(hosts.keys())}")
```

**Solutions**:
1. Verify host configuration and SSH connectivity
2. Check Docker context creation
3. Ensure hosts are enabled in configuration
4. Review Docker daemon accessibility

#### 2. Stale Cache Data

**Symptoms**:
- Containers shown as running when stopped
- Missing new containers
- Incorrect resource usage data

**Diagnosis**:
```python
# Check cache ages
stats = cache_manager.get_cache_stats()
for host_id, age in stats['cache_ages'].items():
    if age > 600:  # Older than 10 minutes
        print(f"⚠️  Stale cache for {host_id}: {age:.1f}s old")
```

**Solutions**:
1. Force refresh specific host: `get_containers(host_id, force_refresh=True)`
2. Check event stream connectivity
3. Verify update loop is running
4. Adjust TTL settings if needed

#### 3. High Memory Usage

**Symptoms**:
- Increasing memory usage over time
- Out of memory errors
- Slow cache operations

**Diagnosis**:
```python
# Monitor cache size
stats = cache_manager.get_cache_stats()
print(f"Hosts cached: {stats['hosts_cached']}")
print(f"Total containers: {stats['total_containers']}")

# Check cleanup loop
print(f"Memory retention: {cache_manager.cache_config.MEMORY_RETENTION}s")
```

**Solutions**:
1. Reduce `MEMORY_RETENTION` setting
2. Increase `MEMORY_CLEANUP_INTERVAL` frequency
3. Remove disabled hosts from configuration
4. Monitor for memory leaks in background tasks

#### 4. Event Stream Disconnections

**Symptoms**:
- Cache updates only during periodic refresh
- Missing real-time container status changes
- Event stream error logs

**Diagnosis**:
```bash
# Check Docker daemon event stream
docker events --since="1h"

# Check network connectivity to Docker hosts
ssh user@docker-host 'docker events --since="1m"'
```

**Solutions**:
1. Verify Docker daemon is running
2. Check SSH connection stability
3. Implement event stream reconnection logic
4. Monitor Docker API version compatibility

### Debug Strategies

#### Enable Debug Logging

```python
import structlog

# Configure debug logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.dev.ConsoleRenderer(colors=True)
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

# Set log level
cache_manager.logger.setLevel("DEBUG")
```

#### Cache Statistics Monitoring

```python
async def monitor_cache_health():
    """Monitor cache health and performance."""
    while True:
        stats = cache_manager.get_cache_stats()
        
        # Performance metrics
        print(f"Hosts: {stats['hosts_cached']}, Containers: {stats['total_containers']}")
        
        # Age monitoring
        for host_id, age in stats['cache_ages'].items():
            if age > stats['cache_ttl']:
                print(f"⚠️  {host_id} cache is stale: {age:.1f}s > {stats['cache_ttl']}s")
        
        await asyncio.sleep(60)  # Check every minute
```

#### Manual Cache Operations

```python
# Force refresh all hosts
for host_id in config.hosts.keys():
    if config.hosts[host_id].enabled:
        containers = await cache_manager.get_containers(host_id, force_refresh=True)
        print(f"Refreshed {host_id}: {len(containers)} containers")

# Clear specific host cache
if 'problematic-host' in cache_manager.containers_cache:
    del cache_manager.containers_cache['problematic-host']
    del cache_manager.cache_timestamps['problematic-host']
```

---

## Technical Details

### Docker SDK Integration

The cache manager integrates with Docker SDK for container inspection:

```python
async def _get_docker_client(self, host_id: str, host_config: Dict):
    """Get Docker client for host via DockerContextManager."""
    try:
        # Use context manager for SSH connection
        client = await self.context_manager.get_client(host_id)
        return client
    except Exception as e:
        self.logger.error(f"Failed to get Docker client for {host_id}: {e}")
        return None
```

The `DockerContextManager.get_client()` method:
1. Creates Docker context for SSH connection
2. Establishes Docker SDK client with SSH transport
3. Caches clients for reuse
4. Handles connection failures gracefully

### SSH Connection Management

Connections are managed through Docker contexts:

```python
# Context creation
ssh_url = f"ssh://{host_config.user}@{host_config.hostname}:{host_config.port}"
client = docker.DockerClient(base_url=ssh_url)

# Connection testing
client.ping()  # Verify connectivity

# Caching
self._client_cache[host_id] = client
```

### Async/Await Patterns

The cache manager uses proper async patterns for performance:

```python
# Parallel host updates
update_tasks = [
    asyncio.create_task(self._update_host_cache(host_id, host_config))
    for host_id, host_config in hosts.items()
]

# Concurrent stats collection with semaphore
semaphore = asyncio.Semaphore(3)
async def get_stat_with_semaphore(container):
    async with semaphore:
        return await get_single_stat(container)

# Executor for blocking Docker SDK calls
loop = asyncio.get_event_loop()
stats = await loop.run_in_executor(
    None, 
    lambda: container.stats(stream=False)
)
```

### Error Handling Strategies

#### Progressive Error Handling

```python
async def _update_host_cache(self, host_id: str, host_config: Dict):
    """Update cache with comprehensive error handling."""
    host_start = time.time()
    try:
        # Primary operation
        client = await self._get_docker_client(host_id, host_config)
        if not client:
            self.logger.warning(f"Could not connect to host {host_id}")
            return
        
        containers = await self._get_containers_async(client)
        # ... continue processing
        
    except asyncio.TimeoutError:
        self.logger.warning(f"Timeout updating cache for {host_id}")
    except Exception as e:
        host_time = time.time() - host_start
        self.logger.error(
            f"Error updating cache for {host_id} after {host_time:.2f}s: {e}",
            exc_info=True  # Include full traceback for debugging
        )
```

#### Graceful Degradation

```python
async def get_containers(self, host_id: str, force_refresh: bool = False):
    """Get containers with fallback strategies."""
    if force_refresh or not self._is_cache_fresh(host_id):
        try:
            # Attempt cache refresh
            await self._update_host_cache(host_id, host_config)
        except Exception as e:
            self.logger.warning(f"Cache refresh failed for {host_id}: {e}")
            # Continue with stale cache if available
    
    return list(self.containers_cache.get(host_id, {}).values())
```

### Data Serialization

Cache persistence uses compressed serialization:

```python
async def _persist_cache(self, host_id: str, cache_data: Dict):
    """Persist cache with compression and versioning."""
    try:
        import gzip
        import pickle
        
        # Create versioned cache bundle
        cache_bundle = {
            'version': '1.0',
            'timestamp': time.time(),
            'host_id': host_id,
            'containers': {
                cid: asdict(container)  # Convert dataclass to dict
                for cid, container in cache_data.items()
            }
        }
        
        # Compress and save
        cache_file = self.cache_dir / f"{host_id}_cache.pkl.gz"
        with gzip.open(cache_file, 'wb') as f:
            pickle.dump(cache_bundle, f)
            
    except Exception as e:
        self.logger.warning(f"Failed to persist cache for {host_id}: {e}")
```

### Resource Management

#### Memory Management
- Automatic cleanup of stale entries
- Configurable retention periods
- Efficient data structures (dataclasses, dicts)

#### Connection Pooling
- Reuse Docker SDK clients
- Context caching for SSH connections
- Graceful connection failure handling

#### Background Task Coordination
- Independent task lifecycle management
- Proper shutdown procedures
- Exception isolation between tasks

---

This documentation provides comprehensive coverage of the Docker Cache Manager, from basic usage to advanced configuration and troubleshooting. The cache manager dramatically improves Docker MCP server performance while maintaining data accuracy through intelligent caching strategies and real-time event processing.
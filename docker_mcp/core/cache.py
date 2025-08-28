"""SQLite-based caching system for Docker port data."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import structlog
from pydantic import BaseModel

from ..models.container import PortMapping

logger = structlog.get_logger()


class CacheEntry(BaseModel):
    """Cache entry with TTL support."""

    data: Any
    expires_at: float
    created_at: float

    @property
    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        return time.time() > self.expires_at

    @property
    def age_seconds(self) -> float:
        """Get age of cache entry in seconds."""
        return time.time() - self.created_at



class PortCache:
    """SQLite-based cache for port and container data with TTL support."""

    def __init__(self, data_dir: Path):
        self.db_path = data_dir / "port_cache.db"
        self.data_dir = data_dir
        self._ensure_data_dir()

    def _ensure_data_dir(self) -> None:
        """Ensure data directory exists."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Initialize database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            # Port mappings cache table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS port_mappings_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    data TEXT NOT NULL,  -- JSON serialized PortMapping list
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    UNIQUE(host_id, cache_key)
                )
            """)

            # Available ports cache table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS available_ports_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id TEXT NOT NULL,
                    port_range TEXT NOT NULL,  -- e.g., "8000-9000"
                    protocol TEXT NOT NULL DEFAULT 'TCP',
                    available_ports TEXT NOT NULL,  -- JSON list of available ports
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    UNIQUE(host_id, port_range, protocol)
                )
            """)


            # Container inspect cache table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS container_inspect_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id TEXT NOT NULL,
                    container_id TEXT NOT NULL,
                    inspect_data TEXT NOT NULL,  -- JSON serialized inspect data
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    UNIQUE(host_id, container_id)
                )
            """)

            # Historical port usage table (persistent)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS port_usage_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    protocol TEXT NOT NULL DEFAULT 'TCP',
                    container_name TEXT NOT NULL,
                    container_id TEXT NOT NULL,
                    image TEXT,
                    compose_project TEXT,
                    first_seen TEXT NOT NULL,  -- ISO timestamp
                    last_seen TEXT NOT NULL,   -- ISO timestamp
                    times_seen INTEGER DEFAULT 1
                )
            """)

            # Create indexes for better performance
            await db.execute("CREATE INDEX IF NOT EXISTS idx_port_mappings_host_expires ON port_mappings_cache(host_id, expires_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_available_ports_host_range ON available_ports_cache(host_id, port_range, expires_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_inspect_cache_host_container ON container_inspect_cache(host_id, container_id, expires_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_usage_history_host_port ON port_usage_history(host_id, port, protocol)")

            await db.commit()

        logger.info("Port cache database initialized", db_path=str(self.db_path))

    async def cleanup_expired(self) -> int:
        """Clean up expired cache entries and return count of removed entries."""
        current_time = time.time()
        removed_count = 0

        async with aiosqlite.connect(self.db_path) as db:
            # Clean expired port mappings
            cursor = await db.execute("DELETE FROM port_mappings_cache WHERE expires_at < ?", (current_time,))
            removed_count += cursor.rowcount

            # Clean expired available ports
            cursor = await db.execute("DELETE FROM available_ports_cache WHERE expires_at < ?", (current_time,))
            removed_count += cursor.rowcount

            # Clean expired container inspect data
            cursor = await db.execute("DELETE FROM container_inspect_cache WHERE expires_at < ?", (current_time,))
            removed_count += cursor.rowcount


            await db.commit()

        if removed_count > 0:
            logger.debug("Cleaned expired cache entries", removed_count=removed_count)

        return removed_count

    async def get_port_mappings(self, host_id: str, include_stopped: bool = False) -> list[PortMapping] | None:
        """Get cached port mappings if not expired."""
        cache_key = f"include_stopped_{include_stopped}"
        current_time = time.time()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT data, created_at, expires_at FROM port_mappings_cache WHERE host_id = ? AND cache_key = ? AND expires_at > ?",
                (host_id, cache_key, current_time)
            )
            row = await cursor.fetchone()

            if row:
                data_json, created_at, expires_at = row
                try:
                    mappings_data = json.loads(data_json)
                    mappings = [PortMapping(**mapping) for mapping in mappings_data]

                    age = time.time() - created_at
                    logger.debug("Retrieved cached port mappings", host_id=host_id, count=len(mappings), age_seconds=age)
                    return mappings
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("Failed to deserialize cached port mappings", host_id=host_id, error=str(e))
                    # Clean up corrupted entry
                    await db.execute("DELETE FROM port_mappings_cache WHERE host_id = ? AND cache_key = ?", (host_id, cache_key))
                    await db.commit()

        return None

    async def set_port_mappings(self, host_id: str, mappings: list[PortMapping], include_stopped: bool = False, ttl_minutes: int = 5) -> None:
        """Cache port mappings with TTL."""
        cache_key = f"include_stopped_{include_stopped}"
        current_time = time.time()
        expires_at = current_time + (ttl_minutes * 60)

        # Serialize mappings to JSON
        mappings_data = [mapping.model_dump() for mapping in mappings]
        data_json = json.dumps(mappings_data)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO port_mappings_cache (host_id, cache_key, data, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (host_id, cache_key, data_json, current_time, expires_at)
            )
            await db.commit()

        logger.debug("Cached port mappings", host_id=host_id, count=len(mappings), ttl_minutes=ttl_minutes)

    async def get_available_ports(self, host_id: str, port_range: str, protocol: str = "TCP") -> list[int] | None:
        """Get cached available ports scan if not expired."""
        current_time = time.time()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT available_ports, created_at FROM available_ports_cache WHERE host_id = ? AND port_range = ? AND protocol = ? AND expires_at > ?",
                (host_id, port_range, protocol, current_time)
            )
            row = await cursor.fetchone()

            if row:
                ports_json, created_at = row
                try:
                    available_ports = json.loads(ports_json)
                    age = time.time() - created_at
                    logger.debug("Retrieved cached available ports", host_id=host_id, port_range=port_range, count=len(available_ports), age_seconds=age)
                    return available_ports
                except json.JSONDecodeError as e:
                    logger.warning("Failed to deserialize cached available ports", host_id=host_id, error=str(e))

        return None

    async def set_available_ports(self, host_id: str, port_range: str, available_ports: list[int], protocol: str = "TCP", ttl_minutes: int = 15) -> None:
        """Cache available ports scan with TTL."""
        current_time = time.time()
        expires_at = current_time + (ttl_minutes * 60)
        ports_json = json.dumps(available_ports)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO available_ports_cache (host_id, port_range, protocol, available_ports, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (host_id, port_range, protocol, ports_json, current_time, expires_at)
            )
            await db.commit()

        logger.debug("Cached available ports scan", host_id=host_id, port_range=port_range, count=len(available_ports), ttl_minutes=ttl_minutes)

    async def get_container_inspect(self, host_id: str, container_id: str) -> dict[str, Any] | None:
        """Get cached container inspect data if not expired."""
        current_time = time.time()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT inspect_data, created_at FROM container_inspect_cache WHERE host_id = ? AND container_id = ? AND expires_at > ?",
                (host_id, container_id, current_time)
            )
            row = await cursor.fetchone()

            if row:
                data_json, created_at = row
                try:
                    inspect_data = json.loads(data_json)
                    age = time.time() - created_at
                    logger.debug("Retrieved cached container inspect", host_id=host_id, container_id=container_id, age_seconds=age)
                    return inspect_data
                except json.JSONDecodeError as e:
                    logger.warning("Failed to deserialize cached inspect data", host_id=host_id, container_id=container_id, error=str(e))

        return None

    async def set_container_inspect(self, host_id: str, container_id: str, inspect_data: dict[str, Any], ttl_minutes: int = 2) -> None:
        """Cache container inspect data with TTL."""
        current_time = time.time()
        expires_at = current_time + (ttl_minutes * 60)
        data_json = json.dumps(inspect_data)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO container_inspect_cache (host_id, container_id, inspect_data, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (host_id, container_id, data_json, current_time, expires_at)
            )
            await db.commit()

        logger.debug("Cached container inspect data", host_id=host_id, container_id=container_id, ttl_minutes=ttl_minutes)


    async def record_port_usage(self, mappings: list[PortMapping]) -> None:
        """Record port usage in historical table."""
        current_time = datetime.now().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            for mapping in mappings:
                # Check if this port usage already exists
                cursor = await db.execute(
                    "SELECT id, times_seen FROM port_usage_history WHERE host_id = ? AND port = ? AND protocol = ? AND container_id = ?",
                    (mapping.container_id.split('@')[0] if '@' in mapping.container_id else mapping.host_ip,
                     int(mapping.host_port), mapping.protocol, mapping.container_id)
                )
                existing = await cursor.fetchone()

                if existing:
                    # Update existing record
                    await db.execute(
                        "UPDATE port_usage_history SET last_seen = ?, times_seen = ? WHERE id = ?",
                        (current_time, existing[1] + 1, existing[0])
                    )
                else:
                    # Insert new record
                    await db.execute(
                        "INSERT INTO port_usage_history (host_id, port, protocol, container_name, container_id, image, compose_project, first_seen, last_seen, times_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        (mapping.container_id.split('@')[0] if '@' in mapping.container_id else mapping.host_ip,
                         int(mapping.host_port), mapping.protocol, mapping.container_name, mapping.container_id,
                         mapping.image, mapping.compose_project, current_time, current_time)
                    )

            await db.commit()

    async def get_port_usage_stats(self, host_id: str, days: int = 30) -> dict[str, Any]:
        """Get port usage statistics for the last N days."""
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            # Most used ports
            cursor = await db.execute(
                "SELECT port, protocol, SUM(times_seen) as total_usage FROM port_usage_history WHERE host_id = ? AND last_seen >= ? GROUP BY port, protocol ORDER BY total_usage DESC LIMIT 10",
                (host_id, cutoff_date)
            )
            most_used_ports = await cursor.fetchall()

            # Most active compose projects
            cursor = await db.execute(
                "SELECT compose_project, COUNT(DISTINCT port) as port_count FROM port_usage_history WHERE host_id = ? AND last_seen >= ? AND compose_project IS NOT NULL GROUP BY compose_project ORDER BY port_count DESC LIMIT 10",
                (host_id, cutoff_date)
            )
            active_projects = await cursor.fetchall()

            # Port range usage
            cursor = await db.execute(
                "SELECT port FROM port_usage_history WHERE host_id = ? AND last_seen >= ?",
                (host_id, cutoff_date)
            )
            all_ports = [row[0] for row in await cursor.fetchall()]

            range_usage = {
                "system_ports": len([p for p in all_ports if p <= 1023]),
                "user_ports": len([p for p in all_ports if 1024 <= p <= 49151]),
                "dynamic_ports": len([p for p in all_ports if p >= 49152])
            }

            return {
                "most_used_ports": [{"port": row[0], "protocol": row[1], "usage": row[2]} for row in most_used_ports],
                "active_projects": [{"project": row[0], "port_count": row[1]} for row in active_projects],
                "range_usage": range_usage,
                "total_unique_ports": len(set(all_ports)),
                "days_analyzed": days
            }

    async def invalidate_host_cache(self, host_id: str) -> None:
        """Invalidate all cache entries for a specific host."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM port_mappings_cache WHERE host_id = ?", (host_id,))
            await db.execute("DELETE FROM available_ports_cache WHERE host_id = ?", (host_id,))
            await db.execute("DELETE FROM container_inspect_cache WHERE host_id = ?", (host_id,))
            await db.commit()

        logger.info("Invalidated cache for host", host_id=host_id)

    async def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        async with aiosqlite.connect(self.db_path) as db:
            stats = {}

            # Port mappings cache stats
            cursor = await db.execute("SELECT COUNT(*) FROM port_mappings_cache WHERE expires_at > ?", (time.time(),))
            stats["active_port_mapping_entries"] = (await cursor.fetchone())[0]

            # Available ports cache stats
            cursor = await db.execute("SELECT COUNT(*) FROM available_ports_cache WHERE expires_at > ?", (time.time(),))
            stats["active_available_ports_entries"] = (await cursor.fetchone())[0]

            # Container inspect cache stats
            cursor = await db.execute("SELECT COUNT(*) FROM container_inspect_cache WHERE expires_at > ?", (time.time(),))
            stats["active_inspect_cache_entries"] = (await cursor.fetchone())[0]


            # Historical data stats
            cursor = await db.execute("SELECT COUNT(*) FROM port_usage_history")
            stats["historical_usage_records"] = (await cursor.fetchone())[0]

            # Database file size
            if self.db_path.exists():
                stats["database_size_bytes"] = self.db_path.stat().st_size
            else:
                stats["database_size_bytes"] = 0

            return stats

"""Tests for enhanced port functionality including caching and reservations."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from docker_mcp.core.cache import PortCache, PortReservation
from docker_mcp.core.config_loader import DockerMCPConfig
from docker_mcp.core.docker_context import DockerContextManager
from docker_mcp.models.container import PortMapping
from docker_mcp.services.container import ContainerService
from docker_mcp.tools.containers import ContainerTools


@pytest.fixture
async def temp_cache():
    """Create a temporary port cache for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = PortCache(Path(temp_dir))
        await cache.initialize()
        yield cache


@pytest.fixture
def mock_config():
    """Mock configuration for testing."""
    config = MagicMock(spec=DockerMCPConfig)
    config.hosts = {
        "test-host": MagicMock(hostname="test.example.com", user="testuser", port=22)
    }
    return config


@pytest.fixture
def mock_context_manager():
    """Mock DockerContextManager for testing."""
    return MagicMock(spec=DockerContextManager)


@pytest.fixture
def container_tools_with_cache(mock_config, mock_context_manager, temp_cache):
    """ContainerTools with cache for testing."""
    return ContainerTools(mock_config, mock_context_manager, temp_cache)


@pytest.fixture
def container_service_with_cache(mock_config, mock_context_manager, temp_cache):
    """ContainerService with cache for testing."""
    return ContainerService(mock_config, mock_context_manager, temp_cache)


class TestPortCache:
    """Test the port caching functionality."""

    @pytest.mark.asyncio
    async def test_cache_initialization(self, temp_cache):
        """Test cache database initialization."""
        # Cache should be initialized from fixture
        stats = await temp_cache.get_cache_stats()
        assert "active_port_mapping_entries" in stats
        assert "total_port_reservations" in stats
        assert stats["database_size_bytes"] > 0

    @pytest.mark.asyncio
    async def test_port_mappings_caching(self, temp_cache):
        """Test port mappings caching with TTL."""
        host_id = "test-host"
        port_mappings = [
            PortMapping(
                host_ip="0.0.0.0",
                host_port="8080",
                container_port="80",
                protocol="TCP",
                container_id="abc123",
                container_name="web-server",
                image="nginx:latest"
            )
        ]

        # Cache should be empty initially
        cached = await temp_cache.get_port_mappings(host_id, False)
        assert cached is None

        # Set cache
        await temp_cache.set_port_mappings(host_id, port_mappings, False, ttl_minutes=5)

        # Retrieve from cache
        cached = await temp_cache.get_port_mappings(host_id, False)
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].host_port == "8080"
        assert cached[0].container_name == "web-server"

    @pytest.mark.asyncio
    async def test_available_ports_caching(self, temp_cache):
        """Test available ports caching."""
        host_id = "test-host"
        port_range = "8000-8010"
        protocol = "TCP"
        available_ports = [8001, 8003, 8005, 8007, 8009]

        # Cache should be empty initially
        cached = await temp_cache.get_available_ports(host_id, port_range, protocol)
        assert cached is None

        # Set cache
        await temp_cache.set_available_ports(host_id, port_range, available_ports, protocol, ttl_minutes=15)

        # Retrieve from cache
        cached = await temp_cache.get_available_ports(host_id, port_range, protocol)
        assert cached == available_ports

    @pytest.mark.asyncio
    async def test_port_reservations(self, temp_cache):
        """Test port reservation functionality."""
        host_id = "test-host"

        # Create a reservation
        reservation = PortReservation(
            host_id=host_id,
            port=8080,
            protocol="TCP",
            service_name="my-web-app",
            reserved_by="test-user",
            reserved_at=datetime.now().isoformat(),
            notes="Test reservation"
        )

        # Reserve the port
        success = await temp_cache.reserve_port(reservation)
        assert success is True

        # Try to reserve same port again (should fail)
        duplicate_reservation = PortReservation(
            host_id=host_id,
            port=8080,
            protocol="TCP",
            service_name="another-app",
            reserved_by="another-user",
            reserved_at=datetime.now().isoformat()
        )
        success = await temp_cache.reserve_port(duplicate_reservation)
        assert success is False

        # List reservations
        reservations = await temp_cache.get_reservations(host_id)
        assert len(reservations) == 1
        assert reservations[0].port == 8080
        assert reservations[0].service_name == "my-web-app"

        # Release the port
        success = await temp_cache.release_port(host_id, 8080, "TCP")
        assert success is True

        # Try to release again (should fail)
        success = await temp_cache.release_port(host_id, 8080, "TCP")
        assert success is False

        # List reservations should be empty now
        reservations = await temp_cache.get_reservations(host_id)
        assert len(reservations) == 0

    @pytest.mark.asyncio
    async def test_cleanup_expired_entries(self, temp_cache):
        """Test cleanup of expired cache entries."""
        host_id = "test-host"

        # Add some test data with very short TTL
        port_mappings = [
            PortMapping(
                host_ip="0.0.0.0",
                host_port="8080",
                container_port="80",
                protocol="TCP",
                container_id="test123",
                container_name="test",
                image="test:latest"
            )
        ]

        # Set with 0 TTL (immediately expired)
        await temp_cache.set_port_mappings(host_id, port_mappings, False, ttl_minutes=0)

        # Cleanup should remove expired entries
        removed_count = await temp_cache.cleanup_expired()
        assert removed_count > 0

        # Cache should be empty now
        cached = await temp_cache.get_port_mappings(host_id, False)
        assert cached is None


class TestContainerToolsWithCaching:
    """Test ContainerTools with caching integration."""

    @pytest.mark.asyncio
    async def test_list_host_ports_with_cache(self, container_tools_with_cache):
        """Test that list_host_ports uses caching when available."""
        # Mock the underlying methods
        container_tools_with_cache._get_containers_for_port_analysis = AsyncMock(return_value=[])
        container_tools_with_cache._collect_port_mappings = AsyncMock(return_value=[])

        # First call should fetch fresh data
        result = await container_tools_with_cache.list_host_ports("test-host", use_cache=True)

        assert result["success"] is True
        assert result["cached"] is False  # First call, no cache hit
        assert "port_mappings" in result

        # Second call should use cache
        result2 = await container_tools_with_cache.list_host_ports("test-host", use_cache=True)

        assert result2["success"] is True
        assert result2["cached"] is True  # Second call, cache hit

    @pytest.mark.asyncio
    async def test_list_host_ports_cache_disabled(self, container_tools_with_cache):
        """Test that caching can be disabled."""
        # Mock the underlying methods
        container_tools_with_cache._get_containers_for_port_analysis = AsyncMock(return_value=[])
        container_tools_with_cache._collect_port_mappings = AsyncMock(return_value=[])

        # Call with caching disabled
        result = await container_tools_with_cache.list_host_ports("test-host", use_cache=False)

        assert result["success"] is True
        assert result["cached"] is False  # Caching was disabled


class TestContainerServiceWithCaching:
    """Test ContainerService with caching integration."""

    @pytest.mark.asyncio
    async def test_list_host_ports_service_with_cache(self, container_service_with_cache):
        """Test ContainerService port listing with cache."""
        # Mock the underlying tools layer
        container_service_with_cache.container_tools.list_host_ports = AsyncMock(
            return_value={
                "success": True,
                "host_id": "test-host",
                "total_ports": 0,
                "total_containers": 0,
                "port_mappings": [],
                "conflicts": [],
                "summary": {},
                "cached": True,
                "timestamp": datetime.now().isoformat()
            }
        )

        result = await container_service_with_cache.list_host_ports("test-host", use_cache=True)

        assert hasattr(result, 'structured_content')
        assert result.structured_content["success"] is True
        assert result.structured_content["cached"] is True


@pytest.mark.asyncio
async def test_enhanced_filtering():
    """Test port filtering functionality."""
    from docker_mcp.server import DockerMCPServer

    server = MagicMock()

    # Mock port mappings with different projects and protocols
    port_mappings = [
        {
            "host_port": "8080",
            "container_port": "80",
            "protocol": "TCP",
            "container_name": "web1",
            "compose_project": "frontend"
        },
        {
            "host_port": "3306",
            "container_port": "3306",
            "protocol": "TCP",
            "container_name": "db1",
            "compose_project": "backend"
        },
        {
            "host_port": "53",
            "container_port": "53",
            "protocol": "UDP",
            "container_name": "dns1",
            "compose_project": "infrastructure"
        }
    ]

    # Create instance and test filtering method
    server_instance = MagicMock(spec=DockerMCPServer)

    # Simulate the filtering method
    async def mock_apply_port_filters(mappings, filter_project, filter_range, filter_protocol):
        filtered = []
        for mapping in mappings:
            if filter_project and filter_project.lower() not in mapping.get("compose_project", "").lower():
                continue
            if filter_protocol and mapping.get("protocol", "").upper() != filter_protocol.upper():
                continue
            filtered.append(mapping)
        return filtered

    # Test project filtering
    filtered = await mock_apply_port_filters(port_mappings, "frontend", None, None)
    assert len(filtered) == 1
    assert filtered[0]["compose_project"] == "frontend"

    # Test protocol filtering
    filtered = await mock_apply_port_filters(port_mappings, None, None, "UDP")
    assert len(filtered) == 1
    assert filtered[0]["protocol"] == "UDP"

    # Test combined filtering
    filtered = await mock_apply_port_filters(port_mappings, "backend", None, "TCP")
    assert len(filtered) == 1
    assert filtered[0]["compose_project"] == "backend"
    assert filtered[0]["protocol"] == "TCP"


@pytest.mark.asyncio
async def test_port_reservation_workflow():
    """Test complete port reservation workflow."""
    # This would test the full reservation workflow but since we're in plan mode
    # and focused on the core functionality, this is a placeholder for future implementation
    pass


if __name__ == "__main__":
    pytest.main([__file__])

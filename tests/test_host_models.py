"""
Comprehensive tests for host-related Pydantic models.

Tests all host models to achieve 95%+ coverage on models/host.py.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from docker_mcp.models.host import AddHostRequest, HostInfo, HostResources, HostStatus


class TestHostInfo:
    """Test HostInfo model validation and serialization."""

    def test_host_info_minimal_required_fields(self):
        """Test HostInfo with only required fields."""
        host = HostInfo(
            host_id="test-host",
            hostname="test.example.com",
            user="testuser"
        )

        assert host.host_id == "test-host"
        assert host.hostname == "test.example.com"
        assert host.user == "testuser"
        assert host.port == 22  # Default value
        assert host.description == ""  # Default value
        assert host.tags == []  # Default factory
        assert host.enabled is True  # Default value
        assert host.connected is False  # Default value
        assert host.docker_version is None  # Default value
        assert host.last_ping is None  # Default value

    def test_host_info_all_fields(self):
        """Test HostInfo with all fields specified."""
        host = HostInfo(
            host_id="prod-host-01",
            hostname="prod01.example.com",
            user="deploy",
            port=2222,
            description="Production Docker host #1",
            tags=["production", "critical", "monitored"],
            enabled=True,
            connected=True,
            docker_version="24.0.7",
            last_ping="2025-01-15T10:30:00Z"
        )

        assert host.host_id == "prod-host-01"
        assert host.hostname == "prod01.example.com"
        assert host.user == "deploy"
        assert host.port == 2222
        assert host.description == "Production Docker host #1"
        assert host.tags == ["production", "critical", "monitored"]
        assert host.enabled is True
        assert host.connected is True
        assert host.docker_version == "24.0.7"
        assert host.last_ping == "2025-01-15T10:30:00Z"

    def test_host_info_serialization(self):
        """Test HostInfo model serialization."""
        host = HostInfo(
            host_id="test-host",
            hostname="test.example.com",
            user="testuser",
            port=3022,
            description="Test host",
            tags=["test", "development"],
            enabled=False,
            connected=True,
            docker_version="25.0.0",
            last_ping="2025-01-15T12:00:00Z"
        )

        data = host.model_dump()

        assert data["host_id"] == "test-host"
        assert data["hostname"] == "test.example.com"
        assert data["user"] == "testuser"
        assert data["port"] == 3022
        assert data["description"] == "Test host"
        assert data["tags"] == ["test", "development"]
        assert data["enabled"] is False
        assert data["connected"] is True
        assert data["docker_version"] == "25.0.0"
        assert data["last_ping"] == "2025-01-15T12:00:00Z"

    def test_host_info_validation_errors(self):
        """Test HostInfo validation with invalid data."""
        # Test missing required fields
        with pytest.raises(ValidationError) as exc_info:
            HostInfo()

        errors = exc_info.value.errors()
        required_fields = {error["loc"][0] for error in errors if error["type"] == "missing"}
        assert "host_id" in required_fields
        assert "hostname" in required_fields
        assert "user" in required_fields

    def test_host_info_empty_values(self):
        """Test HostInfo with empty string values."""
        # Empty strings are actually allowed in Pydantic by default
        # This test validates that the model handles them gracefully
        host = HostInfo(
            host_id="",  # Empty string is allowed
            hostname="test.example.com",
            user="testuser"
        )
        assert host.host_id == ""
        assert host.hostname == "test.example.com"
        assert host.user == "testuser"

    def test_host_info_type_validation(self):
        """Test HostInfo with incorrect types."""
        with pytest.raises(ValidationError):
            HostInfo(
                host_id="test-host",
                hostname="test.example.com",
                user="testuser",
                port="invalid_port"  # Should be int
            )

        with pytest.raises(ValidationError):
            HostInfo(
                host_id="test-host",
                hostname="test.example.com",
                user="testuser",
                enabled="invalid_bool"  # Should be bool
            )


class TestHostStatus:
    """Test HostStatus model validation and serialization."""

    def test_host_status_minimal_required_fields(self):
        """Test HostStatus with only required fields."""
        status = HostStatus(
            host_id="test-host",
            online=True,
            ssh_connected=True,
            docker_connected=True,
            last_check="2025-01-15T10:00:00Z"
        )

        assert status.host_id == "test-host"
        assert status.online is True
        assert status.ssh_connected is True
        assert status.docker_connected is True
        assert status.error_message is None
        assert status.last_check == "2025-01-15T10:00:00Z"
        assert status.response_time_ms is None

    def test_host_status_all_fields(self):
        """Test HostStatus with all fields specified."""
        status = HostStatus(
            host_id="prod-host-01",
            online=False,
            ssh_connected=False,
            docker_connected=False,
            error_message="Connection timeout after 30s",
            last_check="2025-01-15T10:30:00Z",
            response_time_ms=1250.5
        )

        assert status.host_id == "prod-host-01"
        assert status.online is False
        assert status.ssh_connected is False
        assert status.docker_connected is False
        assert status.error_message == "Connection timeout after 30s"
        assert status.last_check == "2025-01-15T10:30:00Z"
        assert status.response_time_ms == 1250.5

    def test_host_status_serialization(self):
        """Test HostStatus model serialization."""
        status = HostStatus(
            host_id="test-host",
            online=True,
            ssh_connected=True,
            docker_connected=False,
            error_message="Docker daemon not running",
            last_check="2025-01-15T11:00:00Z",
            response_time_ms=500.25
        )

        data = status.model_dump()

        assert data["host_id"] == "test-host"
        assert data["online"] is True
        assert data["ssh_connected"] is True
        assert data["docker_connected"] is False
        assert data["error_message"] == "Docker daemon not running"
        assert data["last_check"] == "2025-01-15T11:00:00Z"
        assert data["response_time_ms"] == 500.25

    def test_host_status_validation_errors(self):
        """Test HostStatus validation with invalid data."""
        with pytest.raises(ValidationError) as exc_info:
            HostStatus()

        errors = exc_info.value.errors()
        required_fields = {error["loc"][0] for error in errors if error["type"] == "missing"}
        assert "host_id" in required_fields
        assert "online" in required_fields
        assert "ssh_connected" in required_fields
        assert "docker_connected" in required_fields
        assert "last_check" in required_fields

    def test_host_status_type_validation(self):
        """Test HostStatus with incorrect types."""
        with pytest.raises(ValidationError):
            HostStatus(
                host_id="test-host",
                online="invalid",  # Should be bool
                ssh_connected=True,
                docker_connected=True,
                last_check="2025-01-15T10:00:00Z"
            )


class TestHostResources:
    """Test HostResources model validation and serialization."""

    def test_host_resources_minimal_required_fields(self):
        """Test HostResources with only required fields."""
        resources = HostResources(
            host_id="test-host"
        )

        assert resources.host_id == "test-host"
        assert resources.cpu_count is None
        assert resources.memory_total is None
        assert resources.memory_available is None
        assert resources.disk_total is None
        assert resources.disk_available is None
        assert resources.load_average is None
        assert resources.containers_running == 0
        assert resources.containers_total == 0
        assert resources.images_count == 0

    def test_host_resources_all_fields(self):
        """Test HostResources with all fields specified."""
        resources = HostResources(
            host_id="prod-host-01",
            cpu_count=8,
            memory_total=16 * 1024 * 1024 * 1024,  # 16GB in bytes
            memory_available=8 * 1024 * 1024 * 1024,  # 8GB in bytes
            disk_total=1000 * 1024 * 1024 * 1024,  # 1TB in bytes
            disk_available=500 * 1024 * 1024 * 1024,  # 500GB in bytes
            load_average=[0.5, 0.7, 0.9],
            containers_running=12,
            containers_total=25,
            images_count=45
        )

        assert resources.host_id == "prod-host-01"
        assert resources.cpu_count == 8
        assert resources.memory_total == 16 * 1024 * 1024 * 1024
        assert resources.memory_available == 8 * 1024 * 1024 * 1024
        assert resources.disk_total == 1000 * 1024 * 1024 * 1024
        assert resources.disk_available == 500 * 1024 * 1024 * 1024
        assert resources.load_average == [0.5, 0.7, 0.9]
        assert resources.containers_running == 12
        assert resources.containers_total == 25
        assert resources.images_count == 45

    def test_host_resources_serialization(self):
        """Test HostResources model serialization."""
        resources = HostResources(
            host_id="test-host",
            cpu_count=4,
            memory_total=8589934592,  # 8GB
            memory_available=4294967296,  # 4GB
            disk_total=500000000000,  # ~465GB
            disk_available=250000000000,  # ~233GB
            load_average=[1.2, 1.5, 1.8],
            containers_running=5,
            containers_total=10,
            images_count=15
        )

        data = resources.model_dump()

        assert data["host_id"] == "test-host"
        assert data["cpu_count"] == 4
        assert data["memory_total"] == 8589934592
        assert data["memory_available"] == 4294967296
        assert data["disk_total"] == 500000000000
        assert data["disk_available"] == 250000000000
        assert data["load_average"] == [1.2, 1.5, 1.8]
        assert data["containers_running"] == 5
        assert data["containers_total"] == 10
        assert data["images_count"] == 15

    def test_host_resources_validation_errors(self):
        """Test HostResources validation with invalid data."""
        with pytest.raises(ValidationError) as exc_info:
            HostResources()

        errors = exc_info.value.errors()
        required_fields = {error["loc"][0] for error in errors if error["type"] == "missing"}
        assert "host_id" in required_fields

    def test_host_resources_type_validation(self):
        """Test HostResources with incorrect types."""
        with pytest.raises(ValidationError):
            HostResources(
                host_id="test-host",
                cpu_count="invalid",  # Should be int
            )

        with pytest.raises(ValidationError):
            HostResources(
                host_id="test-host",
                load_average="invalid"  # Should be list[float]
            )

    def test_host_resources_with_partial_data(self):
        """Test HostResources with some None values."""
        resources = HostResources(
            host_id="partial-host",
            cpu_count=4,
            memory_total=None,  # Some metrics unavailable
            memory_available=None,
            disk_total=500000000000,
            disk_available=None,
            load_average=None,
            containers_running=3,
            containers_total=8,
            images_count=0
        )

        assert resources.host_id == "partial-host"
        assert resources.cpu_count == 4
        assert resources.memory_total is None
        assert resources.memory_available is None
        assert resources.disk_total == 500000000000
        assert resources.disk_available is None
        assert resources.load_average is None
        assert resources.containers_running == 3
        assert resources.containers_total == 8
        assert resources.images_count == 0


class TestAddHostRequest:
    """Test AddHostRequest model validation and serialization."""

    def test_add_host_request_minimal_required_fields(self):
        """Test AddHostRequest with only required fields."""
        request = AddHostRequest(
            host_id="new-host",
            ssh_host="new.example.com",
            ssh_user="deploy"
        )

        assert request.host_id == "new-host"
        assert request.ssh_host == "new.example.com"
        assert request.ssh_user == "deploy"
        assert request.ssh_port == 22  # Default value
        assert request.ssh_key_path is None  # Default value
        assert request.description == ""  # Default value
        assert request.tags == []  # Default factory
        assert request.test_connection is True  # Default value

    def test_add_host_request_all_fields(self):
        """Test AddHostRequest with all fields specified."""
        request = AddHostRequest(
            host_id="prod-new-host",
            ssh_host="prodnew.example.com",
            ssh_user="produser",
            ssh_port=2222,
            ssh_key_path="/home/user/.ssh/prod_key",
            description="New production host for scaling",
            tags=["production", "new", "scaling"],
            test_connection=False
        )

        assert request.host_id == "prod-new-host"
        assert request.ssh_host == "prodnew.example.com"
        assert request.ssh_user == "produser"
        assert request.ssh_port == 2222
        assert request.ssh_key_path == "/home/user/.ssh/prod_key"
        assert request.description == "New production host for scaling"
        assert request.tags == ["production", "new", "scaling"]
        assert request.test_connection is False

    def test_add_host_request_serialization(self):
        """Test AddHostRequest model serialization."""
        request = AddHostRequest(
            host_id="test-new-host",
            ssh_host="testnew.example.com",
            ssh_user="testuser",
            ssh_port=3022,
            ssh_key_path="/home/test/.ssh/test_key",
            description="Test host for development",
            tags=["test", "development", "temporary"],
            test_connection=True
        )

        data = request.model_dump()

        assert data["host_id"] == "test-new-host"
        assert data["ssh_host"] == "testnew.example.com"
        assert data["ssh_user"] == "testuser"
        assert data["ssh_port"] == 3022
        assert data["ssh_key_path"] == "/home/test/.ssh/test_key"
        assert data["description"] == "Test host for development"
        assert data["tags"] == ["test", "development", "temporary"]
        assert data["test_connection"] is True

    def test_add_host_request_validation_errors(self):
        """Test AddHostRequest validation with invalid data."""
        with pytest.raises(ValidationError) as exc_info:
            AddHostRequest()

        errors = exc_info.value.errors()
        required_fields = {error["loc"][0] for error in errors if error["type"] == "missing"}
        assert "host_id" in required_fields
        assert "ssh_host" in required_fields
        assert "ssh_user" in required_fields

    def test_add_host_request_type_validation(self):
        """Test AddHostRequest with incorrect types."""
        with pytest.raises(ValidationError):
            AddHostRequest(
                host_id="test-host",
                ssh_host="test.example.com",
                ssh_user="testuser",
                ssh_port="invalid"  # Should be int
            )

        with pytest.raises(ValidationError):
            AddHostRequest(
                host_id="test-host",
                ssh_host="test.example.com",
                ssh_user="testuser",
                test_connection="invalid"  # Should be bool
            )

    def test_add_host_request_empty_values(self):
        """Test AddHostRequest with empty string values."""
        # Empty strings are actually allowed in Pydantic by default
        # This test validates that the model handles them gracefully
        request = AddHostRequest(
            host_id="",  # Empty string is allowed
            ssh_host="test.example.com",
            ssh_user="testuser"
        )
        assert request.host_id == ""
        assert request.ssh_host == "test.example.com"
        assert request.ssh_user == "testuser"

        request2 = AddHostRequest(
            host_id="test-host",
            ssh_host="",  # Empty string is allowed
            ssh_user="testuser"
        )
        assert request2.host_id == "test-host"
        assert request2.ssh_host == ""
        assert request2.ssh_user == "testuser"


class TestModelInteraction:
    """Test interactions between different host models."""

    def test_host_info_to_add_request_conversion(self):
        """Test conceptual conversion between HostInfo and AddHostRequest."""
        # Simulate converting HostInfo back to AddHostRequest format
        host_info = HostInfo(
            host_id="existing-host",
            hostname="existing.example.com",
            user="deploy",
            port=2222,
            description="Existing production host",
            tags=["production", "converted"],
            enabled=True
        )

        # Convert to AddHostRequest-like structure
        request_data = {
            "host_id": host_info.host_id,
            "ssh_host": host_info.hostname,
            "ssh_user": host_info.user,
            "ssh_port": host_info.port,
            "description": host_info.description,
            "tags": host_info.tags.copy(),
            "test_connection": False  # Don't retest existing host
        }

        request = AddHostRequest(**request_data)

        assert request.host_id == host_info.host_id
        assert request.ssh_host == host_info.hostname
        assert request.ssh_user == host_info.user
        assert request.ssh_port == host_info.port
        assert request.description == host_info.description
        assert request.tags == host_info.tags

    def test_host_status_integration(self):
        """Test HostStatus with realistic status scenarios."""
        # Online and healthy
        healthy_status = HostStatus(
            host_id="healthy-host",
            online=True,
            ssh_connected=True,
            docker_connected=True,
            last_check=datetime.now().isoformat(),
            response_time_ms=50.25
        )

        assert healthy_status.online
        assert healthy_status.ssh_connected
        assert healthy_status.docker_connected
        assert healthy_status.error_message is None
        assert healthy_status.response_time_ms < 100

        # Partially degraded
        degraded_status = HostStatus(
            host_id="degraded-host",
            online=True,
            ssh_connected=True,
            docker_connected=False,
            error_message="Docker daemon not responding",
            last_check=datetime.now().isoformat(),
            response_time_ms=250.75
        )

        assert degraded_status.online
        assert degraded_status.ssh_connected
        assert not degraded_status.docker_connected
        assert "Docker daemon" in degraded_status.error_message

        # Completely offline
        offline_status = HostStatus(
            host_id="offline-host",
            online=False,
            ssh_connected=False,
            docker_connected=False,
            error_message="Host unreachable - connection timeout",
            last_check=datetime.now().isoformat(),
            response_time_ms=None
        )

        assert not offline_status.online
        assert not offline_status.ssh_connected
        assert not offline_status.docker_connected
        assert "unreachable" in offline_status.error_message
        assert offline_status.response_time_ms is None

    def test_host_resources_realistic_scenarios(self):
        """Test HostResources with realistic resource scenarios."""
        # High-end production server
        production_resources = HostResources(
            host_id="prod-server-01",
            cpu_count=32,
            memory_total=128 * 1024 * 1024 * 1024,  # 128GB
            memory_available=64 * 1024 * 1024 * 1024,  # 64GB available
            disk_total=10 * 1024 * 1024 * 1024 * 1024,  # 10TB
            disk_available=5 * 1024 * 1024 * 1024 * 1024,  # 5TB available
            load_average=[2.5, 3.1, 3.8],  # Moderate load
            containers_running=45,
            containers_total=60,
            images_count=120
        )

        assert production_resources.cpu_count == 32
        assert production_resources.memory_total > production_resources.memory_available
        assert production_resources.disk_total > production_resources.disk_available
        assert len(production_resources.load_average) == 3
        assert production_resources.containers_running <= production_resources.containers_total

        # Development workstation
        dev_resources = HostResources(
            host_id="dev-workstation",
            cpu_count=8,
            memory_total=16 * 1024 * 1024 * 1024,  # 16GB
            memory_available=8 * 1024 * 1024 * 1024,  # 8GB available
            disk_total=1024 * 1024 * 1024 * 1024,  # 1TB
            disk_available=512 * 1024 * 1024 * 1024,  # 512GB available
            load_average=[0.8, 1.2, 1.5],  # Light load
            containers_running=3,
            containers_total=8,
            images_count=25
        )

        assert dev_resources.cpu_count == 8
        assert all(load < 2.0 for load in dev_resources.load_average)
        assert dev_resources.containers_running < 10
        assert dev_resources.images_count < 50


class TestModelDefaults:
    """Test default values and field factories."""

    def test_default_factory_independence(self):
        """Test that default_factory creates independent instances."""
        host1 = HostInfo(
            host_id="host1",
            hostname="host1.example.com",
            user="user1"
        )

        host2 = HostInfo(
            host_id="host2",
            hostname="host2.example.com",
            user="user2"
        )

        # Modify tags on host1
        host1.tags.append("test-tag")

        # host2 tags should remain empty (independent instances)
        assert len(host1.tags) == 1
        assert len(host2.tags) == 0
        assert host1.tags is not host2.tags

    def test_field_factory_independence_add_request(self):
        """Test field factory independence in AddHostRequest."""
        request1 = AddHostRequest(
            host_id="req1",
            ssh_host="req1.example.com",
            ssh_user="user1"
        )

        request2 = AddHostRequest(
            host_id="req2",
            ssh_host="req2.example.com",
            ssh_user="user2"
        )

        # Modify tags on request1
        request1.tags.append("modified")

        # request2 tags should remain empty
        assert len(request1.tags) == 1
        assert len(request2.tags) == 0
        assert request1.tags is not request2.tags

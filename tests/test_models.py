"""Tests for Docker MCP Pydantic models."""

import pytest
from pydantic import ValidationError

from docker_mcp.models.host import (
    AddHostRequest,
    HostInfo,
    HostResources,
    HostStatus,
)


class TestHostInfo:
    """Test suite for HostInfo model."""

    def test_host_info_minimal(self):
        """Test HostInfo with minimal required fields."""
        host = HostInfo(
            host_id="test-host",
            hostname="test.example.com",
            user="testuser"
        )

        # Check required fields
        assert host.host_id == "test-host"
        assert host.hostname == "test.example.com"
        assert host.user == "testuser"

        # Check default values
        assert host.port == 22
        assert host.description == ""
        assert host.tags == []
        assert host.enabled is True
        assert host.connected is False
        assert host.docker_version is None
        assert host.last_ping is None

    def test_host_info_full(self):
        """Test HostInfo with all fields specified."""
        host = HostInfo(
            host_id="prod-1",
            hostname="prod1.example.com",
            user="deploy",
            port=2222,
            description="Production Docker host",
            tags=["production", "web"],
            enabled=False,
            connected=True,
            docker_version="24.0.7",
            last_ping="2025-01-15T10:30:00Z"
        )

        assert host.host_id == "prod-1"
        assert host.hostname == "prod1.example.com"
        assert host.user == "deploy"
        assert host.port == 2222
        assert host.description == "Production Docker host"
        assert host.tags == ["production", "web"]
        assert host.enabled is False
        assert host.connected is True
        assert host.docker_version == "24.0.7"
        assert host.last_ping == "2025-01-15T10:30:00Z"

    def test_host_info_serialization(self):
        """Test HostInfo serialization to dictionary."""
        host = HostInfo(
            host_id="test",
            hostname="test.com",
            user="user",
            port=2222,
            tags=["tag1", "tag2"]
        )

        data = host.model_dump()

        assert isinstance(data, dict)
        assert data["host_id"] == "test"
        assert data["hostname"] == "test.com"
        assert data["user"] == "user"
        assert data["port"] == 2222
        assert data["tags"] == ["tag1", "tag2"]
        assert data["enabled"] is True
        assert data["connected"] is False

    def test_host_info_validation_errors(self):
        """Test HostInfo validation errors."""
        # Missing required fields
        with pytest.raises(ValidationError):
            HostInfo()

        with pytest.raises(ValidationError):
            HostInfo(host_id="test")

        with pytest.raises(ValidationError):
            HostInfo(host_id="test", hostname="test.com")

    def test_host_info_type_validation(self):
        """Test HostInfo type validation."""
        # Invalid port type
        with pytest.raises(ValidationError):
            HostInfo(
                host_id="test",
                hostname="test.com",
                user="user",
                port="invalid"
            )

        # Invalid enabled type
        with pytest.raises(ValidationError):
            HostInfo(
                host_id="test",
                hostname="test.com",
                user="user",
                enabled="not-a-boolean"
            )

        # Invalid tags type
        with pytest.raises(ValidationError):
            HostInfo(
                host_id="test",
                hostname="test.com",
                user="user",
                tags="not-a-list"
            )


class TestHostStatus:
    """Test suite for HostStatus model."""

    def test_host_status_minimal(self):
        """Test HostStatus with minimal required fields."""
        status = HostStatus(
            host_id="test-host",
            online=True,
            ssh_connected=True,
            docker_connected=True,
            last_check="2025-01-15T10:30:00Z"
        )

        assert status.host_id == "test-host"
        assert status.online is True
        assert status.ssh_connected is True
        assert status.docker_connected is True
        assert status.last_check == "2025-01-15T10:30:00Z"
        assert status.error_message is None
        assert status.response_time_ms is None

    def test_host_status_full(self):
        """Test HostStatus with all fields specified."""
        status = HostStatus(
            host_id="prod-1",
            online=False,
            ssh_connected=False,
            docker_connected=False,
            error_message="Connection timeout",
            last_check="2025-01-15T10:30:00Z",
            response_time_ms=1500.5
        )

        assert status.host_id == "prod-1"
        assert status.online is False
        assert status.ssh_connected is False
        assert status.docker_connected is False
        assert status.error_message == "Connection timeout"
        assert status.last_check == "2025-01-15T10:30:00Z"
        assert status.response_time_ms == 1500.5

    def test_host_status_serialization(self):
        """Test HostStatus serialization to dictionary."""
        status = HostStatus(
            host_id="test",
            online=True,
            ssh_connected=False,
            docker_connected=True,
            last_check="2025-01-15T10:30:00Z",
            response_time_ms=250.0
        )

        data = status.model_dump()

        assert isinstance(data, dict)
        assert data["host_id"] == "test"
        assert data["online"] is True
        assert data["ssh_connected"] is False
        assert data["docker_connected"] is True
        assert data["last_check"] == "2025-01-15T10:30:00Z"
        assert data["response_time_ms"] == 250.0
        assert data["error_message"] is None

    def test_host_status_validation_errors(self):
        """Test HostStatus validation errors."""
        # Missing required fields
        with pytest.raises(ValidationError):
            HostStatus()

        with pytest.raises(ValidationError):
            HostStatus(host_id="test", online=True)


class TestHostResources:
    """Test suite for HostResources model."""

    def test_host_resources_minimal(self):
        """Test HostResources with minimal required fields."""
        resources = HostResources(host_id="test-host")

        assert resources.host_id == "test-host"
        # Check all optional fields have None defaults or 0 for counters
        assert resources.cpu_count is None
        assert resources.memory_total is None
        assert resources.memory_available is None
        assert resources.disk_total is None
        assert resources.disk_available is None
        assert resources.load_average is None
        assert resources.containers_running == 0
        assert resources.containers_total == 0
        assert resources.images_count == 0

    def test_host_resources_full(self):
        """Test HostResources with all fields specified."""
        resources = HostResources(
            host_id="prod-1",
            cpu_count=8,
            memory_total=16_000_000_000,  # 16GB
            memory_available=8_000_000_000,  # 8GB
            disk_total=1_000_000_000_000,  # 1TB
            disk_available=500_000_000_000,  # 500GB
            load_average=[1.5, 1.2, 0.9],
            containers_running=12,
            containers_total=15,
            images_count=25
        )

        assert resources.host_id == "prod-1"
        assert resources.cpu_count == 8
        assert resources.memory_total == 16_000_000_000
        assert resources.memory_available == 8_000_000_000
        assert resources.disk_total == 1_000_000_000_000
        assert resources.disk_available == 500_000_000_000
        assert resources.load_average == [1.5, 1.2, 0.9]
        assert resources.containers_running == 12
        assert resources.containers_total == 15
        assert resources.images_count == 25

    def test_host_resources_serialization(self):
        """Test HostResources serialization to dictionary."""
        resources = HostResources(
            host_id="test",
            cpu_count=4,
            memory_total=8_000_000_000,
            containers_running=5
        )

        data = resources.model_dump()

        assert isinstance(data, dict)
        assert data["host_id"] == "test"
        assert data["cpu_count"] == 4
        assert data["memory_total"] == 8_000_000_000
        assert data["containers_running"] == 5
        assert data["memory_available"] is None

    def test_host_resources_validation_errors(self):
        """Test HostResources validation errors."""
        # Missing required host_id
        with pytest.raises(ValidationError):
            HostResources()

        # Invalid type for numeric fields
        with pytest.raises(ValidationError):
            HostResources(host_id="test", cpu_count="four")

    def test_host_resources_load_average_validation(self):
        """Test load average list validation."""
        # Valid load average list
        resources = HostResources(
            host_id="test",
            load_average=[1.0, 1.5, 2.0]
        )
        assert resources.load_average == [1.0, 1.5, 2.0]

        # Invalid load average type
        with pytest.raises(ValidationError):
            HostResources(
                host_id="test",
                load_average="1.0,1.5,2.0"
            )


class TestAddHostRequest:
    """Test suite for AddHostRequest model."""

    def test_add_host_request_minimal(self):
        """Test AddHostRequest with minimal required fields."""
        request = AddHostRequest(
            host_id="new-host",
            ssh_host="192.168.1.100",
            ssh_user="admin"
        )

        assert request.host_id == "new-host"
        assert request.ssh_host == "192.168.1.100"
        assert request.ssh_user == "admin"

        # Check default values
        assert request.ssh_port == 22
        assert request.ssh_key_path is None
        assert request.description == ""
        assert request.tags == []
        assert request.test_connection is True

    def test_add_host_request_full(self):
        """Test AddHostRequest with all fields specified."""
        request = AddHostRequest(
            host_id="staging-2",
            ssh_host="staging2.example.com",
            ssh_user="deploy",
            ssh_port=2222,
            ssh_key_path="/home/user/.ssh/staging_key",
            description="Staging environment host",
            tags=["staging", "api"],
            test_connection=False
        )

        assert request.host_id == "staging-2"
        assert request.ssh_host == "staging2.example.com"
        assert request.ssh_user == "deploy"
        assert request.ssh_port == 2222
        assert request.ssh_key_path == "/home/user/.ssh/staging_key"
        assert request.description == "Staging environment host"
        assert request.tags == ["staging", "api"]
        assert request.test_connection is False

    def test_add_host_request_serialization(self):
        """Test AddHostRequest serialization to dictionary."""
        request = AddHostRequest(
            host_id="test",
            ssh_host="test.com",
            ssh_user="user",
            ssh_port=2222,
            tags=["test"]
        )

        data = request.model_dump()

        assert isinstance(data, dict)
        assert data["host_id"] == "test"
        assert data["ssh_host"] == "test.com"
        assert data["ssh_user"] == "user"
        assert data["ssh_port"] == 2222
        assert data["tags"] == ["test"]
        assert data["test_connection"] is True

    def test_add_host_request_validation_errors(self):
        """Test AddHostRequest validation errors."""
        # Missing required fields
        with pytest.raises(ValidationError):
            AddHostRequest()

        with pytest.raises(ValidationError):
            AddHostRequest(host_id="test")

        with pytest.raises(ValidationError):
            AddHostRequest(host_id="test", ssh_host="test.com")

    def test_add_host_request_default_factory(self):
        """Test that tags field uses default_factory properly."""
        request1 = AddHostRequest(
            host_id="host1",
            ssh_host="host1.com",
            ssh_user="user1"
        )
        request2 = AddHostRequest(
            host_id="host2",
            ssh_host="host2.com",
            ssh_user="user2"
        )

        # Both should have empty lists, but they should be different objects
        assert request1.tags == []
        assert request2.tags == []
        assert request1.tags is not request2.tags  # Different list objects

        # Modifying one shouldn't affect the other
        request1.tags.append("test")
        assert request1.tags == ["test"]
        assert request2.tags == []


class TestModelIntegration:
    """Integration tests for multiple models working together."""

    def test_model_data_flow(self):
        """Test that models can be used together in a data flow."""
        # Create an AddHostRequest
        add_request = AddHostRequest(
            host_id="new-prod",
            ssh_host="prod.example.com",
            ssh_user="deploy",
            description="New production host",
            tags=["production", "web"]
        )

        # Convert to HostInfo (simulating host creation)
        host_info = HostInfo(
            host_id=add_request.host_id,
            hostname=add_request.ssh_host,
            user=add_request.ssh_user,
            port=add_request.ssh_port,
            description=add_request.description,
            tags=add_request.tags,
            enabled=True,
            connected=False
        )

        # Create HostStatus (simulating status check)
        host_status = HostStatus(
            host_id=host_info.host_id,
            online=True,
            ssh_connected=True,
            docker_connected=True,
            last_check="2025-01-15T12:00:00Z",
            response_time_ms=150.5
        )

        # Create HostResources (simulating resource discovery)
        host_resources = HostResources(
            host_id=host_info.host_id,
            cpu_count=16,
            memory_total=32_000_000_000,
            memory_available=20_000_000_000,
            containers_running=8,
            containers_total=10,
            images_count=15
        )

        # Verify all models reference the same host
        assert host_info.host_id == "new-prod"
        assert host_status.host_id == "new-prod"
        assert host_resources.host_id == "new-prod"

        # Verify data consistency
        assert host_info.hostname == add_request.ssh_host
        assert host_info.tags == add_request.tags
        assert host_status.online is True
        assert host_resources.containers_running > 0

    def test_model_json_serialization(self):
        """Test that all models can be serialized to JSON consistently."""
        models = [
            HostInfo(host_id="test", hostname="test.com", user="user"),
            HostStatus(
                host_id="test",
                online=True,
                ssh_connected=True,
                docker_connected=True,
                last_check="2025-01-15T12:00:00Z"
            ),
            HostResources(host_id="test", cpu_count=4),
            AddHostRequest(host_id="test", ssh_host="test.com", ssh_user="user")
        ]

        for model in models:
            # Should not raise exception
            json_data = model.model_dump_json()
            assert isinstance(json_data, str)
            assert "test" in json_data

            # Should be parseable back to dict
            data = model.model_dump()
            assert isinstance(data, dict)
            assert data["host_id"] == "test"

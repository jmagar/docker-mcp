"""Tests for parameter validation using Pydantic models."""

import pytest
from pydantic import ValidationError

from docker_mcp.models.params import (
    DockerComposeParams,
    DockerContainerParams,
    DockerHostsParams,
)


class TestDockerHostsParams:
    """Test DockerHostsParams model validation."""

    def test_valid_params_minimal(self):
        """Test with minimal required parameters."""
        params = DockerHostsParams(action="list")
        assert params.action == "list"
        assert params.host_id == ""
        assert params.ssh_port == 22
        assert params.enabled is True
        assert params.tags == []
        assert params.compose_path_overrides == {}

    def test_valid_params_full(self):
        """Test with all parameters provided."""
        params = DockerHostsParams(
            action="add",
            host_id="test-host",
            ssh_host="server.example.com",
            ssh_user="dockeruser",
            ssh_port=2222,
            ssh_key_path="/path/to/key",
            description="Test host",
            tags=["prod", "web"],
            test_connection=False,
            include_stopped=True,
            compose_path="/opt/compose",
            enabled=False,
            ssh_config_path="/etc/ssh/config",
            selected_hosts="host1,host2",
            compose_path_overrides={"host1": "/custom/path"},
            auto_confirm=True
        )
        assert params.action == "add"
        assert params.ssh_port == 2222
        assert params.tags == ["prod", "web"]
        assert params.compose_path_overrides == {"host1": "/custom/path"}

    def test_port_validation(self):
        """Test SSH port validation."""
        # Valid port
        params = DockerHostsParams(action="add", ssh_port=8022)
        assert params.ssh_port == 8022

        # Invalid ports should raise validation error
        with pytest.raises(ValidationError):
            DockerHostsParams(action="add", ssh_port=0)

        with pytest.raises(ValidationError):
            DockerHostsParams(action="add", ssh_port=70000)

    def test_missing_required_action(self):
        """Test that action parameter is required."""
        with pytest.raises(ValidationError) as exc_info:
            DockerHostsParams()

        errors = exc_info.value.errors()
        assert any(error["type"] == "missing" and "action" in str(error) for error in errors)


class TestDockerContainerParams:
    """Test DockerContainerParams model validation."""

    def test_valid_params_minimal(self):
        """Test with minimal required parameters."""
        params = DockerContainerParams(action="list")
        assert params.action == "list"
        assert params.host_id == ""
        assert params.limit == 20
        assert params.offset == 0
        assert params.lines == 100
        assert params.timeout == 10

    def test_valid_params_full(self):
        """Test with all parameters provided."""
        params = DockerContainerParams(
            action="logs",
            host_id="test-host",
            container_id="abc123",
            all_containers=True,
            limit=50,
            offset=10,
            follow=True,
            lines=500,
            force=True,
            timeout=30
        )
        assert params.action == "logs"
        assert params.container_id == "abc123"
        assert params.all_containers is True
        assert params.limit == 50
        assert params.lines == 500

    def test_limit_validation(self):
        """Test limit parameter validation."""
        # Valid limits
        params = DockerContainerParams(action="list", limit=1)
        assert params.limit == 1

        params = DockerContainerParams(action="list", limit=1000)
        assert params.limit == 1000

        # Invalid limits should raise validation error
        with pytest.raises(ValidationError):
            DockerContainerParams(action="list", limit=0)

        with pytest.raises(ValidationError):
            DockerContainerParams(action="list", limit=1001)

    def test_offset_validation(self):
        """Test offset parameter validation."""
        # Valid offset
        params = DockerContainerParams(action="list", offset=0)
        assert params.offset == 0

        # Invalid offset should raise validation error
        with pytest.raises(ValidationError):
            DockerContainerParams(action="list", offset=-1)


class TestDockerComposeParams:
    """Test DockerComposeParams model validation."""

    def test_valid_params_minimal(self):
        """Test with minimal required parameters."""
        params = DockerComposeParams(action="list")
        assert params.action == "list"
        assert params.host_id == ""
        assert params.stack_name == ""
        assert params.environment == {}
        assert params.pull_images is True
        assert params.lines == 100

    def test_valid_params_full(self):
        """Test with all parameters provided."""
        compose_content = '''
version: '3.8'
services:
  web:
    image: nginx
'''
        params = DockerComposeParams(
            action="deploy",
            host_id="test-host",
            stack_name="my-stack",
            compose_content=compose_content,
            environment={"ENV": "prod", "DEBUG": "false"},
            pull_images=False,
            recreate=True,
            follow=True,
            lines=200,
            dry_run=True,
            options={"timeout": "30s"},
            target_host_id="target-host",
            remove_source=True,
            skip_stop_source=False,
            start_target=False
        )
        assert params.action == "deploy"
        assert params.stack_name == "my-stack"
        assert params.environment == {"ENV": "prod", "DEBUG": "false"}
        assert params.recreate is True
        assert params.options == {"timeout": "30s"}

    def test_lines_validation(self):
        """Test lines parameter validation."""
        # Valid lines count
        params = DockerComposeParams(action="logs", lines=1)
        assert params.lines == 1

        params = DockerComposeParams(action="logs", lines=10000)
        assert params.lines == 10000

        # Invalid lines count should raise validation error
        with pytest.raises(ValidationError):
            DockerComposeParams(action="logs", lines=0)

        with pytest.raises(ValidationError):
            DockerComposeParams(action="logs", lines=10001)


class TestParameterModelIntegration:
    """Test integration aspects of parameter models."""

    def test_all_models_have_action_field(self):
        """Ensure all parameter models have required action field."""
        models = [DockerHostsParams, DockerContainerParams, DockerComposeParams]

        for model_class in models:
            # Should require action field
            with pytest.raises(ValidationError):
                model_class()

            # Should accept action field
            instance = model_class(action="test")
            assert instance.action == "test"

    def test_model_serialization(self):
        """Test that models can be serialized to dict."""
        params = DockerHostsParams(
            action="add",
            host_id="test",
            tags=["tag1", "tag2"],
            compose_path_overrides={"host1": "/path"}
        )

        # Should serialize to dict
        data = params.model_dump()
        assert isinstance(data, dict)
        assert data["action"] == "add"
        assert data["host_id"] == "test"
        assert data["tags"] == ["tag1", "tag2"]
        assert data["compose_path_overrides"] == {"host1": "/path"}

    def test_model_deserialization(self):
        """Test that models can be created from dict."""
        data = {
            "action": "list",
            "host_id": "test-host",
            "ssh_port": 2222,
            "tags": ["prod"],
            "compose_path_overrides": {}
        }

        params = DockerHostsParams(**data)
        assert params.action == "list"
        assert params.host_id == "test-host"
        assert params.ssh_port == 2222
        assert params.tags == ["prod"]

    def test_field_descriptions_present(self):
        """Test that all fields have descriptions for API documentation."""
        models = [DockerHostsParams, DockerContainerParams, DockerComposeParams]

        for model_class in models:
            schema = model_class.model_json_schema()
            properties = schema.get("properties", {})

            # All fields should have descriptions
            for field_name, field_info in properties.items():
                assert "description" in field_info, f"Field '{field_name}' in {model_class.__name__} missing description"
                assert field_info["description"].strip(), f"Field '{field_name}' in {model_class.__name__} has empty description"

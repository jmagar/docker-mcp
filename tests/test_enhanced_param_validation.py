"""Enhanced tests for parameter validation with FastMCP advanced metadata patterns."""

import pytest
from pydantic import ValidationError

from docker_mcp.models.params import (
    DockerComposeParams,
    DockerContainerParams,
    DockerHostsParams,
)


class TestDockerHostsParamsEnhanced:
    """Enhanced tests for DockerHostsParams with new validation patterns."""

    # ========================================================================
    # Literal Type Validation Tests
    # ========================================================================

    def test_action_literal_validation_valid(self):
        """Test that valid action literals are accepted."""
        valid_actions = [
            "list", "add", "ports", "compose_path", "import_ssh", 
            "cleanup", "disk_usage", "schedule", "reserve_port", 
            "release_port", "list_reservations"
        ]
        
        for action in valid_actions:
            params = DockerHostsParams(action=action)
            assert params.action == action

    def test_action_literal_validation_invalid(self):
        """Test that invalid action literals are rejected."""
        invalid_actions = ["invalid_action", "start", "stop", "CLEANUP", "List"]
        
        for action in invalid_actions:
            with pytest.raises(ValidationError) as exc_info:
                DockerHostsParams(action=action)
            assert "Input should be" in str(exc_info.value)

    def test_cleanup_type_literal_validation(self):
        """Test cleanup_type Literal validation."""
        # Valid values
        valid_types = ["check", "safe", "moderate", "aggressive"]
        for cleanup_type in valid_types:
            params = DockerHostsParams(action="cleanup", cleanup_type=cleanup_type)
            assert params.cleanup_type == cleanup_type

        # None should be allowed
        params = DockerHostsParams(action="cleanup", cleanup_type=None)
        assert params.cleanup_type is None

        # Invalid values should be rejected
        with pytest.raises(ValidationError):
            DockerHostsParams(action="cleanup", cleanup_type="invalid")

    def test_schedule_action_literal_validation(self):
        """Test schedule_action Literal validation."""
        valid_actions = ["add", "remove", "list", "enable", "disable"]
        
        for schedule_action in valid_actions:
            params = DockerHostsParams(action="schedule", schedule_action=schedule_action)
            assert params.schedule_action == schedule_action

        # None should be allowed
        params = DockerHostsParams(action="schedule", schedule_action=None)
        assert params.schedule_action is None

        # Invalid values should be rejected
        with pytest.raises(ValidationError):
            DockerHostsParams(action="schedule", schedule_action="invalid")

    def test_schedule_frequency_literal_validation(self):
        """Test schedule_frequency Literal validation."""
        valid_frequencies = ["daily", "weekly", "monthly", "custom"]
        
        for frequency in valid_frequencies:
            params = DockerHostsParams(action="schedule", schedule_frequency=frequency)
            assert params.schedule_frequency == frequency

        # None should be allowed  
        params = DockerHostsParams(action="schedule", schedule_frequency=None)
        assert params.schedule_frequency is None

        # Invalid values should be rejected
        with pytest.raises(ValidationError):
            DockerHostsParams(action="schedule", schedule_frequency="invalid")

    def test_export_format_literal_validation(self):
        """Test export_format Literal validation."""
        valid_formats = ["json", "csv", "markdown"]
        
        for export_format in valid_formats:
            params = DockerHostsParams(action="ports", export_format=export_format)
            assert params.export_format == export_format

        # None should be allowed
        params = DockerHostsParams(action="ports", export_format=None)
        assert params.export_format is None

        # Invalid values should be rejected
        with pytest.raises(ValidationError):
            DockerHostsParams(action="ports", export_format="xml")

    def test_filter_protocol_literal_validation(self):
        """Test filter_protocol Literal validation."""
        valid_protocols = ["TCP", "UDP"]
        
        for protocol in valid_protocols:
            params = DockerHostsParams(action="ports", filter_protocol=protocol)
            assert params.filter_protocol == protocol

        # None should be allowed
        params = DockerHostsParams(action="ports", filter_protocol=None)
        assert params.filter_protocol is None

        # Invalid values should be rejected
        with pytest.raises(ValidationError):
            DockerHostsParams(action="ports", filter_protocol="HTTP")

    def test_protocol_literal_validation(self):
        """Test protocol Literal validation for port reservation."""
        valid_protocols = ["TCP", "UDP"]
        
        for protocol in valid_protocols:
            params = DockerHostsParams(action="reserve_port", protocol=protocol)
            assert params.protocol == protocol

        # Invalid values should be rejected
        with pytest.raises(ValidationError):
            DockerHostsParams(action="reserve_port", protocol="HTTP")

    # ========================================================================
    # Field Validation Constraint Tests
    # ========================================================================

    def test_host_id_min_length_validation(self):
        """Test host_id min_length validation."""
        # Valid non-empty host_id
        params = DockerHostsParams(action="add", host_id="test-host")
        assert params.host_id == "test-host"

        # Empty string should fail min_length=1 validation
        with pytest.raises(ValidationError) as exc_info:
            DockerHostsParams(action="add", host_id="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_ssh_host_min_length_validation(self):
        """Test ssh_host min_length validation."""
        # Valid non-empty ssh_host
        params = DockerHostsParams(action="add", ssh_host="server.example.com")
        assert params.ssh_host == "server.example.com"

        # Empty string should fail validation
        with pytest.raises(ValidationError) as exc_info:
            DockerHostsParams(action="add", ssh_host="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_ssh_user_min_length_validation(self):
        """Test ssh_user min_length validation."""
        # Valid non-empty ssh_user
        params = DockerHostsParams(action="add", ssh_user="dockeruser")
        assert params.ssh_user == "dockeruser"

        # Empty string should fail validation
        with pytest.raises(ValidationError) as exc_info:
            DockerHostsParams(action="add", ssh_user="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_service_name_min_length_validation(self):
        """Test service_name min_length validation."""
        # Valid non-empty service_name
        params = DockerHostsParams(action="reserve_port", service_name="nginx")
        assert params.service_name == "nginx"

        # Empty string should fail validation
        with pytest.raises(ValidationError) as exc_info:
            DockerHostsParams(action="reserve_port", service_name="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_schedule_time_pattern_validation(self):
        """Test schedule_time pattern validation for HH:MM format."""
        # Valid time formats
        valid_times = ["00:00", "14:30", "23:59"]
        for time_str in valid_times:
            params = DockerHostsParams(action="schedule", schedule_time=time_str)
            assert params.schedule_time == time_str

        # None should be allowed
        params = DockerHostsParams(action="schedule", schedule_time=None)
        assert params.schedule_time is None

        # Invalid patterns should be rejected
        invalid_times = ["2:30", "14:5", "25:00", "14:60", "abc", "14-30"]
        for time_str in invalid_times:
            with pytest.raises(ValidationError) as exc_info:
                DockerHostsParams(action="schedule", schedule_time=time_str)
            # Check that the error mentions the pattern
            errors = exc_info.value.errors()
            assert any("should match pattern" in str(error) for error in errors)

    # ========================================================================
    # Integration Tests
    # ========================================================================

    def test_full_add_host_validation(self):
        """Test complete add host scenario with all validation."""
        params = DockerHostsParams(
            action="add",
            host_id="prod-web-01",
            ssh_host="web01.example.com",
            ssh_user="dockeruser",
            ssh_port=2222,
            description="Production web server",
            tags=["prod", "web"]
        )
        assert params.action == "add"
        assert params.host_id == "prod-web-01"
        assert params.ssh_host == "web01.example.com"
        assert params.ssh_user == "dockeruser"
        assert params.ssh_port == 2222

    def test_full_schedule_validation(self):
        """Test complete schedule scenario with all validation."""
        params = DockerHostsParams(
            action="schedule",
            schedule_action="add",
            schedule_frequency="daily",
            schedule_time="02:30",
            cleanup_type="safe"
        )
        assert params.schedule_action == "add"
        assert params.schedule_frequency == "daily"
        assert params.schedule_time == "02:30"
        assert params.cleanup_type == "safe"

    def test_full_port_reservation_validation(self):
        """Test complete port reservation scenario with all validation."""
        params = DockerHostsParams(
            action="reserve_port",
            host_id="prod-01",
            port=8080,
            protocol="TCP",
            service_name="nginx-proxy",
            reserved_by="admin",
            expires_days=30,
            notes="Reserved for nginx proxy service"
        )
        assert params.action == "reserve_port"
        assert params.host_id == "prod-01"
        assert params.port == 8080
        assert params.protocol == "TCP"
        assert params.service_name == "nginx-proxy"
        assert params.expires_days == 30


class TestDockerContainerParamsEnhanced:
    """Enhanced tests for DockerContainerParams with new validation patterns."""

    def test_action_literal_validation_valid(self):
        """Test that valid container action literals are accepted."""
        valid_actions = ["list", "info", "start", "stop", "restart", "build", "logs", "pull"]
        
        for action in valid_actions:
            params = DockerContainerParams(action=action)
            assert params.action == action

    def test_action_literal_validation_invalid(self):
        """Test that invalid container action literals are rejected."""
        invalid_actions = ["invalid", "deploy", "create", "LIST", "Start"]
        
        for action in invalid_actions:
            with pytest.raises(ValidationError):
                DockerContainerParams(action=action)

    def test_host_id_min_length_validation(self):
        """Test container host_id min_length validation."""
        # Valid host_id
        params = DockerContainerParams(action="list", host_id="docker-host")
        assert params.host_id == "docker-host"

        # Empty string should fail validation  
        with pytest.raises(ValidationError) as exc_info:
            DockerContainerParams(action="list", host_id="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_container_id_min_length_validation(self):
        """Test container_id min_length validation.""" 
        # Valid container_id
        params = DockerContainerParams(action="info", container_id="nginx-container")
        assert params.container_id == "nginx-container"

        # Empty string should fail validation
        with pytest.raises(ValidationError) as exc_info:
            DockerContainerParams(action="info", container_id="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_complete_container_operation(self):
        """Test complete container operation scenario."""
        params = DockerContainerParams(
            action="logs",
            host_id="production-01", 
            container_id="web-server-01",
            follow=True,
            lines=500,
            timeout=30
        )
        assert params.action == "logs"
        assert params.host_id == "production-01"
        assert params.container_id == "web-server-01"
        assert params.follow is True
        assert params.lines == 500
        assert params.timeout == 30


class TestDockerComposeParamsEnhanced:
    """Enhanced tests for DockerComposeParams with new validation patterns."""

    def test_action_literal_validation_valid(self):
        """Test that valid compose action literals are accepted."""
        valid_actions = ["list", "deploy", "up", "down", "restart", "build", "discover", "logs", "migrate"]
        
        for action in valid_actions:
            params = DockerComposeParams(action=action)
            assert params.action == action

    def test_action_literal_validation_invalid(self):
        """Test that invalid compose action literals are rejected."""
        invalid_actions = ["invalid", "start", "stop", "LIST", "Deploy"]
        
        for action in invalid_actions:
            with pytest.raises(ValidationError):
                DockerComposeParams(action=action)

    def test_host_id_min_length_validation(self):
        """Test compose host_id min_length validation."""
        # Valid host_id
        params = DockerComposeParams(action="list", host_id="docker-host")
        assert params.host_id == "docker-host"

        # Empty string should fail validation
        with pytest.raises(ValidationError) as exc_info:
            DockerComposeParams(action="list", host_id="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_stack_name_min_length_validation(self):
        """Test stack_name min_length validation."""
        # Valid stack_name
        params = DockerComposeParams(action="deploy", stack_name="my-web-stack")
        assert params.stack_name == "my-web-stack"

        # Empty string should fail validation
        with pytest.raises(ValidationError) as exc_info:
            DockerComposeParams(action="deploy", stack_name="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_target_host_id_min_length_validation(self):
        """Test target_host_id min_length validation."""
        # Valid target_host_id
        params = DockerComposeParams(action="migrate", target_host_id="target-host")
        assert params.target_host_id == "target-host"

        # Empty string should fail validation
        with pytest.raises(ValidationError) as exc_info:
            DockerComposeParams(action="migrate", target_host_id="")
        errors = exc_info.value.errors()
        assert any("at least 1 character" in str(error) for error in errors)

    def test_complete_deploy_operation(self):
        """Test complete deploy operation scenario."""
        compose_content = '''version: '3.8'
services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"'''
        
        params = DockerComposeParams(
            action="deploy",
            host_id="production-01",
            stack_name="nginx-proxy",
            compose_content=compose_content,
            environment={"ENV": "prod"},
            pull_images=True,
            recreate=False
        )
        assert params.action == "deploy"
        assert params.host_id == "production-01"
        assert params.stack_name == "nginx-proxy"
        assert params.environment == {"ENV": "prod"}
        assert params.pull_images is True
        assert params.recreate is False

    def test_complete_migrate_operation(self):
        """Test complete migrate operation scenario."""
        params = DockerComposeParams(
            action="migrate",
            host_id="source-host",
            target_host_id="target-host",
            stack_name="web-app",
            dry_run=False,
            remove_source=True,
            start_target=True
        )
        assert params.action == "migrate"
        assert params.host_id == "source-host"
        assert params.target_host_id == "target-host"
        assert params.stack_name == "web-app"
        assert params.dry_run is False
        assert params.remove_source is True
        assert params.start_target is True


class TestParameterValidationIntegration:
    """Integration tests for parameter validation across all models."""

    def test_all_models_have_proper_literal_types(self):
        """Test that all models have proper Literal type validation for actions."""
        # Test valid actions for each model
        DockerHostsParams(action="list")
        DockerContainerParams(action="list")
        DockerComposeParams(action="list")

        # Test invalid actions are rejected for each model
        with pytest.raises(ValidationError):
            DockerHostsParams(action="invalid")
        with pytest.raises(ValidationError):
            DockerContainerParams(action="invalid")
        with pytest.raises(ValidationError):
            DockerComposeParams(action="invalid")

    def test_consistent_host_id_validation_across_models(self):
        """Test that host_id validation is consistent across all models."""
        models_and_actions = [
            (DockerHostsParams, "add"),
            (DockerContainerParams, "list"),
            (DockerComposeParams, "list")
        ]
        
        for model_class, action in models_and_actions:
            # Valid host_id should work
            params = model_class(action=action, host_id="test-host")
            assert params.host_id == "test-host"

            # Empty host_id should fail validation
            with pytest.raises(ValidationError):
                model_class(action=action, host_id="")

    def test_field_descriptions_are_clean(self):
        """Test that field descriptions don't contain verbose usage annotations."""
        models = [DockerHostsParams, DockerContainerParams, DockerComposeParams]
        
        for model_class in models:
            schema = model_class.model_json_schema()
            properties = schema.get("properties", {})
            
            for field_name, field_info in properties.items():
                description = field_info.get("description", "")
                # Ensure no verbose usage annotations remain
                assert "**(used by:" not in description, f"Field {field_name} still has verbose annotation"
                # Ensure description is clean and helpful
                assert description.strip(), f"Field {field_name} has empty description"

    def test_model_serialization_with_literal_types(self):
        """Test that models with Literal types serialize correctly."""
        # Test DockerHostsParams with various Literal fields
        params = DockerHostsParams(
            action="cleanup",
            cleanup_type="safe",
            schedule_action="add",
            schedule_frequency="daily",
            export_format="json",
            protocol="TCP"
        )
        
        data = params.model_dump()
        assert data["action"] == "cleanup"
        assert data["cleanup_type"] == "safe"
        assert data["schedule_action"] == "add"
        assert data["schedule_frequency"] == "daily"
        assert data["export_format"] == "json"
        assert data["protocol"] == "TCP"

    def test_edge_case_combinations(self):
        """Test edge cases and unusual but valid parameter combinations."""
        # Schedule with cleanup
        params = DockerHostsParams(
            action="schedule",
            schedule_action="add",
            schedule_frequency="custom",
            schedule_time="03:15",
            cleanup_type="aggressive"
        )
        assert params.schedule_action == "add"
        assert params.cleanup_type == "aggressive"

        # Port reservation with all optional fields
        params = DockerHostsParams(
            action="reserve_port",
            host_id="prod-01",
            port=9000,
            protocol="UDP",
            service_name="monitoring",
            reserved_by="devops-team",
            expires_days=90,
            notes="Reserved for monitoring service UDP traffic"
        )
        assert params.protocol == "UDP"
        assert params.expires_days == 90

        # Container operation with all parameters
        params = DockerContainerParams(
            action="logs",
            host_id="dev-01",
            container_id="test-container",
            follow=True,
            lines=1000,
            timeout=60,
            all_containers=False
        )
        assert params.lines == 1000
        assert params.timeout == 60


# ============================================================================
# Parametrized Tests for Comprehensive Coverage
# ============================================================================

class TestParametrizedValidation:
    """Parametrized tests for comprehensive validation coverage."""

    @pytest.mark.parametrize("action", [
        "list", "add", "ports", "compose_path", "import_ssh", 
        "cleanup", "disk_usage", "schedule", "reserve_port", 
        "release_port", "list_reservations"
    ])
    def test_all_docker_hosts_actions(self, action):
        """Test all valid DockerHostsParams actions."""
        params = DockerHostsParams(action=action)
        assert params.action == action

    @pytest.mark.parametrize("action", [
        "list", "info", "start", "stop", "restart", "build", "logs", "pull"
    ])
    def test_all_docker_container_actions(self, action):
        """Test all valid DockerContainerParams actions."""
        params = DockerContainerParams(action=action)
        assert params.action == action

    @pytest.mark.parametrize("action", [
        "list", "deploy", "up", "down", "restart", "build", "discover", "logs", "migrate"
    ])
    def test_all_docker_compose_actions(self, action):
        """Test all valid DockerComposeParams actions."""
        params = DockerComposeParams(action=action)
        assert params.action == action

    @pytest.mark.parametrize("cleanup_type", ["check", "safe", "moderate", "aggressive"])
    def test_all_cleanup_types(self, cleanup_type):
        """Test all valid cleanup_type Literal values."""
        params = DockerHostsParams(action="cleanup", cleanup_type=cleanup_type)
        assert params.cleanup_type == cleanup_type

    @pytest.mark.parametrize("schedule_frequency", ["daily", "weekly", "monthly", "custom"])
    def test_all_schedule_frequencies(self, schedule_frequency):
        """Test all valid schedule_frequency Literal values."""
        params = DockerHostsParams(action="schedule", schedule_frequency=schedule_frequency)
        assert params.schedule_frequency == schedule_frequency

    @pytest.mark.parametrize("export_format", ["json", "csv", "markdown"])
    def test_all_export_formats(self, export_format):
        """Test all valid export_format Literal values."""
        params = DockerHostsParams(action="ports", export_format=export_format)
        assert params.export_format == export_format

    @pytest.mark.parametrize("protocol", ["TCP", "UDP"])
    def test_all_protocols(self, protocol):
        """Test all valid protocol Literal values."""
        params = DockerHostsParams(action="reserve_port", protocol=protocol)
        assert params.protocol == protocol

    @pytest.mark.parametrize("time_str", ["00:00", "12:30", "23:59", "01:01", "14:45"])
    def test_valid_schedule_times(self, time_str):
        """Test various valid schedule time patterns."""
        params = DockerHostsParams(action="schedule", schedule_time=time_str)
        assert params.schedule_time == time_str

    @pytest.mark.parametrize("invalid_time", ["2:30", "14:5", "25:00", "14:60", "abc", "14-30", ""])
    def test_invalid_schedule_times(self, invalid_time):
        """Test various invalid schedule time patterns."""
        with pytest.raises(ValidationError):
            DockerHostsParams(action="schedule", schedule_time=invalid_time)
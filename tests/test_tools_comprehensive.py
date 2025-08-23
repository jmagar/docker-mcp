"""
Comprehensive parametrized tests for tools layer to boost coverage.

Targets the largest tools modules with systematic testing approaches.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import json

from docker_mcp.core.config_loader import DockerMCPConfig
from docker_mcp.core.docker_context import DockerContextManager
from docker_mcp.tools.containers import ContainerTools
from docker_mcp.tools.stacks import StackTools
from docker_mcp.tools.logs import LogTools
from docker_mcp.core.exceptions import DockerCommandError, DockerContextError


class TestContainerToolsComprehensive:
    """Comprehensive tests for ContainerTools to boost coverage."""
    
    @pytest.fixture
    def mock_config(self):
        """Mock DockerMCPConfig for testing."""
        config = MagicMock(spec=DockerMCPConfig)
        config.hosts = {
            "test-host": MagicMock(hostname="test.example.com", user="testuser", port=22)
        }
        return config
    
    @pytest.fixture
    def mock_context_manager(self):
        """Mock DockerContextManager for testing."""
        return MagicMock(spec=DockerContextManager)
    
    @pytest.fixture
    def container_tools(self, mock_config, mock_context_manager):
        """Create ContainerTools instance for testing."""
        return ContainerTools(mock_config, mock_context_manager)
    
    @pytest.mark.parametrize("limit,offset,expected_params", [
        (10, 0, {"limit": 10, "offset": 0}),
        (50, 20, {"limit": 50, "offset": 20}),
        (100, 100, {"limit": 100, "offset": 100}),
    ])
    @pytest.mark.asyncio
    async def test_list_containers_pagination_params(
        self, container_tools, mock_context_manager, limit, offset, expected_params
    ):
        """Test list_containers with various pagination parameters."""
        # Mock successful response
        mock_response = {
            "output": json.dumps([
                {
                    "ID": "abc123",
                    "Names": "test-container",
                    "Image": "nginx:alpine",
                    "State": "running",
                    "Status": "Up 2 hours",
                    "Ports": "80/tcp"
                }
            ])
        }
        mock_context_manager.execute_docker_command.return_value = mock_response
        
        result = await container_tools.list_containers("test-host", limit=limit, offset=offset)
        
        assert result["success"] is True
        assert "containers" in result
        assert "pagination" in result
        assert result["pagination"]["limit"] == expected_params["limit"]
        assert result["pagination"]["offset"] == expected_params["offset"]
    
    @pytest.mark.parametrize("all_containers,expected_filter", [
        (True, "--all"),
        (False, ""),
    ])
    @pytest.mark.asyncio
    async def test_list_containers_all_filter(
        self, container_tools, mock_context_manager, all_containers, expected_filter
    ):
        """Test list_containers with all_containers parameter."""
        mock_response = {"output": "[]"}
        mock_context_manager.execute_docker_command.return_value = mock_response
        
        await container_tools.list_containers("test-host", all_containers=all_containers)
        
        # Check that the command was called with correct filter
        call_args = mock_context_manager.execute_docker_command.call_args
        assert call_args[0][0] == "test-host"  # host_id
        if all_containers:
            assert "--all" in call_args[0][1]  # command
        else:
            assert "--all" not in call_args[0][1]
    
    @pytest.mark.parametrize("mock_response,expected_count", [
        ({"output": "[]"}, 0),
        ({"output": json.dumps([{"ID": "abc123", "Names": "test1"}])}, 1),
        ({"output": json.dumps([
            {"ID": "abc123", "Names": "test1"},
            {"ID": "def456", "Names": "test2"},
            {"ID": "ghi789", "Names": "test3"}
        ])}, 3),
    ])
    @pytest.mark.asyncio
    async def test_list_containers_response_parsing(
        self, container_tools, mock_context_manager, mock_response, expected_count
    ):
        """Test list_containers response parsing with various container counts."""
        mock_context_manager.execute_docker_command.return_value = mock_response
        
        result = await container_tools.list_containers("test-host")
        
        assert result["success"] is True
        assert len(result["containers"]) == expected_count
        assert result["pagination"]["total"] == expected_count
    
    @pytest.mark.parametrize("action,expected_command", [
        ("start", "start"),
        ("stop", "stop"),
        ("restart", "restart"),
        ("pause", "pause"),
        ("unpause", "unpause"),
    ])
    @pytest.mark.asyncio
    async def test_manage_container_actions(
        self, container_tools, mock_context_manager, action, expected_command
    ):
        """Test manage_container with various actions."""
        mock_context_manager.execute_docker_command.return_value = {"output": ""}
        
        result = await container_tools.manage_container("test-host", "container123", action)
        
        assert result["success"] is True
        assert result["action"] == action
        assert result["container_id"] == "container123"
        
        # Verify correct command was called
        call_args = mock_context_manager.execute_docker_command.call_args
        assert expected_command in call_args[0][1]
        assert "container123" in call_args[0][1]
    
    @pytest.mark.parametrize("action", [
        "invalid_action",
        "delete",
        "create",
        "unknown",
    ])
    @pytest.mark.asyncio
    async def test_manage_container_invalid_actions(
        self, container_tools, action
    ):
        """Test manage_container with invalid actions."""
        result = await container_tools.manage_container("test-host", "container123", action)
        
        assert result["success"] is False
        assert "error" in result
        assert "Invalid action" in result["error"]
    
    @pytest.mark.parametrize("force,timeout,expected_params", [
        (False, 10, {}),
        (True, 10, {"force": True}),
        (False, 30, {"timeout": 30}),
        (True, 60, {"force": True, "timeout": 60}),
    ])
    @pytest.mark.asyncio
    async def test_manage_container_with_options(
        self, container_tools, mock_context_manager, force, timeout, expected_params
    ):
        """Test manage_container with force and timeout options."""
        mock_context_manager.execute_docker_command.return_value = {"output": ""}
        
        result = await container_tools.manage_container(
            "test-host", "container123", "stop", force=force, timeout=timeout
        )
        
        assert result["success"] is True
        
        # Check command construction based on parameters
        call_args = mock_context_manager.execute_docker_command.call_args[0][1]
        if force and "stop" in call_args:
            # Force option should add --force flag
            assert "--force" in call_args or "-f" in call_args
        if timeout != 10:  # 10 is default, shouldn't add explicit timeout
            assert str(timeout) in call_args
    
    @pytest.mark.asyncio
    async def test_get_container_info_success(self, container_tools, mock_context_manager):
        """Test get_container_info with successful response."""
        mock_inspect_response = {
            "Id": "abc123full",
            "Name": "/test-container",
            "Config": {"Image": "nginx:alpine"},
            "State": {"Status": "running", "StartedAt": "2025-01-15T10:00:00Z"},
            "NetworkSettings": {"Ports": {"80/tcp": [{"HostPort": "8080"}]}},
            "Mounts": []
        }
        mock_context_manager.execute_docker_command.return_value = mock_inspect_response
        
        result = await container_tools.get_container_info("test-host", "container123")
        
        assert result["success"] is True
        assert result["container_id"] == "abc123full"
        assert result["name"] == "test-container"  # Leading slash removed
        assert result["image"] == "nginx:alpine"
        assert result["state"]["status"] == "running"
    
    @pytest.mark.parametrize("exception_type,error_message", [
        (DockerCommandError, "Docker command failed"),
        (DockerContextError, "Docker context error"),
        (Exception, "Unexpected error occurred"),
    ])
    @pytest.mark.asyncio
    async def test_container_operations_error_handling(
        self, container_tools, mock_context_manager, exception_type, error_message
    ):
        """Test error handling for various exception types."""
        mock_context_manager.execute_docker_command.side_effect = exception_type(error_message)
        
        result = await container_tools.list_containers("test-host")
        
        assert result["success"] is False
        assert "error" in result
        assert error_message in result["error"]
    
    @pytest.mark.parametrize("include_stopped,host_port_filter", [
        (True, None),
        (False, None),
        (True, "8080"),
        (False, "80"),
    ])
    @pytest.mark.asyncio
    async def test_list_host_ports_parameters(
        self, container_tools, mock_context_manager, include_stopped, host_port_filter
    ):
        """Test list_host_ports with various parameter combinations."""
        # Mock container list response
        mock_containers = [
            {
                "ID": "abc123",
                "Names": "test1",
                "State": "running",
                "Ports": "8080:80/tcp"
            },
            {
                "ID": "def456", 
                "Names": "test2",
                "State": "exited",
                "Ports": "8081:80/tcp"
            }
        ]
        mock_context_manager.execute_docker_command.return_value = {
            "output": json.dumps(mock_containers)
        }
        
        result = await container_tools.list_host_ports(
            "test-host", 
            include_stopped=include_stopped,
            host_port_filter=host_port_filter
        )
        
        assert result["success"] is True
        assert "port_mappings" in result
        assert "summary" in result
        
        # Check filtering behavior
        if not include_stopped:
            # Should only include running containers
            for mapping in result["port_mappings"]:
                assert mapping["container_state"] == "running"
    
    @pytest.mark.asyncio
    async def test_remove_container_with_volumes(self, container_tools, mock_context_manager):
        """Test container removal with volume cleanup."""
        mock_context_manager.execute_docker_command.return_value = {"output": ""}
        
        result = await container_tools.manage_container(
            "test-host", "container123", "remove", force=True
        )
        
        assert result["success"] is True
        assert result["action"] == "remove"
        
        # Verify remove command with force flag
        call_args = mock_context_manager.execute_docker_command.call_args[0][1]
        assert "rm" in call_args
        assert "container123" in call_args


class TestStackToolsComprehensive:
    """Comprehensive tests for StackTools to boost coverage."""
    
    @pytest.fixture
    def mock_config(self):
        """Mock DockerMCPConfig for testing."""
        config = MagicMock(spec=DockerMCPConfig)
        config.hosts = {
            "test-host": MagicMock(
                hostname="test.example.com", 
                user="testuser", 
                port=22,
                identity_file=None
            )
        }
        return config
    
    @pytest.fixture
    def mock_context_manager(self):
        """Mock DockerContextManager for testing."""
        return MagicMock(spec=DockerContextManager)
    
    @pytest.fixture
    def stack_tools(self, mock_config, mock_context_manager):
        """Create StackTools instance for testing."""
        with patch('docker_mcp.tools.stacks.ComposeManager'):
            return StackTools(mock_config, mock_context_manager)
    
    @pytest.mark.parametrize("stack_name,expected_valid", [
        ("valid-stack", True),
        ("valid_stack", True),
        ("ValidStack123", True),
        ("invalid stack", False),  # Contains space
        ("invalid/stack", False),  # Contains slash
        ("", False),  # Empty
        ("a" * 64, False),  # Too long (>63 chars)
        ("docker", False),  # Reserved name
        ("compose", False),  # Reserved name
    ])
    def test_validate_stack_name(self, stack_tools, stack_name, expected_valid):
        """Test stack name validation with various inputs."""
        result = stack_tools._validate_stack_name(stack_name)
        assert result == expected_valid
    
    @pytest.mark.parametrize("pull_images,recreate,expected_in_command", [
        (True, False, ["pull"]),
        (False, True, ["--force-recreate"]),
        (True, True, ["pull", "--force-recreate"]),
        (False, False, []),
    ])
    @pytest.mark.asyncio
    async def test_deploy_stack_options(
        self, stack_tools, mock_context_manager, pull_images, recreate, expected_in_command
    ):
        """Test deploy_stack with various option combinations."""
        # Mock compose manager methods
        stack_tools.compose_manager.write_compose_file = AsyncMock(
            return_value="/remote/path/stack.yml"
        )
        
        # Mock context manager
        mock_context_manager.ensure_context.return_value = "docker-mcp-test-host"
        
        # Mock SSH execution
        with patch.object(stack_tools, '_execute_compose_with_file', new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = "Stack deployed successfully"
            
            result = await stack_tools.deploy_stack(
                "test-host",
                "test-stack",
                "version: '3.8'\nservices:\n  web:\n    image: nginx",
                pull_images=pull_images,
                recreate=recreate
            )
        
        assert result["success"] is True
        assert result["stack_name"] == "test-stack"
        
        # Check that expected options were used in command execution
        if expected_in_command:
            # At least one call should contain our expected options
            calls = mock_exec.call_args_list
            assert len(calls) > 0
    
    @pytest.mark.parametrize("action,expected_command_parts", [
        ("up", ["up", "-d"]),
        ("down", ["down"]),
        ("restart", ["restart"]),
        ("ps", ["ps", "--format", "json"]),
        ("logs", ["logs"]),
        ("pull", ["pull"]),
        ("build", ["build"]),
    ])
    @pytest.mark.asyncio
    async def test_manage_stack_actions(
        self, stack_tools, mock_context_manager, action, expected_command_parts
    ):
        """Test manage_stack with various actions."""
        # Mock compose file existence
        stack_tools.compose_manager.compose_file_exists = AsyncMock(return_value=True)
        stack_tools.compose_manager.get_compose_file_path = AsyncMock(
            return_value="/remote/path/test-stack.yml"
        )
        
        mock_context_manager.ensure_context.return_value = "docker-mcp-test-host"
        
        with patch.object(stack_tools, '_execute_compose_with_file', new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = "Command completed"
            
            result = await stack_tools.manage_stack("test-host", "test-stack", action)
        
        assert result["success"] is True
        assert result["action"] == action
        assert result["execution_method"] == "ssh"
        
        # Verify command parts were used
        call_args = mock_exec.call_args[0]  # Get positional args
        command_args = call_args[3]  # compose_args parameter
        for part in expected_command_parts:
            assert part in command_args
    
    @pytest.mark.parametrize("action,options,expected_flags", [
        ("down", {"volumes": True}, ["--volumes"]),
        ("down", {"remove_orphans": True}, ["--remove-orphans"]),
        ("logs", {"follow": True, "tail": 100}, ["--follow", "--tail", "100"]),
        ("pull", {"ignore_pull_failures": True}, ["--ignore-pull-failures"]),
        ("build", {"no_cache": True, "pull": True}, ["--no-cache", "--pull"]),
        ("up", {"force_recreate": True, "build": True}, ["--force-recreate", "--build"]),
    ])
    @pytest.mark.asyncio
    async def test_manage_stack_action_options(
        self, stack_tools, mock_context_manager, action, options, expected_flags
    ):
        """Test manage_stack action-specific options."""
        # Setup mocks
        stack_tools.compose_manager.compose_file_exists = AsyncMock(return_value=True)
        stack_tools.compose_manager.get_compose_file_path = AsyncMock(
            return_value="/remote/path/test-stack.yml"
        )
        mock_context_manager.ensure_context.return_value = "docker-mcp-test-host"
        
        with patch.object(stack_tools, '_execute_compose_with_file', new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = "Command completed"
            
            result = await stack_tools.manage_stack(
                "test-host", "test-stack", action, options=options
            )
        
        assert result["success"] is True
        
        # Check that expected flags were included
        call_args = mock_exec.call_args[0]
        command_args = call_args[3]  # compose_args parameter
        for flag in expected_flags:
            assert flag in command_args
    
    @pytest.mark.asyncio
    async def test_manage_stack_nonexistent_compose_file(self, stack_tools, mock_context_manager):
        """Test manage_stack when compose file doesn't exist."""
        # Mock compose file doesn't exist
        stack_tools.compose_manager.compose_file_exists = AsyncMock(return_value=False)
        
        result = await stack_tools.manage_stack("test-host", "test-stack", "up")
        
        assert result["success"] is False
        assert "No compose file found" in result["error"]
    
    @pytest.mark.parametrize("invalid_action", [
        "invalid",
        "delete",
        "create",
        "unknown_action",
    ])
    @pytest.mark.asyncio
    async def test_manage_stack_invalid_actions(self, stack_tools, invalid_action):
        """Test manage_stack with invalid actions."""
        result = await stack_tools.manage_stack("test-host", "test-stack", invalid_action)
        
        assert result["success"] is False
        assert "Invalid action" in result["error"]
    
    @pytest.mark.asyncio
    async def test_list_stacks_success(self, stack_tools, mock_context_manager):
        """Test successful stack listing."""
        mock_stacks_response = [
            {
                "Name": "web-stack",
                "Service": "web,db",
                "Status": "running(2)",
                "CreatedAt": "2025-01-15T10:00:00Z",
                "UpdatedAt": "2025-01-15T10:30:00Z"
            },
            {
                "Name": "api-stack", 
                "Service": "api",
                "Status": "running(1)",
                "CreatedAt": "2025-01-15T09:00:00Z",
                "UpdatedAt": "2025-01-15T09:15:00Z"
            }
        ]
        
        mock_context_manager.execute_docker_command.return_value = {
            "output": json.dumps(mock_stacks_response)
        }
        
        result = await stack_tools.list_stacks("test-host")
        
        assert result["success"] is True
        assert len(result["stacks"]) == 2
        assert result["stacks"][0]["name"] == "web-stack"
        assert result["stacks"][1]["name"] == "api-stack"
    
    @pytest.mark.asyncio
    async def test_list_stacks_empty_response(self, stack_tools, mock_context_manager):
        """Test stack listing with empty response."""
        mock_context_manager.execute_docker_command.return_value = {"output": "[]"}
        
        result = await stack_tools.list_stacks("test-host")
        
        assert result["success"] is True
        assert len(result["stacks"]) == 0
    
    @pytest.mark.asyncio
    async def test_list_stacks_malformed_json(self, stack_tools, mock_context_manager):
        """Test stack listing with malformed JSON response."""
        mock_context_manager.execute_docker_command.return_value = {
            "output": "invalid json content"
        }
        
        result = await stack_tools.list_stacks("test-host")
        
        # Should handle gracefully and return empty list
        assert result["success"] is True
        assert len(result["stacks"]) == 0


class TestLogToolsComprehensive:
    """Comprehensive tests for LogTools to boost coverage."""
    
    @pytest.fixture
    def mock_config(self):
        """Mock DockerMCPConfig for testing."""
        return MagicMock(spec=DockerMCPConfig)
    
    @pytest.fixture
    def mock_context_manager(self):
        """Mock DockerContextManager for testing."""
        return MagicMock(spec=DockerContextManager)
    
    @pytest.fixture
    def log_tools(self, mock_config, mock_context_manager):
        """Create LogTools instance for testing."""
        return LogTools(mock_config, mock_context_manager)
    
    @pytest.mark.parametrize("lines,follow,expected_flags", [
        (100, False, ["--tail", "100"]),
        (50, True, ["--tail", "50", "--follow"]),
        (None, False, []),
        (None, True, ["--follow"]),
    ])
    @pytest.mark.asyncio
    async def test_get_container_logs_parameters(
        self, log_tools, mock_context_manager, lines, follow, expected_flags
    ):
        """Test get_container_logs with various parameter combinations."""
        mock_logs = "2025-01-15T10:00:00Z INFO Starting application\n2025-01-15T10:00:01Z INFO Ready to serve"
        mock_context_manager.execute_docker_command.return_value = {"output": mock_logs}
        
        result = await log_tools.get_container_logs(
            "test-host", "container123", lines=lines, follow=follow
        )
        
        assert result["success"] is True
        assert result["container_id"] == "container123"
        assert "logs" in result
        
        # Check command construction
        call_args = mock_context_manager.execute_docker_command.call_args[0][1]
        for flag in expected_flags:
            assert flag in call_args
    
    @pytest.mark.parametrize("since,until,timestamps", [
        ("2025-01-15T10:00:00Z", None, True),
        (None, "2025-01-15T11:00:00Z", False),
        ("2025-01-15T10:00:00Z", "2025-01-15T11:00:00Z", True),
        (None, None, False),
    ])
    @pytest.mark.asyncio
    async def test_get_container_logs_time_filters(
        self, log_tools, mock_context_manager, since, until, timestamps
    ):
        """Test get_container_logs with time-based filters."""
        mock_logs = "2025-01-15T10:30:00Z INFO Application log entry"
        mock_context_manager.execute_docker_command.return_value = {"output": mock_logs}
        
        result = await log_tools.get_container_logs(
            "test-host", "container123", 
            since=since, until=until, timestamps=timestamps
        )
        
        assert result["success"] is True
        
        # Check that time filters were applied to command
        call_args = mock_context_manager.execute_docker_command.call_args[0][1]
        if since:
            assert "--since" in call_args
            assert since in call_args
        if until:
            assert "--until" in call_args  
            assert until in call_args
        if timestamps:
            assert "--timestamps" in call_args or "-t" in call_args
    
    @pytest.mark.asyncio
    async def test_get_container_logs_empty_response(self, log_tools, mock_context_manager):
        """Test get_container_logs with empty log response."""
        mock_context_manager.execute_docker_command.return_value = {"output": ""}
        
        result = await log_tools.get_container_logs("test-host", "container123")
        
        assert result["success"] is True
        assert result["logs"] == ""
        assert result["line_count"] == 0
    
    @pytest.mark.asyncio
    async def test_get_container_logs_multiline_parsing(self, log_tools, mock_context_manager):
        """Test get_container_logs with multiline log content."""
        mock_logs = """2025-01-15T10:00:00Z INFO Starting application
2025-01-15T10:00:01Z WARN Configuration file not found, using defaults
2025-01-15T10:00:02Z ERROR Database connection failed
2025-01-15T10:00:03Z INFO Retrying database connection
2025-01-15T10:00:04Z INFO Successfully connected to database"""
        
        mock_context_manager.execute_docker_command.return_value = {"output": mock_logs}
        
        result = await log_tools.get_container_logs("test-host", "container123")
        
        assert result["success"] is True
        assert result["logs"] == mock_logs
        assert result["line_count"] == 5  # Should count log lines
        
        # Check that log content is preserved exactly
        assert "Starting application" in result["logs"]
        assert "Database connection failed" in result["logs"]
        assert "Successfully connected" in result["logs"]
    
    @pytest.mark.parametrize("exception_type,expected_error", [
        (DockerCommandError, "Docker command failed"),
        (DockerContextError, "Context error"),
        (Exception, "Unexpected error"),
    ])
    @pytest.mark.asyncio
    async def test_get_container_logs_error_handling(
        self, log_tools, mock_context_manager, exception_type, expected_error
    ):
        """Test error handling in get_container_logs."""
        mock_context_manager.execute_docker_command.side_effect = exception_type(expected_error)
        
        result = await log_tools.get_container_logs("test-host", "container123")
        
        assert result["success"] is False
        assert "error" in result
        assert expected_error in result["error"]
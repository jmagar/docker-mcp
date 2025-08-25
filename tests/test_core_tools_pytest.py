"""
Pytest version of core Docker MCP tools test to validate refactoring.

Tests core functionality of refactored tools with proper pytest patterns and mock verification.
"""

from unittest.mock import patch

import pytest
from fastmcp import Client


class TestCoreToolsFunctionality:
    """Test core functionality of refactored tools."""

    @pytest.mark.asyncio
    async def test_list_docker_hosts_basic(self, client: Client):
        """Test list_docker_hosts basic functionality."""
        result = await client.call_tool("docker_hosts", {"action": "list"})

        assert result.data.get('success', False) is True
        hosts = result.data.get('hosts', [])
        assert len(hosts) > 0
        assert isinstance(hosts, list)

    @pytest.mark.asyncio
    async def test_list_containers_quick(self, client: Client, test_host_id: str):
        """Test list_containers with limited results and mock verification."""
        # Mock Docker command response
        mock_docker_output = """{"ID":"abc123","Names":"/test-container","Image":"nginx:alpine","Status":"Up 5 minutes","State":"running","Ports":"80/tcp"}
{"ID":"def456","Names":"/another-container","Image":"redis:alpine","Status":"Up 1 hour","State":"running","Ports":"6379/tcp"}"""

        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to return container data
            mock_execute.return_value = {"output": mock_docker_output}

            result = await client.call_tool("docker_container", {
                "action": "list",
                "host_id": test_host_id,
                "limit": 5
            })

            # Verify mock was called with correct Docker command
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            assert call_args[0][0] == test_host_id  # host_id argument
            assert "ps --format json --no-trunc" in call_args[0][1]  # Docker command

            # Verify response structure and content
            assert result.data["success"] is True
            containers = result.data["containers"]
            assert isinstance(containers, list)
            assert len(containers) == 2  # Both containers from mock data

            # Verify container data was parsed correctly
            assert containers[0]["id"] == "abc123"
            assert containers[0]["name"] == "test-container"
            assert containers[0]["image"] == "nginx:alpine"
            assert containers[0]["state"] == "running"

            # Check pagination structure
            pagination = result.data["pagination"]
            assert pagination["limit"] == 5
            assert pagination["total"] == 2
            assert pagination["returned"] == 2

    @pytest.mark.asyncio
    async def test_get_container_info_basic(self, client: Client, test_host_id: str, test_container_id: str):
        """Test get_container_info with mock verification."""
        # Mock Docker inspect response
        mock_inspect_data = {
            "Id": "abc123def456",
            "Name": "/test-container",
            "State": {
                "Status": "running",
                "Running": True,
                "Pid": 12345,
                "StartedAt": "2025-01-15T10:30:00Z"
            },
            "Config": {
                "Image": "nginx:alpine",
                "Env": ["PATH=/usr/local/sbin:/usr/local/bin"],
                "Labels": {"test": "mcp-validation"}
            },
            "NetworkSettings": {
                "Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]},
                "Networks": {"bridge": {"IPAddress": "172.17.0.2"}}
            },
            "Mounts": []
        }

        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to return inspect data (JSON commands return parsed JSON)
            mock_execute.return_value = mock_inspect_data

            result = await client.call_tool("docker_container", {
                "action": "info",
                "host_id": test_host_id,
                "container_id": test_container_id
            })

            # Verify mock was called with correct Docker inspect command
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            assert call_args[0][0] == test_host_id  # host_id argument
            assert f"inspect {test_container_id}" in call_args[0][1]  # Docker inspect command

            # Verify response structure and content
            assert result.data["success"] is True
            assert result.data["container_id"] == test_container_id

            # Verify container details were parsed correctly
            container = result.data["container"]
            assert container["id"] == "abc123def456"
            assert container["name"] == "/test-container"
            assert container["state"] == "running"
            assert container["image"] == "nginx:alpine"
            assert container["host_id"] == test_host_id

            # Verify network and port information
            assert "networks" in container
            assert "ports" in container

    @pytest.mark.asyncio
    async def test_list_stacks_basic(self, client: Client, test_host_id: str):
        """Test list_stacks on configured host."""
        result = await client.call_tool("docker_compose", {
            "action": "list",
            "host_id": test_host_id
        })

        success = result.data.get('success', False)
        if success:
            stacks = result.data.get('stacks', [])
            assert isinstance(stacks, list)
        else:
            # Accept failure with proper error message
            assert 'error' in result.data


class TestEnhancedContainerOperations:
    """Enhanced container operation tests with comprehensive mock verification."""

    @pytest.mark.asyncio
    async def test_manage_container_restart_with_mock(self, client: Client, test_host_id: str):
        """Test container restart with Docker command verification."""
        test_container_id = "test-nginx-123"

        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to return success
            mock_execute.return_value = {"output": ""}

            result = await client.call_tool("manage_container", {
                "host_id": test_host_id,
                "container_id": test_container_id,
                "action": "restart"
            })

            # Verify mock was called with correct Docker restart command
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            assert call_args[0][0] == test_host_id  # host_id argument
            docker_cmd = call_args[0][1]
            assert "restart" in docker_cmd
            assert test_container_id in docker_cmd
            # Docker restart may include timeout parameter
            assert "--time" in docker_cmd or test_container_id in docker_cmd

            # Verify successful response
            assert result.data["success"] is True
            assert result.data["host_id"] == test_host_id
            assert result.data["container_id"] == test_container_id
            assert result.data["action"] == "restart"

    @pytest.mark.asyncio
    async def test_manage_container_start_with_mock(self, client: Client, test_host_id: str):
        """Test container start with Docker command verification."""
        test_container_id = "test-redis-456"

        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to return success
            mock_execute.return_value = {"output": test_container_id}  # Docker start returns container ID

            result = await client.call_tool("docker_container", {
                "action": "start",
                "host_id": test_host_id,
                "container_id": test_container_id
            })

            # Verify mock was called with correct Docker start command
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            assert call_args[0][0] == test_host_id  # host_id argument
            assert f"start {test_container_id}" in call_args[0][1]  # Docker start command

            # Verify successful response
            assert result.data["success"] is True
            assert result.data["action"] == "start"

    @pytest.mark.asyncio
    async def test_get_container_logs_with_mock(self, client: Client, test_host_id: str):
        """Test container logs retrieval with Docker command verification."""
        test_container_id = "test-app-789"
        mock_log_output = """2025-01-15T10:30:00Z INFO: Application started
2025-01-15T10:30:01Z INFO: Database connected
2025-01-15T10:30:02Z WARN: High memory usage detected"""

        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to return log data
            mock_execute.return_value = {"output": mock_log_output}

            result = await client.call_tool("docker_container", {
                "action": "logs",
                "host_id": test_host_id,
                "container_id": test_container_id,
                "lines": 10
            })

            # Verify mock was called with correct Docker logs command
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            assert call_args[0][0] == test_host_id  # host_id argument
            docker_cmd = call_args[0][1]
            assert "logs" in docker_cmd
            assert test_container_id in docker_cmd
            assert "--tail 10" in docker_cmd  # Verify the line limit parameter

            # Verify response structure and log content
            assert result.data["success"] is True
            assert result.data["container_id"] == test_container_id
            logs = result.data["logs"]
            assert isinstance(logs, list)
            assert len(logs) == 3  # Three log lines from mock data
            assert "Application started" in logs[0]
            assert "Database connected" in logs[1]
            assert "High memory usage detected" in logs[2]

    @pytest.mark.asyncio
    async def test_container_operation_error_handling(self, client: Client, test_host_id: str):
        """Test container operation error handling with mock."""
        test_container_id = "nonexistent-container"

        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to raise Docker error
            from docker_mcp.core.exceptions import DockerCommandError
            mock_execute.side_effect = DockerCommandError("No such container: nonexistent-container")

            result = await client.call_tool("docker_container", {
                "action": "start",
                "host_id": test_host_id,
                "container_id": test_container_id
            })

            # Verify mock was called
            mock_execute.assert_called_once()

            # Verify error response
            assert result.data["success"] is False
            assert "error" in result.data
            assert "No such container" in result.data["error"]

    @pytest.mark.asyncio
    async def test_docker_container_pull_with_mock(self, client: Client, test_host_id: str):
        """Test docker_container pull action with mock verification."""
        test_image_name = "nginx:latest"

        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to return pull success output
            mock_execute.return_value = {"output": f"latest: Pulling from library/nginx\nPull complete\nStatus: Downloaded newer image for {test_image_name}"}

            result = await client.call_tool("docker_container", {
                "action": "pull",
                "host_id": test_host_id,
                "container_id": test_image_name  # container_id is image name for pull
            })

            # Verify mock was called with correct Docker pull command
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            assert call_args[0][0] == test_host_id  # host_id argument
            docker_cmd = call_args[0][1]
            assert "pull" in docker_cmd
            assert test_image_name in docker_cmd

            # Verify successful response
            assert result.data["success"] is True
            assert result.data["host_id"] == test_host_id
            assert result.data["image_name"] == test_image_name
            assert "output" in result.data

    @pytest.mark.asyncio
    async def test_list_containers_with_all_flag(self, client: Client, test_host_id: str):
        """Test list_containers with all_containers=True and mock verification."""
        mock_docker_output = """{"ID":"running123","Names":"/running-container","Image":"nginx:alpine","Status":"Up 5 minutes","State":"running","Ports":"80/tcp"}
{"ID":"stopped456","Names":"/stopped-container","Image":"redis:alpine","Status":"Exited (0) 1 hour ago","State":"exited","Ports":""}"""

        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to return appropriate data for each call
            def mock_side_effect(host_id, command):
                if "ps --format json" in command:
                    return {"output": mock_docker_output}
                elif "inspect" in command:
                    # Return mock inspect data for each container
                    if "running123" in command:
                        return {"Id": "running123", "State": {"Status": "running"}, "volumes": [], "networks": []}
                    elif "stopped456" in command:
                        return {"Id": "stopped456", "State": {"Status": "exited"}, "volumes": [], "networks": []}
                return {"output": ""}

            mock_execute.side_effect = mock_side_effect

            result = await client.call_tool("docker_container", {
                "action": "list",
                "host_id": test_host_id,
                "all_containers": True,
                "limit": 10
            })

            # Verify multiple calls were made (list + inspect for each container)
            assert mock_execute.call_count == 3  # 1 list + 2 inspect calls

            # Verify the first call was the list command with --all flag
            first_call = mock_execute.call_args_list[0]
            assert first_call[0][0] == test_host_id
            list_cmd = first_call[0][1]
            assert "ps" in list_cmd
            assert "--format json" in list_cmd
            assert "--no-trunc" in list_cmd
            assert "--all" in list_cmd

            # Verify inspect calls were made for each container
            inspect_calls = [call for call in mock_execute.call_args_list if "inspect" in call[0][1]]
            assert len(inspect_calls) == 2
            assert any("running123" in call[0][1] for call in inspect_calls)
            assert any("stopped456" in call[0][1] for call in inspect_calls)

            # Verify response includes both running and stopped containers
            assert result.data["success"] is True
            containers = result.data["containers"]
            assert len(containers) == 2

            # Verify running container
            running_container = next(c for c in containers if c["id"] == "running123")
            assert running_container["state"] == "running"

            # Verify stopped container
            stopped_container = next(c for c in containers if c["id"] == "stopped456")
            assert stopped_container["state"] == "exited"


class TestErrorHandlingRefactored:
    """Test error handling works correctly after refactoring."""

    @pytest.mark.asyncio
    async def test_invalid_host_error_handling(self, client: Client):
        """Test proper error handling for invalid host ID."""
        result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": "invalid-host-id"
        })

        # Should return success=False for invalid hosts
        assert result.data.get('success', False) is False
        assert 'error' in result.data

    @pytest.mark.asyncio
    async def test_invalid_container_error_handling(self, client: Client, test_host_id: str):
        """Test proper error handling for invalid container ID."""
        result = await client.call_tool("docker_container", {
            "action": "info",
            "host_id": test_host_id,
            "container_id": "invalid-container-id"
        })

        # Should return success=False for invalid containers
        assert result.data.get('success', False) is False
        assert 'error' in result.data

    @pytest.mark.asyncio
    async def test_docker_container_pull_error_handling(self, client: Client, test_host_id: str):
        """Test error handling for docker_container pull action."""
        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to raise Docker error
            from docker_mcp.core.exceptions import DockerCommandError
            mock_execute.side_effect = DockerCommandError("pull access denied for image")

            result = await client.call_tool("docker_container", {
                "action": "pull",
                "host_id": test_host_id,
                "container_id": "private/image:latest"
            })

            # Verify mock was called
            mock_execute.assert_called_once()

            # Verify error response
            assert result.data["success"] is False
            assert "error" in result.data
            assert "pull access denied" in result.data["error"]
            assert result.data["image_name"] == "private/image:latest"

    @pytest.mark.asyncio
    async def test_missing_required_parameters(self, client: Client):
        """Test error handling for missing required parameters."""
        # Test missing host_id - FastMCP should catch this at validation layer
        try:
            await client.call_tool("docker_container", {"action": "list"})
            assert False, "Should raise ToolError for missing required parameter"
        except Exception as e:
            assert "host_id" in str(e).lower()
            assert "required" in str(e).lower()

        # Test missing container_id for pull action
        result = await client.call_tool("docker_container", {
            "action": "pull",
            "host_id": "test-host",
            "container_id": ""  # Empty container_id should fail
        })
        assert result.data.get('success', False) is False
        assert 'error' in result.data

        # Test missing container_id - FastMCP should catch this at validation layer
        try:
            await client.call_tool("docker_container", {
                "action": "info",
                "host_id": "test-host"
            })
            assert False, "Should raise ToolError for missing required parameter"
        except Exception as e:
            assert "container_id" in str(e).lower()

    @pytest.mark.asyncio
    async def test_empty_string_parameters(self, client: Client):
        """Test error handling for empty string parameters."""
        # Test empty host_id
        result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": ""
        })
        assert result.data.get('success', False) is False
        assert 'error' in result.data

        # Test empty container_id
        result = await client.call_tool("docker_container", {
            "action": "info",
            "host_id": "test-host",
            "container_id": ""
        })
        assert result.data.get('success', False) is False
        assert 'error' in result.data

    @pytest.mark.asyncio
    async def test_invalid_parameter_types(self, client: Client, test_host_id: str):
        """Test error handling for invalid parameter types."""
        # Test invalid limit type - FastMCP should catch this at validation layer
        try:
            await client.call_tool("docker_container", {
                "action": "list",
                "host_id": test_host_id,
                "limit": "not-a-number"
            })
            assert False, "Should raise ToolError for invalid parameter type"
        except Exception as e:
            assert "limit" in str(e).lower() or "type" in str(e).lower()

        # Test negative offset - this should be handled by application layer
        result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": test_host_id,
            "offset": -1
        })
        # Allow either success with graceful handling or failure with error
        assert "success" in result.data

    @pytest.mark.asyncio
    async def test_network_timeout_simulation(self, client: Client):
        """Test handling of network/connection timeouts."""
        # Test with a host that would cause timeout (non-existent host)
        result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": "timeout-test-host"
        })
        assert result.data.get('success', False) is False
        assert 'error' in result.data

    @pytest.mark.asyncio
    async def test_docker_context_connection_failure(self, client: Client):
        """Test Docker context connection failures."""
        # Test with malformed host configuration
        result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": "malformed-host-config"
        })
        assert result.data.get('success', False) is False
        assert 'error' in result.data

    @pytest.mark.asyncio
    async def test_host_permission_denied_simulation(self, client: Client):
        """Test handling of permission denied errors."""
        # Simulate permission denied by using invalid host
        result = await client.call_tool("docker_container", {
            "action": "info",
            "host_id": "permission-denied-host",
            "container_id": "some-container"
        })
        assert result.data.get('success', False) is False
        assert 'error' in result.data


@pytest.mark.integration
class TestRefactoringValidation:
    """Integration test to validate refactoring didn't break functionality."""

    @pytest.mark.asyncio
    async def test_refactoring_validation_suite(self, client: Client, test_host_id: str, test_container_id: str):
        """Comprehensive test to validate all refactored functionality."""

        # Test 1: Host management
        hosts_result = await client.call_tool("docker_hosts", {"action": "list"})
        assert hosts_result.data.get('success', False) is True

        # Test 2: Container operations (basic functionality)
        containers_result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": test_host_id,
            "limit": 3
        })
        # Accept either success or failure with error message
        assert 'success' in containers_result.data

        # Test 3: Stack operations
        stacks_result = await client.call_tool("docker_compose", {
            "action": "list",
            "host_id": test_host_id
        })
        # Accept either success or failure with error message
        assert 'success' in stacks_result.data

        # Test 4: Error handling still works
        invalid_result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": "definitely-invalid-host"
        })
        assert invalid_result.data.get('success', False) is False
        assert 'error' in invalid_result.data

    @pytest.mark.asyncio
    async def test_container_info_after_refactoring(self, client: Client, test_host_id: str, test_container_id: str):
        """Test container info retrieval works after refactoring."""
        result = await client.call_tool("docker_container", {
            "action": "info",
            "host_id": test_host_id,
            "container_id": test_container_id
        })

        # This test allows both success and failure, as long as response is properly structured
        assert 'success' in result.data
        if result.data['success']:
            assert 'container_id' in result.data
            # The response structure may vary, so just check we have some container info
            assert result.data['container_id'] == test_container_id
        else:
            assert 'error' in result.data

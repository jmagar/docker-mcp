"""
Pytest version of stack operations tests with comprehensive SSH-based testing.
"""

import asyncio
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client
from tests.cleanup_utils import with_cleanup


class TestStackDeployment:
    """Test Docker Compose stack deployment operations."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_deploy_nginx_stack(self, client: Client, test_host_id: str):
        """Test deploying a simple nginx stack."""
        compose_content = """version: '3.8'
services:
  test-web:
    image: nginx:alpine
    ports:
      - "8092:80"
    labels:
      - "test=mcp-validation"
"""
        stack_name = "test-mcp-validation"
        
        from tests.cleanup_utils import get_resource_tracker
        tracker = get_resource_tracker()
        
        try:
            result = await client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "compose_content": compose_content,
                "pull_images": False,
                "recreate": False
            })
            
            tracker.add_stack(test_host_id, stack_name)
            
            assert result.data["success"] is True
            assert result.data["stack_name"] == stack_name
            assert "compose_file" in result.data
            
        finally:
            # Clean up
            try:
                cleanup = await client.call_tool("manage_stack", {
                    "host_id": test_host_id,
                    "stack_name": stack_name,
                    "action": "down"
                })
                if cleanup.data["success"]:
                    tracker.remove_stack(test_host_id, stack_name)
                else:
                    tracker.record_failure("stack", stack_name, test_host_id, cleanup.data.get("error", "Unknown"))
            except Exception as e:
                tracker.record_failure("stack", stack_name, test_host_id, str(e))

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_deploy_with_environment_variables(self, client: Client, test_host_id: str):
        """Test deploying stack with environment variables."""
        compose_content = """version: '3.8'
services:
  app:
    image: nginx:alpine
    ports:
      - "8093:80"
    environment:
      - TEST_ENV=${TEST_ENV}
      - DEBUG=${DEBUG}
    labels:
      - "test=mcp-env-test"
"""
        stack_name = "test-mcp-env"
        environment = {
            "TEST_ENV": "production",
            "DEBUG": "false"
        }
        
        from tests.cleanup_utils import get_resource_tracker
        tracker = get_resource_tracker()
        
        try:
            result = await client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "compose_content": compose_content,
                "environment": environment,
                "pull_images": False,
                "recreate": False
            })
            
            tracker.add_stack(test_host_id, stack_name)
            assert result.data["success"] is True
            
        finally:
            # Clean up
            try:
                await client.call_tool("manage_stack", {
                    "host_id": test_host_id,
                    "stack_name": stack_name,
                    "action": "down"
                })
                tracker.remove_stack(test_host_id, stack_name)
            except Exception as e:
                tracker.record_failure("stack", stack_name, test_host_id, str(e))

    @pytest.mark.slow
    @pytest.mark.asyncio 
    async def test_deploy_stack_recreate_option(self, client: Client, test_host_id: str):
        """Test deploying stack with recreate option."""
        compose_content = """version: '3.8'
services:
  test-recreate:
    image: nginx:alpine
    ports:
      - "8094:80"
"""
        stack_name = "test-recreate"
        
        # Deploy first time
        result1 = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "compose_content": compose_content,
            "pull_images": False,
            "recreate": False
        })
        assert result1.data["success"] is True
        
        # Deploy again with recreate=True
        result2 = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "compose_content": compose_content,
            "pull_images": False,
            "recreate": True
        })
        assert result2.data["success"] is True
        
        # Clean up
        await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "action": "down",
            "options": {"volumes": True}
        })


class TestStackManagement:
    """Test Docker Compose stack management operations (SSH-based)."""

    @pytest.fixture
    async def deployed_test_stack(self, client: Client, test_host_id: str, request):
        """Deploy a test stack for management operations with guaranteed cleanup."""
        compose_content = """version: '3.8'
services:
  test-service:
    image: nginx:alpine
    ports:
      - "8095:80"
    labels:
      - "test=stack-management"
"""
        stack_name = "test-stack-mgmt"
        
        from tests.cleanup_utils import get_resource_tracker
        tracker = get_resource_tracker()
        
        # Define cleanup function
        async def cleanup():
            try:
                await client.call_tool("manage_stack", {
                    "host_id": test_host_id,
                    "stack_name": stack_name,
                    "action": "down"
                })
                tracker.remove_stack(test_host_id, stack_name)
            except Exception as e:
                tracker.record_failure("stack", stack_name, test_host_id, str(e))
        
        # Register cleanup as finalizer
        request.addfinalizer(lambda: asyncio.run(cleanup()))
        
        # Deploy the stack
        result = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "compose_content": compose_content,
            "pull_images": False,
            "recreate": False
        })
        assert result.data["success"] is True
        tracker.add_stack(test_host_id, stack_name)
        
        try:
            yield stack_name
        finally:
            # Cleanup will be handled by finalizer
            pass

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_manage_stack_ps(self, client: Client, test_host_id: str, deployed_test_stack: str):
        """Test stack status (ps) operation via SSH."""
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": deployed_test_stack,
            "action": "ps"
        })
        
        assert result.data["success"] is True
        assert result.data["action"] == "ps"
        assert result.data["execution_method"] == "ssh"
        assert "data" in result.data
        
        # Should have service information
        if result.data["data"]:
            assert "services" in result.data["data"]

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_manage_stack_logs(self, client: Client, test_host_id: str, deployed_test_stack: str):
        """Test getting stack logs via SSH."""
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": deployed_test_stack,
            "action": "logs",
            "options": {"tail": 10}
        })
        
        assert result.data["success"] is True
        assert result.data["action"] == "logs"
        assert result.data["execution_method"] == "ssh"

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_manage_stack_restart(self, client: Client, test_host_id: str, deployed_test_stack: str):
        """Test restarting stack services via SSH."""
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": deployed_test_stack,
            "action": "restart"
        })
        
        assert result.data["success"] is True
        assert result.data["action"] == "restart"
        assert result.data["execution_method"] == "ssh"

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_manage_stack_down_with_volumes(self, client: Client, test_host_id: str):
        """Test stack down operation with volume removal."""
        # Deploy a temporary stack
        compose_content = """version: '3.8'
services:
  temp-service:
    image: nginx:alpine
    volumes:
      - test_volume:/data
volumes:
  test_volume:
"""
        stack_name = "test-temp-volumes"
        
        deploy_result = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "compose_content": compose_content,
            "pull_images": False
        })
        assert deploy_result.data["success"] is True
        
        # Now test down with volumes
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "action": "down",
            "options": {
                "volumes": True,
                "remove_orphans": True
            }
        })
        
        assert result.data["success"] is True
        assert result.data["action"] == "down"
        assert result.data["execution_method"] == "ssh"


class TestStackListing:
    """Test Docker Compose stack listing operations."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_list_stacks_basic(self, client: Client, test_host_id: str):
        """Test basic stack listing."""
        result = await client.call_tool("list_stacks", {
            "host_id": test_host_id
        })
        
        assert result.data["success"] is True
        assert "stacks" in result.data
        assert isinstance(result.data["stacks"], list)

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_list_stacks_with_deployed_stack(self, client: Client, test_host_id: str):
        """Test stack listing includes deployed stacks."""
        # Deploy a test stack
        compose_content = """version: '3.8'
services:
  list-test:
    image: nginx:alpine
    labels:
      - "test=list-test"
"""
        stack_name = "test-list-validation"
        
        deploy_result = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "compose_content": compose_content,
            "pull_images": False
        })
        assert deploy_result.data["success"] is True
        
        # List stacks and verify our stack is there
        list_result = await client.call_tool("list_stacks", {
            "host_id": test_host_id
        })
        
        assert list_result.data["success"] is True
        stack_names = [stack["name"] for stack in list_result.data["stacks"]]
        assert stack_name in stack_names
        
        # Clean up
        await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "action": "down",
            "options": {"volumes": True}
        })


class TestStackErrorHandling:
    """Test error handling in stack operations."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_manage_nonexistent_stack(self, client: Client, test_host_id: str):
        """Test managing a stack that doesn't exist."""
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": "nonexistent-stack",
            "action": "ps"
        })
        
        assert result.data["success"] is False
        assert "error" in result.data

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_deploy_stack_invalid_compose(self, client: Client, test_host_id: str):
        """Test deploying with invalid compose content."""
        invalid_compose = """invalid: yaml: content: ["""
        
        result = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": "test-invalid",
            "compose_content": invalid_compose
        })
        
        assert result.data["success"] is False
        assert "error" in result.data

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_manage_stack_invalid_action(self, client: Client, test_host_id: str):
        """Test invalid management action."""
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": "any-stack",
            "action": "invalid_action"
        })
        
        assert result.data["success"] is False
        assert "error" in result.data
        assert "invalid action" in result.data["error"].lower()

    @pytest.mark.asyncio
    async def test_deploy_stack_invalid_stack_names(self, client: Client, test_host_id: str):
        """Test deployment with various invalid stack names."""
        invalid_names = [
            "",  # Empty name
            "invalid/stack",  # Contains slash
            "invalid*stack",  # Contains asterisk
            "invalid stack",  # Contains space
            "DOCKER",  # Reserved name (case insensitive)
            "a" * 100,  # Too long (>63 chars)
            # Note: Current validation allows names starting with numbers (123-invalid would be valid)
            # Following the regex pattern: ^[a-zA-Z0-9][a-zA-Z0-9_-]*$
        ]
        
        compose_content = """version: '3.8'
services:
  test:
    image: nginx:alpine
"""
        
        for invalid_name in invalid_names:
            result = await client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": invalid_name,
                "compose_content": compose_content
            })
            assert result.data["success"] is False, f"Invalid stack name '{invalid_name}' should be rejected"
            assert "error" in result.data

    @pytest.mark.asyncio
    async def test_deploy_stack_empty_compose_content(self, client: Client, test_host_id: str):
        """Test deployment with empty or minimal compose content."""
        # Test completely empty content
        result = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": "test-empty",
            "compose_content": ""
        })
        assert result.data["success"] is False
        assert "error" in result.data
        
        # Test minimal invalid content
        result = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": "test-minimal",
            "compose_content": "version: '3.8'"  # No services
        })
        assert result.data["success"] is False
        assert "error" in result.data

    @pytest.mark.asyncio
    async def test_manage_stack_missing_compose_file(self, client: Client, test_host_id: str):
        """Test managing stack operations on stacks without compose files."""
        # Try to manage a stack that was never deployed via deploy_stack
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": "never-deployed-stack",
            "action": "up"
        })
        assert result.data["success"] is False
        assert "error" in result.data
        assert "compose file" in result.data["error"].lower()

    @pytest.mark.asyncio
    async def test_deploy_stack_invalid_environment_variables(self, client: Client, test_host_id: str):
        """Test deployment with invalid environment variable configurations."""
        compose_content = """version: '3.8'
services:
  test:
    image: nginx:alpine
    environment:
      - INVALID_VAR=${MISSING_VAR}
"""
        
        # Test with environment variables containing special characters
        invalid_environments = [
            {"INVALID=VAR": "value"},  # Key contains equals
            {"": "empty-key"},  # Empty key
            {"KEY": ""},  # Empty value is OK, but test edge case
        ]
        
        for env in invalid_environments:
            result = await client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": "test-env-invalid",
                "compose_content": compose_content,
                "environment": env
            })
            # Some might succeed with warnings, but should handle gracefully
            assert "success" in result.data

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_deploy_stack_valid_stack_names_edge_cases(self, client: Client, test_host_id: str):
        """Test deployment with edge case valid stack names."""
        # These should be valid according to current validation: ^[a-zA-Z0-9][a-zA-Z0-9_-]*$
        valid_names = [
            "123-starts-with-number",  # Starts with number (currently allowed)
            "a",  # Single character
            "a-b_c",  # Mixed separators
            "Test-Stack-1",  # Mixed case
        ]
        
        compose_content = """version: '3.8'
services:
  test:
    image: nginx:alpine
"""
        
        for valid_name in valid_names:
            result = await client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": valid_name,
                "compose_content": compose_content,
                "pull_images": False
            })
            assert result.data["success"] is True, f"Valid stack name '{valid_name}' should be accepted"
            
            # Clean up the deployed stack
            await client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": valid_name,
                "action": "down",
                "options": {"volumes": True}
            })

    @pytest.mark.asyncio
    async def test_stack_operations_on_invalid_host(self, client: Client):
        """Test all stack operations on completely invalid host."""
        invalid_host = "completely-nonexistent-host"
        
        # Test list stacks
        result = await client.call_tool("list_stacks", {
            "host_id": invalid_host
        })
        assert result.data["success"] is False
        assert "error" in result.data
        
        # Test deploy stack
        result = await client.call_tool("deploy_stack", {
            "host_id": invalid_host,
            "stack_name": "test-stack",
            "compose_content": "version: '3.8'\nservices:\n  test:\n    image: nginx"
        })
        assert result.data["success"] is False
        assert "error" in result.data
        
        # Test manage stack
        result = await client.call_tool("manage_stack", {
            "host_id": invalid_host,
            "stack_name": "any-stack",
            "action": "ps"
        })
        assert result.data["success"] is False
        assert "error" in result.data

    @pytest.mark.asyncio
    async def test_manage_stack_invalid_options(self, client: Client, test_host_id: str):
        """Test manage stack with invalid option combinations."""
        # Test with invalid timeout value
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": "test-stack",
            "action": "restart",
            "options": {"timeout": -5}  # Negative timeout
        })
        assert result.data["success"] is False
        assert "error" in result.data
        
        # Test logs with invalid tail value
        result = await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": "test-stack",
            "action": "logs",
            "options": {"tail": "not-a-number"}
        })
        assert result.data["success"] is False
        assert "error" in result.data

    @pytest.mark.asyncio
    async def test_deploy_stack_resource_conflicts(self, client: Client, test_host_id: str):
        """Test deployment with resource conflicts."""
        # Try to deploy stack with conflicting port
        conflicting_compose = """version: '3.8'
services:
  conflict-test:
    image: nginx:alpine
    ports:
      - "22:80"  # Port 22 is likely used by SSH
"""
        
        result = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": "test-port-conflict",
            "compose_content": conflicting_compose,
            "pull_images": False
        })
        # This might succeed or fail depending on the system, but should handle gracefully
        assert "success" in result.data
        
        # Clean up if it succeeded
        if result.data.get("success"):
            await client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": "test-port-conflict",
                "action": "down",
                "options": {"volumes": True}
            })


class TestEnhancedStackOperations:
    """Enhanced stack operation tests with comprehensive SSH mock verification."""

    @pytest.mark.asyncio
    async def test_deploy_stack_with_ssh_mock(self, client: Client, test_host_id: str):
        """Test stack deployment with SSH command verification."""
        stack_name = "test-ssh-deploy"
        compose_content = """version: '3.8'
services:
  test-web:
    image: nginx:alpine
    ports:
      - "8099:80"
    labels:
      - "test=ssh-mock-deploy"
"""
        
        with patch('docker_mcp.tools.stacks.subprocess.run') as mock_subprocess:
            # Configure mock to return successful deployment
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Creating network test-ssh-deploy_default\nCreating test-ssh-deploy_test-web_1 ... done"
            mock_result.stderr = ""
            mock_subprocess.return_value = mock_result
            
            result = await client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "compose_content": compose_content,
                "pull_images": False,
                "recreate": False
            })
            
            # Verify SSH command was called
            mock_subprocess.assert_called()
            call_args = mock_subprocess.call_args
            ssh_command = call_args[0][0]  # First positional argument
            
            # Verify SSH command structure
            assert "ssh" in ssh_command[0]
            assert "-o" in ssh_command  # SSH options should be present
            assert "StrictHostKeyChecking=no" in ssh_command
            
            # Find the remote command part (after the hostname)
            remote_cmd_index = None
            for i, arg in enumerate(ssh_command):
                if "@" in arg:  # This should be user@hostname
                    remote_cmd_index = i + 1
                    break
            
            assert remote_cmd_index is not None, "Should find user@hostname in SSH command"
            remote_command = ssh_command[remote_cmd_index]
            
            # Verify remote Docker compose command
            assert "docker compose" in remote_command
            assert "--project-name" in remote_command
            assert stack_name in remote_command
            assert "up -d" in remote_command
            
            # Verify successful response
            assert result.data["success"] is True
            assert result.data["stack_name"] == stack_name
            assert "compose_file" in result.data

    @pytest.mark.asyncio
    async def test_manage_stack_ps_with_ssh_mock(self, client: Client, test_host_id: str):
        """Test stack ps operation with SSH command verification."""
        stack_name = "test-stack-ps"
        
        with patch('docker_mcp.tools.stacks.subprocess.run') as mock_subprocess:
            # Configure mock to return ps output
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = '{"Name":"test-stack-ps_web_1","State":"running","Status":"Up 5 minutes"}\n'
            mock_result.stderr = ""
            mock_subprocess.return_value = mock_result
            
            result = await client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "action": "ps"
            })
            
            # Verify SSH command was called
            mock_subprocess.assert_called()
            call_args = mock_subprocess.call_args
            ssh_command = call_args[0][0]  # First positional argument
            
            # Verify SSH command structure
            assert "ssh" in ssh_command[0]
            assert "-o" in ssh_command
            assert "StrictHostKeyChecking=no" in ssh_command
            
            # Find the remote command part
            remote_cmd_index = None
            for i, arg in enumerate(ssh_command):
                if "@" in arg:
                    remote_cmd_index = i + 1
                    break
            
            assert remote_cmd_index is not None
            remote_command = ssh_command[remote_cmd_index]
            
            # Verify remote Docker compose ps command
            assert "docker compose" in remote_command
            assert "--project-name" in remote_command
            assert stack_name in remote_command
            assert "ps --format json" in remote_command
            
            # Verify successful response
            assert result.data["success"] is True
            assert result.data["action"] == "ps"
            assert result.data["execution_method"] == "ssh"

    @pytest.mark.asyncio
    async def test_manage_stack_down_with_ssh_mock(self, client: Client, test_host_id: str):
        """Test stack down operation with SSH command verification."""
        stack_name = "test-stack-down"
        
        with patch('docker_mcp.tools.stacks.subprocess.run') as mock_subprocess:
            # Configure mock to return down output
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Stopping test-stack-down_web_1 ... done\nRemoving test-stack-down_web_1 ... done"
            mock_result.stderr = ""
            mock_subprocess.return_value = mock_result
            
            result = await client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "action": "down",
                "options": {"volumes": True, "remove_orphans": True}
            })
            
            # Verify SSH command was called
            mock_subprocess.assert_called()
            call_args = mock_subprocess.call_args
            ssh_command = call_args[0][0]
            
            # Verify SSH command structure
            assert "ssh" in ssh_command[0]
            
            # Find the remote command part
            remote_cmd_index = None
            for i, arg in enumerate(ssh_command):
                if "@" in arg:
                    remote_cmd_index = i + 1
                    break
            
            assert remote_cmd_index is not None
            remote_command = ssh_command[remote_cmd_index]
            
            # Verify remote Docker compose down command with options
            assert "docker compose" in remote_command
            assert "--project-name" in remote_command
            assert stack_name in remote_command
            assert "down" in remote_command
            assert "--volumes" in remote_command
            assert "--remove-orphans" in remote_command
            
            # Verify successful response
            assert result.data["success"] is True
            assert result.data["action"] == "down"
            assert result.data["execution_method"] == "ssh"

    @pytest.mark.asyncio
    async def test_deploy_stack_with_environment_ssh_mock(self, client: Client, test_host_id: str):
        """Test stack deployment with environment variables and SSH verification."""
        stack_name = "test-env-deploy"
        compose_content = """version: '3.8'
services:
  app:
    image: nginx:alpine
    environment:
      - TEST_ENV=${TEST_ENV}
      - DEBUG=${DEBUG}
"""
        environment = {
            "TEST_ENV": "production",
            "DEBUG": "false"
        }
        
        with patch('docker_mcp.tools.stacks.subprocess.run') as mock_subprocess:
            # Configure mock to return successful deployment
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Creating test-env-deploy_app_1 ... done"
            mock_result.stderr = ""
            mock_subprocess.return_value = mock_result
            
            result = await client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "compose_content": compose_content,
                "environment": environment,
                "pull_images": False,
                "recreate": False
            })
            
            # Verify SSH command was called
            mock_subprocess.assert_called()
            call_args = mock_subprocess.call_args
            ssh_command = call_args[0][0]
            
            # Find the remote command part
            remote_cmd_index = None
            for i, arg in enumerate(ssh_command):
                if "@" in arg:
                    remote_cmd_index = i + 1
                    break
            
            assert remote_cmd_index is not None
            remote_command = ssh_command[remote_cmd_index]
            
            # Verify environment variables are passed to remote command
            assert "TEST_ENV=production" in remote_command
            assert "DEBUG=false" in remote_command
            assert "docker compose" in remote_command
            assert stack_name in remote_command
            
            # Verify successful response
            assert result.data["success"] is True
            assert result.data["stack_name"] == stack_name

    @pytest.mark.asyncio
    async def test_list_stacks_with_docker_context_mock(self, client: Client, test_host_id: str):
        """Test stack listing with Docker context command verification."""
        mock_stack_output = """[{"Name":"test-stack-1","Status":"running(2)","Service":"web,db"},{"Name":"test-stack-2","Status":"exited(0)","Service":"app"}]"""
        
        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_execute:
            # Configure mock to return stack list data
            mock_execute.return_value = {"output": mock_stack_output}
            
            result = await client.call_tool("list_stacks", {
                "host_id": test_host_id
            })
            
            # Verify Docker context command was called (list_stacks uses Docker context, not SSH)
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            assert call_args[0][0] == test_host_id
            assert "compose ls --format json" in call_args[0][1]
            
            # Verify successful response
            assert result.data["success"] is True
            assert "stacks" in result.data
            assert len(result.data["stacks"]) == 2

    @pytest.mark.asyncio
    async def test_stack_ssh_error_handling(self, client: Client, test_host_id: str):
        """Test stack operation SSH error handling."""
        stack_name = "test-error-stack"
        
        with patch('docker_mcp.tools.stacks.subprocess.run') as mock_subprocess:
            # Configure mock to return SSH/Docker error
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_result.stderr = "Error: Could not find compose file"
            mock_subprocess.return_value = mock_result
            
            result = await client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "action": "ps"
            })
            
            # Verify SSH command was attempted
            mock_subprocess.assert_called()
            
            # Verify error response
            assert result.data["success"] is False
            assert "error" in result.data
            assert "Could not find compose file" in result.data["error"]


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_stack_lifecycle(client: Client, test_host_id: str, worker_id: str, dynamic_port: int):
    """Integration test of complete stack lifecycle: list -> deploy -> manage -> remove."""
    
    # Step 1: List initial stacks
    initial_list = await client.call_tool("list_stacks", {"host_id": test_host_id})
    assert initial_list.data["success"] is True
    initial_count = len(initial_list.data["stacks"])
    
    # Step 2: Deploy new stack with dynamic port and unique name
    lifecycle_port = dynamic_port + 100  # Offset to avoid conflicts
    stack_suffix = worker_id if worker_id != 'master' else 'main'
    stack_name = f"test-lifecycle-complete-{stack_suffix}"
    
    compose_content = f"""version: '3.8'
services:
  lifecycle-test:
    image: nginx:alpine
    ports:
      - "{lifecycle_port}:80"
    labels:
      - "test=lifecycle"
      - "worker={worker_id}"
"""
    
    deploy_result = await client.call_tool("deploy_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "compose_content": compose_content,
        "pull_images": False
    })
    assert deploy_result.data["success"] is True
    
    # Step 3: Verify stack appears in listing
    post_deploy_list = await client.call_tool("list_stacks", {"host_id": test_host_id})
    assert post_deploy_list.data["success"] is True
    stack_names = [stack["name"] for stack in post_deploy_list.data["stacks"]]
    assert stack_name in stack_names, f"Stack '{stack_name}' should appear in stack list"
    
    # Step 4: Check stack status
    ps_result = await client.call_tool("manage_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "action": "ps"
    })
    assert ps_result.data["success"] is True
    assert ps_result.data["execution_method"] == "ssh"
    
    # Step 5: Remove stack
    down_result = await client.call_tool("manage_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "action": "down",
        "options": {"volumes": True}
    })
    assert down_result.data["success"] is True
    assert down_result.data["execution_method"] == "ssh"
    
    # Step 6: Verify stack is removed from listing
    final_list = await client.call_tool("list_stacks", {"host_id": test_host_id})
    assert final_list.data["success"] is True
    final_stack_names = [stack["name"] for stack in final_list.data["stacks"]]
    assert stack_name not in final_stack_names
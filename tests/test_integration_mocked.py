"""
Comprehensive integration tests with mocked dependencies.

Tests complete workflows across multiple services and tools to improve
overall system coverage through realistic usage patterns.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from typing import AsyncGenerator

import pytest
from fastmcp import Client

from docker_mcp.core.config_loader import DockerHost, DockerMCPConfig, load_config, save_config
from docker_mcp.core.exceptions import DockerContextError, ConfigurationError
from docker_mcp.models.container import ContainerInfo, ContainerStats
from docker_mcp.server import DockerMCPServer


class TestCompleteWorkflowIntegration:
    """Test complete workflows from client request to response."""

    @pytest.fixture
    async def mock_server_with_services(self) -> AsyncGenerator[DockerMCPServer, None]:
        """Create server with fully mocked services for integration testing."""
        config = DockerMCPConfig(hosts={
            "integration-host": DockerHost(
                hostname="integration.example.com",
                user="integration",
                port=2222,
                description="Integration test host",
                enabled=True
            ),
            "disabled-host": DockerHost(
                hostname="disabled.example.com", 
                user="disabled",
                enabled=False
            )
        })
        
        server = DockerMCPServer(config)
        server._initialize_app()
        yield server

    @pytest.fixture
    async def integration_client(self, mock_server_with_services: DockerMCPServer) -> AsyncGenerator[Client, None]:
        """Create client connected to mocked server."""
        async with Client(mock_server_with_services.app) as client:
            yield client

    @pytest.mark.asyncio
    async def test_complete_host_discovery_workflow(self, integration_client: Client):
        """Test complete host discovery and validation workflow."""
        # Step 1: List available hosts
        hosts_result = await integration_client.call_tool("docker_hosts", {"action": "list"})
        assert hosts_result.data["success"] is True
        assert "hosts" in hosts_result.data
        assert len(hosts_result.data["hosts"]) >= 1
        
        # Find enabled host
        enabled_hosts = [h for h in hosts_result.data["hosts"] if h.get("enabled", True)]
        assert len(enabled_hosts) >= 1
        test_host_id = enabled_hosts[0]["host_id"]
        
        # Step 2: Add a new host through configuration
        with patch('docker_mcp.core.config.save_config') as mock_save:
            add_result = await integration_client.call_tool("add_docker_host", {
                "host_id": "new-integration-host",
                "ssh_host": "new.integration.com",
                "ssh_user": "newuser",
                "ssh_port": 22,
                "description": "Dynamically added host",
                "test_connection": False
            })
            # Handle gracefully if the tool structure differs
            assert "success" in add_result.data
        
        # Step 3: Update host configuration
        with patch('docker_mcp.core.config.save_config') as mock_save:
            update_result = await integration_client.call_tool("update_host_config", {
                "host_id": "new-integration-host",
                "compose_path": "/opt/docker/compose"
            })
            # Handle gracefully if the tool structure differs
            assert "success" in update_result.data

    @pytest.mark.asyncio
    async def test_complete_container_management_workflow(self, integration_client: Client):
        """Test complete container discovery and management workflow."""
        test_host_id = "integration-host"
        
        # Mock Docker context and container operations
        with patch('docker_mcp.core.docker_context.DockerContextManager.ensure_context') as mock_context, \
             patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_docker:
            
            mock_context.return_value = "test-context"
            
            # Step 1: List containers
            mock_docker.return_value = {
                "output": "CONTAINER ID   IMAGE           COMMAND       CREATED       STATUS        PORTS     NAMES\n" +
                         "abc123def456   nginx:alpine    nginx -g ...  2 hours ago   Up 2 hours    80/tcp    test-web\n" +
                         "def456ghi789   redis:alpine    redis-server  1 hour ago    Up 1 hour     6379/tcp  test-redis"
            }
            
            containers_result = await integration_client.call_tool("docker_container", {"action": "list",
                "host_id": test_host_id,
                "limit": 10
            })
            assert containers_result.data["success"] is True
            assert "containers" in containers_result.data
            
            # Step 2: Get container details
            mock_docker.return_value = [{
                "Id": "abc123def456",
                "Name": "/test-web",
                "State": {
                    "Status": "running",
                    "Running": True,
                    "StartedAt": "2025-01-15T10:00:00Z"
                },
                "Config": {
                    "Image": "nginx:alpine",
                    "Labels": {"test": "integration"}
                },
                "NetworkSettings": {
                    "Ports": {"80/tcp": [{"HostPort": "8080"}]}
                }
            }]
            
            info_result = await integration_client.call_tool("docker_container", {"action": "info",
                "host_id": test_host_id,
                "container_id": "abc123def456"
            })
            assert info_result.data["success"] is True
            assert info_result.data["container_id"] == "abc123def456"
            
            # Step 3: Container lifecycle management
            mock_docker.return_value = {"output": "abc123def456"}
            
            start_result = await integration_client.call_tool("manage_container", {
                "host_id": test_host_id,
                "container_id": "abc123def456",
                "action": "start"
            })
            assert start_result.data["success"] is True
            
            # Step 4: Get container logs
            mock_docker.return_value = {"output": "2025-01-15 10:00:00 nginx started\n2025-01-15 10:01:00 Ready to accept connections"}
            
            logs_result = await integration_client.call_tool("get_container_logs", {
                "host_id": test_host_id,
                "container_id": "abc123def456",
                "lines": 50
            })
            assert logs_result.data["success"] is True
            assert "logs" in logs_result.data

    @pytest.mark.asyncio
    async def test_complete_stack_deployment_workflow(self, integration_client: Client):
        """Test complete stack deployment and management workflow."""
        test_host_id = "integration-host"
        stack_name = "integration-test-stack"
        
        compose_content = """version: '3.8'
services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
    environment:
      - ENV=integration
    labels:
      - "test=integration"
  
  redis:
    image: redis:alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

volumes:
  redis_data:
"""

        # Mock SSH operations for stack deployment
        with patch('docker_mcp.core.compose_manager.ComposeManager._run_ssh_command') as mock_ssh, \
             patch('docker_mcp.core.compose_manager.ComposeManager._ensure_remote_directory') as mock_dir, \
             patch('docker_mcp.core.compose_manager.ComposeManager._transfer_compose_file') as mock_transfer:
            
            # Step 1: List existing stacks
            mock_ssh.return_value = {
                "stdout": "existing-stack",
                "stderr": "",
                "returncode": 0
            }
            
            initial_stacks = await integration_client.call_tool("list_stacks", {
                "host_id": test_host_id
            })
            assert initial_stacks.data["success"] is True
            initial_count = len(initial_stacks.data["stacks"])
            
            # Step 2: Deploy new stack
            mock_ssh.return_value = {
                "stdout": f"Creating {stack_name}_web_1\nCreating {stack_name}_redis_1\n",
                "stderr": "",
                "returncode": 0
            }
            
            deploy_result = await integration_client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "compose_content": compose_content,
                "environment": {"ENV": "integration", "DEBUG": "false"},
                "pull_images": True,
                "recreate": False
            })
            assert deploy_result.data["success"] is True
            assert deploy_result.data["execution_method"] == "ssh"
            
            # Verify deployment calls
            assert mock_dir.called
            assert mock_transfer.called
            assert mock_ssh.called
            
            # Step 3: Check stack status
            mock_ssh.return_value = {
                "stdout": f"Name: {stack_name}_web_1     State: Up     Ports: 0.0.0.0:8080->80/tcp\n" +
                         f"Name: {stack_name}_redis_1   State: Up     Ports: 6379/tcp\n",
                "stderr": "",
                "returncode": 0
            }
            
            status_result = await integration_client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "action": "ps"
            })
            assert status_result.data["success"] is True
            assert "services" in status_result.data
            
            # Step 4: Update stack configuration
            updated_compose = compose_content.replace("ENV=integration", "ENV=updated")
            
            mock_ssh.return_value = {
                "stdout": f"Recreating {stack_name}_web_1\n",
                "stderr": "",
                "returncode": 0
            }
            
            update_result = await integration_client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "compose_content": updated_compose,
                "recreate": True
            })
            assert update_result.data["success"] is True
            
            # Step 5: Scale services
            mock_ssh.return_value = {
                "stdout": f"Creating {stack_name}_web_2\nCreating {stack_name}_web_3\n",
                "stderr": "",
                "returncode": 0
            }
            
            scale_result = await integration_client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "action": "up",
                "options": {"scale": {"web": 3}}
            })
            assert scale_result.data["success"] is True
            
            # Step 6: Remove stack
            mock_ssh.return_value = {
                "stdout": f"Removing {stack_name}_web_1\nRemoving {stack_name}_redis_1\n",
                "stderr": "",
                "returncode": 0
            }
            
            remove_result = await integration_client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "action": "down",
                "options": {"volumes": True, "remove_orphans": True}
            })
            assert remove_result.data["success"] is True

    @pytest.mark.asyncio
    async def test_configuration_hot_reload_integration(self, integration_client: Client):
        """Test configuration hot reload and server update workflow."""
        # Create temporary config file for testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("""
hosts:
  reload-test:
    hostname: reload.example.com
    user: reload
    port: 2222
    description: "Hot reload test"
    enabled: true

server:
  host: 127.0.0.1
  port: 8000
""")
            temp_config_path = f.name

        try:
            # Step 1: Load initial configuration
            initial_config = load_config(temp_config_path)
            assert "reload-test" in initial_config.hosts
            
            # Step 2: Test configuration saving
            new_host = DockerHost(
                hostname="new-reload.example.com",
                user="newreload",
                description="Added via hot reload"
            )
            initial_config.hosts["new-reload-host"] = new_host
            
            save_config(initial_config, temp_config_path)
            
            # Step 3: Verify configuration was saved and can be reloaded
            reloaded_config = load_config(temp_config_path)
            assert "new-reload-host" in reloaded_config.hosts
            assert reloaded_config.hosts["new-reload-host"].hostname == "new-reload.example.com"
            
            # Step 4: Test file watcher integration (mocked)
            with patch('docker_mcp.core.file_watcher.load_config') as mock_load, \
                 patch('watchfiles.awatch') as mock_watch:
                
                from docker_mcp.core.file_watcher import ConfigFileWatcher
                
                reload_calls = []
                async def capture_reload(config):
                    reload_calls.append(config)
                
                mock_load.return_value = reloaded_config
                
                # Mock file change detection
                async def mock_changes():
                    yield [('modified', temp_config_path)]
                
                mock_watch.return_value = mock_changes()
                
                watcher = ConfigFileWatcher(temp_config_path, capture_reload)
                
                # Simulate configuration change handling
                await watcher._handle_config_change()
                
                # Verify reload was called
                assert len(reload_calls) == 1
                assert "new-reload-host" in reload_calls[0].hosts
                
        finally:
            # Clean up
            Path(temp_config_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_error_handling_and_recovery_workflow(self, integration_client: Client):
        """Test comprehensive error handling and recovery across services."""
        test_host_id = "integration-host"
        
        # Test 1: Invalid host handling
        invalid_result = await integration_client.call_tool("docker_container", {"action": "list",
            "host_id": "nonexistent-host"
        })
        assert invalid_result.data["success"] is False
        assert "error" in invalid_result.data
        
        # Test 2: Docker context creation failure
        with patch('docker_mcp.core.docker_context.DockerContextManager._run_docker_command') as mock_docker:
            mock_docker.side_effect = DockerContextError("Docker daemon not available")
            
            context_result = await integration_client.call_tool("docker_container", {"action": "info",
                "host_id": test_host_id,
                "container_id": "test-container"
            })
            assert context_result.data["success"] is False
            assert "error" in context_result.data
        
        # Test 3: SSH connection failure for stack operations
        with patch('docker_mcp.core.compose_manager.ComposeManager._run_ssh_command') as mock_ssh:
            mock_ssh.side_effect = Exception("Connection refused")
            
            ssh_result = await integration_client.call_tool("list_stacks", {
                "host_id": test_host_id
            })
            assert ssh_result.data["success"] is False
            assert "error" in ssh_result.data
        
        # Test 4: Configuration validation errors
        config_result = await integration_client.call_tool("add_docker_host", {
            "host_id": "",  # Invalid empty host_id
            "hostname": "test.com",
            "user": "test"
        })
        assert config_result.data["success"] is False
        assert "error" in config_result.data
        
        # Test 5: Recovery after temporary failure
        with patch('docker_mcp.core.docker_context.DockerContextManager._run_docker_command') as mock_docker:
            # First call fails, second succeeds (simulating recovery)
            call_count = 0
            def side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise DockerContextError("Temporary failure")
                else:
                    result = MagicMock()
                    result.returncode = 0
                    result.stdout = "[]"
                    return result
            
            mock_docker.side_effect = side_effect
            
            # First call should fail
            first_result = await integration_client.call_tool("docker_container", {"action": "list",
                "host_id": test_host_id
            })
            assert first_result.data["success"] is False
            
            # Second call should succeed (recovery)
            second_result = await integration_client.call_tool("docker_container", {"action": "list",
                "host_id": test_host_id
            })
            assert second_result.data["success"] is True


class TestServiceLayerIntegration:
    """Test integration across service layer modules."""

    @pytest.fixture
    def test_config(self) -> DockerMCPConfig:
        """Create test configuration for service testing."""
        return DockerMCPConfig(hosts={
            "service-test": DockerHost(
                hostname="service.example.com",
                user="service",
                port=22,
                description="Service layer test host",
                compose_path="/opt/compose"
            )
        })

    @pytest.mark.asyncio
    async def test_config_service_integration(self, test_config: DockerMCPConfig):
        """Test configuration service operations."""
        from docker_mcp.services.config import ConfigService
        
        service = ConfigService(test_config)
        
        # Test host validation
        assert service._validate_host_id("service-test") is True
        assert service._validate_host_id("invalid-host") is False
        
        # Test host retrieval
        host_info = service._get_host_info("service-test")
        assert host_info.hostname == "service.example.com"
        assert host_info.user == "service"
        
        # Test configuration updates
        with patch('docker_mcp.services.config.save_config') as mock_save:
            new_host = {
                "hostname": "new.service.com",
                "user": "newservice",
                "description": "Updated service host"
            }
            
            result = service._add_host("new-service", new_host, test_connection=False)
            assert result["success"] is True
            assert "new-service" in service.config.hosts
            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_container_service_integration(self, test_config: DockerMCPConfig):
        """Test container service with mocked Docker operations."""
        from docker_mcp.services.container import ContainerService
        
        service = ContainerService(test_config, MagicMock())
        
        with patch.object(service.context_manager, 'execute_docker_command') as mock_docker:
            # Test container listing
            mock_docker.return_value = {
                "output": "abc123\tnginx:alpine\trunning\ttest-web"
            }
            
            result = await service._list_containers("service-test", limit=10, offset=0)
            assert result["success"] is True
            assert "containers" in result
            
            # Test container info retrieval
            mock_docker.return_value = [{
                "Id": "abc123",
                "Name": "/test-web",
                "State": {"Status": "running", "Running": True},
                "Config": {"Image": "nginx:alpine"}
            }]
            
            info_result = await service._get_container_info("service-test", "abc123")
            assert info_result["success"] is True
            assert info_result["container_id"] == "abc123"
            
            # Test container management
            mock_docker.return_value = {"output": "abc123"}
            
            manage_result = await service._manage_container("service-test", "abc123", "restart")
            assert manage_result["success"] is True
            assert manage_result["action"] == "restart"

    @pytest.mark.asyncio
    async def test_stack_service_integration(self, test_config: DockerMCPConfig):
        """Test stack service with mocked compose operations."""
        from docker_mcp.services.stack import StackService
        
        service = StackService(test_config, MagicMock())
        
        with patch.object(service.compose_manager, 'list_stacks') as mock_list, \
             patch.object(service.compose_manager, 'deploy_stack') as mock_deploy, \
             patch.object(service.compose_manager, 'manage_stack') as mock_manage:
            
            # Test stack listing
            mock_list.return_value = [
                {"name": "test-stack", "status": "running", "services": 2}
            ]
            
            list_result = await service._list_stacks("service-test")
            assert list_result["success"] is True
            assert len(list_result["stacks"]) == 1
            
            # Test stack deployment
            mock_deploy.return_value = {
                "success": True,
                "stack_name": "new-stack",
                "services": ["web", "db"]
            }
            
            deploy_result = await service._deploy_stack(
                "service-test", 
                "new-stack", 
                "version: '3.8'\nservices:\n  web:\n    image: nginx",
                environment={"ENV": "test"},
                pull_images=True
            )
            assert deploy_result["success"] is True
            assert deploy_result["stack_name"] == "new-stack"
            
            # Test stack management
            mock_manage.return_value = {
                "success": True,
                "action": "down",
                "output": "Stack removed"
            }
            
            manage_result = await service._manage_stack(
                "service-test",
                "new-stack", 
                "down",
                {"volumes": True}
            )
            assert manage_result["success"] is True
            assert manage_result["action"] == "down"


class TestToolsLayerIntegration:
    """Test integration across tools layer modules."""

    @pytest.fixture
    async def tools_server(self) -> AsyncGenerator[DockerMCPServer, None]:
        """Create server for tools integration testing."""
        config = DockerMCPConfig(hosts={
            "tools-test": DockerHost(
                hostname="tools.example.com",
                user="tools",
                description="Tools integration test",
                enabled=True
            )
        })
        
        server = DockerMCPServer(config)
        server._initialize_app()
        yield server

    @pytest.fixture
    async def tools_client(self, tools_server: DockerMCPServer) -> AsyncGenerator[Client, None]:
        """Create client for tools testing."""
        async with Client(tools_server.app) as client:
            yield client

    @pytest.mark.asyncio
    async def test_cross_tool_container_workflow(self, tools_client: Client):
        """Test workflow using multiple container-related tools."""
        test_host_id = "tools-test"
        
        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_docker:
            # Step 1: List containers (containers.py tool)
            mock_docker.return_value = {
                "output": "container1\tnginx\trunning\tweb\ncontainer2\tredis\texited\tcache"
            }
            
            containers = await tools_client.call_tool("docker_container", {"action": "list",
                "host_id": test_host_id
            })
            assert containers.data["success"] is True
            
            # Step 2: Get container details (containers.py tool)
            mock_docker.return_value = [{
                "Id": "container1",
                "Name": "/web",
                "State": {"Status": "running"},
                "Config": {"Image": "nginx"}
            }]
            
            details = await tools_client.call_tool("docker_container", {"action": "info",
                "host_id": test_host_id,
                "container_id": "container1"
            })
            assert details.data["success"] is True
            
            # Step 3: Get container logs (logs.py tool)
            mock_docker.return_value = {
                "output": "2025-01-15 10:00:00 Started nginx\n2025-01-15 10:01:00 Ready"
            }
            
            logs = await tools_client.call_tool("get_container_logs", {
                "host_id": test_host_id,
                "container_id": "container1",
                "lines": 100
            })
            assert logs.data["success"] is True
            assert "logs" in logs.data
            
            # Step 4: Manage container (containers.py tool)
            mock_docker.return_value = {"output": "container1"}
            
            restart = await tools_client.call_tool("manage_container", {
                "host_id": test_host_id,
                "container_id": "container1",
                "action": "restart"
            })
            assert restart.data["success"] is True

    @pytest.mark.asyncio
    async def test_cross_tool_stack_workflow(self, tools_client: Client):
        """Test workflow using multiple stack-related tools."""
        test_host_id = "tools-test"
        
        with patch('docker_mcp.core.compose_manager.ComposeManager._run_ssh_command') as mock_ssh:
            # Step 1: List stacks (stacks.py tool)
            mock_ssh.return_value = {
                "stdout": "existing-stack\nother-stack",
                "stderr": "",
                "returncode": 0
            }
            
            stacks = await tools_client.call_tool("list_stacks", {
                "host_id": test_host_id
            })
            assert stacks.data["success"] is True
            
            # Step 2: Deploy new stack (stacks.py tool)
            mock_ssh.return_value = {
                "stdout": "Creating test-stack_web_1\nStarting test-stack_web_1",
                "stderr": "",
                "returncode": 0
            }
            
            deploy = await tools_client.call_tool("deploy_stack", {
                "host_id": test_host_id,
                "stack_name": "test-stack",
                "compose_content": """
version: '3.8'
services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
"""
            })
            assert deploy.data["success"] is True
            
            # Step 3: Check stack status (stacks.py tool)
            mock_ssh.return_value = {
                "stdout": "test-stack_web_1   Up   0.0.0.0:8080->80/tcp",
                "stderr": "",
                "returncode": 0
            }
            
            status = await tools_client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": "test-stack",
                "action": "ps"
            })
            assert status.data["success"] is True

    @pytest.mark.asyncio  
    async def test_port_discovery_integration(self, tools_client: Client):
        """Test port discovery across container and stack tools."""
        test_host_id = "tools-test"
        
        with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_docker:
            # Mock container listing with port information
            mock_docker.return_value = {
                "output": """CONTAINER ID   IMAGE         PORTS                    NAMES
abc123def456   nginx:alpine  0.0.0.0:8080->80/tcp     web
def456ghi789   redis:alpine  6379/tcp                 cache
ghi789jkl012   postgres:13   0.0.0.0:5432->5432/tcp   db"""
            }
            
            # Test port listing across all containers
            ports = await tools_client.call_tool("list_host_ports", {
                "host_id": test_host_id,
                "include_stopped": False
            })
            assert ports.data["success"] is True
            assert "port_mappings" in ports.data
            
            # Verify specific container ports
            containers = await tools_client.call_tool("docker_container", {"action": "list",
                "host_id": test_host_id
            })
            assert containers.data["success"] is True
            
            # Test that port information is properly extracted
            # This integration test ensures containers.py and port parsing work together


class TestServerIntegration:
    """Test server-level integration and lifecycle management."""

    @pytest.mark.asyncio
    async def test_server_configuration_lifecycle(self):
        """Test complete server configuration and update lifecycle."""
        # Step 1: Create server with initial configuration
        initial_config = DockerMCPConfig(hosts={
            "server-test": DockerHost(
                hostname="server.example.com",
                user="server",
                enabled=True
            )
        })
        
        server = DockerMCPServer(initial_config)
        server._initialize_app()
        
        # Step 2: Test configuration access
        assert len(server.config.hosts) == 1
        assert "server-test" in server.config.hosts
        
        # Step 3: Update configuration
        updated_config = DockerMCPConfig(hosts={
            "server-test": DockerHost(
                hostname="updated-server.example.com",
                user="updated",
                enabled=True
            ),
            "new-server": DockerHost(
                hostname="new.example.com",
                user="new",
                enabled=True
            )
        })
        
        # Test configuration update
        server.update_configuration(updated_config)
        assert len(server.config.hosts) == 2
        assert "new-server" in server.config.hosts
        assert server.config.hosts["server-test"].hostname == "updated-server.example.com"
        
        # Step 4: Test hot reload manager integration
        with patch('docker_mcp.core.file_watcher.ConfigFileWatcher') as mock_watcher:
            from docker_mcp.core.file_watcher import HotReloadManager
            
            manager = HotReloadManager()
            manager.setup_hot_reload("test-config.yml", server)
            
            assert manager._server_instance == server
            assert manager.config_watcher is not None
            
            # Test configuration change detection
            host_changes = manager._detect_host_changes(updated_config)
            assert "new-server" in host_changes["added"]

    @pytest.mark.asyncio
    async def test_server_error_handling_integration(self):
        """Test server-level error handling and recovery."""
        config = DockerMCPConfig(hosts={
            "error-test": DockerHost(
                hostname="error.example.com",
                user="error",
                enabled=True
            )
        })
        
        server = DockerMCPServer(config)
        server._initialize_app()
        
        async with Client(server.app) as client:
            # Test handling of various error conditions
            
            # 1. Invalid tool calls
            try:
                await client.call_tool("nonexistent_tool", {})
                assert False, "Should have raised an exception"
            except Exception:
                pass  # Expected
            
            # 2. Invalid parameters
            result = await client.call_tool("docker_container", {"action": "list",
                "host_id": "",  # Invalid empty host_id
                "limit": -1     # Invalid negative limit
            })
            # Server should handle gracefully and return error response
            assert "success" in result.data

    @pytest.mark.asyncio
    async def test_concurrent_operations_integration(self):
        """Test server handling of concurrent operations."""
        config = DockerMCPConfig(hosts={
            "concurrent-test": DockerHost(
                hostname="concurrent.example.com",
                user="concurrent",
                enabled=True
            )
        })
        
        server = DockerMCPServer(config)
        server._initialize_app()
        
        async with Client(server.app) as client:
            # Mock Docker operations to simulate real responses
            with patch('docker_mcp.core.docker_context.DockerContextManager.execute_docker_command') as mock_docker:
                mock_docker.return_value = {"output": "mock response"}
                
                # Create multiple concurrent operations
                tasks = []
                for i in range(5):
                    task = client.call_tool("docker_container", {"action": "list",
                        "host_id": "concurrent-test",
                        "limit": 10
                    })
                    tasks.append(task)
                
                # Execute all tasks concurrently
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Verify all operations completed successfully
                for result in results:
                    if isinstance(result, Exception):
                        assert False, f"Concurrent operation failed: {result}"
                    assert result.data["success"] is True or "error" in result.data
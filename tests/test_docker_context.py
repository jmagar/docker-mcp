"""
Comprehensive tests for Docker context management functionality.

Tests Docker context creation, caching, command execution, and lifecycle management.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from docker_mcp.core.config_loader import DockerHost, DockerMCPConfig
from docker_mcp.core.docker_context import DockerContextManager
from docker_mcp.core.exceptions import DockerContextError


class TestDockerContextManagerInit:
    """Test DockerContextManager initialization and setup."""

    def test_docker_context_manager_creation(self):
        """Test creating DockerContextManager with configuration."""
        config = DockerMCPConfig(hosts={
            "test-host": DockerHost(
                hostname="test.example.com",
                user="testuser",
                port=2222,
                description="Test host"
            )
        })

        manager = DockerContextManager(config)

        assert manager.config == config
        assert isinstance(manager._context_cache, dict)
        assert len(manager._context_cache) == 0
        # Docker binary path depends on system - just verify it's set
        assert manager._docker_bin is not None

    @patch('docker_mcp.core.docker_context.shutil.which')
    def test_docker_context_manager_custom_docker_bin(self, mock_which):
        """Test DockerContextManager with custom docker binary path."""
        mock_which.return_value = "/usr/local/bin/docker"

        config = DockerMCPConfig()
        manager = DockerContextManager(config)

        assert manager._docker_bin == "/usr/local/bin/docker"
        mock_which.assert_called_once_with("docker")

    @patch('docker_mcp.core.docker_context.shutil.which')
    def test_docker_context_manager_no_docker_bin(self, mock_which):
        """Test DockerContextManager when docker binary not found."""
        mock_which.return_value = None

        config = DockerMCPConfig()
        manager = DockerContextManager(config)

        assert manager._docker_bin == "docker"  # Falls back to "docker"


class TestDockerCommandExecution:
    """Test Docker command execution and validation."""

    @pytest.fixture
    def manager(self):
        """Create DockerContextManager for testing."""
        config = DockerMCPConfig(hosts={
            "test-host": DockerHost(
                hostname="test.example.com",
                user="testuser",
                description="Test host"
            )
        })
        return DockerContextManager(config)

    @pytest.mark.asyncio
    async def test_run_docker_command_success(self, manager):
        """Test successful docker command execution."""
        with patch('docker_mcp.core.docker_context.subprocess.run') as mock_run:
            # Configure mock to return success
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = '{"version": "1.0"}'
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            result = await manager._run_docker_command(["version"])

            assert result.returncode == 0
            assert result.stdout == '{"version": "1.0"}'
            mock_run.assert_called_once()

            # Verify command structure
            call_args = mock_run.call_args
            assert call_args[0][0][0] == "docker"  # First arg is command
            assert call_args[0][0][1] == "version"  # Second arg is subcommand

    @pytest.mark.asyncio
    async def test_run_docker_command_with_custom_timeout(self, manager):
        """Test docker command execution with custom timeout."""
        with patch('docker_mcp.core.docker_context.subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "output"
            mock_run.return_value = mock_result

            result = await manager._run_docker_command(["ps"], timeout=45)

            assert result.returncode == 0
            mock_run.assert_called_once()

            # Check that timeout was passed
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 45

    def test_validate_docker_command_allowed_commands(self, manager):
        """Test validation of allowed Docker commands."""
        allowed_commands = [
            "ps", "logs", "start", "stop", "restart", "stats",
            "compose", "pull", "build", "inspect", "images",
            "volume", "network", "system", "info", "version"
        ]

        for command in allowed_commands:
            # These should not raise exceptions
            manager._validate_docker_command(command)
            manager._validate_docker_command(f"{command} --help")

    def test_validate_docker_command_disallowed_commands(self, manager):
        """Test validation rejects disallowed Docker commands."""
        disallowed_commands = [
            "rm", "rmi", "exec", "run", "create", "commit",
            "save", "load", "export", "import", "cp", "diff"
        ]

        for command in disallowed_commands:
            with pytest.raises(ValueError, match="Command not allowed"):
                manager._validate_docker_command(command)

    def test_validate_docker_command_empty_command(self, manager):
        """Test validation rejects empty commands."""
        with pytest.raises(ValueError, match="Empty command"):
            manager._validate_docker_command("")

        with pytest.raises(ValueError, match="Empty command"):
            manager._validate_docker_command("   ")


class TestDockerContextLifecycle:
    """Test Docker context creation, checking, and management."""

    @pytest.fixture
    def manager_with_host(self):
        """Create DockerContextManager with test host."""
        config = DockerMCPConfig(hosts={
            "test-host": DockerHost(
                hostname="test.example.com",
                user="testuser",
                port=2222,
                description="Test Docker host",
                docker_context="test-context"
            ),
            "default-port-host": DockerHost(
                hostname="default.example.com",
                user="user"
                # port defaults to 22
            )
        })
        return DockerContextManager(config)

    @pytest.mark.asyncio
    async def test_context_exists_true(self, manager_with_host):
        """Test checking if Docker context exists (true case)."""
        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_cmd.return_value = mock_result

            exists = await manager_with_host._context_exists("test-context")

            assert exists is True
            mock_cmd.assert_called_once_with(["context", "inspect", "test-context"], timeout=10)

    @pytest.mark.asyncio
    async def test_context_exists_false(self, manager_with_host):
        """Test checking if Docker context exists (false case)."""
        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_cmd.return_value = mock_result

            exists = await manager_with_host._context_exists("nonexistent-context")

            assert exists is False

    @pytest.mark.asyncio
    async def test_context_exists_exception_handling(self, manager_with_host):
        """Test context existence check handles exceptions gracefully."""
        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_cmd.side_effect = Exception("Connection error")

            exists = await manager_with_host._context_exists("test-context")

            assert exists is False

    @pytest.mark.asyncio
    async def test_create_context_basic_host(self, manager_with_host):
        """Test creating Docker context for basic host."""
        host_config = DockerHost(
            hostname="basic.example.com",
            user="basicuser",
            port=22
        )

        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_cmd.return_value = mock_result

            await manager_with_host._create_context("basic-context", host_config)

            mock_cmd.assert_called_once()
            call_args = mock_cmd.call_args[0][0]

            # Verify command structure
            assert call_args[0] == "context"
            assert call_args[1] == "create"
            assert call_args[2] == "basic-context"
            assert call_args[3] == "--docker"
            assert call_args[4] == "host=ssh://basicuser@basic.example.com"

    @pytest.mark.asyncio
    async def test_create_context_custom_port(self, manager_with_host):
        """Test creating Docker context for host with custom port."""
        host_config = DockerHost(
            hostname="custom.example.com",
            user="customuser",
            port=2222
        )

        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_cmd.return_value = mock_result

            await manager_with_host._create_context("custom-context", host_config)

            call_args = mock_cmd.call_args[0][0]

            # Verify SSH URL includes custom port
            assert call_args[4] == "host=ssh://customuser@custom.example.com:2222"

    @pytest.mark.asyncio
    async def test_create_context_with_description(self, manager_with_host):
        """Test creating Docker context with description."""
        host_config = DockerHost(
            hostname="described.example.com",
            user="user",
            description="A test host with description"
        )

        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_cmd.return_value = mock_result

            await manager_with_host._create_context("described-context", host_config)

            call_args = mock_cmd.call_args[0][0]

            # Verify description is included
            assert "--description" in call_args
            desc_index = call_args.index("--description")
            assert call_args[desc_index + 1] == "A test host with description"

    @pytest.mark.asyncio
    async def test_create_context_failure(self, manager_with_host):
        """Test Docker context creation failure."""
        host_config = DockerHost(hostname="fail.example.com", user="user")

        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Failed to create context"
            mock_cmd.return_value = mock_result

            with pytest.raises(DockerContextError, match="Failed to create context"):
                await manager_with_host._create_context("fail-context", host_config)

    @pytest.mark.asyncio
    async def test_create_context_timeout(self, manager_with_host):
        """Test Docker context creation timeout."""
        host_config = DockerHost(hostname="timeout.example.com", user="user")

        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_cmd.side_effect = subprocess.TimeoutExpired("docker", 30)

            with pytest.raises(DockerContextError, match="Context creation timed out"):
                await manager_with_host._create_context("timeout-context", host_config)

    @pytest.mark.asyncio
    async def test_create_context_general_exception(self, manager_with_host):
        """Test Docker context creation with general exception."""
        host_config = DockerHost(hostname="error.example.com", user="user")

        with patch.object(manager_with_host, '_run_docker_command') as mock_cmd:
            mock_cmd.side_effect = Exception("Unexpected error")

            with pytest.raises(DockerContextError, match="Failed to create context"):
                await manager_with_host._create_context("error-context", host_config)


class TestContextEnsuring:
    """Test context ensuring logic with caching."""

    @pytest.fixture
    def manager_with_hosts(self):
        """Create manager with multiple test hosts."""
        config = DockerMCPConfig(hosts={
            "cached-host": DockerHost(
                hostname="cached.example.com",
                user="cached",
                docker_context="cached-context"
            ),
            "new-host": DockerHost(
                hostname="new.example.com",
                user="new"
            ),
            "existing-host": DockerHost(
                hostname="existing.example.com",
                user="existing",
                docker_context="existing-context"
            )
        })
        return DockerContextManager(config)

    @pytest.mark.asyncio
    async def test_ensure_context_invalid_host(self, manager_with_hosts):
        """Test ensuring context for non-configured host."""
        with pytest.raises(DockerContextError, match="Host invalid-host not configured"):
            await manager_with_hosts.ensure_context("invalid-host")

    @pytest.mark.asyncio
    async def test_ensure_context_cached_valid(self, manager_with_hosts):
        """Test ensuring context when cached context is valid."""
        # Simulate cached context
        manager_with_hosts._context_cache["cached-host"] = "cached-context"

        with patch.object(manager_with_hosts, '_context_exists') as mock_exists:
            mock_exists.return_value = True

            context_name = await manager_with_hosts.ensure_context("cached-host")

            assert context_name == "cached-context"
            mock_exists.assert_called_once_with("cached-context")

    @pytest.mark.asyncio
    async def test_ensure_context_cached_invalid(self, manager_with_hosts):
        """Test ensuring context when cached context no longer exists."""
        # Simulate cached context that no longer exists
        manager_with_hosts._context_cache["cached-host"] = "old-context"

        with patch.object(manager_with_hosts, '_context_exists') as mock_exists, \
             patch.object(manager_with_hosts, '_create_context') as mock_create:

            # First call (cached context) returns False, second call (configured context) returns False,
            # so it creates the context
            mock_exists.return_value = False

            context_name = await manager_with_hosts.ensure_context("cached-host")

            # Should use the configured docker_context name
            assert context_name == "cached-context"
            assert "cached-host" in manager_with_hosts._context_cache
            # Should check cached context, then configured context, then create
            assert mock_exists.call_count == 2
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_context_existing_context(self, manager_with_hosts):
        """Test ensuring context when context already exists."""
        with patch.object(manager_with_hosts, '_context_exists') as mock_exists:
            mock_exists.return_value = True

            context_name = await manager_with_hosts.ensure_context("existing-host")

            assert context_name == "existing-context"
            assert manager_with_hosts._context_cache["existing-host"] == "existing-context"

    @pytest.mark.asyncio
    async def test_ensure_context_create_new(self, manager_with_hosts):
        """Test ensuring context by creating new one."""
        with patch.object(manager_with_hosts, '_context_exists') as mock_exists, \
             patch.object(manager_with_hosts, '_create_context') as mock_create:

            mock_exists.return_value = False

            context_name = await manager_with_hosts.ensure_context("new-host")

            assert context_name == "docker-mcp-new-host"  # Default naming
            assert manager_with_hosts._context_cache["new-host"] == "docker-mcp-new-host"
            mock_create.assert_called_once()


class TestDockerCommandExecution:
    """Test full Docker command execution workflow."""

    @pytest.fixture
    def manager_with_host(self):
        """Create manager with test host."""
        config = DockerMCPConfig(hosts={
            "exec-host": DockerHost(
                hostname="exec.example.com",
                user="execuser"
            )
        })
        return DockerContextManager(config)

    @pytest.mark.asyncio
    async def test_execute_docker_command_json_output(self, manager_with_host):
        """Test executing Docker command that returns JSON."""
        version_output = '{"Client": {"Version": "20.10.0"}}'

        with patch.object(manager_with_host, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_host, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "exec-context"
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = version_output
            mock_run.return_value = mock_result

            result = await manager_with_host.execute_docker_command("exec-host", "version")

            assert isinstance(result, dict)
            assert result["Client"]["Version"] == "20.10.0"
            mock_ensure.assert_called_once_with("exec-host")

    @pytest.mark.asyncio
    async def test_execute_docker_command_text_output(self, manager_with_host):
        """Test executing Docker command that returns text."""
        ps_output = "CONTAINER ID   IMAGE   COMMAND   CREATED   STATUS   PORTS   NAMES"

        with patch.object(manager_with_host, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_host, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "exec-context"
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ps_output
            mock_run.return_value = mock_result

            result = await manager_with_host.execute_docker_command("exec-host", "ps")

            assert result == {"output": ps_output}

    @pytest.mark.asyncio
    async def test_execute_docker_command_invalid_json(self, manager_with_host):
        """Test executing command that should return JSON but doesn't."""
        invalid_json = "Not valid JSON output"

        with patch.object(manager_with_host, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_host, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "exec-context"
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = invalid_json
            mock_run.return_value = mock_result

            result = await manager_with_host.execute_docker_command("exec-host", "inspect container")

            assert result == {"output": invalid_json}

    @pytest.mark.asyncio
    async def test_execute_docker_command_failure(self, manager_with_host):
        """Test executing Docker command that fails."""
        with patch.object(manager_with_host, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_host, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "exec-context"
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Container not found"
            mock_run.return_value = mock_result

            with pytest.raises(DockerContextError, match="Docker command failed"):
                await manager_with_host.execute_docker_command("exec-host", "start nonexistent")

    @pytest.mark.asyncio
    async def test_execute_docker_command_timeout(self, manager_with_host):
        """Test executing Docker command that times out."""
        with patch.object(manager_with_host, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_host, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "exec-context"
            mock_run.side_effect = subprocess.TimeoutExpired("docker", 60)

            with pytest.raises(DockerContextError, match="Docker command timed out"):
                await manager_with_host.execute_docker_command("exec-host", "ps")

    @pytest.mark.asyncio
    async def test_execute_docker_command_invalid_command(self, manager_with_host):
        """Test executing invalid Docker command."""
        with pytest.raises(ValueError, match="Command not allowed"):
            await manager_with_host.execute_docker_command("exec-host", "rm container")


class TestContextListingAndRemoval:
    """Test Docker context listing and removal operations."""

    @pytest.fixture
    def manager(self):
        """Create basic manager for testing."""
        config = DockerMCPConfig()
        return DockerContextManager(config)

    @pytest.mark.asyncio
    async def test_list_contexts_success(self, manager):
        """Test successful context listing."""
        context_output = '''{"Name":"default","Current":true}
{"Name":"test-context","Current":false}
{"Name":"another-context","Current":false}'''

        with patch.object(manager, '_run_docker_command') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = context_output
            mock_run.return_value = mock_result

            contexts = await manager.list_contexts()

            assert len(contexts) == 3
            assert contexts[0]["Name"] == "default"
            assert contexts[0]["Current"] is True
            assert contexts[1]["Name"] == "test-context"
            assert contexts[1]["Current"] is False

    @pytest.mark.asyncio
    async def test_list_contexts_failure(self, manager):
        """Test failed context listing."""
        with patch.object(manager, '_run_docker_command') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Access denied"
            mock_run.return_value = mock_result

            with pytest.raises(DockerContextError, match="Failed to list contexts"):
                await manager.list_contexts()

    @pytest.mark.asyncio
    async def test_list_contexts_invalid_json(self, manager):
        """Test context listing with invalid JSON lines."""
        context_output = '''{"Name":"valid","Current":true}
invalid json line
{"Name":"another-valid","Current":false}'''

        with patch.object(manager, '_run_docker_command') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = context_output
            mock_run.return_value = mock_result

            contexts = await manager.list_contexts()

            # Should skip invalid line but parse valid ones
            assert len(contexts) == 2
            assert contexts[0]["Name"] == "valid"
            assert contexts[1]["Name"] == "another-valid"

    @pytest.mark.asyncio
    async def test_list_contexts_timeout(self, manager):
        """Test context listing timeout."""
        with patch.object(manager, '_run_docker_command') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("docker", 10)

            with pytest.raises(DockerContextError, match="Context listing timed out"):
                await manager.list_contexts()

    @pytest.mark.asyncio
    async def test_remove_context_success(self, manager):
        """Test successful context removal."""
        # Set up cache to test cache cleanup
        manager._context_cache["test-host"] = "test-context"

        with patch.object(manager, '_run_docker_command') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            await manager.remove_context("test-context")

            # Verify cache was cleaned up
            assert "test-host" not in manager._context_cache
            mock_run.assert_called_once_with(["context", "rm", "test-context"], timeout=10)

    @pytest.mark.asyncio
    async def test_remove_context_failure(self, manager):
        """Test failed context removal."""
        with patch.object(manager, '_run_docker_command') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Context not found"
            mock_run.return_value = mock_result

            with pytest.raises(DockerContextError, match="Failed to remove context"):
                await manager.remove_context("nonexistent-context")

    @pytest.mark.asyncio
    async def test_remove_context_timeout(self, manager):
        """Test context removal timeout."""
        with patch.object(manager, '_run_docker_command') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("docker", 10)

            with pytest.raises(DockerContextError, match="Context removal timed out"):
                await manager.remove_context("timeout-context")


class TestConnectionTesting:
    """Test Docker context connection testing functionality."""

    @pytest.fixture
    def manager_with_host(self):
        """Create manager with test host."""
        config = DockerMCPConfig(hosts={
            "test-connection": DockerHost(
                hostname="connection.example.com",
                user="connuser"
            )
        })
        return DockerContextManager(config)

    @pytest.mark.asyncio
    async def test_test_context_connection_success(self, manager_with_host):
        """Test successful connection testing."""
        version_output = '{"Client": {"Version": "20.10.0"}, "Server": {"Version": "20.10.0"}}'

        with patch.object(manager_with_host, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_host, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "test-context"
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = version_output
            mock_run.return_value = mock_result

            success = await manager_with_host.test_context_connection("test-connection")

            assert success is True
            mock_ensure.assert_called_once_with("test-connection")

    @pytest.mark.asyncio
    async def test_test_context_connection_failure(self, manager_with_host):
        """Test failed connection testing."""
        with patch.object(manager_with_host, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_host, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "test-context"
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Connection refused"
            mock_run.return_value = mock_result

            success = await manager_with_host.test_context_connection("test-connection")

            assert success is False

    @pytest.mark.asyncio
    async def test_test_context_connection_invalid_json(self, manager_with_host):
        """Test connection testing with non-JSON version output."""
        with patch.object(manager_with_host, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_host, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "test-context"
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Docker version 20.10.0"  # Non-JSON
            mock_run.return_value = mock_result

            success = await manager_with_host.test_context_connection("test-connection")

            assert success is True  # Returns True based on returncode

    @pytest.mark.asyncio
    async def test_test_context_connection_exception(self, manager_with_host):
        """Test connection testing with exception."""
        with patch.object(manager_with_host, 'ensure_context') as mock_ensure:
            mock_ensure.side_effect = DockerContextError("Context creation failed")

            success = await manager_with_host.test_context_connection("test-connection")

            assert success is False


class TestDockerContextIntegration:
    """Integration tests for Docker context manager functionality."""

    @pytest.fixture
    def manager_with_multiple_hosts(self):
        """Create manager with multiple hosts for integration testing."""
        config = DockerMCPConfig(hosts={
            "host1": DockerHost(
                hostname="host1.example.com",
                user="user1",
                description="First test host"
            ),
            "host2": DockerHost(
                hostname="host2.example.com",
                user="user2",
                port=2222,
                docker_context="custom-context"
            ),
            "host3": DockerHost(
                hostname="host3.example.com",
                user="user3"
            )
        })
        return DockerContextManager(config)

    @pytest.mark.asyncio
    async def test_multiple_host_context_management(self, manager_with_multiple_hosts):
        """Test managing contexts for multiple hosts."""
        with patch.object(manager_with_multiple_hosts, '_context_exists') as mock_exists, \
             patch.object(manager_with_multiple_hosts, '_create_context') as mock_create, \
             patch.object(manager_with_multiple_hosts, '_run_docker_command') as mock_run:

            # Mock context existence checks
            mock_exists.return_value = False

            # Mock successful Docker command execution
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "success"
            mock_run.return_value = mock_result

            # Ensure contexts for all hosts
            context1 = await manager_with_multiple_hosts.ensure_context("host1")
            context2 = await manager_with_multiple_hosts.ensure_context("host2")
            context3 = await manager_with_multiple_hosts.ensure_context("host3")

            # Verify different context names
            assert context1 == "docker-mcp-host1"
            assert context2 == "custom-context"
            assert context3 == "docker-mcp-host3"

            # Verify all are cached
            assert len(manager_with_multiple_hosts._context_cache) == 3
            assert manager_with_multiple_hosts._context_cache["host1"] == context1
            assert manager_with_multiple_hosts._context_cache["host2"] == context2
            assert manager_with_multiple_hosts._context_cache["host3"] == context3

    @pytest.mark.asyncio
    async def test_command_execution_workflow(self, manager_with_multiple_hosts):
        """Test complete command execution workflow."""
        with patch.object(manager_with_multiple_hosts, '_context_exists') as mock_exists, \
             patch.object(manager_with_multiple_hosts, '_create_context') as mock_create, \
             patch.object(manager_with_multiple_hosts, '_run_docker_command') as mock_run:

            # Setup mocks for context creation
            mock_exists.return_value = False

            # Mock Docker command responses
            def mock_docker_responses(*args, **kwargs):
                command = args[0]

                if command[0] == "context" and command[1] == "create":
                    # Context creation
                    result = MagicMock()
                    result.returncode = 0
                    return result
                elif "--context" in command:
                    # Docker command execution
                    result = MagicMock()
                    result.returncode = 0
                    result.stdout = '{"containers": []}'
                    return result
                else:
                    result = MagicMock()
                    result.returncode = 0
                    result.stdout = "success"
                    return result

            mock_run.side_effect = mock_docker_responses

            # Execute commands on different hosts
            result1 = await manager_with_multiple_hosts.execute_docker_command("host1", "ps")
            result2 = await manager_with_multiple_hosts.execute_docker_command("host2", "info")

            assert "containers" in result1 or "output" in result1
            assert isinstance(result2, dict)

            # Verify contexts were created and cached
            assert len(manager_with_multiple_hosts._context_cache) == 2

    @pytest.mark.asyncio
    async def test_context_cache_invalidation_and_recreation(self, manager_with_multiple_hosts):
        """Test context cache invalidation and recreation."""
        # Pre-populate cache with invalid context
        manager_with_multiple_hosts._context_cache["host1"] = "old-context"

        with patch.object(manager_with_multiple_hosts, '_context_exists') as mock_exists, \
             patch.object(manager_with_multiple_hosts, '_create_context') as mock_create:

            # First call returns False (cached context doesn't exist)
            # Second call returns False (default context doesn't exist), so create it
            mock_exists.return_value = False

            context_name = await manager_with_multiple_hosts.ensure_context("host1")

            # Should get the default context name
            assert context_name == "docker-mcp-host1"

            # Should have called _context_exists twice and created new context
            assert mock_exists.call_count == 2
            mock_create.assert_called_once()

            # Cache should be updated
            assert manager_with_multiple_hosts._context_cache["host1"] == "docker-mcp-host1"

    @pytest.mark.asyncio
    async def test_error_handling_across_operations(self, manager_with_multiple_hosts):
        """Test comprehensive error handling across different operations."""
        # Test 1: Invalid host ID
        with pytest.raises(DockerContextError, match="Host invalid not configured"):
            await manager_with_multiple_hosts.ensure_context("invalid")

        # Test 2: Context creation failure
        with patch.object(manager_with_multiple_hosts, '_context_exists') as mock_exists, \
             patch.object(manager_with_multiple_hosts, '_create_context') as mock_create:

            mock_exists.return_value = False
            mock_create.side_effect = DockerContextError("Creation failed")

            with pytest.raises(DockerContextError, match="Creation failed"):
                await manager_with_multiple_hosts.ensure_context("host1")

        # Test 3: Command validation failure
        with pytest.raises(ValueError, match="Command not allowed"):
            await manager_with_multiple_hosts.execute_docker_command("host1", "rm container")

        # Test 4: Command execution failure
        with patch.object(manager_with_multiple_hosts, 'ensure_context') as mock_ensure, \
             patch.object(manager_with_multiple_hosts, '_run_docker_command') as mock_run:

            mock_ensure.return_value = "test-context"
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Command failed"
            mock_run.return_value = mock_result

            with pytest.raises(DockerContextError, match="Docker command failed"):
                await manager_with_multiple_hosts.execute_docker_command("host1", "ps")

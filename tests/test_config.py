"""Tests for configuration management."""

import tempfile
from pathlib import Path

import pytest  # type: ignore[import-not-found]

from docker_mcp.core.config_loader import DockerHost, DockerMCPConfig, load_config


def test_default_config():
    """Test default configuration creation."""
    config = DockerMCPConfig()

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8000
    assert config.server.log_level == "INFO"
    assert len(config.hosts) == 0


def test_load_yaml_config():
    """Test loading configuration from YAML file."""
    import os
    from unittest.mock import patch

    yaml_content = """
hosts:
  test-host:
    hostname: test.example.com
    user: testuser
    port: 2222
    description: "Test host"
    tags: ["test"]

server:
  host: 127.0.0.1
  port: 9000
  log_level: DEBUG
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(yaml_content)
        config_path = f.name

    try:
        # Temporarily unset environment variables to test YAML loading
        original_env = {}
        env_vars_to_clear = ["FASTMCP_HOST", "FASTMCP_PORT", "LOG_LEVEL"]
        for var in env_vars_to_clear:
            if var in os.environ:
                original_env[var] = os.environ[var]
                del os.environ[var]

        # Mock load_dotenv to prevent loading .env file
        with patch("docker_mcp.core.config.load_dotenv"):
            config = load_config(config_path)

        # Restore environment variables
        for var, value in original_env.items():
            os.environ[var] = value

        # Check hosts
        assert "test-host" in config.hosts
        host = config.hosts["test-host"]
        assert host.hostname == "test.example.com"
        assert host.user == "testuser"
        assert host.port == 2222
        assert host.description == "Test host"
        assert host.tags == ["test"]

        # Check server config
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 9000
        assert config.server.log_level == "DEBUG"

    finally:
        Path(config_path).unlink()


def test_invalid_config_file():
    """Test handling of invalid configuration file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("invalid: yaml: content: [")
        config_path = f.name

    try:
        with pytest.raises(ValueError, match="Failed to load config"):
            load_config(config_path)
    finally:
        Path(config_path).unlink()


def test_docker_host_validation():
    """Test DockerHost model validation."""
    # Valid host
    host = DockerHost(hostname="test.example.com", user="testuser")
    assert host.port == 22  # Default value
    assert host.enabled is True  # Default value

    # Test with all fields
    host = DockerHost(
        hostname="test.example.com",
        user="testuser",
        port=2222,
        identity_file="/path/to/key",
        description="Test host",
        tags=["test", "staging"],
        enabled=False,
    )
    assert host.port == 2222
    assert host.identity_file == "/path/to/key"
    assert host.description == "Test host"
    assert host.tags == ["test", "staging"]
    assert host.enabled is False


def test_config_priority():
    """Test configuration priority order."""
    # This would be more complex in a real test
    # For now, just test that load_config doesn't crash
    config = load_config()
    assert isinstance(config, DockerMCPConfig)

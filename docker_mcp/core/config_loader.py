"""Configuration management for Docker MCP server."""

import os
from pathlib import Path
from typing import Any

import structlog
import yaml  # type: ignore[import-untyped]
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

logger = structlog.get_logger()


class DockerHost(BaseModel):
    """Configuration for a Docker host."""

    hostname: str
    user: str
    port: int = 22
    identity_file: str | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    docker_context: str | None = None
    compose_path: str | None = None  # Path where compose files are stored on this host
    appdata_path: str | None = None  # Path where container data volumes are stored
    enabled: bool = True


class ServerConfig(BaseModel):
    """Server configuration."""

    host: str = Field(default="127.0.0.1", alias="FASTMCP_HOST")  # Use 0.0.0.0 for container deployment
    port: int = Field(default=8000, alias="FASTMCP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_connections: int = 10


class DockerMCPConfig(BaseSettings):
    """Main configuration for Docker MCP server."""

    hosts: dict[str, DockerHost] = Field(default_factory=dict)
    server: ServerConfig = Field(default_factory=ServerConfig)
    config_file: str = Field(default="config/hosts.yml", alias="DOCKER_HOSTS_CONFIG")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def load_config(config_path: str | None = None) -> DockerMCPConfig:
    """Load configuration from multiple sources.

    Args:
        config_path: Optional path to YAML config file

    Returns:
        Loaded configuration
    """
    # Load .env file first
    load_dotenv()

    # Start with base configuration
    config = DockerMCPConfig()

    # Load user config if exists
    user_config_path = Path.home() / ".config" / "docker-mcp" / "hosts.yml"
    _load_config_file(config, user_config_path)

    # Load project config (from env var or default)
    default_config_file = os.getenv("DOCKER_HOSTS_CONFIG", "config/hosts.yml")
    project_config_path = Path(config_path or default_config_file)
    _load_config_file(config, project_config_path)

    # Override with environment variables (highest priority)
    _apply_env_overrides(config)

    return config


def _load_config_file(config: DockerMCPConfig, config_path: Path) -> None:
    """Load and apply configuration from a YAML file."""
    if not config_path.exists():
        return

    yaml_config = _load_yaml_config(config_path)
    _apply_host_config(config, yaml_config)
    _apply_server_config(config, yaml_config)


def _apply_host_config(config: DockerMCPConfig, yaml_config: dict[str, Any]) -> None:
    """Apply host configuration from YAML data."""
    if "hosts" in yaml_config and yaml_config["hosts"]:
        for host_id, host_data in yaml_config["hosts"].items():
            config.hosts[host_id] = DockerHost(**host_data)


def _apply_server_config(config: DockerMCPConfig, yaml_config: dict[str, Any]) -> None:
    """Apply server configuration from YAML data."""
    if "server" in yaml_config:
        for key, value in yaml_config["server"].items():
            if hasattr(config.server, key):
                setattr(config.server, key, value)


def _apply_env_overrides(config: DockerMCPConfig) -> None:
    """Apply environment variable overrides."""
    if os.getenv("FASTMCP_HOST"):
        config.server.host = os.getenv("FASTMCP_HOST", config.server.host)
    if port_env := os.getenv("FASTMCP_PORT"):
        config.server.port = int(port_env)
    if os.getenv("LOG_LEVEL"):
        config.server.log_level = os.getenv("LOG_LEVEL", config.server.log_level)


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    """Load YAML configuration file."""
    try:
        with open(config_path) as f:
            content = f.read()

        # Expand environment variables
        content = os.path.expandvars(content)

        return yaml.safe_load(content) or {}
    except Exception as e:
        raise ValueError(f"Failed to load config from {config_path}: {e}") from e


def save_config(config: DockerMCPConfig, config_path: str | None = None) -> None:
    """Save configuration to YAML file.

    Args:
        config: Configuration to save
        config_path: Path to save to (defaults to config/hosts.yml)

    Raises:
        ValueError: If unable to save configuration
    """
    if config_path is None:
        config_path = "config/hosts.yml"

    config_path = Path(config_path)

    try:
        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Build YAML structure
        yaml_data = _build_yaml_data(config)

        # Write YAML file with proper formatting
        with open(config_path, "w", encoding="utf-8") as f:
            _write_yaml_header(f)
            _write_hosts_section(f, yaml_data["hosts"])

        logger.info("Configuration saved", path=str(config_path), hosts=len(config.hosts))

    except Exception as e:
        logger.error("Failed to save configuration", path=str(config_path), error=str(e))
        raise ValueError(f"Failed to save configuration to {config_path}: {e}") from e


def _build_yaml_data(config: DockerMCPConfig) -> dict[str, Any]:
    """Build YAML data structure from configuration."""
    yaml_data: dict[str, Any] = {"hosts": {}}

    for host_id, host_config in config.hosts.items():
        yaml_data["hosts"][host_id] = _build_host_data(host_config)

    return yaml_data


def _build_host_data(host_config: DockerHost) -> dict[str, Any]:
    """Build host data dictionary with non-default values."""
    host_data = {"hostname": host_config.hostname, "user": host_config.user}

    # Only include non-default values
    if host_config.port != 22:
        host_data["port"] = host_config.port

    if host_config.identity_file:
        host_data["identity_file"] = host_config.identity_file

    if host_config.description:
        host_data["description"] = host_config.description

    if host_config.tags:
        host_data["tags"] = host_config.tags

    if host_config.compose_path:
        host_data["compose_path"] = host_config.compose_path

    if not host_config.enabled:
        host_data["enabled"] = host_config.enabled

    return host_data


def _write_yaml_header(f) -> None:
    """Write file header comments."""
    f.write("# FastMCP Docker Context Manager Configuration\n")
    f.write("# Copy from hosts.example.yml and customize for your environment\n")
    f.write("\n")
    f.write(
        "# Server settings are configured via .env file (FASTMCP_HOST, FASTMCP_PORT, LOG_LEVEL)\n"
    )
    f.write("# Docker contexts handle all SSH connection management automatically\n")
    f.write("\n")


def _write_hosts_section(f, hosts_data: dict[str, Any]) -> None:
    """Write hosts section to YAML file."""
    f.write("hosts:\n")
    for host_id, host_data in hosts_data.items():
        f.write(f"  {host_id}:\n")
        for key, value in host_data.items():
            _write_yaml_value(f, key, value)
        f.write("\n")


def _write_yaml_value(f, key: str, value: Any) -> None:
    """Format and write a single YAML value."""
    if isinstance(value, str):
        f.write(f"    {key}: {value}\n")
    elif isinstance(value, int):
        f.write(f"    {key}: {value}\n")
    elif isinstance(value, bool):
        f.write(f"    {key}: {value}\n")
    elif isinstance(value, list):
        f.write(f"    {key}: {yaml.dump(value, default_flow_style=True).strip()}\n")


def _merge_config(base: dict[str, Any], update: dict[str, Any]) -> None:
    """Merge configuration dictionaries with deep merging."""
    for key, value in update.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge_config(base[key], value)
        else:
            base[key] = value

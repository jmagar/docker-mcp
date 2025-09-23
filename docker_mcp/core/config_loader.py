"""Configuration management for Docker MCP server."""

import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml
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


class CleanupSchedule(BaseModel):
    """Cleanup schedule configuration."""

    host_id: str
    cleanup_type: Literal["safe", "moderate"]  # Only safe and moderate for scheduling
    frequency: Literal["daily", "weekly", "monthly", "custom"]
    time: str  # HH:MM (24h)
    enabled: bool = True
    log_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ServerConfig(BaseModel):
    """Server configuration."""

    host: str = Field(
        default="127.0.0.1", alias="FASTMCP_HOST"
    )  # Use 0.0.0.0 for container deployment
    port: int = Field(default=8000, alias="FASTMCP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_connections: int = 10


class DockerMCPConfig(BaseSettings):
    """Main configuration for Docker MCP server."""

    hosts: dict[str, DockerHost] = Field(default_factory=dict)
    cleanup_schedules: dict[str, CleanupSchedule] = Field(default_factory=dict)
    server: ServerConfig = Field(default_factory=ServerConfig)
    config_file: str = Field(default="config/hosts.yml", alias="DOCKER_HOSTS_CONFIG")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def load_config(config_path: str | None = None) -> DockerMCPConfig:
    """Load configuration from multiple sources (synchronous interface).

    Args:
        config_path: Optional path to YAML config file

    Returns:
        Loaded configuration

    Note:
        This function safely handles both sync and async contexts.
        For async code, use load_config_async() instead.
    """
    try:
        # Check if we're already in an event loop
        asyncio.get_running_loop()
        # If we get here, there's a running loop - we can't use asyncio.run()
        raise RuntimeError(
            "load_config() cannot be called from within an async context. "
            "Use 'await load_config_async()' instead."
        )
    except RuntimeError as e:
        if "no running event loop" in str(e).lower():
            # Safe to use asyncio.run() - no existing loop
            return asyncio.run(load_config_async(config_path))
        else:
            # Re-raise the specific error about being in async context
            raise


async def load_config_async(config_path: str | None = None) -> DockerMCPConfig:
    """Load configuration from multiple sources (async interface).

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
    await _load_config_file(config, user_config_path)

    # Load project config (from env var or default)
    from ..server import get_config_dir  # Import at use to avoid circular imports

    default_config_file = os.getenv("DOCKER_HOSTS_CONFIG", str(get_config_dir() / "hosts.yml"))
    project_config_path = Path(config_path or default_config_file)
    await _load_config_file(config, project_config_path)

    # Set the actual config file path on the config object
    config.config_file = str(project_config_path)

    # Override with environment variables (highest priority)
    _apply_env_overrides(config)

    return config


async def _load_config_file(config: DockerMCPConfig, config_path: Path) -> None:
    """Load and apply configuration from a YAML file."""
    if not config_path.exists():
        return

    yaml_config = await _load_yaml_config(config_path)
    _apply_host_config(config, yaml_config)
    _apply_server_config(config, yaml_config)
    _apply_cleanup_schedules(config, yaml_config)


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


def _apply_cleanup_schedules(config: DockerMCPConfig, yaml_config: dict[str, Any]) -> None:
    """Apply cleanup schedules from YAML data."""
    schedules = yaml_config.get("cleanup_schedules")
    if not schedules:
        return
    config.cleanup_schedules = {
        schedule_id: CleanupSchedule(**sched_data) for schedule_id, sched_data in schedules.items()
    }


def _apply_env_overrides(config: DockerMCPConfig) -> None:
    """Apply environment variable overrides."""
    if os.getenv("FASTMCP_HOST"):
        config.server.host = os.getenv("FASTMCP_HOST", config.server.host)
    if port_env := os.getenv("FASTMCP_PORT"):
        config.server.port = int(port_env)
    if os.getenv("LOG_LEVEL"):
        config.server.log_level = os.getenv("LOG_LEVEL", config.server.log_level)


async def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    """Load YAML configuration file."""
    try:
        content = await asyncio.to_thread(config_path.read_text)

        # Securely expand only allowed environment variables
        content = _expand_yaml_config(content)

        loaded = yaml.safe_load(content)
        # Ensure we always return a dict (yaml.safe_load can return None, str, list, etc.)
        if not isinstance(loaded, dict):
            return {}
        return loaded
    except Exception as e:
        raise ValueError(f"Failed to load config from {config_path}: {e}") from e


def _expand_yaml_config(content: str) -> str:
    """Securely expand environment variables with allowlist."""

    # Define allowed environment variables for Docker MCP config
    allowed_env_vars = {
        "HOME",
        "USER",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "DOCKER_HOSTS_CONFIG",
        "DOCKER_MCP_CONFIG_DIR",
        "FASTMCP_HOST",
        "FASTMCP_PORT",
        "LOG_LEVEL",
        "SSH_CONFIG_PATH",
        "COMPOSE_PATH",
        "APPDATA_PATH",
    }

    def replace_var(match):
        var_name = match.group(1)
        if var_name in allowed_env_vars:
            return os.getenv(var_name, f"${{{var_name}}}")  # Keep original if not found
        else:
            logger.warning(
                f"Environment variable ${{{var_name}}} not in allowlist, skipping expansion"
            )
            return match.group(0)  # Return original unexpanded

    def replace_if_allowed(match):
        """Helper to conditionally replace $VAR pattern based on allowlist."""
        var_name = match.group(1)
        original_pattern = match.group(0)  # Extract for clarity

        if var_name in allowed_env_vars:
            return os.getenv(var_name, original_pattern)  # Keep original if not found
        else:
            logger.warning(
                "Environment variable not in allowlist, skipping expansion",
                variable=var_name,
                pattern=original_pattern
            )
            return original_pattern  # Return original unexpanded

    # Replace ${VAR} and $VAR patterns with allowlist check
    content = re.sub(r"\$\{([^}]+)\}", replace_var, content)
    content = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", replace_if_allowed, content)

    return content


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
            _write_cleanup_schedules_section(f, yaml_data["cleanup_schedules"])

        logger.info("Configuration saved", path=str(config_path), hosts=len(config.hosts))

    except Exception as e:
        logger.error("Failed to save configuration", path=str(config_path), error=str(e))
        raise ValueError(f"Failed to save configuration to {config_path}: {e}") from e


def _build_yaml_data(config: DockerMCPConfig) -> dict[str, Any]:
    """Build YAML data structure from configuration."""
    yaml_data: dict[str, Any] = {"hosts": {}, "cleanup_schedules": {}}

    for host_id, host_config in config.hosts.items():
        yaml_data["hosts"][host_id] = _build_host_data(host_config)

    # Persist schedules as plain dicts
    if getattr(config, "cleanup_schedules", None):
        for sched_id, sched in config.cleanup_schedules.items():
            yaml_data["cleanup_schedules"][sched_id] = (
                sched.model_dump() if hasattr(sched, "model_dump") else dict(sched)
            )

    return yaml_data


def _build_host_data(host_config: DockerHost) -> dict[str, Any]:
    """Build host data dictionary with non-default values."""
    # Start with required fields
    host_data = {"hostname": host_config.hostname, "user": host_config.user}

    # Define conditional fields with their conditions
    conditional_fields = [
        ("port", host_config.port, host_config.port != 22),
        ("identity_file", host_config.identity_file, bool(host_config.identity_file)),
        ("description", host_config.description, bool(host_config.description)),
        ("tags", host_config.tags, bool(host_config.tags)),
        ("compose_path", host_config.compose_path, bool(host_config.compose_path)),
        ("docker_context", host_config.docker_context, bool(host_config.docker_context)),
        ("appdata_path", host_config.appdata_path, bool(host_config.appdata_path)),
        ("enabled", host_config.enabled, not host_config.enabled),
    ]

    # Add fields that meet their conditions
    for field_name, field_value, condition in conditional_fields:
        if condition:
            host_data[field_name] = field_value

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


def _write_cleanup_schedules_section(f, schedules: dict[str, Any]) -> None:
    """Write cleanup_schedules section to YAML file."""
    f.write("cleanup_schedules:\n")
    if not schedules:
        f.write("  {}\n")
        return
    # Use safe_dump for nested mapping serialization
    dumped = yaml.safe_dump(schedules, default_flow_style=False, sort_keys=False, indent=2)
    # Indent by two spaces under the section key
    indented = "".join(f"  {line}" for line in dumped.splitlines(True))
    f.write(indented)


def _write_yaml_value(f, key: str, value: Any) -> None:
    """Format and write a single YAML value."""
    if isinstance(value, str):
        f.write(f"    {key}: {value}\n")
    elif isinstance(value, int):
        f.write(f"    {key}: {value}\n")
    elif isinstance(value, bool):
        f.write(f"    {key}: {value}\n")
    elif isinstance(value, list):
        f.write(f"    {key}: {yaml.safe_dump(value, default_flow_style=True).strip()}\n")
    elif isinstance(value, dict):
        dumped = yaml.safe_dump(value, default_flow_style=False, sort_keys=False, indent=2).rstrip()
        indented = "".join(f"      {line}" for line in dumped.splitlines(True))
        f.write(f"    {key}:\n{indented}\n")
    elif value is None:
        f.write(f"    {key}: null\n")


def _merge_config(base: dict[str, Any], update: dict[str, Any]) -> None:
    """Merge configuration dictionaries with deep merging."""
    for key, value in update.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge_config(base[key], value)
        else:
            base[key] = value

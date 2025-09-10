"""Timeout settings configuration for Docker MCP operations.

Provides centralized timeout configuration using Pydantic BaseSettings
with environment variable support for operational tuning.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DockerTimeoutSettings(BaseSettings):
    """Docker operation timeout configuration."""

    docker_client_timeout: int = Field(
        30, alias="DOCKER_CLIENT_TIMEOUT", description="Docker SDK client timeout in seconds"
    )

    docker_cli_timeout: int = Field(
        60, alias="DOCKER_CLI_TIMEOUT", description="Docker CLI command timeout in seconds"
    )

    subprocess_timeout: int = Field(
        120, alias="SUBPROCESS_TIMEOUT", description="General subprocess timeout in seconds"
    )

    archive_timeout: int = Field(
        300, alias="ARCHIVE_TIMEOUT", description="Archive operations timeout in seconds"
    )

    rsync_timeout: int = Field(
        600, alias="RSYNC_TIMEOUT", description="Rsync transfer timeout in seconds"
    )

    backup_timeout: int = Field(
        300, alias="BACKUP_TIMEOUT", description="Backup operations timeout in seconds"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Global settings instance
timeout_settings = DockerTimeoutSettings(
    DOCKER_CLIENT_TIMEOUT=30,
    DOCKER_CLI_TIMEOUT=60,
    SUBPROCESS_TIMEOUT=120,
    ARCHIVE_TIMEOUT=300,
    RSYNC_TIMEOUT=600,
    BACKUP_TIMEOUT=300,
)

# Timeout constants for easy import
DOCKER_CLIENT_TIMEOUT: int = timeout_settings.docker_client_timeout
DOCKER_CLI_TIMEOUT: int = timeout_settings.docker_cli_timeout
SUBPROCESS_TIMEOUT: int = timeout_settings.subprocess_timeout
ARCHIVE_TIMEOUT: int = timeout_settings.archive_timeout
RSYNC_TIMEOUT: int = timeout_settings.rsync_timeout
BACKUP_TIMEOUT: int = timeout_settings.backup_timeout

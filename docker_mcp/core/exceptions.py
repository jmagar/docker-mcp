"""Core exceptions for Docker MCP operations."""


class DockerMCPError(Exception):
    """Base exception for Docker MCP operations."""


class DockerCommandError(DockerMCPError):
    """Docker command execution failed."""


class DockerContextError(DockerMCPError):
    """Docker context operation failed."""


class ConfigurationError(DockerMCPError):
    """Configuration validation or loading failed."""


# Removed unused HostNotFoundError to reduce unused exceptions surface.

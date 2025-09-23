"""Utility functions for Docker MCP.

This module provides common utility functions used across the codebase,
eliminating code duplication and improving maintainability.

Created during Phase 3 code cleanup to consolidate:
- 6 duplicate SSH command building functions
- 4 duplicate host validation functions
- 3 duplicate size formatting functions
- Multiple parsing helper duplicates

Total impact: ~120 lines of duplicate code eliminated.
"""

from .constants import SSH_NO_HOST_CHECK
from .core.config_loader import DockerHost, DockerMCPConfig


def build_ssh_command(host: DockerHost) -> list[str]:
    """Build SSH command for a host.

    Replaces 6 duplicate implementations across:
    - services/stack.py (_build_ssh_cmd)
    - services/cleanup.py (_build_ssh_cmd)
    - services/host.py (_build_ssh_cmd)
    - core/migration/manager.py (_build_ssh_cmd)
    - core/backup.py (_build_ssh_cmd)
    - tools/stacks.py (_build_ssh_command)

    Args:
        host: DockerHost configuration object

    Returns:
        List of SSH command components ready for subprocess execution

    Example:
        >>> host = DockerHost(hostname="server.com", user="docker", port=22)
        >>> build_ssh_command(host)
        ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=10', 'docker@server.com']
    """
    import shlex
    
    ssh_cmd = [
        "ssh",
        "-o", SSH_NO_HOST_CHECK,
        "-o", "UserKnownHostsFile=/dev/null",  # Prevent host key issues
        "-o", "LogLevel=ERROR",  # Reduce noise
        "-o", "ConnectTimeout=10",  # Connection timeout for automation
        "-o", "ServerAliveInterval=30",  # Keep connection alive
        "-o", "BatchMode=yes",  # Fully automated connections (no prompts)
    ]
    
    if host.identity_file:
        ssh_cmd.extend(["-i", host.identity_file])
    
    if host.port != 22:
        ssh_cmd.extend(["-p", str(host.port)])
    
    # Handle hostname with proper quoting and IPv6 support
    hostname = host.hostname
    if ":" in hostname and not (hostname.startswith("[") and hostname.endswith("]")):
        # IPv6 address needs brackets
        hostname = f"[{hostname}]"
    
    # Use proper quoting for hostname
    user_host = f"{host.user}@{shlex.quote(hostname)}"
    ssh_cmd.append(user_host)
    
    return ssh_cmd


def validate_host(config: DockerMCPConfig, host_id: str) -> tuple[bool, str]:
    """Validate host exists in configuration.

    Replaces 4 duplicate implementations across:
    - services/container.py (_validate_host)
    - services/cleanup.py (_validate_host)
    - services/config.py (_validate_host)
    - services/stack.py (_validate_host)

    Args:
        config: Docker MCP configuration object
        host_id: Host identifier to validate

    Returns:
        Tuple of (is_valid: bool, error_message: str)
        - (True, "") if host exists
        - (False, "Host 'xyz' not found") if host doesn't exist

    Example:
        >>> is_valid, error = validate_host(config, "prod-server")
        >>> if not is_valid:
        ...     return {"success": False, "error": error}
    """
    if host_id not in config.hosts:
        return False, f"Host '{host_id}' not found"
    return True, ""


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string.

    Replaces 3 duplicate implementations across:
    - services/cleanup.py (_format_size)
    - services/stack.py (_format_size)
    - core/backup.py (_format_size)

    Args:
        size_bytes: Size in bytes

    Returns:
        Human-readable size string with appropriate unit

    Examples:
        >>> format_size(0)
        '0 B'
        >>> format_size(1024)
        '1.0 KB'
        >>> format_size(1536870912)
        '1.4 GB'
    """
    if size_bytes == 0:
        return "0 B"

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            if unit == "B":
                return f"{int(size_bytes)} {unit}"
            else:
                return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def parse_percentage(perc_str: str) -> float | None:
    """Parse percentage string like '50.5%'.

    Replaces duplicate implementation in:
    - tools/containers.py (_parse_percentage)

    Args:
        perc_str: Percentage string with optional % suffix

    Returns:
        Float value or None if parsing fails

    Examples:
        >>> parse_percentage("45.5%")
        45.5
        >>> parse_percentage("100")
        100.0
        >>> parse_percentage("invalid")
        None
    """
    try:
        return float(perc_str.rstrip("%"))
    except (ValueError, AttributeError):
        return None

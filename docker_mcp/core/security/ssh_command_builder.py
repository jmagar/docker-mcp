"""Secure SSH command builder with injection protection."""

import re
import shlex
from pathlib import Path
from typing import Any
import hashlib
import time
from collections import defaultdict
from datetime import datetime, timedelta

from docker_mcp.core.exceptions import DockerMCPError

class SSHSecurityError(DockerMCPError):
    """SSH security-related error."""
    pass


class SSHCommandBuilder:
    """Builds secure SSH commands with injection protection."""
    
    # Strict validation patterns
    VALID_HOSTNAME_PATTERN = re.compile(
        r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$'
    )
    VALID_USERNAME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,31}$')
    VALID_PATH_PATTERN = re.compile(r'^[a-zA-Z0-9/_.\-]+$')
    VALID_STACK_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$')
    VALID_ENV_VAR_NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,63}$')
    
    # Dangerous patterns to block
    DANGEROUS_PATTERNS = [
        r'\$\(',  # Command substitution
        r'`',     # Backticks
        r'&&',    # Command chaining
        r'\|\|',  # OR operator
        r';',     # Command separator
        r'\|',    # Pipe
        r'>>?',   # Redirect
        r'<',     # Input redirect
        r'\*',    # Glob
        r'\?',    # Glob
        r'\[',    # Glob
        r'~',     # Home expansion
        r'\.\.',  # Parent directory
    ]
    
    # Allowed Docker commands
    ALLOWED_DOCKER_COMMANDS = {
        'ps', 'logs', 'start', 'stop', 'restart', 'stats',
        'compose', 'pull', 'build', 'inspect', 'images',
        'exec', 'run', 'rm', 'kill', 'pause', 'unpause'
    }
    
    # Allowed compose subcommands
    ALLOWED_COMPOSE_SUBCOMMANDS = {
        'up', 'down', 'ps', 'logs', 'build', 'pull',
        'restart', 'stop', 'start', 'exec', 'run'
    }
    
    def __init__(self, max_command_length: int = 4096):
        """Initialize the SSH command builder.
        
        Args:
            max_command_length: Maximum allowed command length
        """
        self.max_command_length = max_command_length
        self._dangerous_regex = re.compile('|'.join(self.DANGEROUS_PATTERNS))
    
    def validate_hostname(self, hostname: str) -> str:
        """Validate and sanitize hostname.
        
        Args:
            hostname: The hostname to validate
            
        Returns:
            Validated hostname
            
        Raises:
            SSHSecurityError: If hostname is invalid
        """
        if not hostname or len(hostname) > 253:
            raise SSHSecurityError(f"Invalid hostname length: {len(hostname)}")
        
        # Check for IP address (simplified check)
        if re.match(r'^(\d{1,3}\.){3}\d{1,3}$', hostname):
            parts = hostname.split('.')
            if all(0 <= int(part) <= 255 for part in parts):
                return hostname
        
        # Validate as domain name
        if not self.VALID_HOSTNAME_PATTERN.match(hostname):
            raise SSHSecurityError(f"Invalid hostname format: {hostname}")
        
        return hostname
    
    def validate_username(self, username: str) -> str:
        """Validate and sanitize username.
        
        Args:
            username: The username to validate
            
        Returns:
            Validated username
            
        Raises:
            SSHSecurityError: If username is invalid
        """
        if not username or not self.VALID_USERNAME_PATTERN.match(username):
            raise SSHSecurityError(f"Invalid username: {username}")
        return username
    
    def validate_port(self, port: int) -> int:
        """Validate SSH port.
        
        Args:
            port: The port number to validate
            
        Returns:
            Validated port
            
        Raises:
            SSHSecurityError: If port is invalid
        """
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise SSHSecurityError(f"Invalid port: {port}")
        return port
    
    def validate_path(self, path: str, allow_relative: bool = False) -> str:
        """Validate and sanitize file path.
        
        Args:
            path: The path to validate
            allow_relative: Whether to allow relative paths
            
        Returns:
            Validated path
            
        Raises:
            SSHSecurityError: If path is invalid or contains dangerous patterns
        """
        if not path:
            raise SSHSecurityError("Empty path provided")
        
        # Check for dangerous patterns
        if self._dangerous_regex.search(path):
            raise SSHSecurityError(f"Path contains dangerous patterns: {path}")
        
        # Normalize path
        try:
            normalized = Path(path).as_posix()
        except Exception as e:
            raise SSHSecurityError(f"Invalid path format: {e}")
        
        # Check for parent directory traversal
        if '..' in normalized:
            raise SSHSecurityError("Path traversal detected")
        
        # Ensure absolute path if required
        if not allow_relative and not normalized.startswith('/'):
            raise SSHSecurityError(f"Absolute path required: {normalized}")
        
        # Additional validation
        if len(normalized) > 4096:
            raise SSHSecurityError("Path too long")
        
        return normalized
    
    def validate_stack_name(self, stack_name: str) -> str:
        """Validate Docker stack name.
        
        Args:
            stack_name: The stack name to validate
            
        Returns:
            Validated stack name
            
        Raises:
            SSHSecurityError: If stack name is invalid
        """
        if not stack_name or not self.VALID_STACK_NAME_PATTERN.match(stack_name):
            raise SSHSecurityError(f"Invalid stack name: {stack_name}")
        
        # Check reserved names
        reserved = {'docker', 'compose', 'system', 'network', 'volume', 'config'}
        if stack_name.lower() in reserved:
            raise SSHSecurityError(f"Reserved stack name: {stack_name}")
        
        return stack_name
    
    def validate_docker_command(self, command: str) -> str:
        """Validate Docker command.
        
        Args:
            command: The Docker command to validate
            
        Returns:
            Validated command
            
        Raises:
            SSHSecurityError: If command is not allowed
        """
        if command not in self.ALLOWED_DOCKER_COMMANDS:
            raise SSHSecurityError(f"Docker command not allowed: {command}")
        return command
    
    def validate_compose_subcommand(self, subcommand: str) -> str:
        """Validate Docker Compose subcommand.
        
        Args:
            subcommand: The compose subcommand to validate
            
        Returns:
            Validated subcommand
            
        Raises:
            SSHSecurityError: If subcommand is not allowed
        """
        if subcommand not in self.ALLOWED_COMPOSE_SUBCOMMANDS:
            raise SSHSecurityError(f"Compose subcommand not allowed: {subcommand}")
        return subcommand
    
    def validate_environment_variable(self, key: str, value: str) -> tuple[str, str]:
        """Validate environment variable.
        
        Args:
            key: Environment variable name
            value: Environment variable value
            
        Returns:
            Validated (key, value) tuple
            
        Raises:
            SSHSecurityError: If environment variable is invalid
        """
        # Validate key
        if not key or not self.VALID_ENV_VAR_NAME.match(key):
            raise SSHSecurityError(f"Invalid environment variable name: {key}")
        
        # Validate value - escape dangerous characters
        if self._dangerous_regex.search(value):
            raise SSHSecurityError(f"Environment value contains dangerous patterns: {value}")
        
        # Length check
        if len(value) > 32768:
            raise SSHSecurityError("Environment value too long")
        
        return key, value
    
    def build_ssh_base_command(
        self,
        hostname: str,
        username: str,
        port: int = 22,
        identity_file: str | None = None,
        extra_options: dict[str, str] | None = None
    ) -> list[str]:
        """Build base SSH command with security options.
        
        Args:
            hostname: Target hostname
            username: SSH username
            port: SSH port (default: 22)
            identity_file: Path to SSH identity file
            extra_options: Additional SSH options
            
        Returns:
            List of SSH command arguments
            
        Raises:
            SSHSecurityError: If any parameter is invalid
        """
        # Validate all inputs
        hostname = self.validate_hostname(hostname)
        username = self.validate_username(username)
        port = self.validate_port(port)
        
        cmd = ['ssh']
        
        # Add security options first
        security_options = [
            '-o', 'StrictHostKeyChecking=yes',  # Changed from 'no' for security
            '-o', 'UserKnownHostsFile=/etc/ssh/ssh_known_hosts',  # Use system known hosts
            '-o', 'LogLevel=ERROR',
            '-o', 'PasswordAuthentication=no',  # Force key-based auth
            '-o', 'PreferredAuthentications=publickey',
            '-o', 'BatchMode=yes',  # Fail instead of prompting
            '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=60',
            '-o', 'ServerAliveCountMax=3',
            '-o', 'ControlMaster=auto',  # Connection pooling
            '-o', 'ControlPath=/tmp/ssh-%r@%h:%p',
            '-o', 'ControlPersist=10m',
        ]
        cmd.extend(security_options)
        
        # Add port if not default
        if port != 22:
            cmd.extend(['-p', str(port)])
        
        # Add identity file if specified
        if identity_file:
            validated_path = self.validate_path(identity_file)
            cmd.extend(['-i', validated_path])
        
        # Add extra options if provided
        if extra_options:
            for key, value in extra_options.items():
                if not re.match(r'^[A-Za-z]+$', key):
                    raise SSHSecurityError(f"Invalid SSH option: {key}")
                cmd.extend(['-o', f'{key}={value}'])
        
        # Add target
        cmd.append(f'{username}@{hostname}')
        
        return cmd
    
    def build_docker_compose_command(
        self,
        project_name: str,
        compose_file: str,
        subcommand: str,
        args: list[str] | None = None,
        environment: dict[str, str] | None = None
    ) -> str:
        """Build secure Docker Compose command.
        
        Args:
            project_name: Docker Compose project name
            compose_file: Path to compose file
            subcommand: Compose subcommand (up, down, etc.)
            args: Additional arguments for the subcommand
            environment: Environment variables
            
        Returns:
            Shell-escaped command string
            
        Raises:
            SSHSecurityError: If any parameter is invalid
        """
        # Validate inputs
        project_name = self.validate_stack_name(project_name)
        compose_file = self.validate_path(compose_file)
        subcommand = self.validate_compose_subcommand(subcommand)
        
        # Build command parts
        cmd_parts = [
            'docker', 'compose',
            '--project-name', shlex.quote(project_name),
            '-f', shlex.quote(compose_file),
            subcommand
        ]
        
        # Add arguments with validation
        if args:
            for arg in args:
                # Validate common compose arguments
                if arg.startswith('-'):
                    if arg not in ['--build', '--force-recreate', '--no-deps', '--detach', '-d', 
                                  '--remove-orphans', '--pull', '--quiet-pull', '--no-start']:
                        raise SSHSecurityError(f"Compose argument not allowed: {arg}")
                    cmd_parts.append(arg)
                else:
                    # Service names - validate as stack names
                    validated = self.validate_stack_name(arg)
                    cmd_parts.append(shlex.quote(validated))
        
        # Build final command with environment
        if environment:
            env_parts = []
            for key, value in environment.items():
                key, value = self.validate_environment_variable(key, value)
                env_parts.append(f'{key}={shlex.quote(value)}')
            return ' '.join(env_parts + cmd_parts)
        
        return ' '.join(cmd_parts)
    
    def build_remote_command(
        self,
        working_directory: str,
        command_parts: list[str],
        environment: dict[str, str] | None = None
    ) -> str:
        """Build secure remote command with proper escaping.
        
        Args:
            working_directory: Directory to execute command in
            command_parts: Parts of the command to execute
            environment: Environment variables
            
        Returns:
            Shell-escaped command string
            
        Raises:
            SSHSecurityError: If command would be too long or contains dangerous patterns
        """
        # Validate working directory
        working_directory = self.validate_path(working_directory)
        
        # Build cd command
        cd_part = f'cd {shlex.quote(working_directory)}'
        
        # Build environment variables
        env_parts = []
        if environment:
            for key, value in environment.items():
                key, value = self.validate_environment_variable(key, value)
                env_parts.append(f'{key}={shlex.quote(value)}')
        
        # Validate and escape command parts
        escaped_parts = []
        for part in command_parts:
            # Check for dangerous patterns in raw part
            if self._dangerous_regex.search(part):
                raise SSHSecurityError(f"Command part contains dangerous patterns: {part}")
            escaped_parts.append(shlex.quote(part))
        
        # Build final command
        if env_parts:
            command = f"{cd_part} && {' '.join(env_parts)} {' '.join(escaped_parts)}"
        else:
            command = f"{cd_part} && {' '.join(escaped_parts)}"
        
        # Check total length
        if len(command) > self.max_command_length:
            raise SSHSecurityError(f"Command too long: {len(command)} > {self.max_command_length}")
        
        return command


class SSHRateLimiter:
    """Rate limiter for SSH operations."""
    
    def __init__(
        self,
        max_requests_per_minute: int = 60,
        max_requests_per_hour: int = 600,
        max_concurrent: int = 10
    ):
        """Initialize rate limiter.
        
        Args:
            max_requests_per_minute: Maximum requests per minute per host
            max_requests_per_hour: Maximum requests per hour per host
            max_concurrent: Maximum concurrent SSH connections per host
        """
        self.max_requests_per_minute = max_requests_per_minute
        self.max_requests_per_hour = max_requests_per_hour
        self.max_concurrent = max_concurrent
        
        # Track requests per host
        self._minute_requests: dict[str, list[float]] = defaultdict(list)
        self._hour_requests: dict[str, list[float]] = defaultdict(list)
        self._concurrent: dict[str, int] = defaultdict(int)
    
    def check_rate_limit(self, host_id: str) -> tuple[bool, str]:
        """Check if request is within rate limits.
        
        Args:
            host_id: Host identifier
            
        Returns:
            (allowed, reason) tuple
        """
        now = time.time()
        
        # Clean old entries
        self._clean_old_entries(host_id, now)
        
        # Check concurrent connections
        if self._concurrent[host_id] >= self.max_concurrent:
            return False, f"Maximum concurrent connections ({self.max_concurrent}) reached"
        
        # Check per-minute limit
        minute_count = len(self._minute_requests[host_id])
        if minute_count >= self.max_requests_per_minute:
            return False, f"Rate limit exceeded: {minute_count}/{self.max_requests_per_minute} per minute"
        
        # Check per-hour limit
        hour_count = len(self._hour_requests[host_id])
        if hour_count >= self.max_requests_per_hour:
            return False, f"Rate limit exceeded: {hour_count}/{self.max_requests_per_hour} per hour"
        
        return True, ""
    
    def record_request(self, host_id: str) -> None:
        """Record a new request.
        
        Args:
            host_id: Host identifier
        """
        now = time.time()
        self._minute_requests[host_id].append(now)
        self._hour_requests[host_id].append(now)
        self._concurrent[host_id] += 1
    
    def release_connection(self, host_id: str) -> None:
        """Release a connection.
        
        Args:
            host_id: Host identifier
        """
        if self._concurrent[host_id] > 0:
            self._concurrent[host_id] -= 1
    
    def _clean_old_entries(self, host_id: str, now: float) -> None:
        """Clean old request entries.
        
        Args:
            host_id: Host identifier
            now: Current timestamp
        """
        # Clean minute entries older than 60 seconds
        minute_cutoff = now - 60
        self._minute_requests[host_id] = [
            t for t in self._minute_requests[host_id] if t > minute_cutoff
        ]
        
        # Clean hour entries older than 3600 seconds
        hour_cutoff = now - 3600
        self._hour_requests[host_id] = [
            t for t in self._hour_requests[host_id] if t > hour_cutoff
        ]


class SSHAuditLog:
    """Audit logger for SSH operations."""
    
    def __init__(self, log_file: str | None = None):
        """Initialize audit logger.
        
        Args:
            log_file: Path to audit log file
        """
        self.log_file = log_file
    
    def log_command(
        self,
        host_id: str,
        username: str,
        command: str,
        result: str | None = None,
        error: str | None = None
    ) -> None:
        """Log SSH command execution.
        
        Args:
            host_id: Host identifier
            username: SSH username
            command: Command executed
            result: Command result (if successful)
            error: Error message (if failed)
        """
        entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'host_id': host_id,
            'username': username,
            'command_hash': hashlib.sha256(command.encode()).hexdigest()[:16],
            'command_length': len(command),
            'success': error is None,
            'error': error
        }
        
        if self.log_file:
            import json
            try:
                with open(self.log_file, 'a') as f:
                    f.write(json.dumps(entry) + '\n')
            except Exception:
                pass  # Silently fail audit logging
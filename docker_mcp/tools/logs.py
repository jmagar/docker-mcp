"""Log streaming MCP tools."""

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import docker
import structlog

from ..core.config_loader import DockerMCPConfig
from ..core.docker_context import DockerContextManager
from ..core.error_response import DockerMCPErrorResponse, create_success_response
from ..core.exceptions import DockerCommandError, DockerContextError
from ..models.container import ContainerLogs, LogStreamRequest

logger = structlog.get_logger()


class LogTools:
    """Log management tools for MCP."""

    def __init__(self, config: DockerMCPConfig, context_manager: DockerContextManager):
        self.config = config
        self.context_manager = context_manager
        self._init_log_sanitization_patterns()

    def _init_log_sanitization_patterns(self) -> None:
        """Initialize regex patterns for sanitizing sensitive information from logs."""
        self.sanitization_patterns = [
            # API Keys and tokens
            {
                "pattern": re.compile(r'\b[Aa]pi[_-]?[Kk]ey["\s]*[:=]["\s]*([a-zA-Z0-9\-_]{16,})', re.IGNORECASE),
                "replacement": r'api_key="[REDACTED_API_KEY]"',
                "description": "API keys"
            },
            {
                "pattern": re.compile(r'\b[Tt]oken["\s]*[:=]["\s]*([a-zA-Z0-9\-_\.]{20,})', re.IGNORECASE),
                "replacement": r'token="[REDACTED_TOKEN]"',
                "description": "Authentication tokens"
            },
            {
                "pattern": re.compile(r'\b[Bb]earer\s+([a-zA-Z0-9\-_\.]{20,})', re.IGNORECASE),
                "replacement": r'Bearer [REDACTED_BEARER_TOKEN]',
                "description": "Bearer tokens"
            },
            # Password patterns
            {
                "pattern": re.compile(r'\b[Pp]assword["\s]*[:=]["\s]*([^\s"\']{6,})', re.IGNORECASE),
                "replacement": r'password="[REDACTED_PASSWORD]"',
                "description": "Passwords"
            },
            {
                "pattern": re.compile(r'\b[Pp]asswd["\s]*[:=]["\s]*([^\s"\']{6,})', re.IGNORECASE),
                "replacement": r'passwd="[REDACTED_PASSWORD]"',
                "description": "Password abbreviations"
            },
            # Secret patterns
            {
                "pattern": re.compile(r'\b[Ss]ecret["\s]*[:=]["\s]*([^\s"\']{8,})', re.IGNORECASE),
                "replacement": r'secret="[REDACTED_SECRET]"',
                "description": "Secrets"
            },
            # Database connection strings
            {
                "pattern": re.compile(r'([a-zA-Z]+://[^:]+:)([^@]+)(@[^\s]+)', re.IGNORECASE),
                "replacement": r'\1[REDACTED_DB_PASSWORD]\3',
                "description": "Database connection strings"
            },
            # Private keys (basic detection)
            {
                "pattern": re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----.*?-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----', re.DOTALL | re.IGNORECASE),
                "replacement": '[REDACTED_PRIVATE_KEY]',
                "description": "Private keys"
            },
            # AWS/Cloud credentials
            {
                "pattern": re.compile(r'\bAKIA[0-9A-Z]{16}\b', re.IGNORECASE),
                "replacement": '[REDACTED_AWS_ACCESS_KEY]',
                "description": "AWS access keys"
            },
            {
                "pattern": re.compile(r'\b[0-9a-zA-Z/+]{40}\b'),
                "replacement": '[REDACTED_AWS_SECRET_KEY]',
                "description": "AWS secret keys"
            },
            # Personal information patterns (basic)
            {
                "pattern": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
                "replacement": '[REDACTED_EMAIL]',
                "description": "Email addresses"
            },
            # Credit card numbers (basic detection)
            {
                "pattern": re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),
                "replacement": '[REDACTED_CARD_NUMBER]',
                "description": "Credit card numbers"
            },
            # JWT tokens
            {
                "pattern": re.compile(r'\beyJ[a-zA-Z0-9\-_\.]{20,}\b'),
                "replacement": '[REDACTED_JWT_TOKEN]',
                "description": "JWT tokens"
            }
        ]

    def _sanitize_log_content(self, log_content: list[str]) -> list[str]:
        """Sanitize log content to remove sensitive information.
        
        Args:
            log_content: List of log lines to sanitize
            
        Returns:
            List of sanitized log lines
        """
        if not log_content:
            return log_content
            
        sanitized_logs = []
        redaction_count = 0
        
        for line in log_content:
            if not line or not isinstance(line, str):
                sanitized_logs.append(line)
                continue
                
            sanitized_line = line
            line_redactions = []
            
            # Apply each sanitization pattern
            for pattern_info in self.sanitization_patterns:
                pattern = pattern_info["pattern"]
                replacement = pattern_info["replacement"]
                description = pattern_info["description"]
                
                # Count matches before replacement
                matches = pattern.findall(sanitized_line)
                if matches:
                    line_redactions.append(f"{len(matches)} {description}")
                    redaction_count += len(matches)
                    
                # Apply sanitization
                sanitized_line = pattern.sub(replacement, sanitized_line)
            
            sanitized_logs.append(sanitized_line)
            
            # Log redaction details (without the actual sensitive data)
            if line_redactions:
                logger.debug(
                    "Log line sanitized",
                    redactions=line_redactions,
                    original_length=len(line),
                    sanitized_length=len(sanitized_line)
                )
        
        if redaction_count > 0:
            # Count lines that were actually modified
            modified_lines = sum(1 for i, (original, sanitized) in enumerate(zip(log_content, sanitized_logs)) if original != sanitized)
            
            logger.info(
                "Log content sanitized",
                total_redactions=redaction_count,
                total_lines=len(log_content),
                modified_lines=modified_lines
            )
        
        return sanitized_logs

    def _build_error_response(
        self,
        host_id: str,
        operation: str,
        error_message: str,
        container_id: str | None = None,
        problem_type: str | None = None,
        **context,
    ) -> dict[str, Any]:
        """Build standardized error response with container context.

        Args:
            host_id: ID of the Docker host
            operation: Name of the operation that failed
            error_message: Error message to include
            container_id: Optional container ID for container-specific errors
            problem_type: Explicit error type specification for precise categorization.
                         Valid values: 'container_not_found', 'host_not_found',
                         'docker_context_error', 'generic'
            **context: Additional context to include in error response
        """
        base_context = {"host_id": host_id, "operation": operation}
        if container_id:
            base_context["container_id"] = container_id
        # Merge additional context parameters
        base_context.update(context)

        # Use explicit problem_type if provided for precise error categorization
        if problem_type == "container_not_found":
            return DockerMCPErrorResponse.container_not_found(host_id, container_id or "unknown")
        elif problem_type == "host_not_found":
            return DockerMCPErrorResponse.host_not_found(host_id)
        elif problem_type == "docker_context_error":
            return DockerMCPErrorResponse.docker_context_error(host_id, operation, error_message)
        elif problem_type == "generic":
            return DockerMCPErrorResponse.generic_error(error_message, base_context)

        # Fallback to pattern matching for backward compatibility when no explicit type provided
        if "not found" in error_message.lower():
            if container_id:
                return DockerMCPErrorResponse.container_not_found(host_id, container_id)
            else:
                return DockerMCPErrorResponse.host_not_found(host_id)
        elif "could not connect" in error_message.lower():
            return DockerMCPErrorResponse.docker_context_error(host_id, operation, error_message)
        else:
            return DockerMCPErrorResponse.generic_error(error_message, base_context)

    async def get_container_logs(
        self,
        host_id: str,
        container_id: str,
        lines: int = 100,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Get logs from a container.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name
            lines: Number of lines to retrieve (default: 100)
            since: Only logs since this timestamp (e.g., '2023-01-01T00:00:00Z')
            timestamps: Include timestamps in output

        Returns:
            Container logs
        """
        try:
            client = await self.context_manager.get_client(host_id)
            if client is None:
                return self._build_error_response(
                    host_id,
                    "get_container_logs",
                    f"Could not connect to Docker on host {host_id}",
                    container_id,
                    problem_type="docker_context_error",
                )

            # Get container and retrieve logs using Docker SDK
            container = await asyncio.to_thread(client.containers.get, container_id)

            # Build kwargs for logs method
            logs_kwargs = {
                "tail": lines,
                "timestamps": timestamps,
            }
            if since:
                try:
                    dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                    logs_kwargs["since"] = int(dt.timestamp())
                except Exception:
                    logs_kwargs["since"] = since  # fallback

            # Get logs using Docker SDK
            logs_bytes = await asyncio.to_thread(container.logs, **logs_kwargs)

            # Parse logs (logs_bytes is bytes, need to decode)
            logs_str = logs_bytes.decode("utf-8", errors="replace")
            logs_data = logs_str.strip().split("\n") if logs_str.strip() else []

            # Sanitize logs before returning
            sanitized_logs = self._sanitize_log_content(logs_data)

            # Create logs response
            logs = ContainerLogs(
                container_id=container_id,
                host_id=host_id,
                logs=sanitized_logs,
                timestamp=datetime.now(UTC),
                truncated=len(sanitized_logs) >= lines,
            )

            logger.info(
                "Retrieved container logs",
                host_id=host_id,
                container_id=container_id,
                lines_returned=len(sanitized_logs),
                sanitization_applied=len(sanitized_logs) != len(logs_data) or any(s != o for s, o in zip(sanitized_logs, logs_data)),
            )

            return create_success_response(
                data=logs.model_dump(),
                context={
                    "host_id": host_id,
                    "operation": "get_container_logs",
                    "container_id": container_id,
                },
            )

        except docker.errors.NotFound:
            logger.error("Container not found for logs", host_id=host_id, container_id=container_id)
            return self._build_error_response(
                host_id, "get_container_logs", f"Container {container_id} not found", container_id,
                problem_type="container_not_found"
            )
        except docker.errors.APIError as e:
            logger.error(
                "Docker API error getting container logs",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return self._build_error_response(
                host_id, "get_container_logs", f"Failed to get logs: {str(e)}", container_id
            )
        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get container logs",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return self._build_error_response(host_id, "get_container_logs", str(e), container_id)

    async def stream_container_logs_setup(
        self,
        host_id: str,
        container_id: str,
        follow: bool = True,
        tail: int = 100,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Setup real-time log streaming for a container.

        This creates a streaming endpoint that can be used for real-time log monitoring.
        The actual streaming is handled by FastMCP's HTTP streaming capabilities.

        Args:
            host_id: ID of the Docker host
            container_id: Container ID or name
            follow: Continue streaming new logs (default: True)
            tail: Number of initial lines to include
            since: Only logs since this timestamp
            timestamps: Include timestamps in output

        Returns:
            Streaming configuration and endpoint information
        """
        try:
            # Validate container exists and is accessible
            await self._validate_container_exists(host_id, container_id)

            # Create stream configuration
            stream_config = LogStreamRequest(
                host_id=host_id,
                container_id=container_id,
                follow=follow,
                tail=tail,
                since=since,
                timestamps=timestamps,
            )

            # In a real implementation, this would register the stream
            # with FastMCP's streaming system and return an endpoint URL
            stream_id = f"{host_id}_{container_id}_{uuid.uuid4().hex}"

            logger.info(
                "Log stream setup created",
                host_id=host_id,
                container_id=container_id,
                stream_id=stream_id,
            )

            return create_success_response(
                data={
                    "stream_id": stream_id,
                    "stream_endpoint": f"/streams/logs/{stream_id}",
                    "config": stream_config.model_dump(),
                    "message": f"Log stream setup for container {container_id} on host {host_id}",
                    "instructions": {
                        "connect": "Connect to the streaming endpoint to receive real-time logs",
                        "format": "Server-sent events (SSE)",
                        "reconnect": "Client should handle reconnection on connection loss",
                    },
                },
                context={
                    "host_id": host_id,
                    "operation": "stream_container_logs_setup",
                    "container_id": container_id,
                },
            )

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to setup log stream",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return self._build_error_response(
                host_id, "stream_container_logs_setup", str(e), container_id
            )

    async def get_service_logs(
        self,
        host_id: str,
        service_name: str,
        lines: int = 100,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Get logs from a Docker Compose service.

        Args:
            host_id: ID of the Docker host
            service_name: Name of the service
            lines: Number of lines to retrieve
            since: Only logs since this timestamp
            timestamps: Include timestamps in output

        Returns:
            Service logs
        """
        try:
            # Build Docker Compose logs command
            cmd = f"compose logs --tail {lines}"

            if since:
                cmd += f" --since {since}"

            if timestamps:
                cmd += " --timestamps"

            cmd += f" {service_name}"

            result = await self.context_manager.execute_docker_command(host_id, cmd)

            # Parse logs
            logs_data = []
            if isinstance(result, dict) and "output" in result:
                logs_data = result["output"].strip().split("\n")

            # Sanitize service logs before returning
            sanitized_logs = self._sanitize_log_content(logs_data)

            logger.info(
                "Retrieved service logs",
                host_id=host_id,
                service_name=service_name,
                lines_returned=len(sanitized_logs),
                sanitization_applied=len(sanitized_logs) != len(logs_data) or any(s != o for s, o in zip(sanitized_logs, logs_data)),
            )

            return create_success_response(
                data={
                    "service_name": service_name,
                    "host_id": host_id,
                    "logs": sanitized_logs,
                    "truncated": len(sanitized_logs) >= lines,
                },
                context={
                    "host_id": host_id,
                    "operation": "get_service_logs",
                    "service_name": service_name,
                },
            )

        except (DockerCommandError, DockerContextError) as e:
            logger.error(
                "Failed to get service logs",
                host_id=host_id,
                service_name=service_name,
                error=str(e),
            )
            return self._build_error_response(
                host_id, "get_service_logs", str(e), service_name=service_name
            )

    async def _validate_container_exists(self, host_id: str, container_id: str) -> None:
        """Validate that a container exists and is accessible."""
        try:
            cmd = f"inspect {container_id}"
            await self.context_manager.execute_docker_command(host_id, cmd)

            # If we get here without exception, container exists
            logger.debug(
                "Container validation successful", host_id=host_id, container_id=container_id
            )

        except DockerCommandError as e:
            if "No such container" in str(e):
                raise DockerCommandError(
                    f"Container {container_id} not found on host {host_id}"
                ) from e
            raise

    async def _stream_logs_generator(self, stream_config: LogStreamRequest):
        """Generator for streaming logs (used internally by FastMCP streaming)."""
        # This would be used by FastMCP's streaming system
        # to continuously yield log lines as they become available

        try:
            # Build streaming command
            cmd = f"logs --tail {stream_config.tail}"

            if stream_config.follow:
                cmd += " --follow"

            if stream_config.since:
                cmd += f" --since {stream_config.since}"

            if stream_config.timestamps:
                cmd += " --timestamps"

            cmd += f" {stream_config.container_id}"

            # In a real implementation, this would use asyncio subprocess
            # to stream logs continuously
            logger.info(
                "Starting log stream",
                host_id=stream_config.host_id,
                container_id=stream_config.container_id,
            )

            # Placeholder for actual streaming implementation
            yield f"data: Starting log stream for {stream_config.container_id}\n\n"

        except Exception as e:
            logger.error("Log streaming error", error=str(e))
            yield f"data: Error: {str(e)}\n\n"

"""RFC 7807 compliant error response helpers.

This module provides standardized error response formatting following RFC 7807:
Problem Details for HTTP APIs standard, adapted for MCP tool responses.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """RFC 7807 compliant error detail structure.

    Required fields:
    - success: Always False for error responses
    - error: Human-readable error message

    Optional RFC 7807 fields:
    - type: URI reference that identifies the problem type
    - title: Short, human-readable summary of the problem type
    - detail: Human-readable explanation specific to this occurrence
    - instance: URI reference that identifies the specific occurrence
    """

    success: bool = Field(default=False, description="Always False for errors")
    error: str = Field(description="Human-readable error message")
    type: str | None = Field(default=None, description="Problem type URI")
    title: str | None = Field(default=None, description="Problem type summary")
    detail: str | None = Field(default=None, description="Specific problem details")
    instance: str | None = Field(default=None, description="Problem occurrence URI")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DockerMCPErrorResponse:
    """Factory for creating standardized Docker MCP error responses."""

    # Standard problem types for Docker MCP operations
    PROBLEM_TYPES: dict[str, dict[str, str]] = {
        "host-not-found": {
            "type": "/problems/host-not-found",
            "title": "Host Not Found",
        },
        "docker-context-error": {
            "type": "/problems/docker-context-error",
            "title": "Docker Context Error",
        },
        "docker-command-error": {
            "type": "/problems/docker-command-error",
            "title": "Docker Command Failed",
        },
        "container-not-found": {
            "type": "/problems/container-not-found",
            "title": "Container Not Found",
        },
        "stack-not-found": {
            "type": "/problems/stack-not-found",
            "title": "Stack Not Found",
        },
        "migration-error": {
            "type": "/problems/migration-error",
            "title": "Migration Failed",
        },
        "transfer-error": {
            "type": "/problems/transfer-error",
            "title": "Data Transfer Failed",
        },
        "zfs-error": {
            "type": "/problems/zfs-error",
            "title": "ZFS Operation Failed",
        },
        "backup-error": {
            "type": "/problems/backup-error",
            "title": "Backup Operation Failed",
        },
        "validation-error": {
            "type": "/problems/validation-error",
            "title": "Input Validation Failed",
        },
        "configuration-error": {
            "type": "/problems/configuration-error",
            "title": "Configuration Error",
        },
        "permission-error": {
            "type": "/problems/permission-error",
            "title": "Insufficient Permissions",
        },
        "network-error": {
            "type": "/problems/network-error",
            "title": "Network Communication Failed",
        },
        "timeout-error": {
            "type": "/problems/timeout-error",
            "title": "Operation Timed Out",
        },
    }

    @classmethod
    def create_error(
        cls,
        error_message: str,
        problem_type: str | None = None,
        detail: str | None = None,
        instance: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a standardized error response.

        Args:
            error_message: Primary error message
            problem_type: Standard problem type key or custom type URI
            detail: Additional problem-specific details
            instance: Identifier for this specific occurrence
            context: Additional context fields (host_id, container_id, etc.)

        Returns:
            RFC 7807 compliant error response dictionary
        """
        error_detail = ErrorDetail(error=error_message, detail=detail, instance=instance)

        # Add standard problem type if provided
        if problem_type and problem_type in cls.PROBLEM_TYPES:
            problem_info = cls.PROBLEM_TYPES[problem_type]
            error_detail.type = problem_info["type"]
            error_detail.title = problem_info["title"]
        elif problem_type:
            # Custom type URI provided directly
            error_detail.type = problem_type

        # Convert to dict and add context
        response = error_detail.model_dump(exclude_none=True)

        if context:
            # Filter out RFC 7807 reserved fields from context to avoid overwriting
            reserved_fields = {
                "success",
                "error",
                "type",
                "title",
                "detail",
                "instance",
                "timestamp",
            }
            filtered_context = {k: v for k, v in context.items() if k not in reserved_fields}
            response.update(filtered_context)

        return response

    @classmethod
    def host_not_found(
        cls, host_id: str, available_hosts: list[str] | None = None
    ) -> dict[str, Any]:
        """Standard host not found error."""
        context = {"host_id": host_id}
        if available_hosts:
            context["available_hosts"] = available_hosts

        return cls.create_error(
            error_message=f"Host '{host_id}' not found in configuration",
            problem_type="host-not-found",
            detail=f"The specified host '{host_id}' is not configured. Check your hosts.yml configuration.",
            instance=f"/hosts/{host_id}",
            context=context,
        )

    @classmethod
    def docker_context_error(cls, host_id: str, operation: str, cause: str) -> dict[str, Any]:
        """Standard Docker context error."""
        return cls.create_error(
            error_message=f"Docker context operation failed: {cause}",
            problem_type="docker-context-error",
            detail=f"Failed to {operation} Docker context for host '{host_id}': {cause}",
            instance=f"/hosts/{host_id}/docker-context",
            context={"host_id": host_id, "operation": operation, "cause": cause},
        )

    @classmethod
    def docker_command_error(
        cls, host_id: str, command: str, exit_code: int, stderr: str
    ) -> dict[str, Any]:
        """Standard Docker command execution error."""
        return cls.create_error(
            error_message=f"Docker command failed with exit code {exit_code}",
            problem_type="docker-command-error",
            detail=f"Command '{command}' failed on host '{host_id}': {stderr}",
            instance=f"/hosts/{host_id}/docker-commands/{command}",
            context={
                "host_id": host_id,
                "command": command,
                "exit_code": exit_code,
                "stderr": stderr,
            },
        )

    @classmethod
    def container_not_found(cls, host_id: str, container_id: str) -> dict[str, Any]:
        """Standard container not found error."""
        return cls.create_error(
            error_message=f"Container '{container_id}' not found on host '{host_id}'",
            problem_type="container-not-found",
            detail="The specified container does not exist or is not accessible.",
            instance=f"/hosts/{host_id}/containers/{container_id}",
            context={"host_id": host_id, "container_id": container_id},
        )

    @classmethod
    def stack_not_found(cls, host_id: str, stack_name: str) -> dict[str, Any]:
        """Standard stack not found error."""
        return cls.create_error(
            error_message=f"Stack '{stack_name}' not found on host '{host_id}'",
            problem_type="stack-not-found",
            detail="The specified Docker Compose stack does not exist.",
            instance=f"/hosts/{host_id}/stacks/{stack_name}",
            context={"host_id": host_id, "stack_name": stack_name},
        )

    @classmethod
    def validation_error(cls, field: str, value: Any, reason: str) -> dict[str, Any]:
        """Standard validation error."""
        return cls.create_error(
            error_message=f"Validation failed for '{field}': {reason}",
            problem_type="validation-error",
            detail=f"The value '{value}' for field '{field}' is invalid: {reason}",
            instance=f"/validation/{field}",
            context={"field": field, "value": str(value), "reason": reason},
        )

    @classmethod
    def migration_error(
        cls, source_host: str, target_host: str, stack_name: str, stage: str, cause: str
    ) -> dict[str, Any]:
        """Standard migration error."""
        return cls.create_error(
            error_message=f"Migration failed during {stage}: {cause}",
            problem_type="migration-error",
            detail=f"Stack '{stack_name}' migration from '{source_host}' to '{target_host}' failed",
            instance=f"/migrations/{source_host}/{target_host}/{stack_name}",
            context={
                "source_host": source_host,
                "target_host": target_host,
                "stack_name": stack_name,
                "stage": stage,
                "cause": cause,
            },
        )

    @classmethod
    def generic_error(
        cls,
        error_message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generic error response for unexpected errors."""
        return cls.create_error(
            error_message=error_message,
            context=context or {},
        )

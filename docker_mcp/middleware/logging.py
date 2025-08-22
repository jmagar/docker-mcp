"""Logging middleware for Docker MCP server using FastMCP Middleware base class."""

import time
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

try:
    from ..core.logging_config import get_middleware_logger
except ImportError:
    from docker_mcp.core.logging_config import get_middleware_logger


class LoggingMiddleware(Middleware):
    """FastMCP middleware for comprehensive request/response logging.
    
    Logs all MCP messages to both console and middleware.log file with:
    - Request details with sanitized parameters
    - Response status and timing
    - Error details and stack traces
    - Structured logging for easy parsing
    """
    
    def __init__(self, include_payloads: bool = True, max_payload_length: int = 1000):
        """Initialize logging middleware.
        
        Args:
            include_payloads: Whether to include request/response payloads in logs
            max_payload_length: Maximum length for payload strings before truncation
        """
        self.logger = get_middleware_logger()
        self.include_payloads = include_payloads
        self.max_payload_length = max_payload_length
        
    async def on_message(self, context: MiddlewareContext, call_next):
        """Log all MCP messages with comprehensive details."""
        start_time = time.time()
        
        # Log request start with sanitized parameters
        log_data = {
            "method": context.method,
            "source": context.source,
            "message_type": context.type,
            "timestamp": context.timestamp
        }
        
        # Add sanitized payload if enabled
        if self.include_payloads and hasattr(context.message, '__dict__'):
            log_data["params"] = self._sanitize_message(context.message)
        
        self.logger.info("MCP request started", **log_data)
        
        try:
            # Execute the request
            result = await call_next(context)
            
            # Log successful completion
            duration_ms = round((time.time() - start_time) * 1000, 2)
            self.logger.info(
                "MCP request completed",
                method=context.method,
                success=True,
                duration_ms=duration_ms
            )
            
            return result
            
        except Exception as e:
            # Log error with full context
            duration_ms = round((time.time() - start_time) * 1000, 2)
            self.logger.error(
                "MCP request failed",
                method=context.method,
                success=False,
                duration_ms=duration_ms,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True  # Include stack trace
            )
            raise
    
    def _sanitize_message(self, message: Any) -> dict[str, Any]:
        """Sanitize message data for safe logging.
        
        Args:
            message: The MCP message object to sanitize
            
        Returns:
            Dictionary with sanitized message data
        """
        if not hasattr(message, '__dict__'):
            return {"message": str(message)[:self.max_payload_length]}
        
        sanitized = {}
        
        for key, value in message.__dict__.items():
            # Skip private attributes
            if key.startswith('_'):
                continue
                
            # Redact sensitive information
            if self._is_sensitive_field(key):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, str):
                # Truncate long strings
                if len(value) > self.max_payload_length:
                    sanitized[key] = value[:self.max_payload_length] + "... [TRUNCATED]"
                else:
                    sanitized[key] = value
            elif isinstance(value, (dict, list)):
                # Convert complex objects to string and truncate if needed
                str_value = str(value)
                if len(str_value) > self.max_payload_length:
                    sanitized[key] = str_value[:self.max_payload_length] + "... [TRUNCATED]"
                else:
                    sanitized[key] = value
            else:
                sanitized[key] = value
        
        return sanitized
    
    def _is_sensitive_field(self, field_name: str) -> bool:
        """Check if field contains sensitive data that should be redacted.
        
        Args:
            field_name: Name of the field to check
            
        Returns:
            True if field contains sensitive data
        """
        sensitive_keywords = [
            "password", "passwd", "pwd",
            "token", "access_token", "refresh_token", "api_token",
            "key", "api_key", "private_key", "secret_key", "ssh_key", 
            "identity_file", "cert", "certificate",
            "secret", "client_secret", "auth_secret",
            "credential", "auth", "authorization"
        ]
        
        field_lower = field_name.lower()
        return any(sensitive in field_lower for sensitive in sensitive_keywords)

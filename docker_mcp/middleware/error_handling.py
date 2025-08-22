"""Error handling middleware for Docker MCP server."""

from collections import defaultdict
from typing import Any, Dict, Optional

from fastmcp.server.middleware import Middleware, MiddlewareContext

try:
    from ..core.logging_config import get_middleware_logger
except ImportError:
    from docker_mcp.core.logging_config import get_middleware_logger


class ErrorHandlingMiddleware(Middleware):
    """FastMCP middleware for comprehensive error handling and tracking.
    
    Features:
    - Error statistics and categorization
    - Structured error logging
    - Error context preservation
    - Exception type tracking
    - Proper MCP error formatting
    """
    
    def __init__(self, 
                 include_traceback: bool = True,
                 track_error_stats: bool = True):
        """Initialize error handling middleware.
        
        Args:
            include_traceback: Whether to include full stack traces in logs
            track_error_stats: Whether to track error statistics
        """
        self.logger = get_middleware_logger()
        self.include_traceback = include_traceback
        self.track_error_stats = track_error_stats
        
        # Error statistics tracking
        self.error_stats: Dict[str, int] = defaultdict(int)
        self.method_errors: Dict[str, int] = defaultdict(int)
        
    async def on_message(self, context: MiddlewareContext, call_next):
        """Handle all MCP messages with comprehensive error tracking."""
        try:
            return await call_next(context)
            
        except Exception as e:
            await self._handle_error(e, context)
            raise  # Always re-raise to preserve FastMCP error handling
    
    async def _handle_error(self, error: Exception, context: MiddlewareContext) -> None:
        """Handle and log error with comprehensive context.
        
        Args:
            error: The exception that occurred
            context: The MCP middleware context
        """
        error_type = type(error).__name__
        method = context.method
        
        # Update statistics if enabled
        if self.track_error_stats:
            error_key = f"{error_type}:{method}"
            self.error_stats[error_key] += 1
            self.method_errors[method] += 1
        
        # Create comprehensive error log
        error_data = {
            "error_type": error_type,
            "error_message": str(error),
            "method": method,
            "source": context.source,
            "message_type": context.type,
            "timestamp": context.timestamp
        }
        
        # Add statistics if tracking is enabled
        if self.track_error_stats:
            error_data.update({
                "error_occurrence_count": self.error_stats[f"{error_type}:{method}"],
                "method_error_count": self.method_errors[method],
                "total_error_types": len(self.error_stats)
            })
        
        # Add context information if available
        if hasattr(context.message, '__dict__'):
            # Safely extract message info without exposing sensitive data
            message_info = {}
            for key, value in context.message.__dict__.items():
                if not key.startswith('_') and not self._is_sensitive_field(key):
                    message_info[key] = str(value)[:100]  # Limit length
            error_data["message_context"] = message_info
        
        # Log the error with appropriate level
        if self._is_critical_error(error):
            self.logger.critical(
                "Critical error in MCP request",
                **error_data,
                exc_info=self.include_traceback
            )
        elif self._is_warning_level_error(error):
            self.logger.warning(
                "Warning-level error in MCP request",
                **error_data,
                exc_info=False  # Don't include traceback for warnings
            )
        else:
            self.logger.error(
                "Error in MCP request",
                **error_data,
                exc_info=self.include_traceback
            )
    
    def _is_sensitive_field(self, field_name: str) -> bool:
        """Check if field contains sensitive data that should not be logged.
        
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
    
    def _is_critical_error(self, error: Exception) -> bool:
        """Determine if error should be logged as critical.
        
        Args:
            error: The exception to categorize
            
        Returns:
            True if error is critical
        """
        critical_types = (
            SystemError,
            MemoryError, 
            RecursionError,
            KeyboardInterrupt,
            SystemExit
        )
        return isinstance(error, critical_types)
    
    def _is_warning_level_error(self, error: Exception) -> bool:
        """Determine if error should be logged as warning instead of error.
        
        Args:
            error: The exception to categorize
            
        Returns:
            True if error should be logged as warning
        """
        warning_types = (
            TimeoutError,
            ConnectionError,
            FileNotFoundError,
            PermissionError
        )
        return isinstance(error, warning_types)
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """Get comprehensive error statistics.
        
        Returns:
            Dictionary with error statistics and analysis
        """
        if not self.track_error_stats:
            return {"error_tracking": "disabled"}
        
        total_errors = sum(self.error_stats.values())
        
        # Get top error types
        top_errors = sorted(
            self.error_stats.items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:10]
        
        # Get methods with most errors
        top_error_methods = sorted(
            self.method_errors.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        return {
            "total_errors": total_errors,
            "unique_error_types": len(self.error_stats),
            "top_errors": top_errors,
            "top_error_methods": top_error_methods,
            "error_distribution": dict(self.error_stats)
        }
    
    def reset_statistics(self) -> None:
        """Reset all error statistics."""
        self.error_stats.clear()
        self.method_errors.clear()
        self.logger.info("Error statistics reset")
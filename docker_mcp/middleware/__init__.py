"""FastMCP middleware for Docker MCP server.

This module provides comprehensive middleware for request/response processing:
- LoggingMiddleware: Structured logging with dual output (console + files)
- ErrorHandlingMiddleware: Comprehensive error tracking and recovery
- TimingMiddleware: Performance monitoring and timing statistics  
- RateLimitingMiddleware: Token bucket rate limiting with burst capacity

All middleware follows FastMCP patterns and integrates with the dual logging system.
"""

from .logging import LoggingMiddleware
from .error_handling import ErrorHandlingMiddleware
from .timing import TimingMiddleware
from .rate_limiting import RateLimitingMiddleware

__all__ = [
    "LoggingMiddleware",
    "ErrorHandlingMiddleware", 
    "TimingMiddleware",
    "RateLimitingMiddleware"
]

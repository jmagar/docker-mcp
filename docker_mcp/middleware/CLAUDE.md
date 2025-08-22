# Middleware Layer - Development Memory

## FastMCP Middleware Architecture

### Function-based Middleware Pattern (Preferred)
```python
from fastmcp import Context
import time

async def logging_middleware(ctx: Context, next_handler):
    """Middleware for structured logging of MCP requests."""
    start_time = time.time()
    
    # Pre-processing: Log request start
    await ctx.info(
        "MCP request started",
        method=getattr(ctx, "method", "unknown"),
        params=_sanitize_params(getattr(ctx, "params", {})),
    )
    
    try:
        # Execute the request
        response = await next_handler(ctx)
        
        # Post-processing: Log success
        duration = time.time() - start_time
        await ctx.info("MCP request completed", duration_ms=round(duration * 1000, 2))
        
        return response
        
    except Exception as e:
        # Error handling: Log failure
        duration = time.time() - start_time
        await ctx.error("MCP request failed", error=str(e), duration_ms=round(duration * 1000, 2))
        raise  # Re-raise for FastMCP to handle
```

### Middleware Dependencies
- **FastMCP Context**: For request context and structured logging
- **Structlog**: For consistent structured logging across middleware
- **Time Module**: For performance measurement and timing

## Middleware Chain Pattern

### Request Flow
```
Client Request → Middleware 1 → Middleware 2 → ... → Handler → Response
              ↖              ↖               ↖        ↗
               Post-process ← Post-process ← Post-process
```

### Chain Execution
```python
async def middleware_template(ctx: Context, next_handler):
    # 1. Pre-processing (before request)
    setup_logging()
    start_timer()
    
    try:
        # 2. Continue chain
        response = await next_handler(ctx)
        
        # 3. Post-processing (after successful request)
        log_success()
        record_metrics()
        
        return response
        
    except Exception as e:
        # 4. Error processing (after failed request)
        log_error(e)
        record_failure()
        raise  # Always re-raise for proper error handling
```

## Context Usage Patterns

### FastMCP Context Access
```python
async def context_aware_middleware(ctx: Context, next_handler):
    # Access request metadata
    method = getattr(ctx, "method", "unknown")
    params = getattr(ctx, "params", {})
    
    # Use context for structured logging
    await ctx.info("Processing request", method=method, param_count=len(params))
    
    # Continue processing
    return await next_handler(ctx)
```

### Context Logging Methods
```python
# Information logging
await ctx.info("Operation started", operation="deploy", host_id="prod-1")

# Warning logging
await ctx.warning("Slow operation detected", duration_ms=5000)

# Error logging
await ctx.error("Operation failed", error=str(e), error_type=type(e).__name__)

# Debug logging
await ctx.debug("Internal state", cache_size=len(cache))
```

## Timing and Performance Middleware

### Performance Measurement
```python
async def timing_middleware(ctx: Context, next_handler):
    """Middleware for timing MCP operations."""
    start_time = time.perf_counter()  # High precision timing
    
    try:
        response = await next_handler(ctx)
        
        # Record successful timing
        duration = time.perf_counter() - start_time
        _record_timing(duration, success=True)
        
        return response
        
    except Exception:
        # Record failed timing too
        duration = time.perf_counter() - start_time
        _record_timing(duration, success=False)
        raise
```

### Timing Best Practices
```python
# Use perf_counter for precise measurements
start_time = time.perf_counter()  # Not time.time()

# Round to appropriate precision
duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
duration_seconds = round(time.perf_counter() - start_time, 4)

# Always record timing for both success and failure
def _record_timing(duration: float, success: bool) -> None:
    """Record timing metrics for monitoring."""
    logger.debug("Request timing", duration_seconds=duration, success=success)
    # Could extend to send metrics to monitoring system
```

## Error Handling Middleware

### Comprehensive Error Handling
```python
async def error_handling_middleware(ctx: Context, next_handler):
    """Middleware for handling and logging errors."""
    try:
        return await next_handler(ctx)
        
    except Exception as e:
        # Log with full context and stack trace
        logger.exception(
            "Unhandled error in MCP request",
            error=str(e),
            error_type=type(e).__name__,
            method=getattr(ctx, "method", "unknown")
        )
        
        # Always re-raise - let FastMCP format the error response
        raise
```

### Error Context Preservation
```python
# CORRECT: Re-raise to preserve stack trace and context
except Exception as e:
    logger.exception("Error occurred", error=str(e))
    raise  # Preserves original traceback

# INCORRECT: Don't swallow exceptions or create new ones
except Exception as e:
    logger.error("Error occurred", error=str(e))
    return {"error": "Something went wrong"}  # Loses context!
```

## Data Sanitization Patterns

### Parameter Sanitization
```python
def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Sanitize request parameters for logging."""
    if not isinstance(params, dict):
        return {}
    
    sanitized = {}
    
    for key, value in params.items():
        # Redact sensitive information
        if any(sensitive in key.lower() for sensitive in ["password", "token", "key", "secret"]):
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > 1000:
            # Truncate very long strings (like compose files)
            sanitized[key] = value[:1000] + "... [TRUNCATED]"
        else:
            sanitized[key] = value
    
    return sanitized
```

### Sensitive Data Keywords
```python
SENSITIVE_KEYWORDS = [
    "password", "passwd", "pwd",
    "token", "access_token", "refresh_token", "api_token",
    "key", "api_key", "private_key", "secret_key", "ssh_key",
    "secret", "client_secret", "auth_secret",
    "credential", "auth", "authorization"
]

def is_sensitive_field(field_name: str) -> bool:
    """Check if field contains sensitive data."""
    field_lower = field_name.lower()
    return any(sensitive in field_lower for sensitive in SENSITIVE_KEYWORDS)
```

## Legacy Compatibility Pattern

### Class-based Middleware (Legacy)
```python
class LoggingMiddleware:
    """Legacy class-based middleware for backwards compatibility."""
    
    @staticmethod
    async def __call__(ctx: Context, next_handler):
        return await logging_middleware(ctx, next_handler)

class TimingMiddleware:
    """Legacy timing middleware."""
    
    @staticmethod
    async def __call__(ctx: Context, next_handler):
        return await timing_middleware(ctx, next_handler)
```

### Migration Strategy
```python
# OLD: Class-based middleware
middleware_stack = [LoggingMiddleware(), TimingMiddleware()]

# NEW: Function-based middleware (preferred)
middleware_stack = [logging_middleware, timing_middleware]

# TRANSITION: Both work, but function-based is preferred for new code
```

## Middleware Registration

### Server Integration
```python
from fastmcp import FastMCPServer
from .middleware.logging import logging_middleware, timing_middleware, error_handling_middleware

# Register middleware in order (executed in sequence)
server = FastMCPServer(
    middleware=[
        logging_middleware,       # First: Log requests
        timing_middleware,        # Second: Time operations
        error_handling_middleware, # Last: Handle errors
        # ... additional middleware
    ]
)
```

### Middleware Order Considerations
```python
# Typical middleware order:
[
    logging_middleware,         # Always first - logs everything
    authentication_middleware,  # Security before business logic
    rate_limiting_middleware,   # Rate limits after auth
    timing_middleware,          # Performance measurement
    caching_middleware,         # Caching before processing
    error_handling_middleware,  # Error handling (often last)
]
```

## Structured Logging Integration

### Consistent Log Format
```python
# Use structured logging for machine readability
logger = structlog.get_logger()

async def structured_logging_middleware(ctx: Context, next_handler):
    # Structured log entries
    await ctx.info(
        "MCP request processing",
        method=getattr(ctx, "method", "unknown"),
        timestamp=datetime.now().isoformat(),
        request_id=getattr(ctx, "request_id", None),
        user_agent=getattr(ctx, "user_agent", None)
    )
```

### Log Context Enrichment
```python
# Add context to all logs in this request
async def context_enrichment_middleware(ctx: Context, next_handler):
    # Add request-specific context
    with structlog.contextvars.bind_contextvars(
        request_id=generate_request_id(),
        method=getattr(ctx, "method", "unknown"),
        timestamp=datetime.now().isoformat()
    ):
        return await next_handler(ctx)
```

## Metrics and Monitoring

### Metrics Collection
```python
def _record_timing(duration: float, success: bool) -> None:
    """Record timing metrics for monitoring systems."""
    # Debug logging (always available)
    logger.debug("Request timing", duration_seconds=duration, success=success)
    
    # Metrics system integration (if available)
    if hasattr(metrics, 'record_request_duration'):
        metrics.record_request_duration(duration, success)
    
    # Prometheus metrics (if configured)
    if prometheus_metrics:
        prometheus_metrics.request_duration.observe(duration)
        prometheus_metrics.request_count.inc(labels={'success': str(success).lower()})
```

### Health Check Integration
```python
async def health_check_middleware(ctx: Context, next_handler):
    """Middleware that contributes to health check status."""
    try:
        response = await next_handler(ctx)
        # Record healthy operation
        health_tracker.record_success()
        return response
    except Exception as e:
        # Record unhealthy operation
        health_tracker.record_failure(str(e))
        raise
```

Middleware provides cross-cutting concerns like logging, timing, error handling, and security that apply to all MCP requests, ensuring consistent behavior and observability across the entire application.
"""Rate limiting middleware for Docker MCP server."""

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp import McpError
from mcp.types import ErrorData

try:
    from ..core.logging_config import get_middleware_logger
except ImportError:
    from docker_mcp.core.logging_config import get_middleware_logger


class TokenBucket:
    """Token bucket implementation for rate limiting."""

    def __init__(self, capacity: int, refill_rate: float):
        """Initialize token bucket.

        Args:
            capacity: Maximum number of tokens in bucket
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.time()
        self._lock = asyncio.Lock()

    async def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens from bucket.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were successfully consumed
        """
        async with self._lock:
            now = time.time()

            # Refill bucket based on elapsed time
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            # Check if we have enough tokens
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def get_status(self) -> dict[str, Any]:
        """Get current bucket status."""
        return {
            "capacity": self.capacity,
            "current_tokens": round(self.tokens, 2),
            "refill_rate": self.refill_rate,
            "last_refill": self.last_refill,
        }


class RateLimitingMiddleware(Middleware):
    """FastMCP middleware for request rate limiting using token bucket algorithm.

    Features:
    - Token bucket algorithm for smooth rate limiting
    - Per-client rate limiting with configurable identification
    - Burst capacity support
    - Rate limit statistics and monitoring
    - Configurable error responses
    - Global and per-method rate limits
    """

    def __init__(
        self,
        max_requests_per_second: float = 10.0,
        burst_capacity: int | None = None,
        client_id_func: Callable[[MiddlewareContext], str] | None = None,
        enable_global_limit: bool = True,
        per_method_limits: dict[str, float] | None = None,
        cleanup_interval: float = 300.0,
    ):  # 5 minutes
        """Initialize rate limiting middleware.

        Args:
            max_requests_per_second: Maximum requests per second per client
            burst_capacity: Maximum burst size (defaults to 2x rate limit)
            client_id_func: Function to extract client ID from context
            enable_global_limit: Whether to enforce global rate limits
            per_method_limits: Per-method rate limits (method -> requests/sec)
            cleanup_interval: Interval to clean up inactive client buckets (seconds)
        """
        self.logger = get_middleware_logger()
        self.max_requests_per_second = max_requests_per_second
        self.burst_capacity = burst_capacity or int(max_requests_per_second * 2)
        self.client_id_func = client_id_func or self._default_client_id
        self.enable_global_limit = enable_global_limit
        self.per_method_limits = per_method_limits or {}
        self.cleanup_interval = cleanup_interval

        # Client token buckets
        self.client_buckets: dict[str, TokenBucket] = {}
        self.method_buckets: dict[str, dict[str, TokenBucket]] = defaultdict(dict)

        # Statistics
        self.rate_limit_hits = 0
        self.total_requests = 0
        self.client_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"requests": 0, "rate_limited": 0, "last_request": time.time()}
        )

        # Start cleanup task
        self.last_cleanup = time.time()

    async def on_message(self, context: MiddlewareContext, call_next):
        """Apply rate limiting to MCP requests."""
        client_id = self.client_id_func(context)
        method = context.method

        self.total_requests += 1
        self.client_stats[client_id]["requests"] += 1
        self.client_stats[client_id]["last_request"] = time.time()

        # Check global rate limit
        if self.enable_global_limit:
            if not await self._check_client_rate_limit(client_id):
                await self._handle_rate_limit_exceeded(client_id, method or "unknown", "global")
                return

        # Check per-method rate limit
        if method and method in self.per_method_limits:
            if not await self._check_method_rate_limit(client_id, method):
                await self._handle_rate_limit_exceeded(client_id, method, "method")
                return

        # Perform periodic cleanup
        await self._periodic_cleanup()

        # Request is allowed, proceed
        try:
            result = await call_next(context)

            # Log successful request
            self.logger.debug(
                "Rate limit check passed",
                client_id=client_id,
                method=method,
                global_tokens_remaining=self._get_remaining_tokens(client_id),
                total_requests=self.total_requests,
            )

            return result

        except Exception as e:
            # Still count failed requests for rate limiting
            self.logger.debug(
                "Request failed after rate limit check",
                client_id=client_id,
                method=method,
                error=str(e),
            )
            raise

    async def _check_client_rate_limit(self, client_id: str) -> bool:
        """Check if client is within global rate limit.

        Args:
            client_id: Client identifier

        Returns:
            True if request is allowed
        """
        if client_id not in self.client_buckets:
            self.client_buckets[client_id] = TokenBucket(
                capacity=self.burst_capacity, refill_rate=self.max_requests_per_second
            )

        return await self.client_buckets[client_id].consume()

    async def _check_method_rate_limit(self, client_id: str, method: str) -> bool:
        """Check if client is within method-specific rate limit.

        Args:
            client_id: Client identifier
            method: MCP method name

        Returns:
            True if request is allowed
        """
        if client_id not in self.method_buckets[method]:
            method_rate = self.per_method_limits[method]
            method_burst = int(method_rate * 2)

            self.method_buckets[method][client_id] = TokenBucket(
                capacity=method_burst, refill_rate=method_rate
            )

        return await self.method_buckets[method][client_id].consume()

    async def _handle_rate_limit_exceeded(
        self, client_id: str, method: str, limit_type: str
    ) -> None:
        """Handle rate limit exceeded scenario.

        Args:
            client_id: Client identifier
            method: MCP method name
            limit_type: Type of limit exceeded ("global" or "method")
        """
        self.rate_limit_hits += 1
        self.client_stats[client_id]["rate_limited"] += 1

        # Log rate limit hit
        self.logger.warning(
            "Rate limit exceeded",
            client_id=client_id,
            method=method,
            limit_type=limit_type,
            total_rate_limits=self.rate_limit_hits,
            client_requests=self.client_stats[client_id]["requests"],
            client_rate_limited=self.client_stats[client_id]["rate_limited"],
        )

        # Raise MCP error
        error_message = f"Rate limit exceeded for {limit_type} limits. Try again later."
        raise McpError(
            ErrorData(
                code=-32000,  # Internal Error
                message=error_message,
            )
        )

    def _default_client_id(self, context: MiddlewareContext) -> str:
        """Default client identification function.

        Args:
            context: MCP middleware context

        Returns:
            Client identifier string
        """
        # Try to extract client info from context
        if hasattr(context, "client_info") and context.client_info:
            return str(context.client_info)

        # Fallback to source + timestamp for basic identification
        return f"{context.source}:default"

    def _get_remaining_tokens(self, client_id: str) -> float:
        """Get remaining tokens for a client.

        Args:
            client_id: Client identifier

        Returns:
            Number of remaining tokens
        """
        if client_id in self.client_buckets:
            return round(self.client_buckets[client_id].tokens, 2)
        return float(self.burst_capacity)

    async def _periodic_cleanup(self) -> None:
        """Clean up inactive client buckets to prevent memory leaks."""
        now = time.time()

        # Only run cleanup periodically
        if now - self.last_cleanup < self.cleanup_interval:
            return

        self.last_cleanup = now
        inactive_threshold = now - self.cleanup_interval * 2  # Double the cleanup interval

        # Clean up inactive clients
        inactive_clients = [
            client_id
            for client_id, stats in self.client_stats.items()
            if stats["last_request"] < inactive_threshold
        ]

        for client_id in inactive_clients:
            # Remove from all tracking structures
            self.client_buckets.pop(client_id, None)
            self.client_stats.pop(client_id, None)

            for method_buckets in self.method_buckets.values():
                method_buckets.pop(client_id, None)

        if inactive_clients:
            self.logger.info(
                "Cleaned up inactive clients",
                removed_clients=len(inactive_clients),
                remaining_clients=len(self.client_stats),
            )

    def get_rate_limit_statistics(self) -> dict[str, Any]:
        """Get comprehensive rate limiting statistics.

        Returns:
            Dictionary with rate limiting statistics
        """
        active_clients = len(self.client_buckets)

        # Calculate rate limit hit rate
        hit_rate = self.rate_limit_hits / max(self.total_requests, 1)

        # Get top rate-limited clients
        top_limited_clients = sorted(
            [(client_id, stats["rate_limited"]) for client_id, stats in self.client_stats.items()],
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        # Get busiest clients
        busiest_clients = sorted(
            [(client_id, stats["requests"]) for client_id, stats in self.client_stats.items()],
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        return {
            "total_requests": self.total_requests,
            "rate_limit_hits": self.rate_limit_hits,
            "hit_rate": round(hit_rate, 4),
            "active_clients": active_clients,
            "max_requests_per_second": self.max_requests_per_second,
            "burst_capacity": self.burst_capacity,
            "per_method_limits": self.per_method_limits,
            "top_limited_clients": top_limited_clients,
            "busiest_clients": busiest_clients,
            "client_count": len(self.client_stats),
        }

    def get_client_status(self, client_id: str) -> dict[str, Any] | None:
        """Get rate limiting status for a specific client.

        Args:
            client_id: Client identifier

        Returns:
            Client rate limiting status or None if client not found
        """
        if client_id not in self.client_stats:
            return None

        status: dict[str, Any] = {
            "client_id": client_id,
            "stats": self.client_stats[client_id].copy(),
            "global_bucket": None,
            "method_buckets": {},
        }

        # Add global bucket status
        if client_id in self.client_buckets:
            status["global_bucket"] = self.client_buckets[client_id].get_status()

        # Add method bucket status
        for method, buckets in self.method_buckets.items():
            if client_id in buckets:
                status["method_buckets"][method] = buckets[client_id].get_status()

        return status

    def reset_statistics(self) -> None:
        """Reset all rate limiting statistics."""
        self.rate_limit_hits = 0
        self.total_requests = 0
        self.client_stats.clear()
        self.logger.info("Rate limiting statistics reset")

    def update_rate_limits(
        self,
        max_requests_per_second: float | None = None,
        burst_capacity: int | None = None,
        per_method_limits: dict[str, float] | None = None,
    ) -> None:
        """Update rate limiting configuration.

        Args:
            max_requests_per_second: New global rate limit
            burst_capacity: New burst capacity
            per_method_limits: New per-method limits
        """
        if max_requests_per_second is not None:
            self.max_requests_per_second = max_requests_per_second

        if burst_capacity is not None:
            self.burst_capacity = burst_capacity

        if per_method_limits is not None:
            self.per_method_limits = per_method_limits

        # Clear existing buckets to apply new limits
        self.client_buckets.clear()
        self.method_buckets.clear()

        self.logger.info(
            "Rate limit configuration updated",
            max_requests_per_second=self.max_requests_per_second,
            burst_capacity=self.burst_capacity,
            per_method_limits=self.per_method_limits,
        )

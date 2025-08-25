"""Timing middleware for Docker MCP server performance monitoring."""

import time
from collections import defaultdict, deque
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

try:
    from ..core.logging_config import get_middleware_logger
except ImportError:
    from docker_mcp.core.logging_config import get_middleware_logger


class TimingMiddleware(Middleware):
    """FastMCP middleware for comprehensive request timing and performance monitoring.
    
    Features:
    - High-precision timing with perf_counter
    - Per-method timing statistics
    - Request duration tracking
    - Performance trend analysis
    - Slow request detection and alerting
    """

    def __init__(self,
                 slow_request_threshold_ms: float = 5000.0,
                 track_statistics: bool = True,
                 max_history_size: int = 1000):
        """Initialize timing middleware.
        
        Args:
            slow_request_threshold_ms: Threshold for logging slow requests (milliseconds)
            track_statistics: Whether to track timing statistics
            max_history_size: Maximum number of timing records to keep in memory
        """
        self.logger = get_middleware_logger()
        self.slow_threshold_ms = slow_request_threshold_ms
        self.track_statistics = track_statistics
        self.max_history_size = max_history_size

        # Timing statistics
        self.request_times: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history_size))
        self.method_stats: dict[str, dict[str, Any]] = defaultdict(dict)
        self.total_requests = 0
        self.slow_requests = 0

    async def on_message(self, context: MiddlewareContext, call_next):
        """Time all MCP requests with detailed performance tracking."""
        start_time = time.perf_counter()
        method = context.method
        success = False

        try:
            result = await call_next(context)
            success = True
            return result

        except Exception:
            success = False
            raise

        finally:
            # Calculate timing metrics
            end_time = time.perf_counter()
            duration_seconds = end_time - start_time
            duration_ms = duration_seconds * 1000

            # Update statistics if enabled
            if self.track_statistics:
                await self._update_statistics(method, duration_ms, success)

            # Log timing information
            await self._log_timing(method, duration_ms, success, context)

    async def _update_statistics(self, method: str, duration_ms: float, success: bool) -> None:
        """Update internal timing statistics.
        
        Args:
            method: The MCP method name
            duration_ms: Request duration in milliseconds
            success: Whether the request succeeded
        """
        self.total_requests += 1

        # Track slow requests
        if duration_ms > self.slow_threshold_ms:
            self.slow_requests += 1

        # Add to history
        self.request_times[method].append({
            'duration_ms': duration_ms,
            'success': success,
            'timestamp': time.time()
        })

        # Update method statistics
        method_times = [req['duration_ms'] for req in self.request_times[method]]

        if method_times:
            self.method_stats[method] = {
                'count': len(method_times),
                'avg_ms': sum(method_times) / len(method_times),
                'min_ms': min(method_times),
                'max_ms': max(method_times),
                'success_rate': sum(1 for req in self.request_times[method] if req['success']) / len(method_times),
                'slow_count': sum(1 for t in method_times if t > self.slow_threshold_ms)
            }

    async def _log_timing(self, method: str, duration_ms: float, success: bool, context: MiddlewareContext) -> None:
        """Log timing information with appropriate level and detail.
        
        Args:
            method: The MCP method name
            duration_ms: Request duration in milliseconds  
            success: Whether the request succeeded
            context: The MCP middleware context
        """
        log_data = {
            "method": method,
            "duration_ms": round(duration_ms, 2),
            "success": success,
            "source": context.source,
            "message_type": context.type
        }

        # Add performance context if statistics are enabled
        if self.track_statistics and method in self.method_stats:
            stats = self.method_stats[method]
            log_data.update({
                "avg_duration_ms": round(stats['avg_ms'], 2),
                "method_request_count": stats['count'],
                "success_rate": round(stats['success_rate'], 3)
            })

        # Log based on performance characteristics
        if duration_ms > self.slow_threshold_ms:
            self.logger.warning(
                "Slow request detected",
                **log_data,
                slow_threshold_ms=self.slow_threshold_ms,
                performance_impact="high"
            )
        elif duration_ms > self.slow_threshold_ms * 0.5:
            self.logger.info(
                "Moderate duration request",
                **log_data,
                performance_impact="medium"
            )
        else:
            self.logger.debug(
                "Request completed",
                **log_data,
                performance_impact="low"
            )

    def get_performance_statistics(self) -> dict[str, Any]:
        """Get comprehensive performance statistics.
        
        Returns:
            Dictionary with timing statistics and performance metrics
        """
        if not self.track_statistics:
            return {"performance_tracking": "disabled"}

        # Calculate overall statistics
        all_durations = []
        for method_times in self.request_times.values():
            all_durations.extend(req['duration_ms'] for req in method_times)

        overall_stats = {}
        if all_durations:
            overall_stats = {
                "avg_duration_ms": sum(all_durations) / len(all_durations),
                "min_duration_ms": min(all_durations),
                "max_duration_ms": max(all_durations),
                "median_duration_ms": sorted(all_durations)[len(all_durations) // 2] if all_durations else 0
            }

        # Get top slowest methods
        slowest_methods = sorted(
            [(method, stats['avg_ms']) for method, stats in self.method_stats.items()],
            key=lambda x: x[1],
            reverse=True
        )[:10]

        # Get methods with most requests
        busiest_methods = sorted(
            [(method, stats['count']) for method, stats in self.method_stats.items()],
            key=lambda x: x[1],
            reverse=True
        )[:10]

        return {
            "total_requests": self.total_requests,
            "slow_requests": self.slow_requests,
            "slow_request_rate": self.slow_requests / max(self.total_requests, 1),
            "slow_threshold_ms": self.slow_threshold_ms,
            "overall_stats": overall_stats,
            "method_stats": dict(self.method_stats),
            "slowest_methods": slowest_methods,
            "busiest_methods": busiest_methods,
            "methods_tracked": len(self.method_stats)
        }

    def get_recent_slow_requests(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent slow requests for debugging.
        
        Args:
            limit: Maximum number of slow requests to return
            
        Returns:
            List of recent slow request details
        """
        if not self.track_statistics:
            return []

        slow_requests = []

        for method, requests in self.request_times.items():
            for req in requests:
                if req['duration_ms'] > self.slow_threshold_ms:
                    slow_requests.append({
                        'method': method,
                        'duration_ms': req['duration_ms'],
                        'success': req['success'],
                        'timestamp': req['timestamp']
                    })

        # Sort by timestamp (most recent first) and limit
        slow_requests.sort(key=lambda x: x['timestamp'], reverse=True)
        return slow_requests[:limit]

    def reset_statistics(self) -> None:
        """Reset all timing statistics."""
        self.request_times.clear()
        self.method_stats.clear()
        self.total_requests = 0
        self.slow_requests = 0
        self.logger.info("Timing statistics reset")

    def update_slow_threshold(self, new_threshold_ms: float) -> None:
        """Update the slow request threshold.
        
        Args:
            new_threshold_ms: New threshold in milliseconds
        """
        old_threshold = self.slow_threshold_ms
        self.slow_threshold_ms = new_threshold_ms

        self.logger.info(
            "Slow request threshold updated",
            old_threshold_ms=old_threshold,
            new_threshold_ms=new_threshold_ms
        )

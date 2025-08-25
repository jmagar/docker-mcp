"""Shared pytest fixtures for Docker MCP tests."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog
from fastmcp import Client, FastMCP

from docker_mcp.core.config_loader import DockerMCPConfig, load_config
from docker_mcp.middleware import (
    ErrorHandlingMiddleware,
    LoggingMiddleware,
    RateLimitingMiddleware,
    TimingMiddleware,
)
from docker_mcp.server import DockerMCPServer


@pytest.fixture
def config_path() -> str:
    """Get path to test configuration file."""
    return str(Path(__file__).parent.parent / "config" / "hosts.yml")


@pytest.fixture
def config(config_path: str) -> DockerMCPConfig:
    """Load Docker MCP configuration for testing."""
    return load_config(config_path)


@pytest.fixture
def server(config: DockerMCPConfig) -> DockerMCPServer:
    """Create Docker MCP server instance for testing."""
    server = DockerMCPServer(config)
    server._initialize_app()
    return server


@pytest.fixture
async def client(server: DockerMCPServer) -> AsyncGenerator[Client, None]:
    """Create FastMCP client connected to server in-memory."""
    async with Client(server.app) as client:
        yield client


@pytest.fixture
def test_host_id() -> str:
    """Default test host ID for container tests."""
    return "squirts"


@pytest.fixture
async def test_nginx_container(client: Client, test_host_id: str, worker_id: str,
                               dynamic_port: int, request) -> AsyncGenerator[str, None]:
    """Deploy a dedicated test nginx container for testing with guaranteed cleanup."""
    # Create unique names for parallel execution
    container_suffix = worker_id if worker_id != 'master' else 'main'
    container_name = f"test-nginx-mcp-{container_suffix}"
    stack_name = f"test-mcp-nginx-{container_suffix}"

    # Track for cleanup
    from tests.cleanup_utils import get_resource_tracker
    tracker = get_resource_tracker()

    # Deploy test container using deploy_stack with dynamic port
    compose_content = f"""version: '3.8'
services:
  {container_name}:
    image: nginx:alpine
    container_name: {container_name}
    ports:
      - "{dynamic_port}:80"
    labels:
      - "test=mcp-container-testing"
      - "purpose=dedicated-test-container"
      - "worker={worker_id}"
    restart: "no"
"""

    # Deploy the stack
    deploy_result = await client.call_tool("deploy_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "compose_content": compose_content,
        "pull_images": False,
        "recreate": False
    })

    if not deploy_result.data.get("success", False):
        pytest.skip(f"Failed to deploy test container: {deploy_result.data.get('error', 'Unknown error')}")

    # Track the deployed stack
    tracker.add_stack(test_host_id, stack_name)

    # Wait a moment for container to be ready
    await asyncio.sleep(2)

    try:
        yield container_name
    finally:
        # Cleanup using direct async call (no new event loop)
        try:
            await client.call_tool("manage_stack", {
                "host_id": test_host_id,
                "stack_name": stack_name,
                "action": "down"
            })
            tracker.remove_stack(test_host_id, stack_name)
            print(f"✓ Cleaned up test stack: {stack_name}")
        except Exception as e:
            # Record failure but don't raise
            tracker.record_failure("stack", stack_name, test_host_id, str(e))
            print(f"✗ Failed to cleanup test stack {stack_name}: {e}")


@pytest.fixture
def test_container_id(test_nginx_container: str) -> str:
    """Default test container ID for container operations."""
    return test_nginx_container


# Test data fixtures
@pytest.fixture
def worker_id(request) -> str:
    """Get pytest-xdist worker ID for parallel test isolation."""
    worker_id = getattr(request.config, 'workerinput', {}).get('workerid', 'master')
    return worker_id

@pytest.fixture
def dynamic_port(worker_id: str) -> int:
    """Generate dynamic port based on worker ID to avoid conflicts."""
    import random

    # Base port range: 8090-8199 (110 ports available)
    base_port = 8090

    if worker_id == 'master':
        # Single worker/sequential execution
        port_offset = 0
    else:
        # Extract worker number from workerid (e.g., 'gw0', 'gw1', etc.)
        worker_num = int(worker_id.replace('gw', '')) if worker_id.startswith('gw') else 0
        port_offset = (worker_num * 10) + random.randint(0, 9)

    return base_port + port_offset

@pytest.fixture
def simple_compose_content(dynamic_port: int) -> str:
    """Simple Docker Compose content for testing with dynamic ports."""
    return f"""version: '3.8'
services:
  test-web:
    image: nginx:alpine
    ports:
      - "{dynamic_port}:80"
    labels:
      - "test=mcp-validation"
"""


@pytest.fixture
def complex_compose_content(dynamic_port: int) -> str:
    """Complex Docker Compose content for testing with environment variables."""
    redis_port = dynamic_port + 1
    return f"""version: '3.8'
services:
  app:
    image: nginx:alpine
    ports:
      - "{dynamic_port}:80"
    environment:
      - TEST_ENV=${{TEST_ENV}}
      - DEBUG=${{DEBUG}}
    labels:
      - "test=mcp-complex"
  redis:
    image: redis:alpine
    ports:
      - "{redis_port}:6379"
"""


@pytest.fixture
def test_environment() -> dict[str, str]:
    """Test environment variables for compose deployments."""
    return {
        "TEST_ENV": "production",
        "DEBUG": "false"
    }


# Pytest marks for different test categories
pytest_plugins = []

# Mark for tests that require real Docker hosts
requires_docker_host = pytest.mark.skipif(
    False,  # We always have Docker hosts available in our test environment
    reason="Requires configured Docker host"
)

# Mark for tests that are slow (>10 seconds)
slow_test = pytest.mark.slow

# Mark for integration tests
integration_test = pytest.mark.integration


# Session-level cleanup fixture
@pytest.fixture(scope="session", autouse=True)
async def session_cleanup(request):
    """Session-level cleanup to ensure all test resources are removed."""
    import atexit
    import signal

    # Register cleanup for abnormal termination
    def emergency_cleanup_sync():
        """Synchronous cleanup for signal handlers."""
        try:
            from tests.cleanup_utils import get_resource_tracker
            tracker = get_resource_tracker()
            report = tracker.get_cleanup_report()

            if report["summary"]["total_remaining_containers"] > 0 or \
               report["summary"]["total_remaining_stacks"] > 0:
                print("\n⚠️  EMERGENCY CLEANUP: Cleaning up test resources on exit...")
                # Note: Can't do async cleanup in signal handler, just report
                print(f"  - {report['summary']['total_remaining_containers']} containers remaining")
                print(f"  - {report['summary']['total_remaining_stacks']} stacks remaining")
                print("  Run 'python tests/cleanup.py' to clean up manually")
        except Exception as e:
            print(f"Emergency cleanup error: {e}")

    # Register cleanup handlers
    atexit.register(emergency_cleanup_sync)

    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def cleanup_signal_handler(signum, frame):
        emergency_cleanup_sync()
        # Call original handler
        if callable(original_sigint):
            original_sigint(signum, frame)
        else:
            exit(1)

    signal.signal(signal.SIGINT, cleanup_signal_handler)
    signal.signal(signal.SIGTERM, cleanup_signal_handler)

    try:
        yield  # Run tests
    finally:
        # Restore original signal handlers
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)

        # After all tests complete, clean up any remaining resources
        from tests.cleanup_utils import emergency_cleanup, get_resource_tracker

        tracker = get_resource_tracker()
        report = tracker.get_cleanup_report()

        if report["summary"]["total_remaining_containers"] > 0 or \
           report["summary"]["total_remaining_stacks"] > 0:
            print("\n" + "="*60)
            print("SESSION CLEANUP: Cleaning up remaining test resources")
            print("="*60)

        # Try to clean up remaining resources
        try:
            # Load config to get hosts
            config_path = Path(__file__).parent.parent / "config" / "hosts.yml"
            from docker_mcp.core.config_loader import load_config
            from docker_mcp.server import DockerMCPServer

            config = load_config(str(config_path))
            server = DockerMCPServer(config)
            server._initialize_app()

            async with Client(server.app) as client:
                for host_id in config.hosts.keys():
                    if host_id in tracker.stacks or host_id in tracker.containers:
                        print(f"\nCleaning up resources on {host_id}...")
                        await emergency_cleanup(client, host_id)

        except Exception as e:
            print(f"Session cleanup error: {e}")

        # Print final report
        final_report = tracker.get_cleanup_report()
        if final_report["summary"]["total_failures"] > 0:
            print("\n⚠️  Some resources could not be cleaned up:")
            for failure in final_report["failed_cleanups"]:
                print(f"  - {failure['type']} {failure['name']} on {failure['host_id']}: {failure['error']}")
        else:
            print("\n✅ All test resources cleaned up successfully!")


# ====================
# MIDDLEWARE TESTING FIXTURES
# ====================

@pytest.fixture
def caplog_setup(caplog):
    """Setup caplog with proper logging configuration."""
    caplog.set_level(logging.DEBUG)

    # Configure structlog for testing
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True
    )

    yield caplog


@pytest.fixture
def mock_server_base():
    """Create a basic FastMCP server without middleware for testing."""
    server = FastMCP("test-server")

    # Add test tools
    @server.tool
    async def success_tool() -> dict[str, Any]:
        """Tool that always succeeds."""
        return {"status": "success", "message": "Operation completed"}

    @server.tool
    async def error_tool() -> dict[str, Any]:
        """Tool that raises an error."""
        raise ValueError("Test error message")

    @server.tool
    async def slow_tool() -> dict[str, Any]:
        """Tool that simulates slow operation."""
        await asyncio.sleep(0.1)  # 100ms delay
        return {"status": "slow", "duration": "100ms"}

    @server.tool
    async def timeout_tool() -> dict[str, Any]:
        """Tool that raises timeout error."""
        raise TimeoutError("Operation timed out")

    @server.tool
    async def permission_tool() -> dict[str, Any]:
        """Tool that raises permission error."""
        raise PermissionError("Access denied")

    @server.tool
    async def sensitive_tool(password: str, api_key: str) -> dict[str, Any]:
        """Tool with sensitive parameters for testing sanitization."""
        return {"status": "authenticated", "password": password, "api_key": api_key}

    return server


@pytest.fixture
def logging_middleware():
    """Create LoggingMiddleware for testing."""
    return LoggingMiddleware(
        include_payloads=True,
        max_payload_length=100
    )


@pytest.fixture
def error_handling_middleware():
    """Create ErrorHandlingMiddleware for testing."""
    return ErrorHandlingMiddleware(
        include_traceback=True,
        track_error_stats=True
    )


@pytest.fixture
def timing_middleware():
    """Create TimingMiddleware for testing."""
    return TimingMiddleware(
        slow_request_threshold_ms=50.0,  # 50ms threshold for testing
        track_statistics=True,
        max_history_size=100
    )


@pytest.fixture
def rate_limiting_middleware():
    """Create RateLimitingMiddleware for testing."""
    return RateLimitingMiddleware(
        max_requests_per_second=5.0,  # Low limit for testing
        burst_capacity=10,
        enable_global_limit=True,
        cleanup_interval=1.0  # 1 second cleanup for testing
    )


@pytest.fixture
def server_with_logging(mock_server_base, logging_middleware):
    """Server with logging middleware."""
    mock_server_base.add_middleware(logging_middleware)
    return mock_server_base


@pytest.fixture
def server_with_error_handling(mock_server_base, error_handling_middleware):
    """Server with error handling middleware."""
    mock_server_base.add_middleware(error_handling_middleware)
    return mock_server_base


@pytest.fixture
def server_with_timing(mock_server_base, timing_middleware):
    """Server with timing middleware."""
    mock_server_base.add_middleware(timing_middleware)
    return mock_server_base


@pytest.fixture
def server_with_rate_limiting(mock_server_base, rate_limiting_middleware):
    """Server with rate limiting middleware."""
    mock_server_base.add_middleware(rate_limiting_middleware)
    return mock_server_base


@pytest.fixture
def server_with_all_middleware(
    mock_server_base,
    error_handling_middleware,
    rate_limiting_middleware,
    timing_middleware,
    logging_middleware
):
    """Server with full middleware stack for integration testing."""
    # Add middleware in execution order (first added = first executed)
    mock_server_base.add_middleware(error_handling_middleware)
    mock_server_base.add_middleware(rate_limiting_middleware)
    mock_server_base.add_middleware(timing_middleware)
    mock_server_base.add_middleware(logging_middleware)
    return mock_server_base


@pytest.fixture
def mock_context():
    """Create mock MiddlewareContext for unit tests."""

    context = MagicMock()
    context.method = "test_method"
    context.source = "test_client"
    context.type = "request"
    context.timestamp = 1640995200.0  # Fixed timestamp for predictable tests
    context.message = MagicMock()
    context.message.__dict__ = {
        "method": "test_method",
        "params": {"test_param": "test_value"}
    }

    return context


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create temporary log directory for testing."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir


@pytest.fixture
def setup_logging_config(temp_log_dir):
    """Setup logging configuration for testing."""
    from docker_mcp.core.logging_config import setup_logging

    setup_logging(
        log_dir=temp_log_dir,
        log_level="DEBUG",
        max_file_size_mb=1  # Small file size for testing
    )

    return temp_log_dir


# ====================
# MIDDLEWARE TEST UTILITIES
# ====================

def assert_log_contains(caplog, level, message_part):
    """Assert that logs contain a specific message at a specific level."""
    for record in caplog.records:
        if record.levelname == level and message_part in record.getMessage():
            return True
    pytest.fail(f"Expected {level} log containing '{message_part}' not found")


def get_log_messages(caplog, level=None):
    """Get all log messages, optionally filtered by level."""
    if level:
        return [record.getMessage() for record in caplog.records if record.levelname == level]
    return [record.getMessage() for record in caplog.records]


class MockCall:
    """Mock call_next function for middleware testing."""

    def __init__(self, return_value=None, exception=None, delay=0):
        self.return_value = return_value or {"status": "success"}
        self.exception = exception
        self.delay = delay
        self.call_count = 0

    async def __call__(self, context):
        self.call_count += 1

        if self.delay > 0:
            await asyncio.sleep(self.delay)

        if self.exception:
            raise self.exception

        return self.return_value


class MockTimestamps:
    """Mock timestamps for predictable timing tests."""

    def __init__(self, start_time=1640995200.0):
        self.current_time = start_time
        self.time_increments = []

    def add_increment(self, seconds):
        """Add time increment for next call."""
        self.time_increments.append(seconds)

    def time(self):
        """Mock time.time() function."""
        if self.time_increments:
            self.current_time += self.time_increments.pop(0)
        return self.current_time

    def perf_counter(self):
        """Mock time.perf_counter() function."""
        return self.time()

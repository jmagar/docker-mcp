"""
FastMCP Docker SSH Manager Server

A production-ready FastMCP server for managing Docker containers and stacks
across multiple remote hosts via SSH connections.
"""

import argparse
import importlib
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

if TYPE_CHECKING:
    from docker_mcp.core.docker_context import DockerContextManager
    from docker_mcp.services.container import ContainerService
    from docker_mcp.services.host import HostService
    from docker_mcp.services.stack_service import StackService

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from pydantic import Field

try:
    from .core.config_loader import DockerMCPConfig, load_config
    from .core.docker_context import DockerContextManager
    from .core.file_watcher import HotReloadManager
    from .core.logging_config import get_server_logger
    from .middleware import (
        ErrorHandlingMiddleware,
        LoggingMiddleware,
        RateLimitingMiddleware,
        TimingMiddleware,
    )
    from .models.params import DockerComposeParams, DockerContainerParams, DockerHostsParams

    # All tool_params removed - they were unused
    from .resources import (
        DockerComposeResource,
        DockerContainersResource,
        DockerInfoResource,
        PortMappingResource,
    )
    from .services import ConfigService, ContainerService, HostService, StackService
    from .services.cleanup import CleanupService
except ImportError:
    from docker_mcp.core.config_loader import DockerMCPConfig, load_config
    from docker_mcp.core.docker_context import DockerContextManager
    from docker_mcp.core.file_watcher import HotReloadManager
    from docker_mcp.core.logging_config import get_server_logger
    from docker_mcp.middleware import (
        ErrorHandlingMiddleware,
        LoggingMiddleware,
        RateLimitingMiddleware,
        TimingMiddleware,
    )
    from docker_mcp.resources import (
        DockerComposeResource,
        DockerContainersResource,
        DockerInfoResource,
        PortMappingResource,
    )
    from docker_mcp.services import ConfigService, ContainerService, HostService, StackService
    from docker_mcp.services.cleanup import CleanupService


# Import enum definitions
try:
    from .models.enums import (
        ComposeAction,
        ContainerAction,
        HostAction,
    )
except ImportError:
    from docker_mcp.models.enums import (
        ComposeAction,
        ContainerAction,
        HostAction,
    )


def get_data_dir() -> Path:
    """Get data directory based on environment with comprehensive validation.

    Priority order:
    1. FASTMCP_DATA_DIR (explicit override)
    2. DOCKER_MCP_DATA_DIR (application-specific)
    3. XDG_DATA_HOME (Linux/Unix standard)
    4. Container detection (/app/data)
    5. User home fallback (~/.docker-mcp/data)
    6. System temp fallback (/tmp/docker-mcp)
    """
    # Environment variable candidates in priority order (normalized to Path)
    env_candidates: list[Path | None] = [
        (Path(p) if (p := os.getenv("FASTMCP_DATA_DIR")) else None),
        (Path(p) if (p := os.getenv("DOCKER_MCP_DATA_DIR")) else None),
        (Path(xdg_path) / "docker-mcp") if (xdg_path := os.getenv("XDG_DATA_HOME")) else None,
    ]

    # Check explicit environment overrides
    for candidate in env_candidates:
        if candidate:
            candidate_path = candidate
            # Validate the path can be created and is writable
            try:
                candidate_path.mkdir(parents=True, exist_ok=True)
                # Test write permissions
                test_file = candidate_path / ".write_test"
                test_file.touch()
                test_file.unlink()
                return candidate_path
            except (OSError, PermissionError, FileNotFoundError):
                # If we can't create/write, continue to next candidate
                continue

    # Check if running in container with comprehensive detection
    container_indicators = [
        os.getenv("DOCKER_CONTAINER", "").lower() in ("1", "true", "yes", "on"),
        os.path.exists("/.dockerenv"),
        os.path.exists("/app"),
        os.getenv("container") is not None,  # systemd container detection
    ]

    if any(container_indicators):
        container_path = Path("/app/data")
        try:
            container_path.mkdir(parents=True, exist_ok=True)
            return container_path
        except (OSError, PermissionError):
            # Container path failed, fall through to other options
            pass

    # Standard user data directory fallbacks
    fallback_candidates = [
        Path.home() / ".docker-mcp" / "data",  # Primary user directory
        Path.home() / ".local" / "share" / "docker-mcp",  # XDG-style fallback
        Path(tempfile.gettempdir())
        / "docker-mcp"
        / str(os.getuid() if hasattr(os, "getuid") else "user"),  # Temp with user isolation
        Path(tempfile.gettempdir()) / "docker-mcp",  # Final fallback
    ]

    for fallback_path in fallback_candidates:
        try:
            fallback_path.mkdir(parents=True, exist_ok=True)
            # Test write permissions
            test_file = fallback_path / ".write_test"
            test_file.touch()
            test_file.unlink()
            return fallback_path
        except (OSError, PermissionError, FileNotFoundError):
            continue

    # If all else fails, return the primary fallback even if not writable
    # Let the calling code handle the permission error
    return Path.home() / ".docker-mcp" / "data"


def get_config_dir() -> Path:
    """Get config directory based on environment with comprehensive validation.

    Priority order:
    1. FASTMCP_CONFIG_DIR (explicit override)
    2. DOCKER_MCP_CONFIG_DIR (application-specific)
    3. XDG_CONFIG_HOME (Linux/Unix standard)
    4. Container detection (/app/config)
    5. Local project config (./config)
    6. User config fallback (~/.config/docker-mcp)
    7. System config fallback (/etc/docker-mcp)
    """
    # Try environment variables first
    if env_path := _try_environment_config_dirs():
        return env_path if env_path.is_absolute() else (Path.cwd() / env_path)

    # Check for container environment
    if container_path := _try_container_config_dir():
        return container_path if container_path.is_absolute() else (Path.cwd() / container_path)

    # Try fallback directories
    if fallback_path := _try_fallback_config_dirs():
        return fallback_path if fallback_path.is_absolute() else (Path.cwd() / fallback_path)

    # Final fallback — prefer absolute path for consistency
    return Path.cwd() / "config"


def _try_environment_config_dirs() -> Path | None:
    """Try environment variable config directories in priority order."""
    env_candidates = [
        os.getenv("FASTMCP_CONFIG_DIR"),
        os.getenv("DOCKER_MCP_CONFIG_DIR"),
        _get_xdg_config_dir(),
    ]

    for candidate in env_candidates:
        if candidate:
            if path := _try_create_config_dir(Path(candidate)):
                return path

    return None


def _get_xdg_config_dir() -> str | None:
    """Get XDG config directory path if available."""
    xdg_home = os.getenv("XDG_CONFIG_HOME")
    return str(Path(xdg_home) / "docker-mcp") if xdg_home else None


def _try_container_config_dir() -> Path | None:
    """Try container config directory if running in container."""
    container_indicators = [
        os.getenv("DOCKER_CONTAINER", "").lower() in ("1", "true", "yes", "on"),
        os.path.exists("/.dockerenv"),
        os.path.exists("/app"),
        os.getenv("container") is not None,  # systemd container detection
    ]

    if any(container_indicators):
        container_path = Path("/app/config")
        return _try_create_config_dir(container_path)

    return None


def _try_fallback_config_dirs() -> Path | None:
    """Try fallback config directories, preferring existing ones."""
    fallback_candidates = [
        Path("config"),  # Local project config (for development)
        Path.cwd() / "config",  # Current working directory config
        Path.home() / ".config" / "docker-mcp",  # User config directory
        Path.home() / ".docker-mcp" / "config",  # Alternative user config
        Path("/etc/docker-mcp"),  # System-wide config (read-only usually)
    ]

    # First pass: look for existing readable directories
    for candidate_path in fallback_candidates:
        if _is_readable_config_dir(candidate_path):
            return candidate_path

    # Second pass: try to create directories (skip system dir)
    for candidate_path in fallback_candidates[:-1]:
        if path := _try_create_config_dir(candidate_path):
            return path

    return None


def _try_create_config_dir(candidate_path: Path) -> Path | None:
    """Try to create or validate a config directory."""
    try:
        if candidate_path.exists() and candidate_path.is_dir():
            return candidate_path
        # Try to create if it doesn't exist
        candidate_path.mkdir(parents=True, exist_ok=True)
        return candidate_path
    except (OSError, PermissionError):
        return None


def _is_readable_config_dir(candidate_path: Path) -> bool:
    """Check if a path is an existing readable config directory."""
    if not (candidate_path.exists() and candidate_path.is_dir()):
        return False

    try:
        # Test if we can read from the directory
        list(candidate_path.iterdir())
        return True
    except (OSError, PermissionError):
        return False


class DockerMCPServer:
    """FastMCP server for Docker management via Docker contexts."""

    def _parse_env_float(self, var_name: str, default: float) -> float:
        """Safely parse environment variable as float with default fallback."""
        try:
            value = os.getenv(var_name)
            if value is None:
                return default
            return float(value)
        except ValueError:
            self.logger.warning(
                f"Invalid {var_name}; using default", value=os.getenv(var_name), default=default
            )
            return default

    def _parse_env_int(self, var_name: str, default: int) -> int:
        """Safely parse environment variable as int with default fallback."""
        try:
            value = os.getenv(var_name)
            if value is None:
                return default
            return int(value)
        except ValueError:
            self.logger.warning(
                f"Invalid {var_name}; using default", value=os.getenv(var_name), default=default
            )
            return default

    def _parse_env_bool(self, var_name: str, default: bool) -> bool:
        """Safely parse environment variable as bool with default fallback."""
        try:
            value = os.getenv(var_name)
            if value is None:
                return default
            return value.strip().lower() in ("1", "true", "yes", "on")
        except (ValueError, AttributeError):
            self.logger.warning(
                f"Invalid {var_name}; using default", value=os.getenv(var_name), default=default
            )
            return default

    def __init__(self, config: DockerMCPConfig, config_path: str | None = None):
        self.config = config
        self._config_path: str = (
            config_path or os.getenv("DOCKER_HOSTS_CONFIG") or str(get_config_dir() / "hosts.yml")
        )

        # Use server logger (writes to mcp_server.log)
        self.logger = get_server_logger()

        # Initialize core managers
        self.context_manager = DockerContextManager(config)

        # Initialize service layer
        from .services.logs import LogsService

        self.logs_service: LogsService = LogsService(config, self.context_manager)
        self.host_service: HostService = HostService(config, self.context_manager)
        self.container_service: ContainerService = ContainerService(
            config, self.context_manager, self.logs_service
        )
        self.stack_service: StackService = StackService(
            config, self.context_manager, self.logs_service
        )
        self.config_service = ConfigService(config, self.context_manager)
        self.cleanup_service = CleanupService(config)

        # No legacy log tools; logs handled via LogsService

        # Initialize hot reload manager (always enabled)
        self.hot_reload_manager = HotReloadManager()
        self.hot_reload_manager.setup_hot_reload(self._config_path, self)

        # FastMCP app will be created later to prevent auto-start
        self.app: FastMCP | None = None

        self.logger.info(
            "Docker MCP Server initialized",
            hosts=list(config.hosts.keys()),
            server_config=config.server.model_dump(),
            hot_reload_enabled=True,
            config_path=self._config_path,
        )

    def _initialize_app(self) -> None:
        """Initialize FastMCP app, middleware, and register tools."""
        # Create FastMCP server (attach Google OAuth if configured)
        auth_provider = self._build_auth_provider()
        if auth_provider is not None:
            provider_name = auth_provider.__class__.__name__
            self.app = FastMCP("Docker Context Manager", auth=auth_provider)
            self.logger.info("Authentication provider enabled", provider=provider_name)
            self._register_auth_diagnostic_tools()
        else:
            self.app = FastMCP("Docker Context Manager")
            self.logger.info("Authentication provider disabled")

        # Set up test compatibility wrapper
        self._setup_test_compatibility()

        # Configure middleware stack
        self._configure_middleware()

        # Parse environment values safely at runtime
        rate_limit_val = self._parse_env_float("RATE_LIMIT_PER_SECOND", 50.0)
        threshold_val = self._parse_env_float("SLOW_REQUEST_THRESHOLD_MS", 5000.0)

        rate_limit_display = f"{rate_limit_val} req/sec"
        threshold_display = f"{threshold_val}ms threshold"

        self.logger.info(
            "FastMCP middleware initialized",
            error_handling=True,
            rate_limiting=rate_limit_display,
            timing_monitoring=threshold_display,
            logging="dual output (console + files)",
        )

        if auth_provider is None:
            self.logger.info("OAuth diagnostic tools unavailable (authentication disabled)")

        # Register consolidated tools (3 tools replace 13 individual tools)
        self.app.tool(
            self.docker_hosts,
            annotations={
                "title": "Docker Host Management",
                "readOnlyHint": False,  # Some actions (list, ports) read-only, others modify
                "destructiveHint": False,  # Most actions are safe, cleanup can be destructive
                "idempotentHint": False,  # Varies by action (add is not, list is)
                "openWorldHint": True,  # Connects to external Docker hosts via SSH
            },
        )
        self.app.tool(
            self.docker_container,
            annotations={
                "title": "Docker Container Management",
                "readOnlyHint": False,  # Some actions (list, info, logs) read-only, others modify
                "destructiveHint": False,  # Containers are ephemeral, operations are non-destructive
                "idempotentHint": False,  # Varies by action (start/stop not idempotent, list is)
                "openWorldHint": True,  # Connects to external Docker hosts
            },
        )
        self.app.tool(
            self.docker_compose,
            annotations={
                "title": "Docker Compose Stack Management",
                "readOnlyHint": False,  # Some actions (list, discover, logs) read-only, others modify
                "destructiveHint": True,  # down action destroys containers, migrate can remove source
                "idempotentHint": False,  # Varies by action (deploy can be, up/down are not)
                "openWorldHint": True,  # Connects to external Docker hosts and file systems
            },
        )

        # Register MCP resources for data access (complement tools with clean URI-based data retrieval)
        self._register_resources()

    def _setup_test_compatibility(self) -> None:
        """Set up test compatibility wrapper for list_tools."""
        if self.app is None:
            return
        try:
            app_ref = self.app

            def _list_tools_sync():
                """Synchronous wrapper for list_tools."""
                return self._get_tools_from_app(app_ref)

            # Attach wrapper only if list_tools is absent
            if not hasattr(self.app, "list_tools"):
                self.app.list_tools = _list_tools_sync
        except Exception as e:
            # Log the exception but continue
            self.logger.debug("Failed to set up test compatibility wrapper", error=str(e))

    def _get_tools_from_app(self, app_ref) -> list:
        """Extract tools from FastMCP app with proper async handling."""
        getter = self._get_tool_getter(app_ref)
        if getter is None:
            return []

        result = getter()
        result = self._handle_async_result(result)

        if result is None:
            return []

        tools_iterable = self._extract_tools_iterable(result)
        return self._build_compatibility_tools(tools_iterable)

    def _get_tool_getter(self, app_ref):
        """Get the appropriate tool getter method from the app."""
        getter = getattr(app_ref, "get_tools", None)
        if getter is None:
            # Some FastMCP versions might already have list_tools
            getter = getattr(app_ref, "list_tools", None)
        return getter

    def _handle_async_result(self, result):
        """Handle async coroutine results properly."""
        import inspect

        if not inspect.iscoroutine(result):
            return result

        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        try:
            # If no loop is currently running in this thread, run directly
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(result)
        else:
            # When a loop is already active (pytest-asyncio, etc.), execute the
            # coroutine in a helper thread to avoid re-entry issues.
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, result)
                result = future.result()

        return result

    def _extract_tools_iterable(self, result):
        """Extract tools from result object."""
        if isinstance(result, dict):
            return result.values()
        return result or []

    def _build_compatibility_tools(self, tools_iterable) -> list:
        """Build compatibility tool objects."""
        from copy import deepcopy
        from types import SimpleNamespace

        tools_list = list(tools_iterable)
        compat_tools = []

        for tool in tools_list:
            input_schema = getattr(tool, "input_schema", None)
            if input_schema is None:
                input_schema = getattr(tool, "parameters", None)

            if isinstance(input_schema, dict | list):
                schema_copy = deepcopy(input_schema)
            else:
                schema_copy = input_schema

            compat_tools.append(
                SimpleNamespace(
                    name=getattr(tool, "name", ""),
                    description=getattr(tool, "description", ""),
                    inputSchema=schema_copy,
                    raw_tool=tool,
                )
            )

        return compat_tools

    def _configure_middleware(self) -> None:
        """Configure FastMCP middleware stack."""
        if self.app is None:
            return
        # Add middleware in logical order (first added = first executed)
        # Error handling first to catch all errors
        self.app.add_middleware(
            ErrorHandlingMiddleware(
                include_traceback=os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG",
                track_error_stats=True,
            )
        )

        # Rate limiting to protect against abuse (robust parsing)
        rate_limit = self._parse_env_float("RATE_LIMIT_PER_SECOND", 50.0)
        burst_capacity = int(rate_limit * 2)
        self.app.add_middleware(
            RateLimitingMiddleware(
                max_requests_per_second=rate_limit,
                burst_capacity=burst_capacity,
                enable_global_limit=True,
            )
        )

        # Timing middleware to monitor performance (robust parsing)
        slow_threshold = self._parse_env_float("SLOW_REQUEST_THRESHOLD_MS", 5000.0)
        self.app.add_middleware(
            TimingMiddleware(slow_request_threshold_ms=slow_threshold, track_statistics=True)
        )

        # Logging middleware last to log everything (including middleware processing)
        include_payloads = self._parse_env_bool("LOG_INCLUDE_PAYLOADS", True)
        max_payload_length = self._parse_env_int("LOG_MAX_PAYLOAD_LENGTH", 1000)
        self.app.add_middleware(
            LoggingMiddleware(
                include_payloads=include_payloads,
                max_payload_length=max_payload_length,
            )
        )

    def _register_auth_diagnostic_tools(self) -> None:
        """Register small diagnostic tools when OAuth authentication is active."""
        if self.app is None:
            return

        try:
            from fastmcp.server.dependencies import get_access_token
        except Exception:
            self.logger.debug(
                "Auth dependencies unavailable; skipping whoami/get_user_info tools",
                exc_info=True,
            )
            return

        @self.app.tool
        async def whoami() -> dict[str, Any]:
            """Return identity claims for the authenticated user."""
            token = get_access_token()
            if token is None:
                return {}
            return {
                "iss": token.claims.get("iss"),
                "sub": token.claims.get("sub"),
                "email": token.claims.get("email"),
                "name": token.claims.get("name"),
                "picture": token.claims.get("picture"),
            }

        self.logger.info("OAuth diagnostic tools enabled", tools=["whoami"])

    def _build_auth_provider(self) -> Any | None:
        """Build OAuth provider from environment if configured."""
        provider_path = os.getenv("FASTMCP_SERVER_AUTH", "").strip()
        if not provider_path:
            self.logger.info("OAuth authentication disabled")
            return None

        try:
            module_path, class_name = provider_path.rsplit(".", 1)
        except ValueError:
            self.logger.error(
                "Invalid FASTMCP_SERVER_AUTH value", provider=provider_path
            )
            return None

        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            self.logger.error(
                "Failed to import auth provider module",
                provider=provider_path,
                error=str(exc),
            )
            return None

        provider_cls = getattr(module, class_name, None)
        if provider_cls is None:
            self.logger.error(
                "Auth provider class not found", provider=provider_path
            )
            return None

        try:
            if provider_path.endswith("GoogleProvider"):
                base_url = self._get_auth_base_url()
                redirect_path = os.getenv(
                    "FASTMCP_SERVER_AUTH_GOOGLE_REDIRECT_PATH", "/auth/callback"
                )
                required_scopes = self._parse_auth_scopes()
                timeout = self._parse_auth_timeout()
                kwargs = self._build_provider_kwargs(
                    base_url, redirect_path, required_scopes, timeout
                )
                provider = provider_cls(**kwargs)
                self._configure_allowed_redirects(provider)
            else:
                provider = provider_cls()
        except Exception as exc:
            self.logger.error(
                "Failed to initialize auth provider",
                provider=provider_path,
                error=str(exc),
            )
            return None

        return provider

    def _get_auth_base_url(self) -> str:
        """Get the base URL for auth callbacks."""
        base_url = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL")
        if not base_url:
            # Check for TLS environment variable
            scheme = (
                "https"
                if os.getenv("FASTMCP_ENABLE_TLS", "").lower() in ("1", "true", "yes")
                else "http"
            )
            host = self.config.server.host
            port = self.config.server.port
            base_url = f"{scheme}://{host}:{port}"
        return base_url

    def _parse_auth_scopes(self) -> list[str]:
        """Parse required auth scopes from environment with enhanced validation."""
        scopes_raw = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_REQUIRED_SCOPES", "")

        if scopes_raw.strip().startswith("["):
            required_scopes = self._parse_json_scopes(scopes_raw)
        else:
            required_scopes = self._parse_comma_scopes(scopes_raw)

        # Provide sensible defaults
        if not required_scopes:
            required_scopes = [
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
            ]

        return required_scopes

    def _parse_json_scopes(self, scopes_raw: str) -> list[str]:
        """Parse JSON array format scopes."""
        try:
            import json

            parsed_scopes = json.loads(scopes_raw)
            return self._validate_parsed_scopes(parsed_scopes)
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.warning(
                "Failed to parse FASTMCP_SERVER_AUTH_GOOGLE_REQUIRED_SCOPES as JSON",
                error=str(e),
                raw_value=scopes_raw[:100] + "..." if len(scopes_raw) > 100 else scopes_raw,
            )
            return []

    def _parse_comma_scopes(self, scopes_raw: str) -> list[str]:
        """Parse comma/space-separated format scopes."""
        parts = [p.strip() for p in scopes_raw.replace(" ", ",").split(",") if p.strip()]
        validated_scopes = []
        for scope in parts:
            if self._validate_oauth_scope(scope):
                validated_scopes.append(scope)
            else:
                self.logger.warning("Invalid OAuth scope format: %s - skipping", scope)
        return validated_scopes

    def _validate_parsed_scopes(self, parsed_scopes: Any) -> list[str]:
        """Validate and filter parsed scopes."""
        if not isinstance(parsed_scopes, list):
            self.logger.warning(
                "Invalid scope format - expected JSON array, got %s", type(parsed_scopes).__name__
            )
            return []

        if len(parsed_scopes) > 50:  # Reasonable limit
            self.logger.warning("Too many scopes - maximum 50 allowed, got %d", len(parsed_scopes))
            return []

        if not all(
            isinstance(s, str) and len(s.strip()) > 0 and len(s) < 500 for s in parsed_scopes
        ):
            self.logger.warning(
                "Invalid scope entries - all entries must be non-empty strings under 500 characters"
            )
            return []

        # Validate each scope
        validated_scopes = []
        for raw_scope in parsed_scopes:
            scope = raw_scope.strip()
            if self._validate_oauth_scope(scope):
                validated_scopes.append(scope)
            else:
                self.logger.warning("Invalid OAuth scope format: %s - skipping", scope)

        return validated_scopes

    def _validate_oauth_scope(self, scope: str) -> bool:
        """Validate OAuth scope format."""
        import re

        # OAuth scope should be either:
        # 1. Simple identifier (letters, numbers, underscores, dots)
        # 2. Full URL (https://...)
        # 3. Known standard scopes (openid, profile, email, etc.)

        if not scope or len(scope) > 500:  # Reasonable length limit
            return False

        # Known standard OpenID Connect scopes
        standard_scopes = {"openid", "profile", "email", "address", "phone", "offline_access"}

        if scope in standard_scopes:
            return True

        # Valid URL pattern for Google API scopes
        url_pattern = r"^https://www\.googleapis\.com/auth/[a-zA-Z0-9._-]+$"
        if re.match(url_pattern, scope):
            return True

        # Simple identifier pattern (letters, numbers, dots, underscores, hyphens)
        simple_pattern = r"^[a-zA-Z][a-zA-Z0-9._-]*$"
        if re.match(simple_pattern, scope):
            return True

        return False

    def _parse_auth_timeout(self) -> int | None:
        """Parse auth timeout from environment."""
        timeout = None
        try:
            timeout_val = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_TIMEOUT_SECONDS")
            if timeout_val:
                timeout = int(timeout_val)
        except ValueError:
            timeout = None
        return timeout

    def _build_provider_kwargs(
        self, base_url: str, redirect_path: str, required_scopes: list[str], timeout: int | None
    ) -> dict[str, Any]:
        """Build kwargs for GoogleProvider initialization."""
        kwargs: dict[str, Any] = {
            "base_url": base_url,
            "required_scopes": required_scopes,
            "redirect_path": redirect_path,
        }

        client_id = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID")
        client_secret = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET")

        if client_id:
            kwargs["client_id"] = client_id
        if client_secret:
            kwargs["client_secret"] = client_secret
        if timeout is not None:
            kwargs["timeout_seconds"] = timeout

        return kwargs

    def _configure_allowed_redirects(self, provider) -> None:
        """Configure allowed client redirect URIs if specified."""
        allowed_redirects = os.getenv(
            "FASTMCP_SERVER_AUTH_GOOGLE_ALLOWED_CLIENT_REDIRECT_URIS", ""
        ).strip()

        if allowed_redirects:
            try:
                if allowed_redirects.startswith("["):
                    import json

                    parsed_patterns = json.loads(allowed_redirects)
                    if not isinstance(parsed_patterns, list):
                        raise ValueError("Expected list for redirect patterns")
                    patterns = [str(p).strip() for p in parsed_patterns if str(p).strip()]
                else:
                    patterns = [p.strip() for p in allowed_redirects.split(",") if p.strip()]

                # property exists in FastMCP 2.12.x
                provider.allowed_client_redirect_uris = patterns
            except Exception:
                # Older FastMCP versions may not support this; log and continue
                self.logger.debug(
                    "Skipping allowed_client_redirect_uris; provider does not support or failed to set",
                    exc_info=True,
                )

    def _register_resources(self) -> None:
        """Register MCP resources for data access.

        Resources provide clean, URI-based access to data without side effects.
        They complement tools by offering cacheable, parametrized data retrieval.
        """
        if self.app is None:
            return
        try:
            # Port mapping resource - ports://{host_id}
            port_resource = PortMappingResource(self.container_service, self)
            self.app.add_resource(port_resource)

            # Docker host info resource - docker://{host_id}/info
            info_resource = DockerInfoResource(self.context_manager, self.host_service)
            self.app.add_resource(info_resource)

            # Docker containers resource - docker://{host_id}/containers
            containers_resource = DockerContainersResource(self.container_service)
            self.app.add_resource(containers_resource)

            # Docker compose resource - docker://{host_id}/compose
            compose_resource = DockerComposeResource(self.stack_service)
            self.app.add_resource(compose_resource)

            self.logger.info(
                "MCP resources registered successfully",
                resources_count=4,
                uri_schemes=["ports://", "docker://"],
            )

        except Exception as e:
            self.logger.error("Failed to register MCP resources", error=str(e))
            # Don't fail the server startup, just log the error
            # Resources are optional enhancements to the tool-based API

    # Consolidated Tools Implementation

    async def docker_hosts(
        self,
        action: Annotated[
            str | HostAction | None,
            Field(default=None, description="Action to perform (defaults to list if not provided)"),
        ] = None,
        ssh_host: Annotated[str, Field(default="", description="SSH hostname or IP address")] = "",
        ssh_user: Annotated[str, Field(default="", description="SSH username")] = "",
        ssh_port: Annotated[
            int, Field(default=22, ge=1, le=65535, description="SSH port number")
        ] = 22,
        ssh_key_path: Annotated[
            str, Field(default="", description="Path to SSH private key file")
        ] = "",
        description: Annotated[str, Field(default="", description="Host description")] = "",
        tags: Annotated[list[str] | None, Field(default=None, description="Host tags")] = None,
        compose_path: Annotated[
            str, Field(default="", description="Docker Compose file path")
        ] = "",
        appdata_path: Annotated[
            str, Field(default="", description="Application data storage path")
        ] = "",
        enabled: Annotated[bool, Field(default=True, description="Whether host is enabled")] = True,
        ssh_config_path: Annotated[
            str, Field(default="", description="Path to SSH config file")
        ] = "",
        selected_hosts: Annotated[
            str, Field(default="", description="Comma-separated list of hosts to select")
        ] = "",
        cleanup_type: Annotated[
            Literal["check", "safe", "moderate", "aggressive"] | None,
            Field(default=None, description="Type of cleanup to perform"),
        ] = None,
        port: Annotated[
            int, Field(default=0, ge=0, le=65535, description="Port number to check availability")
        ] = 0,
        host_id: Annotated[str, Field(default="", description="Host identifier")] = "",
    ) -> ToolResult | dict[str, Any]:
        """Simplified Docker hosts management tool.

        Actions:
        • list: List all configured Docker hosts
          - Required: none

        • add: Add a new Docker host (auto-runs test_connection and discover)
          - Required: ssh_host, ssh_user, host_id
          - Optional: ssh_port (default: 22), ssh_key_path, description, tags, enabled (default: true)

        • ports: List or check port usage on a host
          - Required: host_id
          - Optional: port (for availability check)

        • import_ssh: Import hosts from SSH config (auto-runs test_connection and discover for each)
          - Required: none
          - Optional: ssh_config_path, selected_hosts

        • cleanup: Docker system cleanup
          - Required: cleanup_type, host_id
          - Valid cleanup_type: "check" | "safe" | "moderate" | "aggressive"

        • test_connection: Test host connectivity (also runs discover)
          - Required: host_id

        • discover: Discover paths and capabilities on hosts
          - Required: host_id (use 'all' to discover all hosts sequentially)
          - Discovers: compose_path, appdata_path
          - Single host: Fast discovery (5-15 seconds)
          - All hosts: Sequential discovery (30-60 seconds total)
          - Auto-tags: Adds discovery status tags

        • edit: Modify host configuration
          - Required: host_id
          - Optional: ssh_host, ssh_user, ssh_port, ssh_key_path, description, tags, compose_path, appdata_path, enabled

        • remove: Remove host from configuration
          - Required: host_id
        """
        # Parse and validate parameters using the parameter model
        try:
            # Convert string action to enum
            if isinstance(action, str):
                action_enum = HostAction(action)
            elif action is None:
                action_enum = HostAction.LIST
            else:
                action_enum = action

            params = DockerHostsParams(
                action=action_enum,
                ssh_host=ssh_host,
                ssh_user=ssh_user,
                ssh_port=ssh_port,
                ssh_key_path=ssh_key_path if ssh_key_path else None,
                description=description,
                tags=tags or [],
                compose_path=compose_path if compose_path else None,
                appdata_path=appdata_path if appdata_path else None,
                enabled=enabled,
                port=port,
                cleanup_type=cleanup_type,
                ssh_config_path=ssh_config_path if ssh_config_path else None,
                selected_hosts=selected_hosts if selected_hosts else None,
                host_id=host_id,
            )
            # Use validated enum from parameter model
            action = params.action
        except Exception as e:
            return {
                "success": False,
                "error": f"Parameter validation failed: {str(e)}",
                "action": str(action) if action else "unknown",
            }

        # Delegate to service layer for business logic
        service_result = await self.host_service.handle_action(
            action, **params.model_dump(exclude={"action"})
        )

        # Check if service returned formatted output and convert to ToolResult
        # This preserves the token-efficient formatting created by ContainerService
        if isinstance(service_result, dict) and "formatted_output" in service_result:
            from fastmcp.tools.tool import ToolResult
            from mcp.types import TextContent

            formatted_text = service_result.get("formatted_output", "")
            if formatted_text:
                return ToolResult(
                    content=[TextContent(type="text", text=formatted_text)],
                    structured_content=service_result
                )

        # Return service result as-is (dict for unformatted actions)
        return service_result

    async def docker_container(
        self,
        action: Annotated[str | ContainerAction, Field(description="Action to perform")],
        container_id: Annotated[str, Field(default="", description="Container identifier")] = "",
        image_name: Annotated[str, Field(default="", description="Image name for pull action")] = "",
        all_containers: Annotated[
            bool, Field(default=False, description="Include all containers, not just running")
        ] = False,
        limit: Annotated[
            int, Field(default=20, ge=1, le=1000, description="Maximum number of results to return")
        ] = 20,
        offset: Annotated[int, Field(default=0, ge=0, description="Number of results to skip")] = 0,
        follow: Annotated[bool, Field(default=False, description="Follow log output")] = False,
        lines: Annotated[
            int, Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve")
        ] = 100,
        force: Annotated[bool, Field(default=False, description="Force the operation")] = False,
        timeout: Annotated[
            int, Field(default=10, ge=1, le=300, description="Operation timeout in seconds")
        ] = 10,
        host_id: Annotated[str, Field(default="", description="Host identifier")] = "",
    ) -> ToolResult | dict[str, Any]:
        """Consolidated Docker container management tool.

        Actions:
        • list: List containers on a host
          - Required: host_id
          - Optional: all_containers, limit, offset

        • info: Get container information
          - Required: container_id, host_id

        • start: Start a container
          - Required: container_id, host_id
          - Optional: force, timeout

        • stop: Stop a container
          - Required: container_id, host_id
          - Optional: force, timeout

        • restart: Restart a container
          - Required: container_id, host_id
          - Optional: force, timeout

        • remove: Remove a container
          - Required: container_id, host_id
          - Optional: force

        • logs: Get container logs
          - Required: container_id, host_id
          - Optional: follow, lines

        • pull: Pull a container image
          - Required: image_name, host_id
        """
        # Parse and validate parameters using the parameter model
        try:
            # Convert string action to enum
            if isinstance(action, str):
                action_enum = ContainerAction(action)
            else:
                action_enum = action

            params = DockerContainerParams(
                action=action_enum,
                container_id=container_id,
                image_name=image_name,
                all_containers=all_containers,
                limit=limit,
                offset=offset,
                follow=follow,
                lines=lines,
                force=force,
                timeout=timeout,
                host_id=host_id,
            )
            # Use validated enum from parameter model
            action = params.action
        except Exception as e:
            return {
                "success": False,
                "error": f"Parameter validation failed: {str(e)}",
                "action": str(action) if action else "unknown",
            }

        # Delegate to service layer for business logic
        return await self.container_service.handle_action(
            action, **params.model_dump(exclude={"action"})
        )

    async def docker_compose(
        self,
        action: Annotated[str | ComposeAction, Field(description="Action to perform")],
        stack_name: Annotated[str, Field(default="", description="Stack name")] = "",
        compose_content: Annotated[
            str, Field(default="", description="Docker Compose file content")
        ] = "",
        environment: Annotated[
            dict[str, str] | None, Field(default=None, description="Environment variables")
        ] = None,
        pull_images: Annotated[
            bool, Field(default=True, description="Pull images before deploying")
        ] = True,
        recreate: Annotated[bool, Field(default=False, description="Recreate containers")] = False,
        follow: Annotated[bool, Field(default=False, description="Follow log output")] = False,
        lines: Annotated[
            int, Field(default=100, ge=1, le=10000, description="Number of log lines to retrieve")
        ] = 100,
        dry_run: Annotated[
            bool, Field(default=False, description="Perform a dry run without making changes")
        ] = False,
        options: Annotated[
            dict[str, str] | None,
            Field(default=None, description="Additional options for the operation"),
        ] = None,
        target_host_id: Annotated[
            str, Field(default="", description="Target host ID for migration operations")
        ] = "",
        remove_source: Annotated[
            bool, Field(default=False, description="Remove source stack after migration")
        ] = False,
        skip_stop_source: Annotated[
            bool, Field(default=False, description="Skip stopping source stack before migration")
        ] = False,
        start_target: Annotated[
            bool, Field(default=True, description="Start target stack after migration")
        ] = True,
        host_id: Annotated[str, Field(default="", description="Host identifier")] = "",
    ) -> ToolResult | dict[str, Any]:
        """Consolidated Docker Compose stack management tool.

        Actions:
        • list: List stacks on a host
          - Required: host_id

        • view: View the compose file for a stack
          - Required: stack_name, host_id

        • deploy: Deploy a stack
          - Required: stack_name, compose_content, host_id
          - Optional: environment, pull_images, recreate

        • up/down/restart/build/pull: Manage stack lifecycle
          - Required: stack_name, host_id
          - Optional: options

        • ps: List services in a stack
          - Required: stack_name, host_id
          - Optional: options

        • discover: Discover compose paths on a host
          - Required: host_id

        • logs: Get stack logs
          - Required: stack_name, host_id
          - Optional: follow, lines

        • migrate: Migrate stack between hosts
          - Required: stack_name, target_host_id, host_id
          - Optional: remove_source, skip_stop_source, start_target, dry_run
        """
        # Parse and validate parameters using the parameter model
        try:
            # Convert string action to enum
            if isinstance(action, str):
                action_enum = ComposeAction(action)
            else:
                action_enum = action

            params = DockerComposeParams(
                action=action_enum,
                stack_name=stack_name,
                compose_content=compose_content,
                environment=environment or {},
                pull_images=pull_images,
                recreate=recreate,
                follow=follow,
                lines=lines,
                dry_run=dry_run,
                options=options or {},
                target_host_id=target_host_id,
                remove_source=remove_source,
                skip_stop_source=skip_stop_source,
                start_target=start_target,
                host_id=host_id,
            )
            # Use validated enum from parameter model
            action = params.action
        except Exception as e:
            return {
                "success": False,
                "error": f"Parameter validation failed: {str(e)}",
                "action": str(action) if action else "unknown",
            }

        # Delegate to service layer for business logic
        return await self.stack_service.handle_action(
            action, **params.model_dump(exclude={"action"})
        )

    async def add_docker_host(
        self,
        host_id: str,
        ssh_host: str,
        ssh_user: str,
        ssh_port: int = 22,
        ssh_key_path: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        compose_path: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Add a new Docker host for management."""
        return await self.host_service.add_docker_host(
            host_id,
            ssh_host,
            ssh_user,
            ssh_port,
            ssh_key_path,
            description,
            tags,
            compose_path,
            enabled,
        )

    async def list_docker_hosts(self) -> dict[str, Any]:
        """List all configured Docker hosts."""
        return await self.host_service.list_docker_hosts()

    async def list_containers(
        self, host_id: str, all_containers: bool = False, limit: int = 20, offset: int = 0
    ) -> ToolResult:
        """List containers on a specific Docker host with pagination."""
        return await self.container_service.list_containers(host_id, all_containers, limit, offset)

    async def get_container_info(self, host_id: str, container_id: str) -> ToolResult:
        """Get detailed information about a specific container."""
        return await self.container_service.get_container_info(host_id, container_id)

    async def get_container_logs(
        self, host_id: str, container_id: str, lines: int = 100, follow: bool = False
    ) -> dict[str, Any]:
        """Get logs from a container.

        Args:
            host_id: Target Docker host identifier
            container_id: Container ID or name
            lines: Number of log lines to retrieve
            follow: Stream logs in real-time

        Returns:
            Container logs
        """
        try:
            if host_id not in self.config.hosts:
                return {"success": False, "error": f"Host {host_id} not found"}

            # Use logs service to get logs
            logs_result = await self.logs_service.get_container_logs(
                host_id=host_id,
                container_id=container_id,
                lines=lines,
                since=None,
                timestamps=False,
            )

            # Extract logs array from ContainerLogs model for cleaner API
            if isinstance(logs_result, dict) and "logs" in logs_result:
                logs = logs_result["logs"]  # This is the list[str] of actual log lines
                truncated = logs_result.get("truncated", False)
            else:
                logs = []
                truncated = False

            return {
                "success": True,
                "host_id": host_id,
                "container_id": container_id,
                "logs": logs,  # Now this is list[str] of actual log lines
                "lines_requested": lines,
                "lines_returned": len(logs),
                "truncated": truncated,
                "follow": follow,
            }

        except Exception as e:
            self.logger.error(
                "Failed to get container logs",
                host_id=host_id,
                container_id=container_id,
                error=str(e),
            )
            return {
                "success": False,
                "error": str(e),
                "host_id": host_id,
                "container_id": container_id,
            }

    async def manage_container(
        self, host_id: str, container_id: str, action: str, force: bool = False, timeout: int = 10
    ) -> ToolResult:
        """Unified container action management."""
        return await self.container_service.manage_container(
            host_id, container_id, action, force, timeout
        )

    async def pull_image(self, host_id: str, image_name: str) -> ToolResult:
        """Pull a Docker image on a remote host."""
        return await self.container_service.pull_image(host_id, image_name)

    async def list_host_ports(self, host_id: str, include_stopped: bool = False) -> ToolResult:
        """List all ports currently in use by containers on a Docker host."""
        # Note: ContainerService.list_host_ports only takes host_id (includes stopped containers by default)
        return await self.container_service.list_host_ports(host_id)

    async def deploy_stack(
        self,
        host_id: str,
        stack_name: str,
        compose_content: str,
        environment: dict[str, str] | None = None,
        pull_images: bool = True,
        recreate: bool = False,
    ) -> ToolResult:
        """Deploy a Docker Compose stack to a remote host."""
        return await self.stack_service.deploy_stack(
            host_id, stack_name, compose_content, environment, pull_images, recreate
        )

    async def manage_stack(
        self, host_id: str, stack_name: str, action: str, options: dict[str, Any] | None = None
    ) -> ToolResult:
        """Unified stack lifecycle management."""
        return await self.stack_service.manage_stack(host_id, stack_name, action, options)

    async def list_stacks(self, host_id: str) -> ToolResult:
        """List Docker Compose stacks on a host."""
        return await self.stack_service.list_stacks(host_id)

    async def update_host_config(self, host_id: str, compose_path: str) -> ToolResult:
        """Update host configuration with compose file path."""
        return await self.config_service.update_host_config(host_id, compose_path)

    async def import_ssh_config(
        self,
        ssh_config_path: str | None = None,
        selected_hosts: str | None = None,
        compose_path_overrides: dict[str, str] | None = None,
        auto_confirm: bool = False,
    ) -> ToolResult:
        """Import hosts from SSH config with interactive selection and compose path discovery."""
        return await self.config_service.import_ssh_config(
            ssh_config_path, selected_hosts, self._config_path
        )

    def _to_dict(self, result: Any, fallback_msg: str = "No structured content") -> dict[str, Any]:
        """Convert ToolResult to dictionary for programmatic access."""
        if hasattr(result, "structured_content"):
            return result.structured_content or {"success": True, "data": fallback_msg}
        return result

    # Removed unused _normalize_protocol helper; reintroduce with tests when needed.

    def update_configuration(self, new_config: DockerMCPConfig) -> None:
        """Update server configuration and reinitialize components."""
        self.config = new_config

        # Update managers with new config
        self.context_manager.config = new_config

        # Update service classes with new config
        self.host_service.config = new_config
        self.container_service.config = new_config
        self.stack_service.config = new_config
        self.config_service.config = new_config
        self.cleanup_service.config = new_config

        # Recreate logs service with updated config
        from .services.logs import LogsService

        self.logs_service = LogsService(new_config, self.context_manager)
        # Propagate the new logs service to dependent services
        try:
            self.container_service.logs_service = self.logs_service
        except Exception as e:
            self.logger.debug("Failed to set logs_service on container_service", error=str(e))
        try:
            self.stack_service.logs_service = self.logs_service
        except Exception as e:
            self.logger.debug("Failed to set logs_service on stack_service", error=str(e))

        self.logger.info("Configuration updated", hosts=list(new_config.hosts.keys()))

    async def start_hot_reload(self) -> None:
        """Start hot reload watcher if configured."""
        await self.hot_reload_manager.start_hot_reload()

    async def stop_hot_reload(self) -> None:
        """Stop hot reload watcher."""
        await self.hot_reload_manager.stop_hot_reload()

    def run(self) -> None:
        """Run the FastMCP server."""
        try:
            # Initialize FastMCP app and run once
            self._initialize_app()

            self.logger.info(
                "Starting Docker MCP Server",
                host=self.config.server.host,
                port=self.config.server.port,
            )

            # FastMCP.run() is synchronous and manages its own event loop
            if self.app is None:
                raise RuntimeError("FastMCP app not initialized")
            self.app.run(
                transport="http",
                host=self.config.server.host,
                port=self.config.server.port,
            )

        except Exception as e:
            self.logger.error("Server startup failed", error=str(e))
            raise


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        # dotenv is optional - continue without it if not available
        pass
    except Exception as e:
        # Log unexpected errors but continue - environment loading shouldn't block startup
        import logging
        logging.getLogger("docker_mcp").debug("Failed to load .env file: %s", str(e))

    default_host = os.getenv("FASTMCP_HOST", "127.0.0.1")  # nosec B104 - Use 0.0.0.0 for container deployment
    default_port = int(os.getenv("FASTMCP_PORT", "8000"))
    default_log_level = os.getenv("LOG_LEVEL", "INFO")
    default_config = os.getenv("DOCKER_HOSTS_CONFIG", str(get_config_dir() / "hosts.yml"))

    parser = argparse.ArgumentParser(description="FastMCP Docker SSH Manager")
    parser.add_argument("--host", default=default_host, help="Server host")
    parser.add_argument("--port", type=int, default=default_port, help="Server port")
    parser.add_argument("--config", default=default_config, help="Configuration file path")
    parser.add_argument(
        "--log-level",
        default=default_log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--validate-config", action="store_true", help="Validate configuration and exit"
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Setup logging
    log_dir = _setup_log_directory()
    logger = _setup_logging_system(args, log_dir)

    # Load and configure application
    config, config_path_for_reload = _load_and_configure(args, logger)
    if config is None:  # Validation-only mode
        return

    # Create server and setup hot reload
    server = DockerMCPServer(config, config_path=config_path_for_reload)
    _setup_hot_reload(server, logger)

    # Run server with error handling
    _run_server(server, logger)


def _setup_log_directory() -> str | None:
    """Setup log directory with fallback options."""
    log_dir_candidates = [
        os.getenv("LOG_DIR"),  # Explicit environment override
        str(get_data_dir() / "logs"),  # Primary data directory
        str(Path.home() / ".local" / "share" / "docker-mcp" / "logs"),  # User fallback
        str(Path(tempfile.gettempdir()) / "docker-mcp-logs"),  # System fallback
    ]

    for candidate in log_dir_candidates:
        if candidate:
            try:
                candidate_path = Path(candidate)
                candidate_path.mkdir(parents=True, exist_ok=True)
                if candidate_path.is_dir() and os.access(candidate_path, os.W_OK):
                    return str(candidate_path)
            except (OSError, PermissionError):
                continue

    print("Warning: Unable to create log directory, using console-only logging")
    return None


def _setup_logging_system(args, log_dir: str | None):
    """Setup logging system with error handling."""
    from docker_mcp.core.logging_config import get_server_logger, setup_logging

    # Parse log file size with validation
    try:
        max_file_size_mb = int(os.getenv("LOG_FILE_SIZE_MB", "10"))
        if max_file_size_mb < 1 or max_file_size_mb > 100:
            max_file_size_mb = 10  # Reset to default if out of range
    except ValueError:
        max_file_size_mb = 10

    # Setup logging with error handling
    try:
        setup_logging(
            log_dir=log_dir or "/tmp", log_level=args.log_level, max_file_size_mb=max_file_size_mb
        )
        logger = get_server_logger()

        # Log successful initialization with configuration details
        logger.info(
            "Logging system initialized",
            log_dir=log_dir,
            log_level=args.log_level,
            max_file_size_mb=max_file_size_mb,
            console_logging=True,
            file_logging=log_dir is not None,
        )
        return logger
    except Exception as e:
        print(f"Logging setup failed ({e}), using basic console logging")
        import logging

        logging.basicConfig(
            level=getattr(logging, args.log_level.upper(), logging.INFO),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        return logging.getLogger("docker_mcp")


def _load_and_configure(args, logger) -> tuple[DockerMCPConfig | None, str]:
    """Load and configure application, returning None for validation-only mode."""
    # Load configuration
    config = load_config(args.config)

    # Override server config from CLI args
    config.server.host = args.host
    config.server.port = args.port
    config.server.log_level = args.log_level

    # Validate configuration only
    if args.validate_config:
        logger.info("Configuration validation successful")
        logger.info("✅ Configuration is valid")
        return None, ""

    # Determine config path for reload
    config_path_for_reload = args.config or os.getenv(
        "DOCKER_HOSTS_CONFIG", str(get_config_dir() / "hosts.yml")
    )

    logger.info(
        "Hot reload configuration",
        config_path=config_path_for_reload,
        args_config=args.config,
        env_config=os.getenv("DOCKER_HOSTS_CONFIG"),
    )

    return config, config_path_for_reload


def _setup_hot_reload(server: "DockerMCPServer", logger) -> None:
    """Setup hot reload in background thread."""
    import asyncio
    import threading

    async def start_hot_reload():
        await server.start_hot_reload()

    def run_hot_reload():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_hot_reload())
        # Keep the loop running to handle file changes
        loop.run_forever()

    hot_reload_thread = threading.Thread(target=run_hot_reload, daemon=True)
    hot_reload_thread.start()
    logger.info("Hot reload enabled for configuration changes")


def _run_server(server: "DockerMCPServer", logger) -> None:
    """Run server with error handling."""
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error("Server error", error=str(e))
        sys.exit(1)


# Note: FastMCP dev mode not used - we run our own server with hot reload

if __name__ == "__main__":
    main()

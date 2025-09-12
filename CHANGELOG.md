# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Type hints for resource initialization methods
- Forward references using TYPE_CHECKING pattern
- ProtocolLiteral type alias for protocol strings
- Artifact upload to GitHub Actions workflow (upload-artifact@v4) with always() condition
- Analysis scope documentation in CODE_HEALTH_DUPLICATES_DEAD_CODE.md
- RFC 7807 compliant error response helpers in core/error_response.py
- DockerMCPErrorResponse factory class for standardized error formatting

### Changed
- Tags in resources changed from tuples to sets for deterministic ordering
- Replaced loop.run_in_executor with asyncio.to_thread for modern async pattern
- Updated PortListResponse model to use typed lists (PortMapping, PortConflict)
- Updated ContainerInfo.ports type from list[dict[str, Any]] to list[str]
- GitHub Actions workflow trigger from shorthand to explicit mapping
- Improved error handling in migration with isinstance(ToolResult) checks
- Converted all remaining asyncio.get_event_loop() calls to asyncio.get_running_loop()
- Migrated all blocking I/O operations to use asyncio.to_thread instead of run_in_executor
- Standardized error responses to include "success": false field
- Fixed parameter delegation in server.py to use validated computed fields

### Fixed
- Removed duplicate DockerContextManager import in migration_orchestrator.py
- Added missing newline in GitHub Actions workflow command
- Type annotations for list and dict parameters
- Python 3.10+ compatibility by modernizing asyncio patterns
- Async executor patterns for better performance and maintainability

### Removed
- HostNotFoundError class (use DockerMCPError instead)
- ensure_log_directory helper function (handled directly in server init)
- BUILD action from ContainerAction enum (build operations belong in compose/stack management)
- **HostNotFoundError** exception class (docker_mcp/core/exceptions.py:20)
  - This exception was removed as it was not being used in the codebase
  - Migration: Use DockerMCPError or DockerContextError instead
- **ensure_log_directory** helper function (docker_mcp/core/logging_config.py:139)
  - Function was removed as it had no callers
  - The server now initializes the log directory directly in its initialization code
- **build** action from container management valid actions
  - Build operations should be handled through stack/compose operations instead
- **_cleanup_target_snapshots** method from ZFS transfer module (docker_mcp/core/transfer/zfs.py)
  - Unused method that was defined but never called
  - Dead code removal improves maintainability

### Security
- **CRITICAL:** Sanitized real hostnames and IP addresses from hosts.yml configuration
- Replaced real domain names with example.com placeholders

## [0.1.0] - Previous release
Initial release of Docker MCP

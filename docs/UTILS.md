# Docker MCP Utilities Module

The `docker_mcp.utils` module provides centralized utility functions that eliminate code duplication across the codebase. This module consolidates ~120 lines of previously duplicated code into 4 reusable functions.

## Overview

Previously, the codebase had multiple duplicate implementations of common functionality scattered across different modules. The utils module centralizes these functions to:

- **Eliminate code duplication**: 13+ duplicate functions consolidated into 4 utilities
- **Improve maintainability**: Single source of truth for common operations
- **Ensure consistency**: Standardized behavior across all modules
- **Reduce errors**: Fix bugs in one place rather than multiple locations

## Functions

### `build_ssh_command(host: DockerHost) -> list[str]`

Constructs SSH command arguments for connecting to a Docker host.

**Parameters:**
- `host` (DockerHost): Host configuration containing connection details

**Returns:**
- `list[str]`: SSH command parts ready for subprocess execution

**Usage:**
```python
from docker_mcp.utils import build_ssh_command

ssh_cmd = build_ssh_command(host_config)
# Result: ["ssh", "-o", "StrictHostKeyChecking=no", "-i", "/path/to/key", "-p", "2222", "user@hostname"]

# Execute remote command
full_cmd = ssh_cmd + ["docker", "ps", "-a"]
result = subprocess.run(full_cmd, capture_output=True, text=True)
```

**Features:**
- Automatically includes security options (`StrictHostKeyChecking=no`)
- Handles optional SSH key file (`-i` flag)
- Manages non-standard ports (`-p` flag for ports != 22)
- Uses consistent SSH_NO_HOST_CHECK constant

**Previously duplicated in:**
- `services/stack.py` (`_build_ssh_cmd`)
- `services/cleanup.py` (`_build_ssh_cmd`) 
- `services/host.py` (`_build_ssh_cmd`)
- `core/migration/manager.py` (`build_ssh_command`)
- `core/backup.py` (`build_ssh_command`)
- `tools/stacks.py` (`_build_ssh_command`)

### `validate_host(config: DockerMCPConfig, host_id: str) -> tuple[bool, str]`

Validates that a host ID exists in the configuration.

**Parameters:**
- `config` (DockerMCPConfig): Configuration object containing host definitions
- `host_id` (str): Host identifier to validate

**Returns:**
- `tuple[bool, str]`: (is_valid, error_message)
  - `is_valid`: True if host exists, False otherwise
  - `error_message`: Empty string if valid, error description if invalid

**Usage:**
```python
from docker_mcp.utils import validate_host

is_valid, error_msg = validate_host(self.config, "production-1")
if not is_valid:
    return ToolResult(
        content=[TextContent(type="text", text=f"Error: {error_msg}")],
        structured_content={"success": False, "error": error_msg}
    )
```

**Previously duplicated in:**
- `services/cleanup.py` (`_validate_host`)
- `services/container.py` (`_validate_host`)
- `services/config.py` (`_validate_host`)
- Multiple tool classes with identical validation logic

### `format_size(size_bytes: int) -> str`

Converts byte counts to human-readable size strings.

**Parameters:**
- `size_bytes` (int): Size in bytes

**Returns:**
- `str`: Formatted size string (e.g., "1.5 GB", "512 MB")

**Usage:**
```python
from docker_mcp.utils import format_size

file_size = 1536000000
readable = format_size(file_size)
# Result: "1.5 GB"

# Common patterns
backup_info = {
    "backup_size": backup_size,
    "backup_size_human": format_size(backup_size),
}
```

**Features:**
- Automatic unit selection (B, KB, MB, GB, TB, PB)
- Sensible decimal precision (0-2 decimal places)
- Handles edge cases (0 bytes, negative values)
- Consistent formatting across all modules

**Previously duplicated in:**
- `services/stack.py` (`_format_size`)
- `services/cleanup.py` (`_format_size`)
- `core/backup.py` (inline formatting)

### `parse_percentage(perc_str: str) -> float | None`

Parses percentage strings into numeric values.

**Parameters:**
- `perc_str` (str): Percentage string (e.g., "75.5%", "100%")

**Returns:**
- `float | None`: Numeric percentage value, or None if parsing fails

**Usage:**
```python
from docker_mcp.utils import parse_percentage

cpu_usage = parse_percentage("75.5%")
# Result: 75.5

memory_usage = parse_percentage("invalid")
# Result: None

# Safe usage with fallback
cpu_value = parse_percentage(stats.get("CPUPerc", "0%")) or 0.0
```

**Features:**
- Handles various percentage formats
- Graceful error handling (returns None for invalid input)
- Strips '%' symbol automatically
- Works with both integer and decimal percentages

**Previously duplicated in:**
- `tools/containers.py` (`_parse_percentage`)

## Import Patterns

### Standard Import
```python
from docker_mcp.utils import build_ssh_command, validate_host, format_size, parse_percentage
```

### Selective Import
```python
from docker_mcp.utils import validate_host, format_size
```

### Module Import
```python
from docker_mcp import utils

ssh_cmd = utils.build_ssh_command(host)
```

## Migration Impact

The utils module consolidation resulted in:

### Code Reduction
- **13+ duplicate functions** eliminated
- **~120 lines of duplicate code** removed
- **10+ files** updated to use centralized utilities

### Improved Files
- `services/stack.py`: Removed 2 duplicate methods
- `services/cleanup.py`: Removed 3 duplicate methods  
- `services/host.py`: Removed 1 duplicate method
- `services/container.py`: Removed 1 duplicate method
- `services/config.py`: Removed 1 duplicate method
- `core/migration/manager.py`: Removed 1 duplicate method
- `core/backup.py`: Removed 2 duplicate methods
- `tools/stacks.py`: Removed 1 duplicate method
- `tools/containers.py`: Removed 1 duplicate method

### Consistency Benefits
- **SSH commands**: All modules now use identical SSH connection logic
- **Host validation**: Standardized error messages and validation logic
- **Size formatting**: Consistent human-readable size display
- **Percentage parsing**: Uniform parsing behavior across tools

## Design Principles

### Single Responsibility
Each function has a clear, focused purpose and handles one specific task.

### Error Handling
Functions gracefully handle edge cases and invalid input:
- `validate_host`: Returns clear boolean + message tuple
- `format_size`: Handles zero and negative values
- `parse_percentage`: Returns None for invalid input rather than raising exceptions

### Type Safety
All functions include proper type hints and return consistent types:
```python
def validate_host(config: DockerMCPConfig, host_id: str) -> tuple[bool, str]
def format_size(size_bytes: int) -> str
def parse_percentage(perc_str: str) -> float | None
```

### Dependencies
The utils module has minimal dependencies:
- `DockerMCPConfig` from core configuration
- `DockerHost` from core models
- Standard library modules only (no external dependencies)

## Testing

Utils functions are thoroughly tested through:
- **Unit tests**: Direct function testing with various inputs
- **Integration tests**: Validation through existing service tests
- **Edge case coverage**: Testing error conditions and boundary values

Example test patterns:
```python
def test_validate_host():
    config = create_test_config()
    
    # Valid host
    is_valid, error = validate_host(config, "test-host")
    assert is_valid
    assert error == ""
    
    # Invalid host
    is_valid, error = validate_host(config, "nonexistent")
    assert not is_valid
    assert "not found" in error

def test_format_size():
    assert format_size(1024) == "1.0 KB"
    assert format_size(1536000000) == "1.5 GB"
    assert format_size(0) == "0 B"
```

## Future Enhancements

Potential additions to the utils module:

### Command Validation
```python
def validate_docker_command(command: str) -> bool:
    """Validate Docker command for security."""
```

### Path Utilities
```python
def normalize_path(path: str) -> str:
    """Normalize file paths for cross-platform compatibility."""
```

### Network Utilities
```python
def parse_port_mapping(port_str: str) -> tuple[str, str]:
    """Parse Docker port mapping strings."""
```

## Best Practices

### When Adding New Utilities

1. **Check for duplication**: Look for similar functionality across modules
2. **Design for reuse**: Make functions generic enough for multiple use cases
3. **Include type hints**: Maintain type safety throughout
4. **Handle errors gracefully**: Return None or empty values rather than raising exceptions
5. **Add comprehensive tests**: Cover edge cases and error conditions
6. **Update documentation**: Add function to this document

### Usage Guidelines

1. **Import at module level**: Import utils at the top of files
2. **Replace existing duplicates**: Always prefer utils functions over local implementations
3. **Maintain consistency**: Use utils functions consistently across all modules
4. **Test integration**: Verify that utils replacement doesn't break existing functionality

## Summary

The utils module represents a significant improvement in code organization and maintainability. By centralizing common functionality, we've eliminated substantial code duplication while improving consistency and reducing the potential for bugs.

The module follows Docker MCP's architectural principles of clear separation of concerns, type safety, and robust error handling, making it a reliable foundation for common operations across the entire codebase.

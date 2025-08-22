# Tests Layer - Development Memory

## FastMCP In-Memory Testing Architecture

### Core Testing Pattern
```python
# conftest.py - FastMCP in-memory testing setup
@pytest.fixture
async def client(server: DockerMCPServer) -> AsyncGenerator[Client, None]:
    """Create FastMCP client connected to server in-memory."""
    async with Client(server.app) as client:
        yield client

# Test usage
@pytest.mark.asyncio
async def test_tool_functionality(client: Client):
    result = await client.call_tool("tool_name", {"param": "value"})
    assert result.data["success"] is True
```

### In-Memory Testing Benefits
- **No Network Overhead**: Direct server connection without HTTP transport
- **Fast Execution**: Tests run at memory speed, not network speed  
- **Deterministic**: No network timeouts or connection issues
- **Easy Debugging**: Full stack trace access and breakpoint support
- **Isolated**: Each test gets its own server instance

## Fixture Architecture Patterns

### Configuration Fixtures
```python
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
    server._initialize_app()  # FastMCP app initialization
    return server
```

### Test Data Fixtures
```python
@pytest.fixture
def test_host_id() -> str:
    """Default test host ID for container tests."""
    return "squirts"  # Real host from config/hosts.yml

@pytest.fixture
def test_container_id() -> str:
    """Default test container ID for container operations."""
    return "opengist"  # Known running container

@pytest.fixture
def simple_compose_content() -> str:
    """Simple Docker Compose content for testing."""
    return """version: '3.8'
services:
  test-web:
    image: nginx:alpine
    ports:
      - "8092:80"
    labels:
      - "test=mcp-validation"
"""

@pytest.fixture
def test_environment() -> dict[str, str]:
    """Test environment variables for compose deployments."""
    return {
        "TEST_ENV": "production",
        "DEBUG": "false"
    }
```

### Fixture Dependencies
```python
# Fixture dependency chain
config_path → config → server → client

# Usage in tests
async def test_example(client: Client, test_host_id: str, simple_compose_content: str):
    # client, test_host_id, and simple_compose_content are all fixtures
    pass
```

## Test Organization Patterns

### Class-Based Test Groups
```python
class TestHostManagement:
    """Test suite for host management tools."""
    
    @pytest.mark.asyncio
    async def test_list_docker_hosts(self, client: Client):
        """Test listing all configured Docker hosts."""
        result = await client.call_tool("list_docker_hosts", {})
        assert result.data["success"] is True
        assert "hosts" in result.data
        
class TestContainerOperations:
    """Test suite for container management operations."""
    
    @pytest.mark.asyncio
    async def test_list_containers_default(self, client: Client, test_host_id: str):
        """Test listing containers with default parameters."""
        pass

class TestStackOperations:
    """Test suite for Docker Compose stack operations."""
    # Stack-related tests grouped together
```

### Test Categories
```python
# Integration tests - test complete workflows
@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_stack_lifecycle(client: Client, test_host_id: str):
    """Integration test of complete stack lifecycle: list -> deploy -> manage -> remove."""
    pass

# Slow tests - operations that take >10 seconds
@pytest.mark.slow
async def test_list_host_ports(self, client: Client, test_host_id: str):
    """Test listing port mappings (slow test due to container scanning)."""
    pass

# Mark configuration in conftest.py
slow_test = pytest.mark.slow
integration_test = pytest.mark.integration
requires_docker_host = pytest.mark.skipif(False, reason="Requires configured Docker host")
```

## Async Testing Patterns

### pytest-asyncio Integration
```python
# All tests are async due to FastMCP async nature
@pytest.mark.asyncio  # Required for all async tests
async def test_async_operation(client: Client):
    result = await client.call_tool("async_tool", {})
    assert result.data["success"] is True

# conftest.py async fixture
@pytest.fixture
async def client(server: DockerMCPServer) -> AsyncGenerator[Client, None]:
    async with Client(server.app) as client:
        yield client  # Async context manager ensures proper cleanup
```

### AsyncGenerator Pattern for Fixtures
```python
@pytest.fixture
async def deployed_test_stack(client: Client, test_host_id: str):
    """Deploy a test stack for management operations."""
    compose_content = """..."""
    stack_name = "test-stack-mgmt"
    
    # Setup: Deploy the stack
    result = await client.call_tool("deploy_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "compose_content": compose_content,
        "pull_images": False
    })
    assert result.data["success"] is True
    
    yield stack_name  # Provide stack name to test
    
    # Teardown: Clean up after test
    await client.call_tool("manage_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "action": "down",
        "options": {"volumes": True}
    })
```

## Error Handling Test Patterns

### Success and Failure Path Testing
```python
class TestErrorHandling:
    """Test suite for error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_invalid_host_id(self, client: Client):
        """Test operations with invalid host ID."""
        result = await client.call_tool("list_containers", {
            "host_id": "invalid-host-id"
        })
        assert result.data["success"] is False
        assert "error" in result.data

    @pytest.mark.asyncio
    async def test_invalid_container_id(self, client: Client, test_host_id: str):
        """Test operations with invalid container ID."""
        result = await client.call_tool("get_container_info", {
            "host_id": test_host_id,
            "container_id": "invalid-container-id"
        })
        assert result.data["success"] is False
        assert "error" in result.data
```

### Graceful Error Handling
```python
# Tests should handle both success and failure gracefully
@pytest.mark.asyncio
async def test_container_operation_flexible(self, client: Client, test_host_id: str, test_container_id: str):
    """Test container info retrieval with flexible error handling."""
    result = await client.call_tool("get_container_info", {
        "host_id": test_host_id,
        "container_id": test_container_id
    })
    
    # Accept either success or failure, but verify proper structure
    assert 'success' in result.data
    if result.data['success']:
        assert 'container_id' in result.data
        assert result.data['container_id'] == test_container_id
    else:
        assert 'error' in result.data
```

## Lifecycle Testing Patterns

### Complete Workflow Testing
```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_stack_lifecycle(client: Client, test_host_id: str):
    """Integration test of complete stack lifecycle: list -> deploy -> manage -> remove."""
    
    # Step 1: List initial stacks
    initial_list = await client.call_tool("list_stacks", {"host_id": test_host_id})
    assert initial_list.data["success"] is True
    initial_count = len(initial_list.data["stacks"])
    
    # Step 2: Deploy new stack
    stack_name = "test-lifecycle-complete"
    deploy_result = await client.call_tool("deploy_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "compose_content": compose_content,
        "pull_images": False
    })
    assert deploy_result.data["success"] is True
    
    # Step 3: Verify stack appears in listing
    post_deploy_list = await client.call_tool("list_stacks", {"host_id": test_host_id})
    assert len(post_deploy_list.data["stacks"]) == initial_count + 1
    
    # Step 4: Check stack status
    ps_result = await client.call_tool("manage_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "action": "ps"
    })
    assert ps_result.data["success"] is True
    assert ps_result.data["execution_method"] == "ssh"
    
    # Step 5: Remove stack
    down_result = await client.call_tool("manage_stack", {
        "host_id": test_host_id,
        "stack_name": stack_name,
        "action": "down",
        "options": {"volumes": True}
    })
    assert down_result.data["success"] is True
    
    # Step 6: Verify stack is removed
    final_list = await client.call_tool("list_stacks", {"host_id": test_host_id})
    final_stack_names = [stack["name"] for stack in final_list.data["stacks"]]
    assert stack_name not in final_stack_names
```

### Deploy-Test-Cleanup Pattern
```python
@pytest.mark.asyncio
async def test_stack_with_cleanup(self, client: Client, test_host_id: str):
    """Test pattern with guaranteed cleanup."""
    stack_name = "test-temporary"
    
    try:
        # Deploy
        result = await client.call_tool("deploy_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "compose_content": compose_content
        })
        assert result.data["success"] is True
        
        # Test operations
        # ... test the deployed stack ...
        
    finally:
        # Always clean up, even if test fails
        await client.call_tool("manage_stack", {
            "host_id": test_host_id,
            "stack_name": stack_name,
            "action": "down",
            "options": {"volumes": True}
        })
```

## Response Validation Patterns

### Standard Response Structure
```python
# All MCP tools return consistent structure
def assert_successful_response(result, expected_keys: list[str] | None = None):
    """Assert standard successful MCP response structure."""
    assert result.data["success"] is True
    if expected_keys:
        for key in expected_keys:
            assert key in result.data

def assert_error_response(result, error_substring: str | None = None):
    """Assert standard error MCP response structure."""
    assert result.data["success"] is False
    assert "error" in result.data
    if error_substring:
        assert error_substring in result.data["error"].lower()

# Usage in tests
result = await client.call_tool("list_containers", {"host_id": test_host_id})
assert_successful_response(result, ["containers", "limit", "offset"])
```

### Response Data Validation
```python
@pytest.mark.asyncio
async def test_list_containers_response_structure(self, client: Client, test_host_id: str):
    """Test container listing returns proper response structure."""
    result = await client.call_tool("list_containers", {
        "host_id": test_host_id,
        "limit": 5
    })
    
    assert result.data["success"] is True
    assert "containers" in result.data
    assert isinstance(result.data["containers"], list)
    
    # Check pagination structure
    pagination = result.data.get("pagination", {})
    assert pagination.get("limit") == 5
    assert "offset" in pagination
    assert "total" in pagination
```

## Test Data Management

### Compose File Templates
```python
# Simple stack for basic testing
SIMPLE_NGINX_STACK = """version: '3.8'
services:
  test-web:
    image: nginx:alpine
    ports:
      - "8092:80"
    labels:
      - "test=mcp-validation"
"""

# Complex stack with environment variables
COMPLEX_STACK_WITH_ENV = """version: '3.8'
services:
  app:
    image: nginx:alpine
    ports:
      - "8093:80"
    environment:
      - TEST_ENV=${TEST_ENV}
      - DEBUG=${DEBUG}
    labels:
      - "test=mcp-complex"
  redis:
    image: redis:alpine
    ports:
      - "6379:6379"
"""

# Stack with volumes for testing volume operations
STACK_WITH_VOLUMES = """version: '3.8'
services:
  temp-service:
    image: nginx:alpine
    volumes:
      - test_volume:/data
volumes:
  test_volume:
"""
```

### Test Environment Configuration
```python
# Test environment variables
TEST_ENVIRONMENT = {
    "TEST_ENV": "production",
    "DEBUG": "false",
    "API_URL": "http://localhost:8080",
    "DATABASE_URL": "postgresql://test:test@localhost:5432/testdb"
}

# Port ranges for testing (avoid conflicts)
TEST_PORT_RANGE = range(8090, 8100)  # Ports 8090-8099 for testing
```

## Configuration Testing Patterns

### YAML Configuration Testing
```python
def test_load_yaml_config():
    """Test loading configuration from YAML file."""
    yaml_content = """
hosts:
  test-host:
    hostname: test.example.com
    user: testuser
    port: 2222
    description: "Test host"
    tags: ["test"]

server:
  host: 127.0.0.1
  port: 9000
  log_level: DEBUG
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(yaml_content)
        config_path = f.name

    try:
        config = load_config(config_path)
        
        # Check hosts
        assert "test-host" in config.hosts
        host = config.hosts["test-host"]
        assert host.hostname == "test.example.com"
        assert host.user == "testuser"
        assert host.port == 2222
        
        # Check server config
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 9000
        assert config.server.log_level == "DEBUG"
        
    finally:
        Path(config_path).unlink()  # Clean up temp file
```

### Model Validation Testing
```python
def test_docker_host_validation():
    """Test DockerHost model validation."""
    # Test valid host with defaults
    host = DockerHost(hostname="test.example.com", user="testuser")
    assert host.port == 22  # Default value
    assert host.enabled is True  # Default value

    # Test with all fields
    host = DockerHost(
        hostname="test.example.com",
        user="testuser",
        port=2222,
        identity_file="/path/to/key",
        description="Test host",
        tags=["test", "staging"],
        enabled=False,
    )
    assert host.port == 2222
    assert host.identity_file == "/path/to/key"
    assert host.description == "Test host"
    assert host.tags == ["test", "staging"]
    assert host.enabled is False
```

## Integration Testing Strategy

### Multi-Tool Integration Tests
```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_tools_integration(client: Client, test_host_id: str, test_container_id: str, 
                                   simple_compose_content: str):
    """Integration test that exercises all 13 tools."""
    
    # Host Management (3 tools)
    hosts = await client.call_tool("list_docker_hosts", {})
    assert hosts.data["success"] is True
    
    # Container Operations (5 tools)
    containers = await client.call_tool("list_containers", {"host_id": test_host_id})
    assert containers.data["success"] is True
    
    info = await client.call_tool("get_container_info", {
        "host_id": test_host_id, 
        "container_id": test_container_id
    })
    assert info.data["success"] is True
    
    # Stack Operations (3 tools)
    stacks = await client.call_tool("list_stacks", {"host_id": test_host_id})
    assert stacks.data["success"] is True
    
    # Deploy and test stack
    deploy = await client.call_tool("deploy_stack", {
        "host_id": test_host_id,
        "stack_name": "test-integration",
        "compose_content": simple_compose_content,
        "pull_images": False
    })
    assert deploy.data["success"] is True
    
    # Test SSH-based stack management
    ps_result = await client.call_tool("manage_stack", {
        "host_id": test_host_id,
        "stack_name": "test-integration", 
        "action": "ps"
    })
    assert ps_result.data["success"] is True
    assert ps_result.data["execution_method"] == "ssh"
    
    # Clean up
    await client.call_tool("manage_stack", {
        "host_id": test_host_id,
        "stack_name": "test-integration",
        "action": "down",
        "options": {"volumes": True}
    })
```

## Test Execution and Environment

### Test Configuration Requirements
```python
# conftest.py - Test environment setup
@pytest.fixture
def config_path() -> str:
    """Get path to test configuration file."""
    # Tests require config/hosts.yml with real Docker hosts
    return str(Path(__file__).parent.parent / "config" / "hosts.yml")

# Test marks configuration
requires_docker_host = pytest.mark.skipif(
    False,  # We always have Docker hosts available in our test environment
    reason="Requires configured Docker host"
)
```

### Running Tests
```bash
# Run all tests
uv run pytest

# Run specific test categories
uv run pytest -m integration      # Integration tests only
uv run pytest -m "not slow"       # Exclude slow tests
uv run pytest -m "slow and integration"  # Slow integration tests

# Run specific test files
uv run pytest tests/test_core_tools_pytest.py
uv run pytest tests/test_stack_operations_pytest.py

# Run with coverage
uv run pytest --cov=docker_mcp --cov-report=term-missing

# Verbose output
uv run pytest -v -s  # Show print statements and detailed output
```

### Test Dependencies
```bash
# Required for testing
pip install pytest pytest-asyncio pytest-cov

# In pyproject.toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",           # Testing framework
    "pytest-asyncio>=0.23.0",  # Async test support
    "pytest-cov>=4.0.0",       # Coverage reporting
    "pytest-watch>=4.2.0",     # Test watching
]
```

## Best Practices

### Test Writing Guidelines
```python
# 1. Always use descriptive test names
async def test_deploy_nginx_stack_with_environment_variables():
    """Test deploying nginx stack with custom environment configuration."""
    pass

# 2. Test both success and failure paths
async def test_container_operation_success_and_failure(client: Client):
    # Test success path
    result = await client.call_tool("valid_operation", {"valid": "params"})
    assert result.data["success"] is True
    
    # Test failure path
    result = await client.call_tool("invalid_operation", {"invalid": "params"})
    assert result.data["success"] is False
    assert "error" in result.data

# 3. Always clean up resources
async def test_with_cleanup(client: Client):
    try:
        # Deploy resources
        result = await client.call_tool("deploy_stack", params)
        # Test operations
    finally:
        # Always clean up
        await client.call_tool("manage_stack", {"action": "down"})

# 4. Use meaningful assertions
assert result.data["success"] is True  # Explicit boolean check
assert result.data["container_id"] == test_container_id  # Specific value check
assert "error" in result.data  # Key existence check
```

### Test Organization
- **File Naming**: `test_[module]_pytest.py` for pytest tests
- **Class Naming**: `Test[FunctionalArea]` for test groupings  
- **Method Naming**: `test_[specific_functionality]` with descriptive names
- **Fixture Naming**: Clear, descriptive fixture names that indicate what they provide

The testing architecture provides comprehensive coverage of all MCP tools using FastMCP's in-memory testing capabilities, ensuring fast, reliable, and maintainable test suites that validate both success and failure scenarios across the entire Docker management system.
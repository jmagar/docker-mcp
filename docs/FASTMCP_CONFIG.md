# FastMCP Configuration Guide

This guide explains how to use the Docker MCP project with FastMCP configuration files instead of command-line arguments.

## Overview

The Docker MCP project provides three FastMCP configuration files for different use cases:

- **`fastmcp.json`** - Production configuration with STDIO transport
- **`dev.fastmcp.json`** - Development configuration with debugging enabled
- **`http.fastmcp.json`** - HTTP transport configuration for network access

## Quick Start

### Production (STDIO Transport)

```bash
# Run with default production configuration
fastmcp run

# Or explicitly specify the config file
fastmcp run --config fastmcp.json
```

### Development

```bash
# Run with development configuration (debug logging, editable install)
fastmcp run --config dev.fastmcp.json
```

### HTTP Transport

```bash
# Run with HTTP transport for network access
fastmcp run --config http.fastmcp.json
```

## Configuration Files

### 1. Production Configuration (`fastmcp.json`)

**Purpose**: Optimized for production use with STDIO transport.

**Key Features**:
- STDIO transport (standard for MCP clients like Claude Desktop)
- Production logging levels
- Standard rate limiting (50 req/sec)
- No debug output
- Environment variable support for all sensitive values

**Usage**: Default configuration for deployment and production use.

### 2. Development Configuration (`dev.fastmcp.json`)

**Purpose**: Optimized for development with enhanced debugging.

**Key Features**:
- Editable installation with dev dependencies
- DEBUG log level
- SSH debugging enabled
- Relaxed rate limiting (10 req/sec)
- Lower slow request threshold (1000ms)
- Pre-configured for local OAuth testing

**Usage**: Use during development, testing, and debugging.

### 3. HTTP Configuration (`http.fastmcp.json`)

**Purpose**: Network-accessible HTTP transport.

**Key Features**:
- HTTP transport on port 8011
- Host binding to 0.0.0.0 for network access
- OAuth-ready configuration
- Production logging levels
- Suitable for web integrations

**Usage**: When you need network access to the MCP server or OAuth authentication.

## Environment Variables

All configurations support environment variable interpolation using `${VAR_NAME:-default}` syntax.

### Core Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCKER_HOSTS_CONFIG` | `config/hosts.yml` | Path to Docker hosts configuration |
| `LOG_LEVEL` | `INFO` (prod), `DEBUG` (dev) | Logging level |
| `LOG_DIR` | _(auto-detected)_ | Log directory path |
| `SSH_CONFIG_PATH` | _(auto-detected)_ | SSH configuration file path |
| `SSH_DEBUG` | `0` (prod), `1` (dev) | SSH debugging level |

### Performance Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_PER_SECOND` | `50.0` (prod), `10.0` (dev) | Request rate limit |
| `SLOW_REQUEST_THRESHOLD_MS` | `5000.0` (prod), `1000.0` (dev) | Slow request threshold |

### HTTP Transport

| Variable | Default | Description |
|----------|---------|-------------|
| `FASTMCP_HOST` | `0.0.0.0` | HTTP server bind address |
| `FASTMCP_PORT` | `8011` | HTTP server port |

### OAuth Authentication

| Variable | Required | Description |
|----------|----------|-------------|
| `FASTMCP_ENABLE_OAUTH` | No | Set to `true` to enable OAuth |
| `FASTMCP_SERVER_AUTH` | Yes* | Auth provider class |
| `FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID` | Yes* | Google OAuth client ID |
| `FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET` | Yes* | Google OAuth client secret |
| `FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL` | Yes* | Server base URL for redirects |
| `FASTMCP_SERVER_AUTH_GOOGLE_REDIRECT_PATH` | No | OAuth redirect path (default: `/auth/callback`) |
| `FASTMCP_SERVER_AUTH_GOOGLE_REQUIRED_SCOPES` | No | Required OAuth scopes |
| `FASTMCP_SERVER_AUTH_GOOGLE_ALLOWED_CLIENT_REDIRECT_URIS` | No | Allowed client redirect URIs |

*Required when OAuth is enabled

## Migration from CLI Usage

### Before (CLI Arguments)

```bash
# Old way using CLI arguments
uv run docker-mcp --config config/hosts.yml --log-level DEBUG --hot-reload
```

### After (FastMCP Configuration)

```bash
# New way using FastMCP configuration
fastmcp run --config dev.fastmcp.json
```

### Environment File Setup

Create a `.env` file in your project root:

```bash
# Copy from example and customize
cp .env.example .env

# Edit with your specific values
# Key variables to set:
DOCKER_HOSTS_CONFIG=config/hosts.yml
LOG_LEVEL=INFO
FASTMCP_ENABLE_OAUTH=false
```

## Integration with MCP Clients

### Claude Desktop

Add to your Claude Desktop MCP configuration:

```json
{
  "mcpServers": {
    "docker-mcp": {
      "command": "fastmcp",
      "args": ["run", "--config", "/path/to/docker-mcp/fastmcp.json"]
    }
  }
}
```

### Continue.dev

Add to your Continue configuration:

```json
{
  "mcpServers": [
    {
      "name": "docker-mcp",
      "command": "fastmcp",
      "args": ["run", "--config", "/path/to/docker-mcp/fastmcp.json"]
    }
  ]
}
```

### HTTP Client Integration

For HTTP transport, connect to `http://localhost:8011`:

```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://localhost:8011/tools/docker_hosts",
        json={"action": "list"}
    )
```

## OAuth Setup

### 1. Enable OAuth

Set environment variables:

```bash
export FASTMCP_ENABLE_OAUTH=true
export FASTMCP_SERVER_AUTH=fastmcp.server.auth.providers.google.GoogleProvider
export FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
export FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET=your-client-secret
export FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL=http://localhost:8011
```

### 2. Use HTTP Configuration

```bash
fastmcp run --config http.fastmcp.json
```

### 3. Access OAuth Endpoints

- **Authorization**: `http://localhost:8011/auth/login`
- **Callback**: `http://localhost:8011/auth/callback`
- **User Info**: `http://localhost:8011/auth/user`

## Development Workflow

### 1. Development Server

```bash
# Start development server with hot reload
fastmcp run --config dev.fastmcp.json

# The dev config includes:
# - DEBUG logging
# - Editable installation
# - Dev dependencies
# - SSH debugging
# - Relaxed rate limiting
```

### 2. Testing OAuth

```bash
# Start HTTP server for OAuth testing
fastmcp run --config http.fastmcp.json

# Visit http://localhost:8011/auth/login to test OAuth flow
```

### 3. Production Deployment

```bash
# Deploy with production configuration
fastmcp run --config fastmcp.json

# Or use environment-specific overrides
DOCKER_HOSTS_CONFIG=prod-hosts.yml fastmcp run
```

## Troubleshooting

### Common Issues

1. **Module not found**: Ensure you're running from the project directory
2. **Config not found**: Use absolute paths in `--config` parameter
3. **Permission denied**: Check SSH key permissions and paths
4. **OAuth errors**: Verify all OAuth environment variables are set

### Debug Mode

Use the development configuration for detailed debugging:

```bash
fastmcp run --config dev.fastmcp.json
```

This enables:
- DEBUG log level
- SSH debugging output
- Detailed request/response logging
- Performance timing information

### Validation

Test your configuration without running the server:

```bash
# Validate configuration only
uv run docker-mcp --config config/hosts.yml --validate-only
```

## Schema Validation

All FastMCP configuration files use the official JSON schema for validation:

```json
{
  "$schema": "https://schemas.fastmcp.com/config/latest.json"
}
```

This provides:
- IDE autocomplete and validation
- Real-time error checking
- Documentation tooltips
- Type safety for configuration values

## Advanced Configuration

### Custom Environment Files

```bash
# Use custom environment file
fastmcp run --config fastmcp.json --env-file custom.env
```

### Override Specific Variables

```bash
# Override specific environment variables
LOG_LEVEL=DEBUG DOCKER_HOSTS_CONFIG=test-hosts.yml fastmcp run
```

### Multiple Configurations

You can create additional configuration files for specific environments:

```bash
# staging.fastmcp.json
{
  "$schema": "https://schemas.fastmcp.com/config/latest.json",
  "name": "docker-mcp-staging",
  "source": {
    "type": "uv",
    "path": ".",
    "app": "docker_mcp.server:app"
  },
  "env": {
    "DOCKER_HOSTS_CONFIG": "config/staging-hosts.yml",
    "LOG_LEVEL": "INFO",
    "FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL": "https://staging.example.com"
  },
  "transport": {
    "type": "stdio"
  }
}
```

## Security Considerations

### Environment Variables

- Never commit sensitive values to configuration files
- Use environment variable interpolation for secrets
- Restrict file permissions on environment files: `chmod 600 .env`

### OAuth Security

- Use HTTPS in production: `FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL=https://your-domain.com`
- Restrict redirect URIs to trusted domains
- Regularly rotate OAuth credentials

### SSH Security

- Use dedicated SSH keys for Docker MCP
- Store keys in `~/.docker-mcp/ssh/` with restricted permissions
- Enable SSH debugging only in development environments

## Best Practices

1. **Use specific configurations**: Choose the right config file for your use case
2. **Environment separation**: Use different configs for dev/staging/prod
3. **Security first**: Never expose secrets in configuration files
4. **Validate early**: Test configurations before deployment
5. **Monitor performance**: Adjust rate limits and thresholds based on usage
6. **Log appropriately**: Use DEBUG in development, INFO in production
7. **Documentation**: Document any custom configurations for your team

This guide covers all aspects of using FastMCP configurations with the Docker MCP project. For additional help, refer to the project's main documentation or create an issue on the project repository.
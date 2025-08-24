# Docker MCP Security Model

## Overview

Docker MCP implements defense-in-depth security for SSH-based Docker management operations. This document describes the security architecture, threat model, and best practices.

## Table of Contents

1. [Security Architecture](#security-architecture)
2. [Threat Model](#threat-model)
3. [Security Controls](#security-controls)
4. [SSH Security](#ssh-security)
5. [Input Validation](#input-validation)
6. [Rate Limiting](#rate-limiting)
7. [Audit Logging](#audit-logging)
8. [Key Management](#key-management)
9. [Best Practices](#best-practices)
10. [Security Testing](#security-testing)

## Security Architecture

### Core Principles

1. **Principle of Least Privilege**: Operations are restricted to minimum necessary permissions
2. **Defense in Depth**: Multiple layers of security controls
3. **Secure by Default**: Security features enabled by default
4. **Input Validation**: All inputs sanitized and validated
5. **Audit Trail**: Comprehensive logging of all operations

### Component Security

```
┌─────────────────────────────────────────┐
│            MCP Client                   │
├─────────────────────────────────────────┤
│         Input Validation Layer          │
│   • Command injection prevention        │
│   • Path traversal protection           │
│   • Parameter sanitization              │
├─────────────────────────────────────────┤
│         Rate Limiting Layer             │
│   • Per-host connection limits          │
│   • Request throttling                  │
│   • Concurrent connection control       │
├─────────────────────────────────────────┤
│      Secure Command Builder             │
│   • Shell escaping (shlex)              │
│   • Command whitelisting                │
│   • Environment variable validation     │
├─────────────────────────────────────────┤
│         SSH Transport Layer             │
│   • Key-based authentication only       │
│   • Connection pooling                  │
│   • Strict host key checking            │
├─────────────────────────────────────────┤
│          Audit Logging                  │
│   • Command execution logging           │
│   • Error tracking                      │
│   • Security event monitoring           │
└─────────────────────────────────────────┘
```

## Threat Model

### Identified Threats

1. **Command Injection** (CRITICAL)
   - Attack Vector: Malicious input in stack names, paths, or environment variables
   - Mitigation: Strict input validation, shell escaping, command whitelisting

2. **Path Traversal** (HIGH)
   - Attack Vector: Accessing files outside intended directories
   - Mitigation: Path normalization, parent directory checks, absolute path requirements

3. **Privilege Escalation** (HIGH)
   - Attack Vector: Exploiting Docker socket access
   - Mitigation: Restricted command set, no raw Docker socket exposure

4. **SSH Key Compromise** (HIGH)
   - Attack Vector: Stolen or leaked SSH keys
   - Mitigation: Key rotation, secure storage, audit logging

5. **Denial of Service** (MEDIUM)
   - Attack Vector: Resource exhaustion through excessive requests
   - Mitigation: Rate limiting, connection pooling, timeouts

6. **Information Disclosure** (MEDIUM)
   - Attack Vector: Error messages revealing system information
   - Mitigation: Generic error messages, secure logging

## Security Controls

### Input Validation

All user inputs undergo strict validation:

```python
# Stack name validation
VALID_STACK_NAME = r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$'

# Path validation
- No parent directory traversal (..)
- No shell metacharacters
- Absolute paths required
- Maximum length limits

# Hostname validation
- RFC-compliant domain names
- Valid IP addresses
- No shell injection patterns

# Username validation
- POSIX-compliant usernames
- No special characters
- Length limits enforced
```

### Command Whitelisting

Only specific Docker commands are allowed:

```python
ALLOWED_DOCKER_COMMANDS = {
    'ps', 'logs', 'start', 'stop', 'restart',
    'stats', 'compose', 'pull', 'build', 'inspect'
}

ALLOWED_COMPOSE_SUBCOMMANDS = {
    'up', 'down', 'ps', 'logs', 'build',
    'pull', 'restart', 'stop', 'start'
}
```

### Shell Escaping

All command arguments are escaped using Python's `shlex.quote()`:

```python
# Safe command construction
cmd = f"docker compose --project-name {shlex.quote(name)} up"
```

## SSH Security

### Connection Security

```yaml
SSH Options:
  StrictHostKeyChecking: yes        # Prevent MITM attacks
  PasswordAuthentication: no        # Key-based auth only
  PreferredAuthentications: publickey
  BatchMode: yes                    # No interactive prompts
  ConnectTimeout: 10                 # Prevent hanging
  ServerAliveInterval: 60           # Keep-alive
  ControlMaster: auto               # Connection pooling
  ControlPersist: 10m               # Reuse connections
```

### Key Management

- **Key Storage**: Keys stored with 600 permissions in `/etc/docker-mcp/ssh-keys/`
- **Key Rotation**: Automatic rotation every 90 days (configurable)
- **Key Types**: Ed25519 preferred, RSA-4096 as fallback
- **Key Archival**: Old keys archived for audit trail

## Rate Limiting

### Configuration

```python
Rate Limits:
  Per Minute: 60 requests per host
  Per Hour: 600 requests per host
  Concurrent: 10 connections per host
```

### Implementation

- Token bucket algorithm for request limiting
- Per-host tracking to prevent single host DoS
- Automatic cleanup of old tracking data
- Graceful degradation under load

## Audit Logging

### Log Format

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "host_id": "production-1",
  "username": "dockeruser",
  "command_hash": "a1b2c3d4",
  "command_length": 256,
  "success": true,
  "error": null
}
```

### Log Storage

- Location: `/var/log/docker-mcp-ssh-audit.log`
- Rotation: Daily with 30-day retention
- Permissions: 600 (root only)
- Format: JSON for easy parsing

## Key Management

### SSH Key Rotation Process

1. **Generation**: Create new Ed25519 key pair
2. **Deployment**: Add new public key to authorized_keys
3. **Verification**: Test new key access
4. **Rotation**: Switch to new key
5. **Archival**: Archive old key
6. **Cleanup**: Remove old keys after grace period

### Key Rotation API

```python
# Check if rotation needed
if key_manager.check_rotation_needed(host_id):
    # Rotate key
    new_private, new_public = await key_manager.rotate_key(
        host_id=host_id,
        hostname=hostname,
        username=username,
        current_key_path=current_key
    )
```

## Best Practices

### Deployment

1. **Use Dedicated User**: Create a dedicated user for Docker operations
2. **Restrict Sudo**: If sudo is required, use NOPASSWD for specific commands only
3. **Network Segmentation**: Isolate Docker hosts in separate network segments
4. **Firewall Rules**: Restrict SSH access to known source IPs
5. **SELinux/AppArmor**: Enable mandatory access controls

### Configuration

```yaml
# Secure host configuration
hosts:
  production:
    hostname: docker1.internal  # Use internal DNS
    user: docker-mcp            # Dedicated user
    port: 22222                 # Non-standard port
    identity_file: /etc/docker-mcp/keys/prod.key
    zfs_capable: true          # Use ZFS snapshots for atomic operations
```

### Monitoring

1. **Log Analysis**: Regular review of audit logs
2. **Anomaly Detection**: Alert on unusual patterns
3. **Failed Authentication**: Monitor SSH auth failures
4. **Rate Limit Violations**: Track hosts hitting limits
5. **Key Usage**: Monitor which keys are being used

## Security Testing

### Test Coverage

```bash
# Run security tests
uv run pytest tests/test_ssh_security.py -v

# Test categories:
- Input validation (injection prevention)
- Path traversal prevention
- Command construction
- Rate limiting
- Key rotation
- Audit logging
```

### Penetration Testing Checklist

- [ ] Command injection via stack names
- [ ] Path traversal in compose files
- [ ] Environment variable injection
- [ ] SSH key compromise simulation
- [ ] Rate limit bypass attempts
- [ ] Concurrent connection flooding
- [ ] Error message information leakage
- [ ] Log injection attacks

### Security Scanning

```bash
# Static analysis
bandit -r docker_mcp/

# Dependency scanning
pip-audit

# Container scanning (if applicable)
trivy image docker-mcp:latest
```

## Incident Response

### Security Event Handling

1. **Detection**: Monitor audit logs for anomalies
2. **Containment**: Disable affected host configurations
3. **Investigation**: Review audit trail and logs
4. **Remediation**: Rotate keys, patch vulnerabilities
5. **Recovery**: Re-enable with enhanced controls
6. **Lessons Learned**: Update security controls

### Emergency Procedures

```bash
# Disable all SSH operations
systemctl stop docker-mcp

# Rotate all keys immediately
docker-mcp rotate-keys --all --force

# Review audit logs
docker-mcp audit --since 24h --suspicious

# Block specific host
docker-mcp block-host <host-id>
```

## Compliance

### Standards Alignment

- **OWASP Top 10**: Addresses A03:2021 (Injection)
- **CIS Docker Benchmark**: Implements relevant controls
- **NIST Cybersecurity Framework**: Maps to Protect and Detect functions
- **PCI DSS**: Supports requirement 2.3 (encrypted connections)

### Audit Requirements

- Maintain 90-day audit log retention
- Regular key rotation (quarterly minimum)
- Annual security assessment
- Incident response plan testing

## Future Enhancements

### Planned Improvements

1. **mTLS Support**: Mutual TLS for enhanced authentication
2. **Hardware Security Module**: HSM integration for key storage
3. **SIEM Integration**: Export audit logs to SIEM systems
4. **Automated Threat Response**: Auto-block on detected attacks
5. **Zero Trust Architecture**: Per-request authentication

### Alternative Approaches

#### Docker API over SSH Tunnel

Instead of executing commands via SSH, establish an SSH tunnel to the Docker API:

```python
# Concept: SSH tunnel to Docker socket
ssh -L 2376:/var/run/docker.sock user@host
docker -H tcp://localhost:2376 ps
```

**Pros:**
- Native Docker API usage
- Better error handling
- Streaming capabilities

**Cons:**
- Requires Docker socket exposure
- More complex setup
- Potential for socket hijacking

## Security Contact

For security issues, please email: security@docker-mcp.example.com

Do NOT create public issues for security vulnerabilities.

## References

- [OWASP Command Injection](https://owasp.org/www-community/attacks/Command_Injection)
- [CIS Docker Benchmark](https://www.cisecurity.org/benchmark/docker)
- [SSH Security Best Practices](https://www.ssh.com/academy/ssh/security)
- [Python Security Guidelines](https://python.readthedocs.io/en/latest/library/security_warnings.html)
"""Deployment assistance prompts for Docker MCP."""

# prompts will be registered directly with the app
from typing import Any


def compose_optimization_prompt(
    compose_content: str, host_id: str, host_resources: dict[str, Any] | None = None
) -> str:
    """Generate a prompt for optimizing Docker Compose files.

    Args:
        compose_content: The Docker Compose YAML content
        host_id: Target deployment host ID
        host_resources: Available resources on the host

    Returns:
        Optimization prompt
    """
    resources_info = ""
    if host_resources:
        resources_info = f"""
Available resources on {host_id}:
- CPU cores: {host_resources.get("cpu_count", "Unknown")}
- Memory: {host_resources.get("memory_total", "Unknown")} bytes total
- Disk space: {host_resources.get("disk_available", "Unknown")} bytes available
- Running containers: {host_resources.get("containers_running", 0)}
"""

    return f"""Analyze this Docker Compose file and suggest optimizations for:

1. **Security Best Practices**:
   - Remove unnecessary privileges
   - Use non-root users where possible
   - Implement proper secrets management
   - Network security configurations

2. **Resource Efficiency**:
   - Memory and CPU limits
   - Resource reservations
   - Restart policies
   - Health checks

3. **Production Readiness**:
   - Logging configuration
   - Volume management
   - Environment variable handling
   - Service dependencies

4. **Multi-host Deployment Considerations**:
   - Port conflicts
   - Volume mounting strategies
   - Network configurations
   - Service discovery

Docker Compose content:
```yaml
{compose_content}
```

Current deployment target: {host_id}
{resources_info}

Please provide:
- Specific recommendations with explanations
- Modified YAML sections where applicable
- Security warnings if any
- Performance optimization suggestions
- Production deployment checklist"""


def troubleshooting_prompt(
    error_message: str,
    host_id: str,
    container_id: str | None = None,
    stack_name: str | None = None,
    recent_logs: list[str] | None = None,
    system_info: dict[str, Any] | None = None,
) -> str:
    """Generate a troubleshooting prompt for Docker deployment issues.

    Args:
        error_message: The error message encountered
        host_id: Host where the error occurred
        container_id: Container ID if applicable
        stack_name: Stack name if applicable
        recent_logs: Recent log entries
        system_info: System information

    Returns:
        Troubleshooting prompt
    """
    context_info = f"Host: {host_id}"

    if container_id:
        context_info += f"\nContainer: {container_id}"

    if stack_name:
        context_info += f"\nStack: {stack_name}"

    logs_section = ""
    if recent_logs:
        logs_section = f"""
Recent logs:
```
{chr(10).join(recent_logs[-20:])}  # Last 20 lines
```
"""

    system_section = ""
    if system_info:
        system_section = f"""
System information:
- Docker version: {system_info.get("docker_version", "Unknown")}
- OS: {system_info.get("os", "Unknown")}
- Available memory: {system_info.get("memory_available", "Unknown")}
- Disk space: {system_info.get("disk_available", "Unknown")}
"""

    return f"""Help diagnose and resolve this Docker deployment issue:

**Error Details:**
```
{error_message}
```

**Context:**
{context_info}
{system_section}
{logs_section}

**Please provide:**

1. **Root Cause Analysis**:
   - Most likely cause of the error
   - Contributing factors
   - Common scenarios that lead to this issue

2. **Immediate Solutions**:
   - Step-by-step troubleshooting commands
   - Quick fixes to try first
   - Emergency recovery procedures

3. **Long-term Prevention**:
   - Configuration improvements
   - Monitoring recommendations
   - Best practices to prevent recurrence

4. **Docker-specific Diagnostics**:
   - Relevant `docker` commands to run
   - Log locations to check
   - System resources to verify

5. **Escalation Path**:
   - When to involve system administrators
   - Additional information to collect
   - External resources for complex issues

Format your response with clear sections and actionable commands."""


def deployment_checklist_prompt(
    stack_name: str, environment: str, services: list[str], host_id: str
) -> str:
    """Generate a deployment checklist prompt.

    Args:
        stack_name: Name of the stack being deployed
        environment: Target environment (dev, staging, prod)
        services: List of services in the stack
        host_id: Target host

    Returns:
        Deployment checklist prompt
    """
    return f"""Create a comprehensive deployment checklist for:

**Stack Information:**
- Name: {stack_name}
- Environment: {environment}
- Target Host: {host_id}
- Services: {", ".join(services)}

**Generate checklists for:**

1. **Pre-deployment Verification**:
   - Host connectivity and resources
   - Required images and dependencies
   - Network and storage requirements
   - Security and access controls

2. **Deployment Process**:
   - Step-by-step deployment commands
   - Configuration validation
   - Service startup verification
   - Health check procedures

3. **Post-deployment Testing**:
   - Service connectivity tests
   - Functionality verification
   - Performance validation
   - Integration testing

4. **Monitoring and Alerts**:
   - Key metrics to monitor
   - Alert thresholds
   - Log monitoring setup
   - Dashboard configuration

5. **Rollback Procedures**:
   - Backup verification
   - Rollback commands
   - Data recovery steps
   - Service restoration

6. **Documentation Updates**:
   - Deployment records
   - Configuration changes
   - Known issues
   - Operational notes

Format as a actionable checklist with checkboxes and clear instructions."""


def security_audit_prompt(compose_content: str, host_environment: str = "production") -> str:
    """Generate a security audit prompt for Docker Compose configurations.

    Args:
        compose_content: Docker Compose YAML content
        host_environment: Target environment (dev, staging, production)

    Returns:
        Security audit prompt
    """
    return f"""Perform a comprehensive security audit of this Docker Compose configuration for a {host_environment} environment:

```yaml
{compose_content}
```

**Analyze for:**

1. **Container Security**:
   - Running as root vs non-root users
   - Unnecessary capabilities
   - Privilege escalation risks
   - Security context configurations

2. **Network Security**:
   - Exposed ports and services
   - Network isolation
   - Internal communication security
   - External access controls

3. **Secrets Management**:
   - Hardcoded passwords/keys
   - Environment variable exposure
   - Secret storage methods
   - Credential rotation capabilities

4. **Image Security**:
   - Base image vulnerabilities
   - Image provenance and trust
   - Registry security
   - Image scanning recommendations

5. **Volume and Data Security**:
   - Sensitive data exposure
   - Volume permissions
   - Host filesystem access
   - Data encryption requirements

6. **Runtime Security**:
   - Resource limits and DoS protection
   - Process isolation
   - System call restrictions
   - Logging and audit trails

**Provide:**
- High/Medium/Low risk ratings for each finding
- Specific remediation steps
- Code examples for fixes
- Environment-specific recommendations
- Compliance considerations (if applicable)

Rate the overall security posture and provide a prioritized action plan."""

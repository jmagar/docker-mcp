"""
Comprehensive tests for deployment prompts module.

Tests all prompt generation functions to achieve 95%+ coverage on prompts/deployment.py.
"""


from docker_mcp.prompts.deployment import (
    compose_optimization_prompt,
    deployment_checklist_prompt,
    security_audit_prompt,
    troubleshooting_prompt,
)


class TestComposeOptimizationPrompt:
    """Test compose_optimization_prompt function."""

    def test_basic_compose_optimization_prompt(self):
        """Test basic compose optimization prompt generation."""
        compose_content = """version: '3.8'
services:
  web:
    image: nginx:alpine
    ports:
      - "80:80"
"""
        host_id = "prod-host-01"

        prompt = compose_optimization_prompt(compose_content, host_id)

        # Check that all expected sections are present
        assert "Security Best Practices" in prompt
        assert "Resource Efficiency" in prompt
        assert "Production Readiness" in prompt
        assert "Multi-host Deployment Considerations" in prompt

        # Check that compose content is included
        assert compose_content in prompt
        assert "```yaml" in prompt

        # Check that host_id is included
        assert host_id in prompt
        assert "Current deployment target: prod-host-01" in prompt

        # Check that specific optimization areas are mentioned
        assert "Remove unnecessary privileges" in prompt
        assert "Memory and CPU limits" in prompt
        assert "Logging configuration" in prompt
        assert "Port conflicts" in prompt

        # Check that output requirements are specified
        assert "Specific recommendations" in prompt
        assert "Modified YAML sections" in prompt
        assert "Security warnings" in prompt
        assert "Performance optimization" in prompt
        assert "Production deployment checklist" in prompt

    def test_compose_optimization_prompt_with_resources(self):
        """Test compose optimization prompt with host resources."""
        compose_content = """version: '3.8'
services:
  app:
    image: myapp:latest
    environment:
      - DATABASE_URL=postgres://localhost:5432/db
"""
        host_id = "prod-host-02"
        host_resources = {
            "cpu_count": 8,
            "memory_total": 16 * 1024 * 1024 * 1024,  # 16GB
            "disk_available": 500 * 1024 * 1024 * 1024,  # 500GB
            "containers_running": 12
        }

        prompt = compose_optimization_prompt(compose_content, host_id, host_resources)

        # Check that resource information is included
        assert "Available resources on prod-host-02:" in prompt
        assert "CPU cores: 8" in prompt
        assert f"Memory: {16 * 1024 * 1024 * 1024} bytes total" in prompt
        assert f"Disk space: {500 * 1024 * 1024 * 1024} bytes available" in prompt
        assert "Running containers: 12" in prompt

        # Check that compose content and basic structure are still present
        assert compose_content in prompt
        assert "Security Best Practices" in prompt
        assert host_id in prompt

    def test_compose_optimization_prompt_with_partial_resources(self):
        """Test compose optimization prompt with partial resource information."""
        compose_content = """version: '3.8'
services:
  redis:
    image: redis:alpine
"""
        host_id = "test-host"
        host_resources = {
            "cpu_count": 4,
            # Missing memory_total, disk_available
            "containers_running": 3
        }

        prompt = compose_optimization_prompt(compose_content, host_id, host_resources)

        # Check that available resources are shown, unknown ones show "Unknown"
        assert "CPU cores: 4" in prompt
        assert "Memory: Unknown" in prompt
        assert "Disk space: Unknown" in prompt
        assert "Running containers: 3" in prompt

    def test_compose_optimization_prompt_with_empty_resources(self):
        """Test compose optimization prompt with empty resource dict."""
        compose_content = """version: '3.8'
services:
  db:
    image: postgres:13
"""
        host_id = "empty-host"
        host_resources = {}

        prompt = compose_optimization_prompt(compose_content, host_id, host_resources)

        # Empty dict is falsy, so no resources section should be included
        # This behaves the same as host_resources=None
        assert "Available resources" not in prompt
        assert compose_content in prompt
        assert host_id in prompt
        assert "Security Best Practices" in prompt

    def test_compose_optimization_prompt_without_resources(self):
        """Test compose optimization prompt without resource information."""
        compose_content = """version: '3.8'
services:
  api:
    image: node:16-alpine
    ports:
      - "3000:3000"
"""
        host_id = "no-resources-host"

        prompt = compose_optimization_prompt(compose_content, host_id)

        # Should not include resources section when resources is None
        assert "Available resources" not in prompt
        assert compose_content in prompt
        assert host_id in prompt
        assert "Security Best Practices" in prompt

    def test_compose_optimization_prompt_with_complex_compose(self):
        """Test with complex Docker Compose content."""
        compose_content = """version: '3.8'
services:
  web:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - app
  app:
    image: python:3.9
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/mydb
      - SECRET_KEY=my-secret-key
    volumes:
      - .:/app
    working_dir: /app
  db:
    image: postgres:13
    environment:
      - POSTGRES_DB=mydb
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=password
    volumes:
      - db_data:/var/lib/postgresql/data
volumes:
  db_data:
"""
        host_id = "complex-host"

        prompt = compose_optimization_prompt(compose_content, host_id)

        # Verify complete compose content is preserved
        assert "nginx:alpine" in prompt
        assert "python:3.9" in prompt
        assert "postgres:13" in prompt
        assert "volumes:" in prompt
        assert "depends_on:" in prompt

        # Verify security recommendations are relevant to the complex setup
        assert "secrets management" in prompt
        assert "Network security" in prompt


class TestTroubleshootingPrompt:
    """Test troubleshooting_prompt function."""

    def test_basic_troubleshooting_prompt(self):
        """Test basic troubleshooting prompt generation."""
        error_message = "Container failed to start: port already in use"
        host_id = "prod-host-01"

        prompt = troubleshooting_prompt(error_message, host_id)

        # Check error details section
        assert "Error Details:" in prompt
        assert error_message in prompt
        assert "```" in prompt  # Error should be in code block

        # Check context section
        assert "Context:" in prompt
        assert f"Host: {host_id}" in prompt

        # Check that all required sections are present
        assert "Root Cause Analysis" in prompt
        assert "Immediate Solutions" in prompt
        assert "Long-term Prevention" in prompt
        assert "Docker-specific Diagnostics" in prompt
        assert "Escalation Path" in prompt

        # Check specific content
        assert "Most likely cause" in prompt
        assert "Step-by-step troubleshooting" in prompt
        assert "docker` commands" in prompt
        assert "system administrators" in prompt

    def test_troubleshooting_prompt_with_container_id(self):
        """Test troubleshooting prompt with container ID."""
        error_message = "Container exited with code 1"
        host_id = "test-host"
        container_id = "nginx-container-123"

        prompt = troubleshooting_prompt(error_message, host_id, container_id=container_id)

        # Check that container ID is included in context
        assert f"Host: {host_id}" in prompt
        assert f"Container: {container_id}" in prompt
        assert error_message in prompt

    def test_troubleshooting_prompt_with_stack_name(self):
        """Test troubleshooting prompt with stack name."""
        error_message = "Stack deployment failed"
        host_id = "prod-host"
        stack_name = "web-application-stack"

        prompt = troubleshooting_prompt(error_message, host_id, stack_name=stack_name)

        # Check that stack name is included in context
        assert f"Host: {host_id}" in prompt
        assert f"Stack: {stack_name}" in prompt
        assert error_message in prompt

    def test_troubleshooting_prompt_with_logs(self):
        """Test troubleshooting prompt with recent logs."""
        error_message = "Service unhealthy"
        host_id = "log-host"
        recent_logs = [
            "2025-01-15 10:00:00 INFO Starting application",
            "2025-01-15 10:00:01 WARN Database connection slow",
            "2025-01-15 10:00:02 ERROR Failed to connect to database",
            "2025-01-15 10:00:03 FATAL Application shutting down"
        ]

        prompt = troubleshooting_prompt(error_message, host_id, recent_logs=recent_logs)

        # Check that logs section is included
        assert "Recent logs:" in prompt
        assert "```" in prompt  # Logs should be in code block

        # Check that log entries are present
        for log_entry in recent_logs:
            assert log_entry in prompt

    def test_troubleshooting_prompt_with_many_logs(self):
        """Test troubleshooting prompt with many log entries (should truncate)."""
        error_message = "Too many logs error"
        host_id = "many-logs-host"

        # Create 30 log entries (more than the 20 limit)
        recent_logs = [f"2025-01-15 10:00:{i:02d} Log entry {i}" for i in range(30)]

        prompt = troubleshooting_prompt(error_message, host_id, recent_logs=recent_logs)

        # Should only include last 20 entries
        assert "Log entry 10" in prompt  # Entry 10 should be included (it's in last 20)
        assert "Log entry 29" in prompt  # Last entry should be included
        assert "Log entry 5" not in prompt  # Early entries should be excluded

    def test_troubleshooting_prompt_with_system_info(self):
        """Test troubleshooting prompt with system information."""
        error_message = "System resource error"
        host_id = "system-host"
        system_info = {
            "docker_version": "24.0.7",
            "os": "Ubuntu 22.04 LTS",
            "memory_available": "8GB",
            "disk_available": "500GB"
        }

        prompt = troubleshooting_prompt(error_message, host_id, system_info=system_info)

        # Check that system information is included
        assert "System information:" in prompt
        assert "Docker version: 24.0.7" in prompt
        assert "OS: Ubuntu 22.04 LTS" in prompt
        assert "Available memory: 8GB" in prompt
        assert "Disk space: 500GB" in prompt

    def test_troubleshooting_prompt_with_partial_system_info(self):
        """Test troubleshooting prompt with partial system information."""
        error_message = "Partial system info error"
        host_id = "partial-host"
        system_info = {
            "docker_version": "25.0.0",
            # Missing os, memory_available, disk_available
        }

        prompt = troubleshooting_prompt(error_message, host_id, system_info=system_info)

        # Should show available info and "Unknown" for missing
        assert "Docker version: 25.0.0" in prompt
        assert "OS: Unknown" in prompt
        assert "Available memory: Unknown" in prompt
        assert "Disk space: Unknown" in prompt

    def test_troubleshooting_prompt_with_all_parameters(self):
        """Test troubleshooting prompt with all parameters provided."""
        error_message = "Complete troubleshooting scenario"
        host_id = "complete-host"
        container_id = "app-container-456"
        stack_name = "production-stack"
        recent_logs = [
            "2025-01-15 12:00:00 INFO Service starting",
            "2025-01-15 12:00:01 ERROR Connection failed"
        ]
        system_info = {
            "docker_version": "24.0.7",
            "os": "CentOS 8",
            "memory_available": "16GB",
            "disk_available": "1TB"
        }

        prompt = troubleshooting_prompt(
            error_message, host_id, container_id, stack_name, recent_logs, system_info
        )

        # Check that all information is included
        assert f"Host: {host_id}" in prompt
        assert f"Container: {container_id}" in prompt
        assert f"Stack: {stack_name}" in prompt
        assert "Recent logs:" in prompt
        assert "System information:" in prompt
        assert error_message in prompt

        # Check specific values
        assert "Service starting" in prompt
        assert "Docker version: 24.0.7" in prompt


class TestDeploymentChecklistPrompt:
    """Test deployment_checklist_prompt function."""

    def test_basic_deployment_checklist_prompt(self):
        """Test basic deployment checklist prompt generation."""
        stack_name = "web-application"
        environment = "production"
        services = ["nginx", "app", "redis", "postgres"]
        host_id = "prod-host-01"

        prompt = deployment_checklist_prompt(stack_name, environment, services, host_id)

        # Check stack information section
        assert "Stack Information:" in prompt
        assert f"Name: {stack_name}" in prompt
        assert f"Environment: {environment}" in prompt
        assert f"Target Host: {host_id}" in prompt
        assert "Services: nginx, app, redis, postgres" in prompt

        # Check that all checklist sections are present
        assert "Pre-deployment Verification" in prompt
        assert "Deployment Process" in prompt
        assert "Post-deployment Testing" in prompt
        assert "Monitoring and Alerts" in prompt
        assert "Rollback Procedures" in prompt
        assert "Documentation Updates" in prompt

        # Check specific checklist items
        assert "Host connectivity and resources" in prompt
        assert "Step-by-step deployment commands" in prompt
        assert "Service connectivity tests" in prompt
        assert "Key metrics to monitor" in prompt
        assert "Backup verification" in prompt
        assert "Deployment records" in prompt

    def test_deployment_checklist_prompt_with_single_service(self):
        """Test deployment checklist with single service."""
        stack_name = "simple-app"
        environment = "development"
        services = ["webapp"]
        host_id = "dev-host"

        prompt = deployment_checklist_prompt(stack_name, environment, services, host_id)

        # Check that single service is handled correctly
        assert "Services: webapp" in prompt
        assert "Name: simple-app" in prompt
        assert "Environment: development" in prompt

        # All sections should still be present even for single service
        assert "Pre-deployment Verification" in prompt
        assert "Rollback Procedures" in prompt

    def test_deployment_checklist_prompt_with_many_services(self):
        """Test deployment checklist with many services."""
        stack_name = "microservices-platform"
        environment = "staging"
        services = [
            "api-gateway", "auth-service", "user-service", "order-service",
            "payment-service", "notification-service", "web-frontend",
            "admin-panel", "redis", "postgres", "elasticsearch", "nginx"
        ]
        host_id = "staging-cluster"

        prompt = deployment_checklist_prompt(stack_name, environment, services, host_id)

        # Check that all services are listed
        services_list = ", ".join(services)
        assert services_list in prompt
        assert "microservices-platform" in prompt
        assert "staging" in prompt

        # Complex deployments should still have all sections
        assert "Pre-deployment Verification" in prompt
        assert "Integration testing" in prompt

    def test_deployment_checklist_prompt_production_environment(self):
        """Test deployment checklist specifically for production environment."""
        stack_name = "critical-app"
        environment = "production"
        services = ["load-balancer", "app", "database"]
        host_id = "prod-cluster-01"

        prompt = deployment_checklist_prompt(stack_name, environment, services, host_id)

        # Production environment should trigger all critical sections
        assert "Environment: production" in prompt
        assert "Security and access controls" in prompt
        assert "Performance validation" in prompt
        assert "Alert thresholds" in prompt
        assert "Data recovery steps" in prompt

    def test_deployment_checklist_prompt_formatting(self):
        """Test deployment checklist prompt formatting requirements."""
        stack_name = "test-stack"
        environment = "test"
        services = ["service1", "service2"]
        host_id = "test-host"

        prompt = deployment_checklist_prompt(stack_name, environment, services, host_id)

        # Check that formatting instructions are included
        assert "actionable checklist" in prompt
        assert "checkboxes" in prompt
        assert "clear instructions" in prompt


class TestSecurityAuditPrompt:
    """Test security_audit_prompt function."""

    def test_basic_security_audit_prompt(self):
        """Test basic security audit prompt generation."""
        compose_content = """version: '3.8'
services:
  web:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./html:/usr/share/nginx/html
"""

        prompt = security_audit_prompt(compose_content)

        # Check that compose content is included
        assert compose_content in prompt
        assert "```yaml" in prompt

        # Check default environment
        assert "production environment" in prompt

        # Check that all security analysis sections are present
        assert "Container Security" in prompt
        assert "Network Security" in prompt
        assert "Secrets Management" in prompt
        assert "Image Security" in prompt
        assert "Volume and Data Security" in prompt
        assert "Runtime Security" in prompt

        # Check specific security concerns
        assert "Running as root vs non-root users" in prompt
        assert "Exposed ports and services" in prompt
        assert "Hardcoded passwords/keys" in prompt
        assert "Base image vulnerabilities" in prompt
        assert "Sensitive data exposure" in prompt
        assert "Resource limits and DoS protection" in prompt

        # Check output requirements
        assert "High/Medium/Low risk ratings" in prompt
        assert "Specific remediation steps" in prompt
        assert "Code examples" in prompt
        assert "prioritized action plan" in prompt

    def test_security_audit_prompt_with_development_environment(self):
        """Test security audit prompt for development environment."""
        compose_content = """version: '3.8'
services:
  app:
    image: python:3.9
    environment:
      - DEBUG=true
      - SECRET_KEY=dev-secret
"""

        prompt = security_audit_prompt(compose_content, "development")

        # Check environment-specific content
        assert "development environment" in prompt
        assert compose_content in prompt
        assert "Environment-specific recommendations" in prompt

    def test_security_audit_prompt_with_staging_environment(self):
        """Test security audit prompt for staging environment."""
        compose_content = """version: '3.8'
services:
  api:
    image: myapi:latest
    ports:
      - "8080:8080"
"""

        prompt = security_audit_prompt(compose_content, "staging")

        # Check staging-specific analysis
        assert "staging environment" in prompt
        assert compose_content in prompt

    def test_security_audit_prompt_with_complex_compose(self):
        """Test security audit with complex, potentially insecure compose file."""
        compose_content = """version: '3.8'
services:
  web:
    image: nginx:latest
    ports:
      - "80:80"
      - "443:443"
    privileged: true
    user: root
    volumes:
      - /:/host
      - /var/run/docker.sock:/var/run/docker.sock
  app:
    image: myapp:dev
    environment:
      - DATABASE_PASSWORD=hardcoded-password
      - API_KEY=sk-1234567890abcdef
      - DEBUG=true
    network_mode: host
    cap_add:
      - SYS_ADMIN
      - NET_ADMIN
  db:
    image: postgres:9.6
    environment:
      - POSTGRES_PASSWORD=admin123
      - POSTGRES_HOST_AUTH_METHOD=trust
    volumes:
      - /etc/passwd:/etc/passwd:ro
    ports:
      - "5432:5432"
"""

        prompt = security_audit_prompt(compose_content, "production")

        # Should analyze all the security issues present
        assert "privileged: true" in compose_content  # Verify problematic content is included
        assert "hardcoded-password" in compose_content
        assert "SYS_ADMIN" in compose_content

        # All security categories should be covered
        assert "Container Security" in prompt
        assert "Privilege escalation risks" in prompt
        assert "Hardcoded passwords" in prompt
        assert "Host filesystem access" in prompt

    def test_security_audit_prompt_output_requirements(self):
        """Test security audit prompt output formatting requirements."""
        compose_content = """version: '3.8'
services:
  simple:
    image: alpine:latest
"""

        prompt = security_audit_prompt(compose_content)

        # Check that all output requirements are specified
        assert "High/Medium/Low risk ratings" in prompt
        assert "Specific remediation steps" in prompt
        assert "Code examples for fixes" in prompt
        assert "Environment-specific recommendations" in prompt
        assert "Compliance considerations" in prompt
        assert "overall security posture" in prompt
        assert "prioritized action plan" in prompt


class TestPromptIntegration:
    """Test integration scenarios and edge cases."""

    def test_all_prompts_return_strings(self):
        """Test that all prompt functions return non-empty strings."""
        # Test compose optimization
        compose_prompt = compose_optimization_prompt("version: '3.8'", "test-host")
        assert isinstance(compose_prompt, str)
        assert len(compose_prompt) > 100  # Should be substantial content

        # Test troubleshooting
        trouble_prompt = troubleshooting_prompt("Error occurred", "test-host")
        assert isinstance(trouble_prompt, str)
        assert len(trouble_prompt) > 100

        # Test deployment checklist
        checklist_prompt = deployment_checklist_prompt("stack", "prod", ["service"], "host")
        assert isinstance(checklist_prompt, str)
        assert len(checklist_prompt) > 100

        # Test security audit
        security_prompt = security_audit_prompt("version: '3.8'")
        assert isinstance(security_prompt, str)
        assert len(security_prompt) > 100

    def test_prompts_with_empty_inputs(self):
        """Test prompts with minimal/empty inputs."""
        # Test with empty compose content
        prompt1 = compose_optimization_prompt("", "host")
        assert isinstance(prompt1, str)
        assert "host" in prompt1

        # Test with empty error message
        prompt2 = troubleshooting_prompt("", "host")
        assert isinstance(prompt2, str)
        assert "host" in prompt2

        # Test with empty services list
        prompt3 = deployment_checklist_prompt("stack", "env", [], "host")
        assert isinstance(prompt3, str)
        assert "Services:" in prompt3

        # Test with empty compose for security
        prompt4 = security_audit_prompt("")
        assert isinstance(prompt4, str)
        assert "security audit" in prompt4

    def test_prompts_with_special_characters(self):
        """Test prompts with special characters and edge cases."""
        # Test with compose content containing special characters
        special_compose = """version: '3.8'
services:
  app:
    image: "my/app:latest"
    environment:
      - "KEY=value with spaces & symbols!@#$%^&*()"
"""

        prompt = compose_optimization_prompt(special_compose, "test-host")
        assert special_compose in prompt
        assert "&" in prompt  # Special characters should be preserved

        # Test with error message containing special characters
        error_msg = "Error: Connection failed [errno: 111] (connection refused) @ 192.168.1.1:5432"
        trouble_prompt = troubleshooting_prompt(error_msg, "host")
        assert error_msg in trouble_prompt

    def test_prompt_consistency(self):
        """Test that prompts maintain consistent structure."""
        compose_content = "version: '3.8'"
        host_id = "consistency-host"

        # Generate same prompt multiple times
        prompt1 = compose_optimization_prompt(compose_content, host_id)
        prompt2 = compose_optimization_prompt(compose_content, host_id)

        # Should be identical (deterministic)
        assert prompt1 == prompt2

        # Test other prompts for consistency
        error_msg = "Test error"
        trouble1 = troubleshooting_prompt(error_msg, host_id)
        trouble2 = troubleshooting_prompt(error_msg, host_id)
        assert trouble1 == trouble2

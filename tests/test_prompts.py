"""Tests for Docker MCP prompt generation functions."""

import pytest

from docker_mcp.prompts.deployment import (
    compose_optimization_prompt,
    troubleshooting_prompt,
    deployment_checklist_prompt,
    security_audit_prompt,
)


class TestComposeOptimizationPrompt:
    """Test suite for compose_optimization_prompt function."""

    def test_minimal_compose_optimization_prompt(self):
        """Test compose optimization prompt with minimal parameters."""
        compose_content = """
version: '3.8'
services:
  web:
    image: nginx:alpine
    ports:
      - "80:80"
"""
        host_id = "prod-1"
        
        result = compose_optimization_prompt(compose_content, host_id)
        
        # Verify basic structure
        assert isinstance(result, str)
        assert len(result) > 100  # Should be substantial prompt
        
        # Verify required content is included
        assert "Analyze this Docker Compose file" in result
        assert compose_content in result
        assert host_id in result
        
        # Verify optimization categories are included
        assert "Security Best Practices" in result
        assert "Resource Efficiency" in result
        assert "Production Readiness" in result
        assert "Multi-host Deployment Considerations" in result
        
        # Verify output specifications
        assert "Specific recommendations" in result
        assert "Modified YAML sections" in result
        assert "Security warnings" in result

    def test_compose_optimization_prompt_with_resources(self):
        """Test compose optimization prompt with host resources."""
        compose_content = "version: '3.8'\nservices:\n  app:\n    image: node:18"
        host_id = "staging-2"
        host_resources = {
            "cpu_count": 8,
            "memory_total": 16_000_000_000,
            "disk_available": 500_000_000_000,
            "containers_running": 5
        }
        
        result = compose_optimization_prompt(compose_content, host_id, host_resources)
        
        # Verify basic content
        assert compose_content in result
        assert host_id in result
        
        # Verify resource information is included
        assert "Available resources on staging-2" in result
        assert "CPU cores: 8" in result
        assert "Memory: 16000000000 bytes total" in result
        assert "Disk space: 500000000000 bytes available" in result
        assert "Running containers: 5" in result

    def test_compose_optimization_prompt_without_resources(self):
        """Test that prompt works without host resources."""
        compose_content = "version: '3.8'"
        host_id = "test-host"
        
        result = compose_optimization_prompt(compose_content, host_id, None)
        
        # Should not contain resource information
        assert "Available resources" not in result
        assert "CPU cores:" not in result
        assert "Memory:" not in result
        
        # But should still contain basic content
        assert compose_content in result
        assert host_id in result
        assert "Current deployment target: test-host" in result

    def test_compose_optimization_prompt_empty_resources(self):
        """Test prompt with empty resources dictionary."""
        compose_content = "version: '3.8'"
        host_id = "test-host"
        host_resources = {}  # Empty dict
        
        result = compose_optimization_prompt(compose_content, host_id, host_resources)
        
        # Should include resource section but with "Unknown" values
        assert "Available resources on test-host" in result
        assert "CPU cores: Unknown" in result
        assert "Memory: Unknown bytes total" in result


class TestTroubleshootingPrompt:
    """Test suite for troubleshooting_prompt function."""

    def test_minimal_troubleshooting_prompt(self):
        """Test troubleshooting prompt with minimal parameters."""
        error_message = "Container failed to start: port already in use"
        host_id = "prod-1"
        
        result = troubleshooting_prompt(error_message, host_id)
        
        # Verify basic structure
        assert isinstance(result, str)
        assert len(result) > 200  # Should be comprehensive prompt
        
        # Verify required content
        assert "Help diagnose and resolve this Docker deployment issue" in result
        assert error_message in result
        assert f"Host: {host_id}" in result
        
        # Verify analysis sections
        assert "Root Cause Analysis" in result
        assert "Immediate Solutions" in result
        assert "Long-term Prevention" in result
        assert "Docker-specific Diagnostics" in result
        assert "Escalation Path" in result

    def test_troubleshooting_prompt_with_container(self):
        """Test troubleshooting prompt with container ID."""
        error_message = "Failed to pull image"
        host_id = "staging-1"
        container_id = "web-server-123"
        
        result = troubleshooting_prompt(
            error_message, host_id, container_id=container_id
        )
        
        # Verify container context is included
        assert f"Host: {host_id}" in result
        assert f"Container: {container_id}" in result

    def test_troubleshooting_prompt_with_stack(self):
        """Test troubleshooting prompt with stack name."""
        error_message = "Stack deployment failed"
        host_id = "prod-2"
        stack_name = "web-application"
        
        result = troubleshooting_prompt(
            error_message, host_id, stack_name=stack_name
        )
        
        # Verify stack context is included
        assert f"Host: {host_id}" in result
        assert f"Stack: {stack_name}" in result

    def test_troubleshooting_prompt_with_logs(self):
        """Test troubleshooting prompt with recent logs."""
        error_message = "Service unhealthy"
        host_id = "dev-1"
        recent_logs = [
            "2025-01-15 10:30:00 ERROR: Database connection failed",
            "2025-01-15 10:30:01 WARN: Retrying connection",
            "2025-01-15 10:30:02 ERROR: Max retries exceeded"
        ]
        
        result = troubleshooting_prompt(
            error_message, host_id, recent_logs=recent_logs
        )
        
        # Verify logs are included
        assert "Recent logs:" in result
        assert "Database connection failed" in result
        assert "Max retries exceeded" in result
        
        # Verify logs are in code block
        assert "```" in result

    def test_troubleshooting_prompt_with_system_info(self):
        """Test troubleshooting prompt with system information."""
        error_message = "Out of memory"
        host_id = "prod-3"
        system_info = {
            "docker_version": "24.0.7",
            "os": "Ubuntu 22.04",
            "memory_available": 2_000_000_000,
            "disk_available": 10_000_000_000
        }
        
        result = troubleshooting_prompt(
            error_message, host_id, system_info=system_info
        )
        
        # Verify system info is included
        assert "System information:" in result
        assert "Docker version: 24.0.7" in result
        assert "OS: Ubuntu 22.04" in result
        assert "Available memory: 2000000000" in result
        assert "Disk space: 10000000000" in result

    def test_troubleshooting_prompt_comprehensive(self):
        """Test troubleshooting prompt with all optional parameters."""
        error_message = "Deployment timeout"
        host_id = "production"
        container_id = "app-container"
        stack_name = "main-app"
        recent_logs = ["Error: timeout waiting for condition"]
        system_info = {"docker_version": "24.0.7"}
        
        result = troubleshooting_prompt(
            error_message=error_message,
            host_id=host_id,
            container_id=container_id,
            stack_name=stack_name,
            recent_logs=recent_logs,
            system_info=system_info
        )
        
        # Verify all context is included
        assert f"Host: {host_id}" in result
        assert f"Container: {container_id}" in result
        assert f"Stack: {stack_name}" in result
        assert "Recent logs:" in result
        assert "System information:" in result
        assert "timeout waiting for condition" in result
        assert "Docker version: 24.0.7" in result

    def test_troubleshooting_prompt_log_truncation(self):
        """Test that logs are truncated to last 20 lines."""
        error_message = "Too many logs"
        host_id = "test"
        # Create 25 log lines
        recent_logs = [f"Log line {i}" for i in range(25)]
        
        result = troubleshooting_prompt(
            error_message, host_id, recent_logs=recent_logs
        )
        
        # Should only include last 20 lines (5-24)
        assert "Log line 24" in result  # Last line should be included
        assert "Log line 5" in result   # 20th from last should be included
        assert "Log line 4" not in result  # 21st from last should be excluded


class TestDeploymentChecklistPrompt:
    """Test suite for deployment_checklist_prompt function."""

    def test_deployment_checklist_prompt_basic(self):
        """Test deployment checklist prompt with basic parameters."""
        stack_name = "web-application"
        environment = "production"
        services = ["web", "api", "database"]
        host_id = "prod-cluster-1"
        
        result = deployment_checklist_prompt(stack_name, environment, services, host_id)
        
        # Verify basic structure
        assert isinstance(result, str)
        assert len(result) > 300  # Should be comprehensive checklist
        
        # Verify stack information section
        assert "Stack Information:" in result
        assert f"Name: {stack_name}" in result
        assert f"Environment: {environment}" in result
        assert f"Target Host: {host_id}" in result
        assert f"Services: {', '.join(services)}" in result
        
        # Verify checklist categories
        assert "Pre-deployment Verification" in result
        assert "Deployment Process" in result
        assert "Post-deployment Testing" in result
        assert "Monitoring and Alerts" in result
        assert "Rollback Procedures" in result
        assert "Documentation Updates" in result

    def test_deployment_checklist_prompt_single_service(self):
        """Test deployment checklist with single service."""
        stack_name = "nginx-proxy"
        environment = "staging"
        services = ["nginx"]
        host_id = "staging-1"
        
        result = deployment_checklist_prompt(stack_name, environment, services, host_id)
        
        # Verify single service is handled correctly
        assert "Services: nginx" in result
        assert f"Name: {stack_name}" in result

    def test_deployment_checklist_prompt_many_services(self):
        """Test deployment checklist with many services."""
        stack_name = "microservices"
        environment = "production"
        services = ["auth", "user", "order", "payment", "notification", "gateway"]
        host_id = "k8s-cluster"
        
        result = deployment_checklist_prompt(stack_name, environment, services, host_id)
        
        # Verify all services are listed
        services_line = f"Services: {', '.join(services)}"
        assert services_line in result
        assert "auth, user, order, payment, notification, gateway" in result

    def test_deployment_checklist_format_requirements(self):
        """Test that checklist format requirements are specified."""
        result = deployment_checklist_prompt("test", "dev", ["app"], "test-host")
        
        # Should specify format requirements
        assert "actionable checklist with checkboxes" in result
        assert "clear instructions" in result


class TestSecurityAuditPrompt:
    """Test suite for security_audit_prompt function."""

    def test_security_audit_prompt_basic(self):
        """Test security audit prompt with basic parameters."""
        compose_content = """
version: '3.8'
services:
  web:
    image: nginx:latest
    ports:
      - "80:80"
    environment:
      - SECRET_KEY=hardcoded_secret
"""
        
        result = security_audit_prompt(compose_content)
        
        # Verify basic structure
        assert isinstance(result, str)
        assert len(result) > 400  # Should be comprehensive audit prompt
        
        # Verify compose content is included
        assert compose_content in result
        assert "```yaml" in result
        
        # Verify default environment
        assert "production environment" in result
        
        # Verify security analysis categories
        assert "Container Security" in result
        assert "Network Security" in result
        assert "Secrets Management" in result
        assert "Image Security" in result
        assert "Volume and Data Security" in result
        assert "Runtime Security" in result

    def test_security_audit_prompt_custom_environment(self):
        """Test security audit prompt with custom environment."""
        compose_content = "version: '3.8'"
        host_environment = "staging"
        
        result = security_audit_prompt(compose_content, host_environment)
        
        # Verify custom environment is used
        assert f"staging environment" in result
        assert "production environment" not in result

    def test_security_audit_prompt_analysis_requirements(self):
        """Test security audit analysis requirements are included."""
        compose_content = "version: '3.8'"
        
        result = security_audit_prompt(compose_content)
        
        # Verify specific analysis requirements
        assert "Running as root vs non-root users" in result
        assert "Unnecessary capabilities" in result
        assert "Exposed ports and services" in result
        assert "Hardcoded passwords/keys" in result
        assert "Base image vulnerabilities" in result
        assert "Resource limits and DoS protection" in result

    def test_security_audit_prompt_output_requirements(self):
        """Test security audit output requirements are specified."""
        compose_content = "version: '3.8'"
        
        result = security_audit_prompt(compose_content)
        
        # Verify output requirements
        assert "High/Medium/Low risk ratings" in result
        assert "Specific remediation steps" in result
        assert "Code examples for fixes" in result
        assert "Environment-specific recommendations" in result
        assert "Compliance considerations" in result
        assert "overall security posture" in result
        assert "prioritized action plan" in result

    def test_security_audit_prompt_environments(self):
        """Test security audit with different environments."""
        compose_content = "version: '3.8'"
        
        # Test different environments
        environments = ["development", "staging", "production", "testing"]
        
        for env in environments:
            result = security_audit_prompt(compose_content, env)
            assert f"{env} environment" in result
            assert compose_content in result


class TestPromptIntegration:
    """Integration tests for prompt functions working together."""

    def test_all_prompts_return_strings(self):
        """Test that all prompt functions return non-empty strings."""
        compose_content = "version: '3.8'\nservices:\n  test:\n    image: nginx"
        
        prompts = [
            compose_optimization_prompt(compose_content, "test-host"),
            troubleshooting_prompt("Error message", "test-host"),
            deployment_checklist_prompt("test", "dev", ["app"], "test-host"),
            security_audit_prompt(compose_content)
        ]
        
        for prompt in prompts:
            assert isinstance(prompt, str)
            assert len(prompt) > 50  # All should be substantial
            assert prompt.strip()  # Should not be just whitespace

    def test_prompts_handle_empty_inputs(self):
        """Test that prompts handle edge cases gracefully."""
        # Empty compose content
        empty_compose = ""
        
        # Should not crash, though output may not be useful
        result1 = compose_optimization_prompt(empty_compose, "host")
        assert isinstance(result1, str)
        
        result2 = security_audit_prompt(empty_compose)
        assert isinstance(result2, str)
        
        # Empty error message
        result3 = troubleshooting_prompt("", "host")
        assert isinstance(result3, str)
        
        # Empty services list
        result4 = deployment_checklist_prompt("stack", "env", [], "host")
        assert isinstance(result4, str)

    def test_prompts_handle_special_characters(self):
        """Test that prompts handle special characters in input."""
        special_compose = '''version: '3.8'
services:
  test:
    image: nginx
    command: echo "Hello & goodbye! $USER @host <tag>"
    environment:
      - KEY=value with spaces & symbols!
'''
        
        result = compose_optimization_prompt(special_compose, "test-host")
        assert special_compose in result
        assert "&" in result
        assert "$" in result
        assert "<" in result
        assert ">" in result

    def test_prompt_context_isolation(self):
        """Test that prompts don't interfere with each other."""
        compose1 = "version: '3.8'\nservices:\n  app1:\n    image: app1"
        compose2 = "version: '3.8'\nservices:\n  app2:\n    image: app2"
        
        result1 = compose_optimization_prompt(compose1, "host1")
        result2 = compose_optimization_prompt(compose2, "host2")
        
        # Each should contain only its own content
        assert "app1" in result1
        assert "app1" not in result2
        assert "app2" in result2
        assert "app2" not in result1
        assert "host1" in result1
        assert "host2" in result2
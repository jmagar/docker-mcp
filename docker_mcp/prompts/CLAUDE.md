# Prompts Layer - Development Memory

## AI Prompt Template Architecture

### Function-based Prompt Templates
```python
def compose_optimization_prompt(
    compose_content: str, 
    host_id: str, 
    host_resources: dict[str, Any] | None = None
) -> str:
    """Generate a prompt for optimizing Docker Compose files.
    
    Args:
        compose_content: The Docker Compose YAML content
        host_id: Target deployment host ID
        host_resources: Available resources on the host (optional)
        
    Returns:
        Formatted optimization prompt string
    """
    # Dynamic section based on available context
    resources_info = ""
    if host_resources:
        resources_info = f"""
Available resources on {host_id}:
- CPU cores: {host_resources.get("cpu_count", "Unknown")}
- Memory: {host_resources.get("memory_total", "Unknown")} bytes total
"""
    
    # Return structured prompt with context injection
    return f"""Analyze this Docker Compose file and suggest optimizations...
    
Docker Compose content:
```yaml
{compose_content}
```

Current deployment target: {host_id}
{resources_info}
"""
```

### Prompt Dependencies
- **Type Annotations**: Full typing for parameters and return values
- **Context Parameters**: Dynamic data injection for personalized prompts
- **Optional Parameters**: Flexible prompt enhancement based on available data
- **String Formatting**: f-string templates for clean context injection

## Prompt Categories

### Optimization Prompts
```python
def compose_optimization_prompt(compose_content: str, host_id: str, host_resources: dict | None = None) -> str:
    """Prompts for improving configurations and performance."""
    return f"""Analyze this Docker Compose file and suggest optimizations for:
    
    1. **Security Best Practices**
    2. **Resource Efficiency** 
    3. **Production Readiness**
    4. **Multi-host Deployment Considerations**
    
    [Context and instructions...]
    """
```

### Troubleshooting Prompts
```python
def troubleshooting_prompt(
    error_message: str,
    host_id: str, 
    container_id: str | None = None,
    recent_logs: list[str] | None = None
) -> str:
    """Prompts for diagnosing and resolving issues."""
    return f"""Help diagnose and resolve this Docker deployment issue:
    
    **Error Details:**
    ```
    {error_message}
    ```
    
    **Please provide:**
    1. **Root Cause Analysis**
    2. **Immediate Solutions**
    3. **Long-term Prevention**
    """
```

### Checklist Prompts
```python
def deployment_checklist_prompt(stack_name: str, environment: str, services: list[str]) -> str:
    """Prompts for generating procedural checklists."""
    return f"""Create a comprehensive deployment checklist for:
    
    **Stack Information:**
    - Name: {stack_name}
    - Environment: {environment}
    - Services: {", ".join(services)}
    
    **Generate checklists for:**
    1. **Pre-deployment Verification**
    2. **Deployment Process**
    3. **Post-deployment Testing**
    """
```

### Audit Prompts  
```python
def security_audit_prompt(compose_content: str, host_environment: str = "production") -> str:
    """Prompts for security analysis and compliance."""
    return f"""Perform a comprehensive security audit for a {host_environment} environment:
    
    **Analyze for:**
    1. **Container Security**
    2. **Network Security**  
    3. **Secrets Management**
    4. **Runtime Security**
    
    **Provide:**
    - High/Medium/Low risk ratings
    - Specific remediation steps
    - Prioritized action plan
    """
```

## Structured Prompt Format

### Standard Prompt Structure
```python
def template_prompt(context_params) -> str:
    return f"""[TITLE]: Clear description of what we're doing

**[CONTEXT SECTION]**: What we're working with
- Key information about the situation
- Relevant data and parameters

**[ANALYSIS REQUIREMENTS]**: What to analyze
1. **Category 1**: Specific analysis areas
2. **Category 2**: Additional analysis areas  
3. **Category 3**: Final analysis areas

**[OUTPUT SPECIFICATIONS]**: What to provide
- Specific format requirements
- Expected response structure
- Quality and detail expectations

[ADDITIONAL INSTRUCTIONS]: Formatting, style, priorities
"""
```

### Code Block Integration
```python
# YAML content blocks
f"""Docker Compose content:
```yaml
{compose_content}
```"""

# Error message blocks  
f"""Error Details:
```
{error_message}
```"""

# Log content blocks
f"""Recent logs:
```
{chr(10).join(recent_logs[-20:])}  # Last 20 lines
```"""
```

## Context Injection Patterns

### Dynamic Section Building
```python
def flexible_prompt(base_info: str, optional_context: dict | None = None) -> str:
    """Build prompt with optional sections based on available data."""
    
    # Base prompt content
    prompt = f"Base analysis for: {base_info}\n\n"
    
    # Add optional sections if data is available
    if optional_context and optional_context.get("logs"):
        prompt += f"""
Recent activity:
```
{chr(10).join(optional_context["logs"])}
```
"""
    
    if optional_context and optional_context.get("resources"):
        prompt += f"""
System resources:
- Memory: {optional_context["resources"].get("memory", "Unknown")}
- CPU: {optional_context["resources"].get("cpu", "Unknown")}
"""
    
    return prompt + "Please analyze and provide recommendations."
```

### Safe Context Handling
```python
def safe_context_prompt(data: dict[str, Any]) -> str:
    """Safely handle optional context data with fallbacks."""
    
    # Safe access with fallbacks
    host_id = data.get("host_id", "unknown-host")
    container_id = data.get("container_id")  # May be None
    error_msg = data.get("error", "No error details provided")
    
    # Build context section with safe defaults
    context_info = f"Host: {host_id}"
    if container_id:
        context_info += f"\nContainer: {container_id}"
    
    return f"""Issue analysis for:
{context_info}

Error: {error_msg}

[Analysis instructions...]
"""
```

## Output Format Specification

### Structured Response Requirements
```python
def detailed_analysis_prompt(content: str) -> str:
    return f"""[Analysis context...]
    
**Please provide:**

1. **Immediate Actions** (High Priority):
   - Specific commands to run
   - Quick fixes to implement
   - Emergency procedures

2. **Analysis Results** (Detailed):  
   - Root cause identification
   - Contributing factors
   - Impact assessment

3. **Recommendations** (Long-term):
   - Best practice improvements
   - Preventive measures
   - Monitoring suggestions

**Format Requirements:**
- Use clear sections with headers
- Provide actionable commands
- Include code examples where applicable
- Rate severity/priority levels
"""
```

### Checklist Response Format
```python
def checklist_prompt(task: str) -> str:
    return f"""Generate an actionable checklist for: {task}

Format as checkboxes with clear instructions:

**Pre-execution:**
- [ ] Step 1: Verify prerequisites
- [ ] Step 2: Check dependencies  
- [ ] Step 3: Backup current state

**Execution:**
- [ ] Step 1: Execute primary task
- [ ] Step 2: Validate results
- [ ] Step 3: Verify functionality

**Post-execution:**
- [ ] Step 1: Run health checks
- [ ] Step 2: Update documentation  
- [ ] Step 3: Clean up temporary resources
"""
```

## Error Handling in Prompts

### Graceful Degradation
```python
def robust_prompt(
    required_data: str,
    optional_data: dict[str, Any] | None = None
) -> str:
    """Handle missing or incomplete context gracefully."""
    
    prompt = f"Analysis of: {required_data}\n\n"
    
    # Handle optional sections gracefully
    try:
        if optional_data and "logs" in optional_data:
            logs = optional_data["logs"]
            if isinstance(logs, list) and logs:
                prompt += f"Recent logs:\n```\n{chr(10).join(logs[-10:])}\n```\n\n"
    except (TypeError, KeyError):
        # Continue without logs if data is malformed
        pass
    
    # Always provide fallback instructions
    prompt += """Please analyze based on available information.
If additional context is needed, specify what information would help."""
    
    return prompt
```

### Input Validation
```python
def validated_prompt(compose_content: str) -> str:
    """Validate input before generating prompt."""
    
    if not compose_content or not compose_content.strip():
        return """No Docker Compose content provided. 
Please provide a valid docker-compose.yml file for analysis."""
    
    if len(compose_content) > 50000:  # Reasonable limit
        compose_content = compose_content[:50000] + "\n[Content truncated...]"
    
    return f"""Analyze this Docker Compose configuration:
    
```yaml
{compose_content}
```

[Analysis instructions...]
"""
```

## Prompt Integration Patterns

### Service Integration
```python
# In service layer
from ..prompts.deployment import compose_optimization_prompt

class DeploymentService:
    async def get_optimization_advice(self, stack_data: dict) -> str:
        """Generate optimization advice using AI prompt."""
        
        # Prepare context data
        compose_content = stack_data["compose_file"]
        host_id = stack_data["host_id"] 
        resources = await self._get_host_resources(host_id)
        
        # Generate contextual prompt
        prompt = compose_optimization_prompt(
            compose_content=compose_content,
            host_id=host_id,
            host_resources=resources
        )
        
        # Send to AI service (implementation varies)
        return await self.ai_service.query(prompt)
```

### Tool Integration
```python
# In tool layer  
from ..prompts.deployment import troubleshooting_prompt

class TroubleshootingTool:
    async def diagnose_error(
        self, 
        error_msg: str, 
        host_id: str,
        container_id: str | None = None
    ) -> dict[str, Any]:
        """Generate troubleshooting guidance."""
        
        # Collect additional context
        recent_logs = await self._get_recent_logs(host_id, container_id)
        system_info = await self._get_system_info(host_id)
        
        # Generate comprehensive troubleshooting prompt
        prompt = troubleshooting_prompt(
            error_message=error_msg,
            host_id=host_id,
            container_id=container_id,
            recent_logs=recent_logs,
            system_info=system_info
        )
        
        return {
            "prompt": prompt,
            "context": {
                "host_id": host_id,
                "container_id": container_id,
                "has_logs": bool(recent_logs)
            }
        }
```

## Best Practices

### Prompt Design Principles
```python
# GOOD: Clear, structured, actionable
def good_prompt(context: str) -> str:
    return f"""Analyze {context} and provide:

1. **Specific Issues**: What exactly is wrong
2. **Root Causes**: Why it's happening  
3. **Solutions**: Step-by-step fixes
4. **Prevention**: How to avoid future issues

Format with clear headers and actionable steps."""

# AVOID: Vague, unstructured requests  
def bad_prompt(context: str) -> str:
    return f"Help me with {context}. What should I do?"
```

### Context Optimization
```python
# Include relevant context, exclude noise
def optimized_prompt(essential_data: dict, optional_data: dict | None = None) -> str:
    """Include only relevant context to avoid token waste."""
    
    # Always include essential context
    prompt = f"""Essential information:
- Task: {essential_data['task']}
- Environment: {essential_data['environment']}
"""
    
    # Add optional context only if relevant
    if optional_data and optional_data.get("performance_issues"):
        prompt += f"- Performance concerns: {optional_data['performance_issues']}\n"
    
    return prompt + "[Analysis instructions...]"
```

Prompts provide structured templates for AI interactions, ensuring consistent, contextual, and actionable AI assistance for Docker deployment and troubleshooting tasks.

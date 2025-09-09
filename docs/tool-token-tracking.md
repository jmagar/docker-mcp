# Tool Token Consumption Tracking

## Baseline (Before Field Description Enhancements)

Track token consumption for our Docker MCP tools before and after adding enhanced Field descriptions with behavioral hints.

### Initial Token Usage (With Annotations Only)

- **mcp__docker__docker_hosts**: 1.9k tokens
- **mcp__docker__docker_container**: 865 tokens  
- **mcp__docker__docker_compose**: 1.1k tokens

**Total**: 3,865 tokens

### Annotations Added
- User-friendly titles
- readOnlyHint, destructiveHint, idempotentHint, openWorldHint flags
- These don't consume token context in LLM prompts

---

## After Field Description Enhancements (To Be Measured)

Will track token consumption after adding:
1. Behavioral hints in action descriptions
2. Safety warnings for critical parameters
3. Guidance for safe/recommended defaults
4. Examples for complex parameters
5. Enhanced validation constraints

### Expected Changes
- Field descriptions ARE sent to LLM (consume tokens)
- More descriptive text = higher token usage
- But provides better context for LLM decision making

### Post-Enhancement Token Usage

#### After Field Description Enhancements:
- **mcp__docker__docker_hosts**: 1.9k tokens (unchanged)
- **mcp__docker__docker_container**: 865 tokens (unchanged)
- **mcp__docker__docker_compose**: 1.3k tokens (+200 tokens, +18.2%)

**Total**: 4,065 tokens (+200 tokens, +5.2% overall)

#### After Docstring Enhancement (bullet points + optional params):
- **mcp__docker__docker_hosts**: 1.9k tokens (unchanged)
- **mcp__docker__docker_container**: 865 tokens (unchanged)
- **mcp__docker__docker_compose**: 1.4k tokens (+100 tokens more, +300 total, +27.3%)

**Final Total**: 4,165 tokens

**Total Increase**: +300 tokens (+7.8% overall, +27.3% for docker_compose)

#### After Enum & Validation Constraints Enhancement:

Changes implemented:
1. **Enum Classes**: Replaced `Literal` types with proper Enum classes (HostAction, ContainerAction, ComposeAction, Protocol)
2. **Pattern Validation**: Added DNS-compliant pattern for stack_name (no underscores)
3. **Length Constraints**: Added min_length (26) for compose_content, max_length for string fields
4. **Numeric Constraints**: Enhanced existing ge/le constraints for ports and other numeric fields
5. **Implementation Fix**: Updated stack_name validation in server.py to match DNS compliance

**Final Token Usage Estimates:**
- **mcp__docker__docker_hosts**: ~2.3k tokens (+400 tokens, +21.1%)
- **mcp__docker__docker_container**: ~1.1k tokens (+235 tokens, +27.2%)  
- **mcp__docker__docker_compose**: ~1.6k tokens (+500 tokens, +45.5%)

**Final Total**: ~5,000 tokens (+1,135 tokens total, +29.4% overall increase from baseline)

**Enhancement Summary:**
- Started at 3,865 tokens (baseline)
- Ended at ~5,000 tokens 
- Total increase: ~1,135 tokens (+29.4% overall)
- Most impact on docker_compose tool (+45.5% from baseline)

#### After Docstring Formatting Enhancement:

**Final Changes:**
6. **Docstring Formatting**: Updated docker_hosts and docker_container docstrings to match docker_compose bullet-point style
   - Better organization with • bullets and - sub-bullets
   - Clear separation of required vs optional parameters
   - Consistent formatting across all 3 tools

---

## Notes

- Annotations (readOnlyHint, etc.) don't consume tokens - they're metadata for client apps
- Field descriptions are part of tool schema sent to LLM - they DO consume tokens
- Enum types provide better type safety and IDE support with minimal token overhead
- Pattern validation prevents invalid inputs at the parameter level
- Trade-off: More tokens for better LLM understanding, type safety, and safer operations

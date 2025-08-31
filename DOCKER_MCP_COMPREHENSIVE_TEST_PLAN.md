# Docker-MCP Comprehensive Test Plan

## Executive Summary

This document outlines the comprehensive testing strategy for all Docker-MCP server actions. The testing will verify the functionality of all 26 available actions across the 3 main tools: `docker_hosts`, `docker_container`, and `docker_compose`.

## Test Environment Setup

### Target Host
- **Host ID:** `squirts`
- **Hostname:** squirts
- **User:** jmagar
- **Port:** 22
- **Capabilities:** ZFS-enabled, SSH-configured

### Test Resources
- **Test Stack:** `test-mcp-simple`
- **Test Container:** `test-web-1` (from test-mcp-simple stack)
- **Additional Test Stack:** `test-mcp-temporary` (to be created during testing)

### Testing Methodology
1. **Safety First:** Read-only operations tested first
2. **State Validation:** Verify expected states before and after operations
3. **Error Handling:** Test both success and failure scenarios
4. **Documentation:** Comprehensive logging of all test results
5. **Cleanup:** Proper cleanup of temporary resources

## Tool 1: docker_hosts (9 Total Actions)

### Actions to Test (6 out of 9)

#### 1. list - List Docker Hosts
- **Purpose:** Verify host discovery and configuration display
- **Parameters:** None
- **Expected Result:** List of configured hosts including squirts
- **Safety Level:** ✅ Read-only

#### 2. ports - Port Usage Analysis
- **Purpose:** Check port usage and conflicts on target host
- **Parameters:** `host_id=squirts`
- **Expected Result:** Port mapping analysis with conflict detection
- **Safety Level:** ✅ Read-only

#### 3. test_connection - Connection Testing
- **Purpose:** Verify SSH and Docker connectivity
- **Parameters:** `host_id=squirts`
- **Expected Result:** Successful connection test with capability discovery
- **Safety Level:** ✅ Read-only

#### 4. discover - Path and Capability Discovery
- **Purpose:** Auto-discover compose paths and ZFS capabilities
- **Parameters:** `host_id=squirts`
- **Expected Result:** Discovery of compose paths and ZFS detection
- **Safety Level:** ✅ Read-only

#### 5. cleanup (check mode) - System Cleanup Analysis
- **Purpose:** Analyze disk usage and cleanup opportunities
- **Parameters:** `host_id=squirts, cleanup_type=check`
- **Expected Result:** Cleanup analysis without making changes
- **Safety Level:** ✅ Read-only analysis

#### 6. ports (specific port) - Port Availability Check
- **Purpose:** Check if specific port is available
- **Parameters:** `host_id=squirts, port=8999`
- **Expected Result:** Port availability status
- **Safety Level:** ✅ Read-only

### Actions Skipped (3 out of 9)
- **add** - Would modify production configuration
- **edit** - Would modify production host settings
- **remove** - Would affect production environment
- **import_ssh** - Already has hosts configured, could cause conflicts

## Tool 2: docker_container (8 Total Actions)

### Target Container: `test-web-1`

#### 1. list - Container Listing
- **Purpose:** List all containers on host with pagination
- **Parameters:** `host_id=squirts, all_containers=true, limit=50`
- **Expected Result:** List including test-web-1 container
- **Safety Level:** ✅ Read-only

#### 2. info - Container Information
- **Purpose:** Get detailed container information
- **Parameters:** `host_id=squirts, container_id=test-web-1`
- **Expected Result:** Comprehensive container details (ports, volumes, networks)
- **Safety Level:** ✅ Read-only

#### 3. logs - Container Logs
- **Purpose:** Retrieve container logs
- **Parameters:** `host_id=squirts, container_id=test-web-1, lines=20`
- **Expected Result:** Recent log entries from test container
- **Safety Level:** ✅ Read-only

#### 4. restart - Container Restart
- **Purpose:** Test container lifecycle management
- **Parameters:** `host_id=squirts, container_id=test-web-1`
- **Expected Result:** Container successfully restarted
- **Safety Level:** ⚠️ State change (safe for test container)

#### 5. stop - Container Stop
- **Purpose:** Test container stopping
- **Parameters:** `host_id=squirts, container_id=test-web-1`
- **Expected Result:** Container stopped successfully
- **Safety Level:** ⚠️ State change (safe for test container)

#### 6. start - Container Start
- **Purpose:** Test container starting
- **Parameters:** `host_id=squirts, container_id=test-web-1`
- **Expected Result:** Container started successfully
- **Safety Level:** ⚠️ State change (safe for test container)

### Actions Skipped (2 out of 8)
- **build** - No build context available for running container
- **remove** - Preserve test container for future testing

## Tool 3: docker_compose (9 Total Actions)

### Target Stack: `test-mcp-simple`

#### 1. list - Stack Listing
- **Purpose:** List all Docker Compose stacks
- **Parameters:** `host_id=squirts`
- **Expected Result:** List of stacks including test-mcp-simple
- **Safety Level:** ✅ Read-only

#### 2. view - Compose File Viewing  
- **Purpose:** Display compose file content
- **Parameters:** `host_id=squirts, stack_name=test-mcp-simple`
- **Expected Result:** Display of docker-compose.yml content
- **Safety Level:** ✅ Read-only

#### 3. logs - Stack Logs
- **Purpose:** Get logs from all stack services
- **Parameters:** `host_id=squirts, stack_name=test-mcp-simple, lines=10`
- **Expected Result:** Aggregated logs from stack services
- **Safety Level:** ✅ Read-only

#### 4. restart - Stack Restart
- **Purpose:** Test stack lifecycle management
- **Parameters:** `host_id=squirts, stack_name=test-mcp-simple`
- **Expected Result:** All stack services restarted
- **Safety Level:** ⚠️ State change (safe for test stack)

#### 5. down - Stack Stop
- **Purpose:** Test stack stopping
- **Parameters:** `host_id=squirts, stack_name=test-mcp-simple`
- **Expected Result:** All stack services stopped
- **Safety Level:** ⚠️ State change (safe for test stack)

#### 6. up - Stack Start
- **Purpose:** Test stack starting
- **Parameters:** `host_id=squirts, stack_name=test-mcp-simple`
- **Expected Result:** All stack services started
- **Safety Level:** ⚠️ State change (safe for test stack)

#### 7. deploy - Stack Deployment
- **Purpose:** Test new stack deployment
- **Parameters:** 
  ```yaml
  host_id: squirts
  stack_name: test-mcp-temporary
  compose_content: |
    services:
      test-nginx:
        image: nginx:alpine
        container_name: test-nginx-temp
        ports:
          - "8897:80"
        environment:
          - NGINX_HOST=test.local
        restart: unless-stopped
        labels:
          - "test.temporary=true"
  ```
- **Expected Result:** New temporary stack deployed successfully
- **Safety Level:** ⚠️ Creates new resources (temporary)

### Actions Skipped (2 out of 9)
- **build** - No custom images to build in test stacks
- **migrate** - Requires second host configuration

## Test Execution Sequence

### Phase 1: Read-Only Operations (9 tests)
1. `docker_hosts list`
2. `docker_hosts ports` (general)
3. `docker_hosts ports` (specific port)
4. `docker_container list`
5. `docker_container info`
6. `docker_container logs`
7. `docker_compose list`
8. `docker_compose view`
9. `docker_compose logs`

### Phase 2: Safe State Changes (5 tests)
1. `docker_hosts test_connection`
2. `docker_hosts discover`
3. `docker_hosts cleanup` (check mode)
4. `docker_container restart`
5. `docker_compose restart`

### Phase 3: Lifecycle Testing (4 tests)
1. `docker_container stop`
2. `docker_container start`
3. `docker_compose down`
4. `docker_compose up`

### Phase 4: Deployment Testing (1 test)
1. `docker_compose deploy` (temporary stack)

### Phase 5: Cleanup
1. Remove temporary stack: `docker_compose down` on test-mcp-temporary
2. Verify test-mcp-simple is running
3. Verify test-web-1 is accessible

## Expected Outcomes

### Success Criteria
- All 21 planned tests execute without errors
- State changes are correctly applied and verified
- Temporary resources are properly cleaned up
- Original test environment is restored

### Documentation Output
A comprehensive results document will be generated containing:
- Test execution summary
- Detailed results for each action
- Performance metrics (execution times)
- Error analysis (if any)
- Recommendations for improvements

### Risk Mitigation
- Only test containers/stacks are modified
- Production containers/stacks remain untouched
- All state changes are reversible
- Backup verification before destructive operations

## Post-Test Validation

### Environment Verification
1. **test-mcp-simple stack:** Running and accessible
2. **test-web-1 container:** Running and responsive
3. **Port 8897:** Available after temporary stack cleanup
4. **squirts host:** All services operational

### Documentation Completion
- Results documented in `DOCKER_MCP_TEST_RESULTS.md`
- Issues logged with severity levels
- Performance baseline established
- Recommendations documented

---

**Test Plan Version:** 1.0  
**Created:** 2025-08-30  
**Target Environment:** squirts host  
**Total Actions:** 21 of 26 (81% coverage)  
**Safety Level:** Production-safe with isolated test resources
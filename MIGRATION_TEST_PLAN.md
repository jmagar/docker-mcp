# 🧪 Live Migration Test Plan

## Overview

This document outlines comprehensive testing for the Docker MCP migration feature with real devices, testing both ZFS send/receive and rsync fallback methods.

## Test Environment

### Host Configuration
- **Source Host:** `tootie` (ZFS capable)
  - ZFS dataset: `cache/appdata`
  - Compose path: `/mnt/cache/compose`
  - Appdata path: `/mnt/cache/appdata`
  
- **ZFS Target:** `shart` (ZFS capable) 
  - ZFS dataset: `tank/docker-appdata`
  - Compose path: Default (auto-discovered)
  - Appdata path: `/opt/docker-appdata`
  
- **Rsync Target:** `steamy-wsl` (Non-ZFS)
  - Compose path: `/home/jmagar/code`
  - Appdata path: `/home/jmagar/docker-appdata`

## Test Stack Definition

### `migration-test-app` Stack

```yaml
version: '3.8'
services:
  web:
    image: nginx:alpine
    ports:
      - "8765:80"  # Unique port to avoid conflicts
    volumes:
      - web_data:/usr/share/nginx/html
      - ./config:/etc/nginx/conf.d:ro
    environment:
      - TEST_ENV=production
      - MIGRATION_TEST=true
    labels:
      - "test=migration-live"
      
  redis:
    image: redis:alpine
    ports:
      - "6380:6379"  # Non-standard port to avoid conflicts
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
    
volumes:
  web_data:
  redis_data:
```

### Test Data Setup

1. **Web Data Volume:**
   ```html
   <!-- index.html -->
   <!DOCTYPE html>
   <html>
   <head><title>Migration Test</title></head>
   <body>
     <h1>Migration Test Data</h1>
     <p>Timestamp: 2025-08-24T10:30:00Z</p>
     <p>Host: ORIGINAL_HOST</p>
   </body>
   </html>
   ```

2. **Redis Data:**
   ```bash
   redis-cli -p 6380 SET test_key "migration_test_value_12345"
   redis-cli -p 6380 SET migration_timestamp "2025-08-24T10:30:00Z"
   redis-cli -p 6380 LPUSH test_list "item1" "item2" "item3"
   ```

3. **Nginx Config (Bind Mount):**
   ```nginx
   server {
       listen 80;
       server_name localhost;
       
       location / {
           root /usr/share/nginx/html;
           index index.html;
       }
       
       location /health {
           return 200 'Migration Test OK';
           add_header Content-Type text/plain;
       }
   }
   ```

## Test 1: ZFS Migration (tootie → shart)

### Pre-flight Checks

1. **Verify ZFS Capability:**
   ```bash
   ssh tootie "zfs list cache/appdata"
   ssh shart "zfs list tank/docker-appdata"
   ```

2. **Check Port Availability:**
   ```bash
   docker_hosts ports shart | grep -E "(8765|6380)"
   ```

3. **Verify Disk Space:**
   ```bash
   ssh shart "df -h /opt/docker-appdata"
   ```

### Execution Steps

1. **Deploy Test Stack on Source:**
   ```json
   {
     "action": "deploy",
     "host_id": "tootie",
     "stack_name": "migration-test-app",
     "compose_content": "[STACK_YAML_ABOVE]",
     "environment": {
       "TEST_ENV": "production",
       "MIGRATION_TEST": "true"
     },
     "pull_images": true,
     "recreate": false
   }
   ```

2. **Populate Test Data:**
   - Create HTML file in web volume
   - Add test keys to Redis
   - Create nginx config file

3. **Dry Run Migration:**
   ```json
   {
     "action": "migrate",
     "host_id": "tootie", 
     "target_host_id": "shart",
     "stack_name": "migration-test-app",
     "skip_stop_source": false,
     "start_target": true,
     "remove_source": false,
     "dry_run": true
   }
   ```

4. **Execute Actual Migration:**
   ```json
   {
     "action": "migrate",
     "host_id": "tootie", 
     "target_host_id": "shart",
     "stack_name": "migration-test-app",
     "skip_stop_source": false,
     "start_target": true,
     "remove_source": false,
     "dry_run": false
   }
   ```

### Expected Results

- **Transfer Method:** ZFS send/receive used (should be mentioned in logs)
- **ZFS Snapshot:** Created on tootie: `cache/appdata@migration_*`
- **Compose File Location:** `/opt/compose/migration-test-app/docker-compose.yml` (or auto-discovered path)
- **Appdata Location:** `/opt/docker-appdata/migration-test-app/`
- **Stack Status:** Running on shart
- **Source Status:** Still exists on tootie (remove_source=false)
- **Performance:** Significantly faster than rsync for large data

## Test 2: Rsync Migration (tootie → steamy-wsl)

### Pre-flight Checks

1. **Cleanup Previous Test:**
   ```bash
   docker_compose down migration-test-app shart --volumes
   ```

2. **Check Port Availability:**
   ```bash
   docker_hosts ports steamy-wsl | grep -E "(8765|6380)"
   ```

3. **Verify Disk Space:**
   ```bash
   ssh steamy-wsl "df -h /home/jmagar/docker-appdata"
   ```

### Execution Steps

1. **Ensure Stack Running on Source:**
   ```bash
   docker_compose list tootie | grep migration-test-app
   ```

2. **Execute Migration:**
   ```json
   {
     "action": "migrate",
     "host_id": "tootie",
     "target_host_id": "steamy-wsl", 
     "stack_name": "migration-test-app",
     "skip_stop_source": false,
     "start_target": true,
     "remove_source": true,
     "dry_run": false
   }
   ```

### Expected Results

- **Transfer Method:** Rsync used (fallback due to no ZFS on target)
- **Archive Creation:** Temporary archive created on tootie
- **Compose File Location:** `/home/jmagar/code/migration-test-app/docker-compose.yml`
- **Appdata Location:** `/home/jmagar/docker-appdata/migration-test-app/`
- **Stack Status:** Running on steamy-wsl
- **Source Status:** Removed from tootie (remove_source=true)
- **Archive Cleanup:** Temporary files cleaned up

## Verification Procedures

### File Location Verification

1. **Compose File Placement:**
   ```bash
   # For shart (ZFS test)
   ssh shart "ls -la /opt/compose/migration-test-app/docker-compose.yml"
   
   # For steamy-wsl (rsync test)  
   ssh steamy-wsl "ls -la /home/jmagar/code/migration-test-app/docker-compose.yml"
   ```

2. **Appdata Directory Structure:**
   ```bash
   # For shart
   ssh shart "find /opt/docker-appdata/migration-test-app -type f | head -20"
   
   # For steamy-wsl
   ssh steamy-wsl "find /home/jmagar/docker-appdata/migration-test-app -type f | head -20"
   ```

### Service Verification

1. **Stack Status Check:**
   ```bash
   docker_compose list [target_host] | grep migration-test-app
   docker_container list [target_host] | grep migration-test
   ```

2. **Container Health:**
   ```bash
   docker_container info migration-test-app-web-1 [target_host]
   docker_container info migration-test-app-redis-1 [target_host]
   ```

### Data Integrity Verification

1. **Web Service Test:**
   ```bash
   curl http://[target_host]:8765
   curl http://[target_host]:8765/health
   ```

2. **Redis Data Test:**
   ```bash
   redis-cli -h [target_host] -p 6380 GET test_key
   redis-cli -h [target_host] -p 6380 GET migration_timestamp
   redis-cli -h [target_host] -p 6380 LRANGE test_list 0 -1
   ```

3. **Volume Data Verification:**
   ```bash
   ssh [target_host] "docker exec migration-test-app-web-1 cat /usr/share/nginx/html/index.html"
   ssh [target_host] "docker exec migration-test-app-redis-1 redis-cli DBSIZE"
   ```

4. **Bind Mount Verification:**
   ```bash
   ssh [target_host] "docker exec migration-test-app-web-1 cat /etc/nginx/conf.d/default.conf"
   ```

## Test 3: Port Conflict Scenario

### Setup Conflicting Service

1. **Deploy Conflicting Stack:**
   ```yaml
   version: '3.8'
   services:
     conflict:
       image: nginx:alpine
       ports:
         - "8765:80"  # Same port as migration test
   ```

2. **Deploy to Target:**
   ```json
   {
     "action": "deploy",
     "host_id": "shart",
     "stack_name": "port-conflict-test",
     "compose_content": "[CONFLICT_YAML]",
     "pull_images": false
   }
   ```

### Test Migration with Conflict

1. **Attempt Migration:**
   ```json
   {
     "action": "migrate",
     "host_id": "tootie",
     "target_host_id": "shart",
     "stack_name": "migration-test-app",
     "dry_run": false
   }
   ```

2. **Expected Behavior:**
   - Migration process completes data transfer
   - Deployment fails due to port conflict
   - Error message indicates port conflict
   - Source stack remains running (safety)

### Resolution and Retry

1. **Clean Up Conflict:**
   ```bash
   docker_compose down port-conflict-test shart --volumes
   ```

2. **Retry Migration:**
   - Same migration command should now succeed
   - Stack deploys successfully on target

## Performance Benchmarking

### ZFS vs Rsync Comparison

1. **Create Large Test Data:**
   ```bash
   # Add ~1GB of test data to volumes
   docker exec migration-test-app-web-1 dd if=/dev/urandom of=/usr/share/nginx/html/large_file.bin bs=1M count=1024
   ```

2. **Time ZFS Migration:**
   - Record migration duration from logs
   - Note transfer speed and efficiency

3. **Time Rsync Migration:**
   - Record migration duration for same data set
   - Compare with ZFS performance

4. **Expected Results:**
   - ZFS: ~2-5 minutes for 1GB
   - Rsync: ~10-25 minutes for 1GB  
   - ZFS should be 3-10x faster

## Error Scenarios Testing

### 1. Network Failure During Migration

- Simulate network interruption
- Verify error handling and cleanup
- Ensure source remains intact

### 2. Insufficient Disk Space

- Attempt migration to target with insufficient space
- Verify error detection and graceful failure
- Ensure no partial data corruption

### 3. ZFS Snapshot Failure

- Test with ZFS dataset that cannot create snapshots
- Verify fallback to rsync
- Ensure migration still completes

### 4. SSH Connection Issues

- Test with invalid SSH credentials
- Test with unreachable target host
- Verify appropriate error messages

## Cleanup Procedures

### Complete Test Cleanup

1. **Remove All Test Stacks:**
   ```bash
   docker_compose down migration-test-app steamy-wsl --volumes
   docker_compose down migration-test-app shart --volumes  
   docker_compose down migration-test-app tootie --volumes
   docker_compose down port-conflict-test shart --volumes
   ```

2. **Clean Up Directories:**
   ```bash
   # steamy-wsl cleanup
   ssh steamy-wsl "rm -rf /home/jmagar/code/migration-test-app"
   ssh steamy-wsl "rm -rf /home/jmagar/docker-appdata/migration-test-app"
   
   # shart cleanup
   ssh shart "rm -rf /opt/compose/migration-test-app"
   ssh shart "rm -rf /opt/docker-appdata/migration-test-app"
   
   # tootie cleanup
   ssh tootie "rm -rf /mnt/cache/compose/migration-test-app"
   ssh tootie "rm -rf /mnt/cache/appdata/migration-test-app"
   ```

3. **Clean Up ZFS Snapshots:**
   ```bash
   ssh tootie "zfs list -t snapshot | grep migration | cut -f1 | xargs -r zfs destroy"
   ```

## Success Criteria Checklist

### ✅ ZFS Migration Success
- [ ] Uses ZFS send/receive (not rsync)
- [ ] Migration completes in <50% time of rsync
- [ ] Preserves all metadata and permissions  
- [ ] Stack runs successfully on shart
- [ ] All data integrity checks pass
- [ ] Compose file in correct location
- [ ] Appdata in correct location

### ✅ Rsync Migration Success  
- [ ] Falls back to rsync (no ZFS on target)
- [ ] Archives exclude unnecessary files (logs, cache, etc.)
- [ ] Stack runs successfully on steamy-wsl
- [ ] Source cleaned up when remove_source=true
- [ ] All data integrity checks pass
- [ ] Compose file in correct location
- [ ] Appdata in correct location

### ✅ Data Integrity
- [ ] HTML content preserved and accessible
- [ ] Redis data preserved (keys, lists, etc.)
- [ ] Bind mount configs preserved
- [ ] Environment variables maintained
- [ ] Services accessible on new host
- [ ] Volume permissions preserved

### ✅ Path Management
- [ ] Compose files in {compose_path}/servicename
- [ ] Appdata in {appdata_path}/servicename  
- [ ] Paths properly translated in compose file
- [ ] No hardcoded paths remain

### ✅ Error Handling
- [ ] Port conflicts detected and reported
- [ ] Network failures handled gracefully
- [ ] Insufficient space detected
- [ ] SSH issues properly reported
- [ ] Source remains intact on failures

### ✅ Performance
- [ ] ZFS 3-10x faster than rsync for large datasets
- [ ] Memory usage reasonable during migration
- [ ] Network bandwidth utilized efficiently
- [ ] Cleanup processes complete successfully

## Notes and Limitations

### Current Port Conflict Handling
- ⚠️ **No Automatic Resolution:** System detects but doesn't auto-resolve port conflicts
- Migration will fail at deployment stage if ports are in use
- User must manually resolve conflicts and retry

### Future Enhancements
- **Port Remapping:** Could auto-suggest alternative ports
- **Pre-flight Port Checks:** Check target ports before starting migration
- **Conflict Resolution UI:** Interactive conflict resolution

### Network Considerations  
- Services get new IPs on target host
- External DNS/URLs may need updating  
- Load balancers need reconfiguration
- SSL certificates may need updating

### Security Considerations
- SSH keys must be configured correctly
- Docker daemon access required on all hosts
- ZFS permissions must allow snapshot creation
- Network connectivity required between hosts

## Troubleshooting Guide

### Migration Fails with "Containers still running"
- Check if containers stopped properly
- Use `skip_stop_source: true` only if certain containers are stopped
- Verify no other processes are using the containers

### ZFS Migration Falls Back to Rsync
- Verify `zfs_capable: true` in host config
- Check ZFS dataset exists and is accessible
- Ensure SSH user has ZFS permissions

### Transfer Speed Issues
- Check network bandwidth between hosts
- Verify disk I/O performance
- Consider ZFS compression settings
- Monitor system resources during transfer

### Data Missing After Migration
- Check volume mappings in compose file
- Verify appdata directory permissions
- Ensure bind mounts use correct paths
- Check for exclusion pattern conflicts
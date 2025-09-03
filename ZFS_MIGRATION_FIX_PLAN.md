# ZFS Migration Fix: Complete Implementation Plan

## Problem Analysis

### Current Issues
1. **ZFS transfer logic flaw**: Migration manager passes entire parent dataset (`rpool/appdata`) instead of individual service datasets
2. **Mixed infrastructure**: Most services ARE ZFS datasets (authelia, authelia-mariadb, etc.) but some are regular directories (~~test-mcp-simple~~ - FIXED)
3. **Code architecture mismatch**: ZFS transfer doesn't iterate through service paths like rsync does
4. **Multi-service stack complexity**: Authelia stack needs 3 separate datasets (authelia, authelia-mariadb, authelia-redis)

### Current ZFS Structure on Squirts
```
rpool/appdata                    # Parent dataset
├── rpool/appdata/authelia       # Service dataset ✅
├── rpool/appdata/authelia-mariadb  # Service dataset ✅
├── rpool/appdata/authelia-redis    # Service dataset ✅
├── rpool/appdata/adguard        # Service dataset ✅
├── ... (30+ service datasets)
└── /mnt/appdata/test-mcp-simple # Regular directory ❌
```

## Solution: Standardize All Services as ZFS Datasets

### Phase 1: Status Update

#### 1.1 Host Configuration
**Status**: ✅ ALREADY CORRECT
- `config/hosts.yml` already has `zfs_dataset: rpool/appdata` for squirts

#### 1.2 Test Dataset Creation  
**Status**: ✅ COMPLETED
- `rpool/appdata/test-mcp-simple` dataset has been created

### Phase 2: ZFS Transfer Logic Improvements

#### 2.1 Add Dataset Auto-Creation
**File**: `docker_mcp/core/transfer/zfs.py`

Add new method:
```python
async def ensure_service_dataset_exists(
    self, 
    host: DockerHost, 
    service_path: str
) -> str:
    """Ensure a service path exists as a ZFS dataset.
    
    Args:
        host: Host configuration
        service_path: Full path to service directory (e.g., /mnt/appdata/authelia)
        
    Returns:
        Dataset name (e.g., rpool/appdata/authelia)
    """
    service_name = service_path.split('/')[-1]
    expected_dataset = f"{host.zfs_dataset}/{service_name}"
    
    # Check if dataset already exists
    ssh_cmd = self.build_ssh_cmd(host)
    check_cmd = ssh_cmd + [f"zfs list {expected_dataset} >/dev/null 2>&1 && echo 'EXISTS' || echo 'MISSING'"]
    
    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: subprocess.run(check_cmd, capture_output=True, text=True, check=False),
    )
    
    if "EXISTS" in result.stdout:
        self.logger.info("Dataset already exists", dataset=expected_dataset)
        return expected_dataset
    
    # Dataset doesn't exist - create it
    self.logger.info("Creating dataset for service", service=service_name, dataset=expected_dataset)
    
    # Check if path exists as directory
    path_check_cmd = ssh_cmd + [f"test -d {shlex.quote(service_path)} && echo 'DIR_EXISTS' || echo 'NO_DIR'"]
    path_result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: subprocess.run(path_check_cmd, capture_output=True, text=True, check=False),
    )
    
    if "DIR_EXISTS" in path_result.stdout:
        # Directory exists - convert to dataset
        await self._convert_directory_to_dataset(host, service_path, expected_dataset)
    else:
        # No existing data - create empty dataset
        create_cmd = ssh_cmd + [f"zfs create {expected_dataset}"]
        create_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(create_cmd, capture_output=True, text=True, check=False),
        )
        
        if create_result.returncode != 0:
            raise ZFSError(f"Failed to create dataset {expected_dataset}: {create_result.stderr}")
    
    return expected_dataset

async def _convert_directory_to_dataset(
    self, 
    host: DockerHost, 
    dir_path: str, 
    dataset_name: str
) -> None:
    """Convert existing directory to ZFS dataset while preserving data."""
    ssh_cmd = self.build_ssh_cmd(host)
    temp_path = f"{dir_path}.zfs_migration_temp"
    
    self.logger.info("Converting directory to dataset", path=dir_path, dataset=dataset_name)
    
    # 1. Move existing data to temp location
    move_cmd = ssh_cmd + [f"mv {shlex.quote(dir_path)} {shlex.quote(temp_path)}"]
    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: subprocess.run(move_cmd, capture_output=True, text=True, check=False),
    )
    
    if result.returncode != 0:
        raise ZFSError(f"Failed to backup directory {dir_path}: {result.stderr}")
    
    try:
        # 2. Create ZFS dataset
        create_cmd = ssh_cmd + [f"zfs create {dataset_name}"]
        create_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(create_cmd, capture_output=True, text=True, check=False),
        )
        
        if create_result.returncode != 0:
            raise ZFSError(f"Failed to create dataset {dataset_name}: {create_result.stderr}")
        
        # 3. Move data back
        restore_cmd = ssh_cmd + [f"cp -r {shlex.quote(temp_path)}/* {shlex.quote(dir_path)}/"]
        restore_result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(restore_cmd, capture_output=True, text=True, check=False),
        )
        
        if restore_result.returncode != 0:
            raise ZFSError(f"Failed to restore data to {dir_path}: {restore_result.stderr}")
        
        # 4. Cleanup temp directory
        cleanup_cmd = ssh_cmd + [f"rm -rf {shlex.quote(temp_path)}"]
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(cleanup_cmd, capture_output=True, text=True, check=False),
        )
        
        self.logger.info("Successfully converted directory to dataset", dataset=dataset_name)
        
    except Exception as e:
        # Rollback on failure
        self.logger.error("Dataset creation failed, rolling back", error=str(e))
        rollback_cmd = ssh_cmd + [f"mv {shlex.quote(temp_path)} {shlex.quote(dir_path)}"]
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(rollback_cmd, capture_output=True, text=True, check=False),
        )
        raise
```

#### 2.2 Update Transfer Method for Multiple Services
**File**: `docker_mcp/core/transfer/zfs.py`

Modify the `transfer` method to handle multiple service datasets:
```python
async def transfer_multiple_services(
    self,
    source_host: DockerHost,
    target_host: DockerHost,
    service_paths: list[str],
    **kwargs,
) -> dict[str, Any]:
    """Transfer multiple service datasets.
    
    Args:
        source_host: Source host configuration
        target_host: Target host configuration  
        service_paths: List of service paths to transfer
        
    Returns:
        Transfer result with per-service statistics
    """
    transfer_results = []
    overall_success = True
    
    for service_path in service_paths:
        service_name = service_path.split('/')[-1]
        
        try:
            # Ensure datasets exist on both sides
            source_dataset = await self.ensure_service_dataset_exists(source_host, service_path)
            
            # Calculate target path and ensure target dataset exists
            target_service_path = service_path.replace(
                source_host.appdata_path, 
                target_host.appdata_path
            )
            target_dataset = await self.ensure_service_dataset_exists(target_host, target_service_path)
            
            # Transfer the dataset
            result = await self.transfer(
                source_host=source_host,
                target_host=target_host,
                source_path=service_path,
                target_path=target_service_path,
                source_dataset=source_dataset,
                target_dataset=target_dataset,
            )
            
            result["service_name"] = service_name
            transfer_results.append(result)
            
            if not result.get("success", False):
                overall_success = False
                
        except Exception as e:
            self.logger.error("Service transfer failed", service=service_name, error=str(e))
            overall_success = False
            transfer_results.append({
                "success": False,
                "service_name": service_name,
                "error": str(e)
            })
    
    return {
        "success": overall_success,
        "transfer_type": "zfs_multi_service",
        "services": transfer_results,
        "services_transferred": len([r for r in transfer_results if r.get("success", False)]),
        "total_services": len(service_paths),
        "message": f"Transferred {len([r for r in transfer_results if r.get('success', False)])}/{len(service_paths)} services"
    }
```

### Phase 3: Migration Manager Updates

#### 3.1 Update Migration Manager ZFS Logic
**File**: `docker_mcp/core/migration/manager.py`

Replace the current ZFS transfer block (lines 211-228) with:
```python
if transfer_type == "zfs":
    # ZFS transfer - handle multiple service datasets
    result = await transfer_instance.transfer_multiple_services(
        source_host=source_host,
        target_host=target_host,
        service_paths=source_paths,  # Pass all service paths
    )
    
    if isinstance(result, dict):
        result.setdefault("transfer_type", "zfs")
        result.setdefault("success", False)
        return result
    else:
        return {
            "success": False, 
            "error": f"Invalid ZFS transfer result: {result}", 
            "transfer_type": "zfs"
        }
```

### Phase 4: Testing Strategy

#### 4.1 Unit Tests
**File**: `tests/test_zfs_transfer.py`
```python
@pytest.mark.asyncio
async def test_ensure_service_dataset_exists_directory():
    """Test converting directory to dataset."""
    # Test setup with mock directory
    
@pytest.mark.asyncio  
async def test_ensure_service_dataset_exists_already_dataset():
    """Test when service is already a dataset."""
    
@pytest.mark.asyncio
async def test_transfer_multiple_services():
    """Test transferring multiple service datasets."""
```

#### 4.2 Integration Tests
1. **Single service migration** (test-mcp-simple)
2. **Multi-service migration** (authelia stack: authelia + authelia-mariadb + authelia-redis)  
3. **Mixed service migration** (some existing datasets, some directories)

#### 4.3 Manual Testing Steps
```bash
# 1. Fix config and create test dataset
# 2. Test single service migration
uv run pytest tests/test_integration_comprehensive.py::test_migration_zfs_roundtrip -v

# 3. Test multi-service stack (create test Authelia stack)
# 4. Verify data integrity after migration
# 5. Test rollback scenarios
```

### Phase 5: Rollout Plan

#### 5.1 Safe Deployment
1. **Backup current configs**: `cp config/hosts.yml config/hosts.yml.backup`
2. **Test on dev stacks first**: Use test-mcp-simple for validation
3. **Monitor logs**: Watch for ZFS errors during migration
4. **Gradual rollout**: Test with non-critical services first

#### 5.2 Success Metrics
- ✅ ZFS migration test passes
- ✅ Multi-service stacks migrate correctly  
- ✅ Data integrity maintained (checksums match)
- ✅ No manual dataset creation required
- ✅ Fallback to rsync still works for non-ZFS hosts

### Phase 6: Documentation Updates

#### 6.1 Update CLAUDE.md
Document the new ZFS patterns and dataset management approach.

#### 6.2 Add ZFS Best Practices
Document when datasets are auto-created and how to manage them.

## Implementation Priority

1. ~~**HIGH**: Fix hosts.yml configuration~~ ✅ ALREADY CORRECT
2. ~~**HIGH**: Create test-mcp-simple dataset manually~~ ✅ COMPLETED
3. **HIGH**: Test basic ZFS migration (30 minutes) - **NEXT STEP**
4. **HIGH**: Update migration manager for multiple services (1 hour) - **KEY FIX**
5. **MEDIUM**: Implement auto-dataset creation (2-3 hours)
6. **LOW**: Comprehensive testing and documentation (2-4 hours)

## Risk Assessment

### Low Risk
- Configuration fixes
- Manual dataset creation for testing
- Code changes (well-isolated)

### Medium Risk  
- Auto-conversion of directories to datasets (data movement)
- Multi-service logic changes

### Mitigation
- Always backup before conversion
- Test on non-production data first
- Implement rollback procedures
- Comprehensive logging of all operations

## Expected Outcome

After implementation:
- ✅ **All services are ZFS datasets** (consistency)  
- ✅ **ZFS migration works for all stacks** (test-mcp-simple, Authelia, etc.)
- ✅ **Multi-service stacks handled correctly** (authelia + authelia-mariadb + authelia-redis)
- ✅ **Auto-dataset creation** (no manual intervention needed)
- ✅ **Maintains compatibility** (rsync fallback still works)
- ✅ **Better performance** (ZFS send/receive > rsync for large datasets)
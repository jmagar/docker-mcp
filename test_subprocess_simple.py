#!/usr/bin/env python3
"""Simplified test of subprocess manager core functionality."""

import asyncio
import sys
import os

# Minimal subprocess manager for testing
class SimpleSubprocessManager:
    def __init__(self):
        self._active_processes = set()
        self._cleanup_lock = asyncio.Lock()
    
    async def run_command(self, cmd, timeout=30, check=True):
        """Run a command with timeout and cleanup."""
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            async with self._cleanup_lock:
                self._active_processes.add(process)
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                print(f"  Timeout after {timeout}s, terminating...")
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    print("  Force killing...")
                    process.kill()
                    await process.wait()
                raise
            
            return {
                'returncode': process.returncode,
                'stdout': stdout.decode() if stdout else '',
                'stderr': stderr.decode() if stderr else '',
                'success': process.returncode == 0
            }
        finally:
            if process:
                async with self._cleanup_lock:
                    self._active_processes.discard(process)
                    
                if process.returncode is None:
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except:
                        try:
                            process.kill()
                            await process.wait()
                        except:
                            pass
    
    async def cleanup_all(self):
        """Cleanup all active processes."""
        async with self._cleanup_lock:
            processes = list(self._active_processes)
        
        for process in processes:
            if process.returncode is None:
                try:
                    process.terminate()
                except:
                    pass
        
        await asyncio.sleep(1)
        
        for process in processes:
            if process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass
        
        async with self._cleanup_lock:
            self._active_processes.clear()


async def test_resource_management():
    """Test resource management aspects."""
    print("Testing subprocess resource management...")
    
    # Test 1: Basic command execution
    print("\n1. Basic command execution...")
    manager = SimpleSubprocessManager()
    result = await manager.run_command(["echo", "test"])
    assert result['success'], "Should succeed"
    assert result['stdout'].strip() == "test", "Output should match"
    print("   ✓ Basic execution works")
    
    # Test 2: Timeout and cleanup
    print("\n2. Timeout and cleanup...")
    try:
        await manager.run_command(["sleep", "10"], timeout=0.1)
        assert False, "Should timeout"
    except asyncio.TimeoutError:
        print("   ✓ Timeout triggered")
    
    # Verify process was cleaned up
    await asyncio.sleep(0.2)
    assert len(manager._active_processes) == 0, "Process should be cleaned up"
    print("   ✓ Process cleaned up after timeout")
    
    # Test 3: Multiple concurrent processes
    print("\n3. Concurrent process management...")
    tasks = []
    for i in range(5):
        tasks.append(manager.run_command(["sh", "-c", f"sleep 0.1 && echo {i}"]))
    
    results = await asyncio.gather(*tasks)
    assert all(r['success'] for r in results), "All should succeed"
    assert len(manager._active_processes) == 0, "All processes should be cleaned up"
    print("   ✓ Concurrent processes managed correctly")
    
    # Test 4: Cleanup all functionality
    print("\n4. Cleanup all active processes...")
    # Start multiple long-running processes
    tasks = []
    for i in range(3):
        tasks.append(asyncio.create_task(
            manager.run_command(["sleep", "30"])
        ))
    
    await asyncio.sleep(0.1)  # Let them start
    assert len(manager._active_processes) == 3, "Should have 3 active processes"
    
    # Cleanup all
    await manager.cleanup_all()
    assert len(manager._active_processes) == 0, "All processes should be terminated"
    print("   ✓ Cleanup all works")
    
    # Cancel tasks
    for task in tasks:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    
    # Test 5: No zombie processes
    print("\n5. Checking for zombie processes...")
    
    # Check for zombies (Linux specific)
    if sys.platform.startswith('linux'):
        # Get baseline zombie count
        result = await manager.run_command(
            ["sh", "-c", "ps aux | grep -c '<defunct>' || true"]
        )
        baseline_zombies = int(result['stdout'].strip())
        
        # Run many short commands
        for _ in range(10):
            result = await manager.run_command(["echo", "test"])
            assert result['success']
        
        # Check for new zombies
        result = await manager.run_command(
            ["sh", "-c", "ps aux | grep -c '<defunct>' || true"]
        )
        final_zombies = int(result['stdout'].strip())
        new_zombies = final_zombies - baseline_zombies
        assert new_zombies <= 0, f"Created {new_zombies} new zombie processes"
        print(f"   ✓ No new zombie processes (baseline: {baseline_zombies}, final: {final_zombies})")
    else:
        print("   ⚠ Zombie check skipped (not Linux)")
    
    # Test 6: File descriptor check
    print("\n6. File descriptor management...")
    if sys.platform.startswith('linux'):
        pid = os.getpid()
        fd_dir = f"/proc/{pid}/fd"
        if os.path.exists(fd_dir):
            initial_fds = len(os.listdir(fd_dir))
            
            # Run many commands
            for _ in range(20):
                result = await manager.run_command(["echo", "test"])
                assert result['success']
            
            await manager.cleanup_all()
            await asyncio.sleep(0.1)
            
            final_fds = len(os.listdir(fd_dir))
            # Allow small variance
            assert final_fds <= initial_fds + 3, \
                f"FD leak detected: {initial_fds} -> {final_fds}"
            print(f"   ✓ FDs managed properly ({initial_fds} -> {final_fds})")
        else:
            print("   ⚠ FD check skipped (no /proc)")
    else:
        print("   ⚠ FD check skipped (not Linux)")
    
    print("\n✅ All resource management tests passed!")
    return 0


async def main():
    """Run all tests."""
    try:
        return await test_resource_management()
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
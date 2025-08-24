#!/usr/bin/env python3
"""Basic test of subprocess manager without pytest."""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docker_mcp.core.subprocess_manager import SubprocessManager, run_command


async def main():
    """Run basic tests."""
    print("Testing subprocess manager...")
    
    # Test 1: Simple command
    print("\n1. Testing simple command...")
    manager = SubprocessManager()
    result = await manager.run_command(["echo", "hello"])
    assert result.success, "Command should succeed"
    assert result.stdout.strip() == "hello", f"Expected 'hello', got '{result.stdout.strip()}'"
    print("✓ Simple command works")
    
    # Test 2: Command with error handling
    print("\n2. Testing error handling...")
    result = await manager.run_command(["sh", "-c", "exit 1"], check=False)
    assert not result.success, "Command should fail"
    assert result.returncode == 1, f"Expected returncode 1, got {result.returncode}"
    print("✓ Error handling works")
    
    # Test 3: Timeout handling
    print("\n3. Testing timeout...")
    try:
        await manager.run_command(["sleep", "10"], timeout=0.1)
        assert False, "Should have timed out"
    except asyncio.TimeoutError:
        print("✓ Timeout works")
    
    # Test 4: Process cleanup
    print("\n4. Testing process cleanup...")
    # Start a long-running process
    task = asyncio.create_task(
        manager.run_command(["sleep", "5"])
    )
    await asyncio.sleep(0.1)
    
    # Check we have active processes
    assert len(manager._active_processes) > 0, "Should have active processes"
    
    # Cleanup
    await manager.cleanup_all()
    assert len(manager._active_processes) == 0, "Should have no active processes after cleanup"
    print("✓ Process cleanup works")
    
    # Cancel the task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    
    # Test 5: Global function
    print("\n5. Testing global run_command...")
    result = await run_command(["echo", "global"])
    assert result.success, "Global command should work"
    assert "global" in result.stdout, "Should contain 'global'"
    print("✓ Global function works")
    
    # Test 6: Multiple concurrent commands
    print("\n6. Testing concurrent commands...")
    tasks = [
        manager.run_command(["echo", f"task{i}"])
        for i in range(5)
    ]
    results = await asyncio.gather(*tasks)
    assert len(results) == 5, "Should have 5 results"
    for i, result in enumerate(results):
        assert result.stdout.strip() == f"task{i}", f"Task {i} output mismatch"
    print("✓ Concurrent commands work")
    
    print("\n✅ All tests passed!")
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
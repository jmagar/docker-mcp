#!/usr/bin/env python3
"""Integration test to verify subprocess manager works with the codebase."""

import sys
import os
import tempfile

# Add the repo root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_imports():
    """Test that all imports work correctly."""
    print("Testing imports...")
    
    try:
        from docker_mcp.core.subprocess_manager import (
            SubprocessManager,
            SubprocessResult,
            run_command,
            run_ssh_command,
            cleanup_all,
            setup_signal_handlers,
        )
        print("  ✓ subprocess_manager imports work")
    except ImportError as e:
        print(f"  ✗ subprocess_manager import failed: {e}")
        return False
    
    try:
        from docker_mcp.services.stack import StackService
        print("  ✓ stack service imports work")
    except ImportError as e:
        print(f"  ✗ stack service import failed: {e}")
        return False
    
    try:
        from docker_mcp.tools.stacks import StackTools
        print("  ✓ stack tools imports work")
    except ImportError as e:
        print(f"  ✗ stack tools import failed: {e}")
        return False
    
    return True


def test_subprocess_manager_standalone():
    """Test subprocess manager without external dependencies."""
    print("\nTesting subprocess manager core functionality...")
    
    import asyncio
    from docker_mcp.core.subprocess_manager import SubprocessManager
    
    async def run_test():
        manager = SubprocessManager()
        
        # Test basic command
        result = await manager.run_command(["echo", "integration test"])
        assert result.success, "Command should succeed"
        assert "integration test" in result.stdout
        print("  ✓ Basic command execution works")
        
        # Test SSH command building
        class MockHost:
            hostname = "test.example.com"
            user = "testuser"
            port = 2222
            identity_file = "/tmp/test_key"
        
        # Mock the actual SSH execution
        from unittest.mock import patch
        with patch.object(manager, 'run_command') as mock_run:
            from docker_mcp.core.subprocess_manager import SubprocessResult
            mock_run.return_value = SubprocessResult(
                returncode=0,
                stdout="mocked output",
                stderr="",
                cmd=[]
            )
            
            result = await manager.run_ssh_command(
                MockHost(),
                "ls -la"
            )
            
            # Verify SSH command was built correctly
            ssh_cmd = mock_run.call_args[0][0]
            assert "ssh" in ssh_cmd
            assert "-p" in ssh_cmd
            assert "2222" in ssh_cmd
            assert "-i" in ssh_cmd
            assert "/tmp/test_key" in ssh_cmd
            print("  ✓ SSH command building works")
        
        # Test cleanup
        await manager.cleanup_all()
        assert len(manager._active_processes) == 0
        print("  ✓ Process cleanup works")
        
        return True
    
    try:
        result = asyncio.run(run_test())
        return result
    except Exception as e:
        print(f"  ✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_integration():
    """Test that subprocess manager works with config objects."""
    print("\nTesting config integration...")
    
    try:
        # Create a mock host config object
        class MockHostConfig:
            hostname = "example.com"
            user = "testuser"
            port = 22
            identity_file = "/tmp/key"
            compose_path = "/opt/compose"
            appdata_path = "/opt/appdata"
        
        host = MockHostConfig()
        print("  ✓ Mock config created")
        
        # Test that the host config works with subprocess manager
        import asyncio
        from docker_mcp.core.subprocess_manager import run_ssh_command
        from unittest.mock import patch, AsyncMock
        
        async def test_ssh():
            with patch('docker_mcp.core.subprocess_manager._subprocess_manager.run_command') as mock_run:
                from docker_mcp.core.subprocess_manager import SubprocessResult
                mock_run.return_value = SubprocessResult(
                    returncode=0,
                    stdout="test",
                    stderr="",
                    cmd=[]
                )
                
                result = await run_ssh_command(host, "echo test")
                assert result.stdout == "test"
                
                # Check SSH command was built correctly
                ssh_cmd = mock_run.call_args[0][0]
                assert "testuser@example.com" in ssh_cmd
                print("  ✓ Config integration with subprocess manager works")
        
        asyncio.run(test_ssh())
        return True
        
    except Exception as e:
        print(f"  ✗ Config integration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all integration tests."""
    print("Running integration tests for subprocess resource management fix...\n")
    
    all_passed = True
    
    # Test 1: Imports
    if not test_imports():
        all_passed = False
    
    # Test 2: Subprocess manager standalone
    if not test_subprocess_manager_standalone():
        all_passed = False
    
    # Test 3: Config integration
    if not test_config_integration():
        all_passed = False
    
    if all_passed:
        print("\n✅ All integration tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
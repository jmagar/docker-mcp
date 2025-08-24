#!/usr/bin/env python3
"""Test script for migration functionality."""

import asyncio
import sys
from pathlib import Path

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent))

from docker_mcp.core.config_loader import load_config
from docker_mcp.services.stack import StackService
from docker_mcp.core.docker_context import DockerContextManager


async def test_migration():
    """Test the opengist migration from squirts to shart."""
    # Load configuration
    config = load_config("config/hosts.yml")
    
    # Initialize services
    context_manager = DockerContextManager(config)
    stack_service = StackService(config, context_manager)
    
    print("🔍 Testing opengist stack migration from squirts to shart...")
    print(f"Source (squirts) appdata_path: {config.hosts['squirts'].appdata_path}")
    print(f"Target (shart) appdata_path: {config.hosts['shart'].appdata_path}")
    
    # First, let's list stacks on squirts to see the opengist stack
    print("\n📋 Listing stacks on squirts...")
    squirts_stacks = await stack_service.list_stacks("squirts")
    print(f"Squirts stacks result: {squirts_stacks.content}")
    
    # Check if opengist stack exists - parse text content
    stack_found = False
    stack_to_test = "gotify-mcp"  # Use an existing stack for testing
    
    if squirts_stacks.content and isinstance(squirts_stacks.content, list):
        text_content = squirts_stacks.content[0].text
        if stack_to_test in text_content:
            stack_found = True
            print(f"✅ Found {stack_to_test} stack in the list")
    
    if not stack_found:
        print(f"❌ {stack_to_test} stack not found on squirts.")
        return
    
    # List stacks on shart (target)
    print("\n📋 Listing stacks on shart...")
    shart_stacks = await stack_service.list_stacks("shart")
    print(f"Shart stacks result: {shart_stacks.content}")
    
    # Now perform the migration
    print("\n🚚 Starting migration...")
    try:
        migration_result = await stack_service.migrate_stack(
            source_host_id="squirts",
            target_host_id="shart", 
            stack_name=stack_to_test,
            skip_stop_source=False,  # Stop containers for safety
            remove_source=False,     # Keep source for verification
            start_target=True,       # Start on target
            dry_run=False            # Actually perform migration
        )
        
        print(f"✅ Migration completed: {migration_result.content}")
        
        # Verify the docker-compose.yml on target has actual paths
        print("\n🔍 Verifying docker-compose.yml on target has actual paths...")
        
        # List stacks on shart again to verify
        print("\n📋 Listing stacks on shart after migration...")
        shart_stacks_after = await stack_service.list_stacks("shart")
        print(f"Shart stacks after migration: {shart_stacks_after.content}")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_migration())
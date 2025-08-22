#!/usr/bin/env python3
"""
Manual cleanup script for Docker MCP test resources.

Usage:
    # Clean up all test resources
    python tests/cleanup.py
    
    # Clean up specific host
    python tests/cleanup.py --host squirts
    
    # Dry run (show what would be cleaned)
    python tests/cleanup.py --dry-run
    
    # Clean up with specific pattern
    python tests/cleanup.py --pattern "test-mcp"
"""

import asyncio
import argparse
import sys
from pathlib import Path
from typing import List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastmcp import Client
from docker_mcp.core.config import load_config
from docker_mcp.server import DockerMCPServer
from tests.cleanup_utils import (
    cleanup_test_containers,
    cleanup_test_stacks,
    emergency_cleanup,
    get_resource_tracker,
    verify_cleanup
)


# Known test container/stack patterns
TEST_PATTERNS = [
    "test-",
    "test_",
    "mcp-test",
    "test-mcp",
    "pytest",
    "test-nginx-mcp",
    "test-lifecycle",
    "test-integration",
    "test-port-conflict",
    "test-env-invalid",
    "123-invalid",  # From the invalid name test
]

# Specific orphaned resources found
KNOWN_ORPHANS = {
    "squirts": {
        "containers": [
            "test-port-conflict-conflict-test-1",
            "test-env-invalid-test-1", 
            "123-invalid-test-1",
            "test-lifecycle-complete-lifecycle-test-1",
            "test-integration-test-web-1",
            "test-mcp-complex-test-app-1",
            "test-mcp-complex-test-cache-1"
        ],
        "stacks": [
            "test-port-conflict",
            "test-env-invalid",
            "123-invalid",
            "test-lifecycle-complete",
            "test-integration",
            "test-mcp-complex"
        ]
    }
}


async def cleanup_known_orphans(client: Client, host_id: str, dry_run: bool = False) -> dict:
    """Clean up specifically known orphaned resources."""
    results = {
        "containers_stopped": [],
        "stacks_removed": [],
        "errors": []
    }
    
    if host_id not in KNOWN_ORPHANS:
        print(f"No known orphans for host {host_id}")
        return results
    
    orphans = KNOWN_ORPHANS[host_id]
    
    # Clean up known orphaned stacks
    for stack_name in orphans.get("stacks", []):
        try:
            if dry_run:
                print(f"[DRY RUN] Would remove stack: {stack_name}")
                results["stacks_removed"].append(f"{stack_name} (dry run)")
            else:
                print(f"Removing stack: {stack_name}")
                result = await client.call_tool("manage_stack", {
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "action": "down"
                })
                
                if result.data.get("success"):
                    results["stacks_removed"].append(stack_name)
                    print(f"  ✓ Stack {stack_name} removed")
                else:
                    error = f"Failed to remove stack {stack_name}: {result.data.get('error')}"
                    results["errors"].append(error)
                    print(f"  ✗ {error}")
                    
        except Exception as e:
            error = f"Error removing stack {stack_name}: {str(e)}"
            results["errors"].append(error)
            print(f"  ✗ {error}")
    
    # Stop known orphaned containers
    for container_name in orphans.get("containers", []):
        try:
            if dry_run:
                print(f"[DRY RUN] Would stop container: {container_name}")
                results["containers_stopped"].append(f"{container_name} (dry run)")
            else:
                print(f"Stopping container: {container_name}")
                result = await client.call_tool("manage_container", {
                    "host_id": host_id,
                    "container_id": container_name,
                    "action": "stop",
                    "timeout": 5
                })
                
                if result.data.get("success"):
                    results["containers_stopped"].append(container_name)
                    print(f"  ✓ Container {container_name} stopped")
                else:
                    # Container might already be stopped or not exist
                    print(f"  ⚠ Container {container_name}: {result.data.get('error', 'Already stopped or not found')}")
                    
        except Exception as e:
            error = f"Error stopping container {container_name}: {str(e)}"
            results["errors"].append(error)
            print(f"  ✗ {error}")
    
    return results


async def cleanup_by_pattern(client: Client, host_id: str, pattern: str, dry_run: bool = False) -> dict:
    """Clean up resources matching a specific pattern."""
    results = {}
    
    print(f"\nCleaning up resources matching pattern '{pattern}' on host {host_id}")
    
    if dry_run:
        print("[DRY RUN MODE - No actual changes will be made]")
        
        # List what would be cleaned
        container_result = await client.call_tool("list_containers", {
            "host_id": host_id,
            "all_containers": True,
            "limit": 100
        })
        
        if container_result.data.get("success"):
            containers = container_result.data.get("containers", [])
            matching = [c for c in containers if pattern in c.get("name", "")]
            if matching:
                print(f"\nWould clean up {len(matching)} containers:")
                for c in matching:
                    print(f"  - {c.get('name')} ({c.get('status', 'unknown')})")
        
        stack_result = await client.call_tool("list_stacks", {
            "host_id": host_id
        })
        
        if stack_result.data.get("success"):
            stacks = stack_result.data.get("stacks", [])
            matching = [s for s in stacks if pattern in s.get("name", "")]
            if matching:
                print(f"\nWould clean up {len(matching)} stacks:")
                for s in matching:
                    print(f"  - {s.get('name')}")
                    
        results["dry_run"] = True
        
    else:
        # Actually clean up
        print("\nCleaning up containers...")
        container_results = await cleanup_test_containers(client, host_id, pattern)
        results["containers"] = container_results
        
        if container_results.get("cleaned"):
            print(f"Cleaned {len(container_results['cleaned'])} containers:")
            for name in container_results["cleaned"]:
                print(f"  ✓ {name}")
        
        if container_results.get("failed"):
            print(f"Failed to clean {len(container_results['failed'])} containers:")
            for item in container_results["failed"]:
                print(f"  ✗ {item['container']}: {item['error']}")
        
        print("\nCleaning up stacks...")
        stack_results = await cleanup_test_stacks(client, host_id, pattern)
        results["stacks"] = stack_results
        
        if stack_results.get("cleaned"):
            print(f"Cleaned {len(stack_results['cleaned'])} stacks:")
            for name in stack_results["cleaned"]:
                print(f"  ✓ {name}")
        
        if stack_results.get("failed"):
            print(f"Failed to clean {len(stack_results['failed'])} stacks:")
            for item in stack_results["failed"]:
                print(f"  ✗ {item['stack']}: {item['error']}")
    
    return results


async def main():
    """Main cleanup function."""
    parser = argparse.ArgumentParser(description="Clean up Docker MCP test resources")
    parser.add_argument("--host", type=str, help="Specific host to clean up (default: all configured hosts)")
    parser.add_argument("--pattern", type=str, help="Specific pattern to match (default: all test patterns)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be cleaned without doing it")
    parser.add_argument("--emergency", action="store_true", help="Emergency cleanup - remove ALL test resources")
    parser.add_argument("--known-orphans", action="store_true", help="Clean up specifically known orphaned resources")
    parser.add_argument("--config", type=str, help="Path to config file", default="config/hosts.yml")
    
    args = parser.parse_args()
    
    # Load configuration
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)
    
    config = load_config(str(config_path))
    
    # Create server and client
    server = DockerMCPServer(config)
    server._initialize_app()
    
    async with Client(server.app) as client:
        # Determine which hosts to clean
        if args.host:
            if args.host not in config.hosts:
                print(f"Error: Host '{args.host}' not found in configuration")
                print(f"Available hosts: {', '.join(config.hosts.keys())}")
                sys.exit(1)
            hosts = [args.host]
        else:
            hosts = list(config.hosts.keys())
        
        print(f"Cleaning up test resources on hosts: {', '.join(hosts)}")
        
        overall_results = {}
        
        for host_id in hosts:
            print(f"\n{'='*60}")
            print(f"Processing host: {host_id}")
            print(f"{'='*60}")
            
            host_results = {}
            
            try:
                if args.known_orphans:
                    # Clean up known orphaned resources
                    results = await cleanup_known_orphans(client, host_id, args.dry_run)
                    host_results["known_orphans"] = results
                    
                elif args.emergency:
                    # Emergency cleanup
                    if args.dry_run:
                        print("[DRY RUN] Would perform emergency cleanup of ALL test resources")
                        host_results["emergency"] = {"dry_run": True}
                    else:
                        confirm = input(f"⚠️  EMERGENCY CLEANUP on {host_id}: Remove ALL test resources? (yes/no): ")
                        if confirm.lower() == "yes":
                            results = await emergency_cleanup(client, host_id)
                            host_results["emergency"] = results
                            
                            print(f"\nEmergency cleanup summary:")
                            print(f"  Containers cleaned: {results['summary']['total_containers_cleaned']}")
                            print(f"  Stacks cleaned: {results['summary']['total_stacks_cleaned']}")
                            print(f"  Failures: {results['summary']['total_failures']}")
                        else:
                            print("Emergency cleanup cancelled")
                            
                elif args.pattern:
                    # Clean up specific pattern
                    results = await cleanup_by_pattern(client, host_id, args.pattern, args.dry_run)
                    host_results["pattern"] = results
                    
                else:
                    # Clean up all test patterns
                    for pattern in TEST_PATTERNS:
                        results = await cleanup_by_pattern(client, host_id, pattern, args.dry_run)
                        if results and not (results.get("containers", {}).get("total_cleaned") == 0 and 
                                          results.get("stacks", {}).get("total_cleaned") == 0):
                            host_results[pattern] = results
                
                overall_results[host_id] = host_results
                
            except Exception as e:
                print(f"Error processing host {host_id}: {str(e)}")
                overall_results[host_id] = {"error": str(e)}
        
        # Print final summary
        print(f"\n{'='*60}")
        print("CLEANUP SUMMARY")
        print(f"{'='*60}")
        
        tracker = get_resource_tracker()
        report = tracker.get_cleanup_report()
        
        if report["summary"]["total_remaining_containers"] > 0:
            print(f"\n⚠️  Remaining containers: {report['summary']['total_remaining_containers']}")
            for host_id, containers in report["remaining_containers"].items():
                if containers:
                    print(f"  {host_id}: {', '.join(containers)}")
        
        if report["summary"]["total_remaining_stacks"] > 0:
            print(f"\n⚠️  Remaining stacks: {report['summary']['total_remaining_stacks']}")
            for host_id, stacks in report["remaining_stacks"].items():
                if stacks:
                    print(f"  {host_id}: {', '.join(stacks)}")
        
        if report["summary"]["total_failures"] > 0:
            print(f"\n❌ Failed cleanups: {report['summary']['total_failures']}")
            for failure in report["failed_cleanups"]:
                print(f"  {failure['type']} {failure['name']} on {failure['host_id']}: {failure['error']}")
        
        if not args.dry_run and report["summary"]["total_remaining_containers"] == 0 and \
           report["summary"]["total_remaining_stacks"] == 0 and \
           report["summary"]["total_failures"] == 0:
            print("\n✅ All test resources cleaned successfully!")
        elif args.dry_run:
            print("\n[DRY RUN COMPLETE - No actual changes were made]")


if __name__ == "__main__":
    asyncio.run(main())
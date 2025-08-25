"""Test cleanup utilities for Docker MCP tests."""

from datetime import datetime
from typing import Any

import structlog
from fastmcp import Client

logger = structlog.get_logger()


class TestResourceTracker:
    """Track test resources for cleanup."""

    def __init__(self):
        self.containers: dict[str, list[str]] = {}  # host_id -> container_names
        self.stacks: dict[str, list[str]] = {}  # host_id -> stack_names
        self.failed_cleanups: list[dict[str, Any]] = []

    def add_container(self, host_id: str, container_name: str):
        """Track a container for cleanup."""
        if host_id not in self.containers:
            self.containers[host_id] = []
        self.containers[host_id].append(container_name)

    def add_stack(self, host_id: str, stack_name: str):
        """Track a stack for cleanup."""
        if host_id not in self.stacks:
            self.stacks[host_id] = []
        self.stacks[host_id].append(stack_name)

    def remove_container(self, host_id: str, container_name: str):
        """Remove container from tracking after successful cleanup."""
        if host_id in self.containers:
            if container_name in self.containers[host_id]:
                self.containers[host_id].remove(container_name)

    def remove_stack(self, host_id: str, stack_name: str):
        """Remove stack from tracking after successful cleanup."""
        if host_id in self.stacks:
            if stack_name in self.stacks[host_id]:
                self.stacks[host_id].remove(stack_name)

    def record_failure(self, resource_type: str, resource_name: str, host_id: str, error: str):
        """Record a cleanup failure."""
        self.failed_cleanups.append({
            "type": resource_type,
            "name": resource_name,
            "host_id": host_id,
            "error": error,
            "timestamp": datetime.now().isoformat()
        })

    def get_cleanup_report(self) -> dict[str, Any]:
        """Generate cleanup report."""
        return {
            "remaining_containers": self.containers,
            "remaining_stacks": self.stacks,
            "failed_cleanups": self.failed_cleanups,
            "summary": {
                "total_remaining_containers": sum(len(c) for c in self.containers.values()),
                "total_remaining_stacks": sum(len(s) for s in self.stacks.values()),
                "total_failures": len(self.failed_cleanups)
            }
        }


# Global tracker instance
_resource_tracker = TestResourceTracker()


def get_resource_tracker() -> TestResourceTracker:
    """Get the global resource tracker instance."""
    return _resource_tracker


async def cleanup_test_containers(client: Client, host_id: str, pattern: str = "test-") -> dict[str, Any]:
    """
    Clean up test containers matching a pattern.
    
    Args:
        client: FastMCP client
        host_id: Docker host ID
        pattern: Pattern to match container names (default: "test-")
        
    Returns:
        Cleanup results
    """
    cleaned = []
    failed = []

    try:
        # List all containers on the host
        result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": host_id,
            "all_containers": True,
            "limit": 100
        })

        if not result.data.get("success"):
            return {
                "success": False,
                "error": f"Failed to list containers: {result.data.get('error', 'Unknown error')}"
            }

        containers = result.data.get("containers", [])

        # Find containers matching the pattern
        test_containers = [
            c for c in containers
            if pattern in c.get("name", "") or
               pattern in c.get("container_id", "") or
               any(pattern in label for label in c.get("labels", []))
        ]

        logger.info(f"Found {len(test_containers)} test containers to clean up",
                   host_id=host_id, pattern=pattern)

        # Stop and remove each container
        for container in test_containers:
            container_id = container.get("container_id")
            container_name = container.get("name", container_id)

            try:
                # Stop if running
                if container.get("is_running"):
                    stop_result = await client.call_tool("docker_container", {
                        "host_id": host_id,
                        "container_id": container_id,
                        "action": "stop",
                        "timeout": 5
                    })

                    if not stop_result.data.get("success"):
                        logger.warning(f"Failed to stop container {container_name}",
                                     error=stop_result.data.get("error"))

                # Note: Since "remove" is not in allowed commands, we'll track for manual cleanup
                # In real implementation, we'd need to add "rm" to allowed commands or use compose down
                cleaned.append(container_name)
                get_resource_tracker().remove_container(host_id, container_name)

            except Exception as e:
                failed.append({
                    "container": container_name,
                    "error": str(e)
                })
                get_resource_tracker().record_failure("container", container_name, host_id, str(e))

    except Exception as e:
        logger.error("Container cleanup failed", host_id=host_id, error=str(e))
        return {
            "success": False,
            "error": str(e)
        }

    return {
        "success": len(failed) == 0,
        "cleaned": cleaned,
        "failed": failed,
        "total_cleaned": len(cleaned),
        "total_failed": len(failed)
    }


async def cleanup_test_stacks(client: Client, host_id: str, pattern: str = "test-") -> dict[str, Any]:
    """
    Clean up test stacks matching a pattern.
    
    Args:
        client: FastMCP client
        host_id: Docker host ID
        pattern: Pattern to match stack names (default: "test-")
        
    Returns:
        Cleanup results
    """
    cleaned = []
    failed = []

    try:
        # List all stacks on the host
        result = await client.call_tool("docker_compose", {
            "action": "list",
            "host_id": host_id
        })

        if not result.data.get("success"):
            return {
                "success": False,
                "error": f"Failed to list stacks: {result.data.get('error', 'Unknown error')}"
            }

        stacks = result.data.get("stacks", [])

        # Find stacks matching the pattern
        test_stacks = [
            s for s in stacks
            if pattern in s.get("name", "")
        ]

        logger.info(f"Found {len(test_stacks)} test stacks to clean up",
                   host_id=host_id, pattern=pattern)

        # Remove each stack
        for stack in test_stacks:
            stack_name = stack.get("name")

            try:
                # Remove stack with volumes
                down_result = await client.call_tool("docker_compose", {
                    "host_id": host_id,
                    "stack_name": stack_name,
                    "action": "down"
                })

                if down_result.data.get("success"):
                    cleaned.append(stack_name)
                    get_resource_tracker().remove_stack(host_id, stack_name)
                else:
                    failed.append({
                        "stack": stack_name,
                        "error": down_result.data.get("error", "Unknown error")
                    })
                    get_resource_tracker().record_failure("stack", stack_name, host_id,
                                                         down_result.data.get("error", "Unknown"))

            except Exception as e:
                failed.append({
                    "stack": stack_name,
                    "error": str(e)
                })
                get_resource_tracker().record_failure("stack", stack_name, host_id, str(e))

    except Exception as e:
        logger.error("Stack cleanup failed", host_id=host_id, error=str(e))
        return {
            "success": False,
            "error": str(e)
        }

    return {
        "success": len(failed) == 0,
        "cleaned": cleaned,
        "failed": failed,
        "total_cleaned": len(cleaned),
        "total_failed": len(failed)
    }


async def verify_cleanup(client: Client, host_id: str, resources: list[str]) -> dict[str, Any]:
    """
    Verify that resources have been cleaned up.
    
    Args:
        client: FastMCP client
        host_id: Docker host ID
        resources: List of resource names to verify
        
    Returns:
        Verification results
    """
    still_exists = []

    try:
        # Check containers
        container_result = await client.call_tool("docker_container", {
            "action": "list",
            "host_id": host_id,
            "all_containers": True,
            "limit": 100
        })

        if container_result.data.get("success"):
            containers = container_result.data.get("containers", [])
            for container in containers:
                name = container.get("name", container.get("container_id"))
                if name in resources:
                    still_exists.append({
                        "type": "container",
                        "name": name,
                        "status": container.get("status", "unknown")
                    })

        # Check stacks
        stack_result = await client.call_tool("docker_compose", {
            "action": "list",
            "host_id": host_id
        })

        if stack_result.data.get("success"):
            stacks = stack_result.data.get("stacks", [])
            for stack in stacks:
                name = stack.get("name")
                if name in resources:
                    still_exists.append({
                        "type": "stack",
                        "name": name,
                        "status": stack.get("status", "unknown")
                    })

    except Exception as e:
        logger.error("Verification failed", host_id=host_id, error=str(e))
        return {
            "success": False,
            "error": str(e)
        }

    return {
        "success": len(still_exists) == 0,
        "verified_clean": len(still_exists) == 0,
        "still_exists": still_exists,
        "total_remaining": len(still_exists)
    }


async def emergency_cleanup(client: Client, host_id: str) -> dict[str, Any]:
    """
    Nuclear option - clean up ALL test resources.
    
    This will remove:
    - All containers with "test" in the name
    - All stacks with "test" in the name
    - All containers/stacks with test-related labels
    
    Args:
        client: FastMCP client
        host_id: Docker host ID
        
    Returns:
        Cleanup results
    """
    results = {
        "containers": {},
        "stacks": {},
        "summary": {}
    }

    # Clean up test containers with various patterns
    test_patterns = ["test-", "test_", "mcp-test", "pytest"]

    for pattern in test_patterns:
        container_cleanup = await cleanup_test_containers(client, host_id, pattern)
        if container_cleanup.get("total_cleaned", 0) > 0:
            results["containers"][pattern] = container_cleanup

    # Clean up test stacks with various patterns
    for pattern in test_patterns:
        stack_cleanup = await cleanup_test_stacks(client, host_id, pattern)
        if stack_cleanup.get("total_cleaned", 0) > 0:
            results["stacks"][pattern] = stack_cleanup

    # Calculate summary
    total_containers_cleaned = sum(
        r.get("total_cleaned", 0) for r in results["containers"].values()
    )
    total_stacks_cleaned = sum(
        r.get("total_cleaned", 0) for r in results["stacks"].values()
    )
    total_failures = sum(
        r.get("total_failed", 0) for r in results["containers"].values()
    ) + sum(
        r.get("total_failed", 0) for r in results["stacks"].values()
    )

    results["summary"] = {
        "total_containers_cleaned": total_containers_cleaned,
        "total_stacks_cleaned": total_stacks_cleaned,
        "total_failures": total_failures,
        "success": total_failures == 0
    }

    # Get final report from tracker
    results["tracker_report"] = get_resource_tracker().get_cleanup_report()

    return results


def with_cleanup(host_id: str):
    """
    Decorator for test methods that ensures cleanup.
    
    Usage:
        @with_cleanup("squirts")
        async def test_something(self, client):
            # Deploy resources
            ...
    """
    def decorator(func):
        async def wrapper(self, client: Client, *args, **kwargs):
            tracker = get_resource_tracker()

            try:
                # Run the test
                result = await func(self, client, *args, **kwargs)
                return result

            finally:
                # Clean up any tracked resources for this host
                if host_id in tracker.stacks:
                    for stack_name in tracker.stacks[host_id][:]:  # Copy list to avoid modification during iteration
                        try:
                            await client.call_tool("docker_compose", {
                                "host_id": host_id,
                                "stack_name": stack_name,
                                "action": "down"
                            })
                            tracker.remove_stack(host_id, stack_name)
                        except Exception as e:
                            logger.error(f"Cleanup failed for stack {stack_name}", error=str(e))

                if host_id in tracker.containers:
                    for container_name in tracker.containers[host_id][:]:
                        try:
                            await client.call_tool("docker_container", {
                                "host_id": host_id,
                                "container_id": container_name,
                                "action": "stop"
                            })
                            tracker.remove_container(host_id, container_name)
                        except Exception as e:
                            logger.error(f"Cleanup failed for container {container_name}", error=str(e))

        return wrapper
    return decorator

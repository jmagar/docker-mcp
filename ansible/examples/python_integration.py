"""
Example Python integration layer for Ansible playbooks.
This shows how the existing Docker MCP services would integrate with Ansible.

DO NOT INTEGRATE YET - This is just an example of the integration approach.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import ansible_runner
import structlog

logger = structlog.get_logger()


class AnsiblePlaybookRunner:
    """Service for running Ansible playbooks from Python."""

    def __init__(self, ansible_dir: Path):
        self.ansible_dir = ansible_dir
        self.playbook_dir = ansible_dir / "playbooks"
        self.inventory_script = ansible_dir / "inventory" / "dynamic_inventory.py"

    async def run_playbook(
        self,
        playbook_name: str,
        extra_vars: dict[str, Any] = None,
        limit: str = None,
        check_mode: bool = False,
        verbose: int = 0,
    ) -> dict[str, Any]:
        """Run an Ansible playbook and return structured results."""

        playbook_path = self.playbook_dir / f"{playbook_name}.yml"
        if not playbook_path.exists():
            raise ValueError(f"Playbook not found: {playbook_path}")

        # Prepare ansible-runner configuration
        runner_config = {
            "playbook": str(playbook_path),
            "inventory": str(self.inventory_script),
            "verbosity": verbose,
            "quiet": verbose == 0,
        }

        if extra_vars:
            runner_config["extravars"] = extra_vars

        if limit:
            runner_config["limit"] = limit

        if check_mode:
            runner_config["check"] = True

        # Create temporary directory for ansible-runner
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Run playbook
                result = await asyncio.to_thread(
                    ansible_runner.run, private_data_dir=temp_dir, **runner_config
                )

                # Process results
                return self._process_ansible_result(result)

            except Exception as e:
                logger.error(
                    "Ansible playbook execution failed", playbook=playbook_name, error=str(e)
                )
                return {"success": False, "error": str(e), "playbook": playbook_name}

    def _process_ansible_result(self, result) -> dict[str, Any]:
        """Process ansible-runner result into standardized format."""

        success = result.status == "successful"

        processed_result = {
            "success": success,
            "status": result.status,
            "playbook": result.config.get("playbook", "unknown"),
            "stats": result.stats,
            "artifacts_dir": result.artifacts_dir,
        }

        if not success:
            processed_result["error"] = f"Playbook failed with status: {result.status}"

        # Extract host results
        host_results = {}
        if hasattr(result, "events"):
            for event in result.events:
                if event.get("event") == "runner_on_ok":
                    host = event.get("event_data", {}).get("host")
                    task = event.get("event_data", {}).get("task")
                    res = event.get("event_data", {}).get("res", {})

                    if host not in host_results:
                        host_results[host] = {}
                    host_results[host][task] = res

        processed_result["host_results"] = host_results
        return processed_result


class AnsibleIntegratedCleanupService:
    """Example of how CleanupService would integrate with Ansible."""

    def __init__(self, ansible_runner: AnsiblePlaybookRunner):
        self.ansible_runner = ansible_runner
        self.logger = structlog.get_logger()

    async def docker_cleanup(self, host_id: str, cleanup_type: str) -> dict[str, Any]:
        """Run Docker cleanup using Ansible playbook."""

        try:
            # Map to Ansible playbook
            extra_vars = {"cleanup_level": cleanup_type, "target_hosts": host_id}

            # Run Ansible playbook
            result = await self.ansible_runner.run_playbook(
                playbook_name="docker-cleanup", extra_vars=extra_vars, limit=host_id
            )

            if result["success"]:
                # Transform Ansible result to Docker MCP format
                return self._transform_cleanup_result(result, host_id, cleanup_type)
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Ansible playbook failed"),
                    "host_id": host_id,
                }

        except Exception as e:
            self.logger.error("Ansible cleanup failed", host_id=host_id, error=str(e))
            return {"success": False, "error": str(e), "host_id": host_id}

    def _transform_cleanup_result(
        self, ansible_result: dict[str, Any], host_id: str, cleanup_type: str
    ) -> dict[str, Any]:
        """Transform Ansible playbook result to Docker MCP format."""

        host_results = ansible_result.get("host_results", {}).get(host_id, {})

        # Extract cleanup results from Ansible output
        cleanup_results = []
        for task_name, task_result in host_results.items():
            if "cleanup" in task_name.lower() and task_result.get("changed"):
                cleanup_results.append(
                    {
                        "resource_type": self._extract_resource_type(task_name),
                        "success": True,
                        "space_reclaimed": self._extract_space_reclaimed(task_result),
                    }
                )

        return {
            "success": True,
            "host_id": host_id,
            "cleanup_type": cleanup_type,
            "results": cleanup_results,
            "message": f"Ansible-powered {cleanup_type} cleanup completed",
            "ansible_stats": ansible_result.get("stats", {}),
        }

    def _extract_resource_type(self, task_name: str) -> str:
        """Extract resource type from Ansible task name."""
        if "container" in task_name.lower():
            return "containers"
        elif "image" in task_name.lower():
            return "images"
        elif "volume" in task_name.lower():
            return "volumes"
        elif "network" in task_name.lower():
            return "networks"
        elif "cache" in task_name.lower():
            return "build cache"
        else:
            return "unknown"

    def _extract_space_reclaimed(self, task_result: dict[str, Any]) -> str:
        """Extract space reclaimed from Ansible task result."""
        # This would parse the actual Docker command output
        # stored in task_result to extract space information
        stdout = task_result.get("stdout", "")
        if "Total reclaimed space:" in stdout:
            # Parse Docker's output format
            import re

            match = re.search(r"Total reclaimed space:\s+(\S+)", stdout)
            return match.group(1) if match else "Unknown"
        return "Unknown"


class AnsibleIntegratedMigrationService:
    """Example of how MigrationService would integrate with Ansible."""

    def __init__(self, ansible_runner: AnsiblePlaybookRunner):
        self.ansible_runner = ansible_runner
        self.logger = structlog.get_logger()

    async def migrate_stack(
        self,
        source_host_id: str,
        target_host_id: str,
        stack_name: str,
        dry_run: bool = False,
        skip_stop_source: bool = False,
    ) -> dict[str, Any]:
        """Migrate stack using Ansible playbook."""

        try:
            extra_vars = {
                "source_host": source_host_id,
                "target_host": target_host_id,
                "stack": stack_name,
                "dry_run_mode": dry_run,
                "skip_stop": skip_stop_source,
            }

            result = await self.ansible_runner.run_playbook(
                playbook_name="migrate-stack", extra_vars=extra_vars, check_mode=dry_run
            )

            if result["success"]:
                return self._transform_migration_result(
                    result, source_host_id, target_host_id, stack_name
                )
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Migration playbook failed"),
                    "source_host_id": source_host_id,
                    "target_host_id": target_host_id,
                    "stack_name": stack_name,
                }

        except Exception as e:
            self.logger.error("Ansible migration failed", stack=stack_name, error=str(e))
            return {"success": False, "error": str(e)}

    def _transform_migration_result(
        self,
        ansible_result: dict[str, Any],
        source_host_id: str,
        target_host_id: str,
        stack_name: str,
    ) -> dict[str, Any]:
        """Transform Ansible migration result to Docker MCP format."""

        return {
            "success": True,
            "source_host_id": source_host_id,
            "target_host_id": target_host_id,
            "stack_name": stack_name,
            "message": "Ansible-powered migration completed successfully",
            "transfer_method": self._determine_transfer_method(ansible_result),
            "ansible_stats": ansible_result.get("stats", {}),
            "execution_time": self._calculate_execution_time(ansible_result),
        }

    def _determine_transfer_method(self, ansible_result: dict[str, Any]) -> str:
        """Determine which transfer method was used based on Ansible output."""
        # Analyze the task results to determine if ZFS or rsync was used
        host_results = ansible_result.get("host_results", {})
        for _host, tasks in host_results.items():
            for task_name in tasks.keys():
                if "zfs" in task_name.lower():
                    return "zfs"
                elif "rsync" in task_name.lower():
                    return "rsync"
        return "unknown"

    def _calculate_execution_time(self, ansible_result: dict[str, Any]) -> float:
        """Calculate total execution time from Ansible result."""
        # This would analyze the Ansible events to calculate timing
        return 0.0  # Placeholder


# Example usage
async def example_usage():
    """Example of how the integrated services would be used."""

    ansible_dir = Path("/home/jmagar/code/docker-mcp/ansible")
    ansible_runner = AnsiblePlaybookRunner(ansible_dir)

    # Cleanup service
    cleanup_service = AnsibleIntegratedCleanupService(ansible_runner)
    cleanup_result = await cleanup_service.docker_cleanup("production-1", "safe")
    print("Cleanup result:", cleanup_result)

    # Migration service
    migration_service = AnsibleIntegratedMigrationService(ansible_runner)
    migration_result = await migration_service.migrate_stack(
        "old-server", "new-server", "my-app", dry_run=True
    )
    print("Migration result:", migration_result)


if __name__ == "__main__":
    # This is just an example - not meant to be executed
    print("This is an example integration file - not meant for execution")
    print("It shows how existing Docker MCP services would integrate with Ansible playbooks")

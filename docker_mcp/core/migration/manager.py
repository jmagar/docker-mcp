"""Main migration orchestrator for Docker stack transfers."""

import asyncio
import json
import shlex
import subprocess
from typing import Any

import structlog

from ..config_loader import DockerHost
from ..exceptions import DockerMCPError
from ..transfer import ArchiveUtils, RsyncTransfer
from .verification import MigrationVerifier
from .volume_parser import VolumeParser

logger = structlog.get_logger()


class MigrationError(DockerMCPError):
    """Migration operation failed."""

    pass


class MigrationManager:
    """Orchestrates Docker stack migrations between hosts using modular components."""

    def __init__(self):
        self.logger = logger.bind(component="migration_manager")

        # Initialize focused components
        self.volume_parser = VolumeParser()
        self.verifier = MigrationVerifier()

        # Initialize transfer methods
        self.archive_utils = ArchiveUtils()
        self.rsync_transfer = RsyncTransfer()

    async def choose_transfer_method(
        self, source_host: DockerHost, target_host: DockerHost
    ) -> tuple[str, Any]:
        """Choose transfer method - uses rsync for universal compatibility.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration

        Returns:
            Tuple of (transfer_type: str, transfer_instance)
        """
        self.logger.info("Using rsync transfer for universal compatibility")
        return "rsync", self.rsync_transfer

    async def verify_containers_stopped(
        self,
        ssh_cmd: list[str],
        stack_name: str,
        force_stop: bool = False,
    ) -> tuple[bool, list[str]]:
        """Verify all containers in a stack are stopped.

        Args:
            ssh_cmd: SSH command parts for remote execution
            stack_name: Stack name to check
            force_stop: Force stop running containers

        Returns:
            Tuple of (all_stopped, list_of_running_containers)
        """
        compose_cmd = (
            "docker compose "
            "--ansi never "
            f"--project-name {shlex.quote(stack_name)} "
            "ps --format json"
        )
        check_cmd = ssh_cmd + [compose_cmd]

        result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            check_cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            error_message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            self.logger.error(
                "docker compose ps verification failed",
                stack=stack_name,
                error=error_message,
            )
            raise MigrationError(
                f"docker compose ps failed while verifying shutdown: {error_message}"
            )

        running_containers: list[str] = []
        for line in result.stdout.splitlines():
            payload = line.strip()
            if not payload:
                continue
            try:
                entry = json.loads(payload)
            except json.JSONDecodeError:
                self.logger.warning(
                    "Failed to parse docker compose ps output line", line=payload
                )
                continue

            state_value = str(entry.get("State") or entry.get("state") or "").lower()
            if state_value.startswith("running") or state_value.startswith("up"):
                name = entry.get("Name") or entry.get("name") or entry.get("ID")
                if name:
                    running_containers.append(str(name))

        if not running_containers:
            return True, []

        if force_stop:
            self.logger.info(
                "Force stopping containers",
                stack=stack_name,
                containers=running_containers,
            )

            # Force stop each container
            for container in running_containers:
                stop_cmd = ssh_cmd + [f"docker kill {shlex.quote(container)}"]
                await asyncio.to_thread(
                    subprocess.run,  # nosec B603
                    stop_cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

            # Wait for containers to stop and processes to fully terminate
            await asyncio.sleep(10)  # Increased from 3s to ensure complete shutdown

            # Re-check
            return await self.verify_containers_stopped(ssh_cmd, stack_name, force_stop=False)

        return False, running_containers

    async def prepare_target_directories(
        self,
        ssh_cmd: list[str],
        appdata_path: str,
        stack_name: str,
    ) -> str:
        """Prepare target directories for migration.

        Args:
            ssh_cmd: SSH command parts for remote execution
            appdata_path: Base appdata path on target host
            stack_name: Stack name for directory organization

        Returns:
            Path to stack-specific appdata directory
        """
        # Create stack-specific directory
        stack_dir = f"{appdata_path}/{stack_name}"
        mkdir_cmd = f"mkdir -p {shlex.quote(stack_dir)}"
        full_cmd = ssh_cmd + [mkdir_cmd]

        result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            full_cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise MigrationError(f"Failed to create target directory: {result.stderr}")

        self.logger.info(
            "Prepared target directory",
            path=stack_dir,
        )

        return stack_dir

    async def transfer_data(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_paths: list[str],
        target_path: str,
        stack_name: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Transfer data between hosts using the optimal method.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_paths: List of paths to transfer from source
            target_path: Target path on destination
            stack_name: Stack name for organization
            dry_run: Whether this is a dry run

        Returns:
            Transfer result dictionary
        """
        if not source_paths:
            return {"success": True, "message": "No data to transfer", "transfer_type": "none"}

        # Choose transfer method
        transfer_type, transfer_instance = await self.choose_transfer_method(
            source_host, target_host
        )

        # Use rsync transfer - direct directory synchronization
        # Rsync transfer - direct directory synchronization (no archiving)
        if dry_run:
            return {
                "success": True,
                "message": "Dry run - would transfer via direct rsync",
                "transfer_type": "rsync",
            }

        # For rsync, directly sync each source path to target
        transfer_results = []
        overall_success = True

        for source_path in source_paths:
            try:
                # For rsync, sync directly to target_path (which already includes stack name from executor)
                # Don't append basename to avoid path duplication like /appdata/stack/stack

                result = await transfer_instance.transfer(
                    source_host=source_host,
                    target_host=target_host,
                    source_path=source_path,
                    target_path=target_path,  # Use target_path directly
                    compress=True,
                    delete=False,  # Safety: don't delete target files
                )

                transfer_results.append(result)
                if not result.get("success", False):
                    overall_success = False

            except Exception as e:
                overall_success = False
                transfer_results.append(
                    {"success": False, "error": str(e), "source_path": source_path}
                )

        final_result = {
            "success": overall_success,
            "transfer_type": "rsync",
            "transfers": transfer_results,
            "paths_transferred": len([r for r in transfer_results if r.get("success", False)]),
            "total_paths": len(source_paths),
        }

        if not overall_success:
            final_result["message"] = "Some rsync transfers failed"
        else:
            final_result["message"] = (
                f"Successfully transferred {final_result['paths_transferred']} paths via rsync"
            )

        return final_result

    # Delegate methods to focused components
    async def parse_compose_volumes(
        self, compose_content: str, source_appdata_path: str = None
    ) -> dict[str, Any]:
        """Delegate to VolumeParser."""
        return await self.volume_parser.parse_compose_volumes(compose_content, source_appdata_path)

    async def get_volume_locations(
        self, ssh_cmd: list[str], named_volumes: list[str]
    ) -> dict[str, str]:
        """Delegate to VolumeParser."""
        return await self.volume_parser.get_volume_locations(ssh_cmd, named_volumes)

    def update_compose_for_migration(
        self,
        compose_content: str,
        old_paths: dict[str, str],
        new_base_path: str,
        target_appdata_path: str = None,
    ) -> str:
        """Delegate to VolumeParser."""
        return self.volume_parser.update_compose_for_migration(
            compose_content, old_paths, new_base_path, target_appdata_path
        )

    async def create_source_inventory(
        self, ssh_cmd: list[str], volume_paths: list[str]
    ) -> dict[str, Any]:
        """Delegate to MigrationVerifier."""
        return await self.verifier.create_source_inventory(ssh_cmd, volume_paths)

    async def verify_migration_completeness(
        self, ssh_cmd: list[str], source_inventory: dict[str, Any], target_path: str
    ) -> dict[str, Any]:
        """Delegate to MigrationVerifier."""
        return await self.verifier.verify_migration_completeness(
            ssh_cmd, source_inventory, target_path
        )

    async def verify_container_integration(
        self,
        ssh_cmd: list[str],
        stack_name: str,
        expected_appdata_path: str,
        expected_volumes: list[str],
    ) -> dict[str, Any]:
        """Delegate to MigrationVerifier."""
        return await self.verifier.verify_container_integration(
            ssh_cmd, stack_name, expected_appdata_path, expected_volumes
        )

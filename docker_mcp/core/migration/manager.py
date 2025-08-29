"""Main migration orchestrator for Docker stack transfers."""

import asyncio
import os
import shlex
import subprocess
import tempfile
from typing import Any

import structlog

from ...constants import DOCKER_COMPOSE_PROJECT
from ...utils import build_ssh_command
from ..config_loader import DockerHost
from ..exceptions import DockerMCPError
from ..transfer import ArchiveUtils, RsyncTransfer, ZFSTransfer
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
        self.zfs_transfer = ZFSTransfer()

    async def choose_transfer_method(
        self, source_host: DockerHost, target_host: DockerHost
    ) -> tuple[str, Any]:
        """Choose the optimal transfer method based on host capabilities.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration

        Returns:
            Tuple of (transfer_type: str, transfer_instance)
        """
        # Check if both hosts have ZFS capability configured
        if (
            source_host.zfs_capable
            and target_host.zfs_capable
            and source_host.zfs_dataset
            and target_host.zfs_dataset
        ):
            # Validate ZFS is actually available
            source_valid, _ = await self.zfs_transfer.validate_requirements(source_host)
            target_valid, _ = await self.zfs_transfer.validate_requirements(target_host)

            if source_valid and target_valid:
                self.logger.info("Using ZFS send/receive for optimal transfer")
                return "zfs", self.zfs_transfer

        # Fall back to rsync transfer
        self.logger.info("Using rsync transfer (ZFS not available on both hosts)")
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
        # Check for running containers
        check_cmd = ssh_cmd + [
            f"docker ps --filter 'label={DOCKER_COMPOSE_PROJECT}={shlex.quote(stack_name)}' --format '{{{{.Names}}}}'"
        ]

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # noqa: S603
                check_cmd, check=False, capture_output=True, text=True
            ),
        )

        if result.returncode != 0:
            self.logger.warning(
                "Failed to check container status",
                stack=stack_name,
                error=result.stderr,
            )
            return False, []

        running_containers = [
            name.strip() for name in result.stdout.strip().split("\n") if name.strip()
        ]

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
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda cmd=stop_cmd: subprocess.run(  # noqa: S603
                        cmd, check=False, capture_output=True, text=True
                    ),
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

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda cmd=full_cmd: subprocess.run(  # noqa: S603
                cmd, check=False, capture_output=True, text=True
            ),
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

        if transfer_type == "zfs":
            # ZFS transfer works on datasets, not individual paths
            # Use the configured datasets
            return await transfer_instance.transfer(
                source_host=source_host,
                target_host=target_host,
                source_path=source_host.appdata_path or "/opt/docker-appdata",
                target_path=target_host.appdata_path or "/opt/docker-appdata",
                source_dataset=source_host.zfs_dataset,
                target_dataset=target_host.zfs_dataset,
            )
        else:
            # Rsync transfer - need to create archive first
            if dry_run:
                return {
                    "success": True,
                    "message": "Dry run - would transfer via rsync",
                    "transfer_type": "rsync",
                }

            # Create archive on source
            ssh_cmd_source = build_ssh_command(source_host)
            archive_path = await self.archive_utils.create_archive(
                ssh_cmd_source, source_paths, f"{stack_name}_migration"
            )

            # Transfer archive to target with random suffix for security
            temp_suffix = os.urandom(8).hex()[:8]
            temp_dir = tempfile.mkdtemp(prefix="docker_mcp_migration_")
            target_archive_path = f"{temp_dir}/{stack_name}_migration_{temp_suffix}.tar.gz"

            transfer_result = await transfer_instance.transfer(
                source_host=source_host,
                target_host=target_host,
                source_path=archive_path,
                target_path=target_archive_path,
            )

            if transfer_result["success"]:
                # Extract on target
                ssh_cmd_target = build_ssh_command(target_host)
                extracted = await self.archive_utils.extract_archive(
                    ssh_cmd_target, target_archive_path, target_path
                )

                if extracted:
                    # Cleanup archive on target
                    await self.archive_utils.cleanup_archive(ssh_cmd_target, target_archive_path)

            # Cleanup archive on source
            await self.archive_utils.cleanup_archive(ssh_cmd_source, archive_path)

            return transfer_result

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

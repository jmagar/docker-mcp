"""Main migration orchestrator for Docker stack transfers."""

import asyncio
import json
import shlex
import subprocess
from typing import Any

import structlog

from ..config_loader import DockerHost
from ..exceptions import DockerMCPError
from ..transfer import ArchiveUtils, ContainerizedRsyncTransfer, RsyncTransfer
from .verification import MigrationVerifier
from .volume_parser import VolumeParser

logger = structlog.get_logger()


class MigrationError(DockerMCPError):
    """Migration operation failed."""

    pass


class MigrationManager:
    """Orchestrates Docker stack migrations between hosts using modular components."""

    def __init__(self, transfer_method: str = "rsync", docker_image: str = "instrumentisto/rsync-ssh:latest"):
        self.logger = logger.bind(component="migration_manager")
        self.transfer_method = transfer_method
        self.docker_image = docker_image

        # Initialize focused components
        self.volume_parser = VolumeParser()
        self.verifier = MigrationVerifier()

        # Initialize transfer methods
        self.archive_utils = ArchiveUtils()
        self.rsync_transfer = RsyncTransfer()
        self.containerized_rsync_transfer = ContainerizedRsyncTransfer(docker_image)

    async def choose_transfer_method(
        self, source_host: DockerHost, target_host: DockerHost
    ) -> tuple[str, Any]:
        """Choose transfer method based on configuration.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration

        Returns:
            Tuple of (transfer_type: str, transfer_instance)
        """
        if self.transfer_method == "containerized":
            self.logger.info(
                "Using containerized rsync transfer for permission handling",
                docker_image=self.docker_image
            )
            return "containerized_rsync", self.containerized_rsync_transfer
        else:
            self.logger.info("Using standard rsync transfer for universal compatibility")
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
        path_mappings: dict[str, str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Transfer data between hosts using the optimal method.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_paths: List of paths to transfer from source
            target_path: Target path on destination
            stack_name: Stack name for organization
            path_mappings: Optional mapping of specific source paths to target paths
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

        self.logger.info(
            "Selected transfer method for migration",
            transfer_type=transfer_type,
            source_host=source_host.hostname,
            target_host=target_host.hostname,
            source_paths_count=len(source_paths)
        )

        # Use rsync transfer - direct directory synchronization
        # Rsync transfer - direct directory synchronization (no archiving)
        if dry_run:
            return {
                "success": True,
                "message": f"Dry run - would transfer via {transfer_type}",
                "transfer_type": transfer_type,
            }

        # For rsync, directly sync each source path to target
        transfer_results = []
        overall_success = True

        target_dirs_created: set[str] = set()
        ssh_cmd_target = self.rsync_transfer.build_ssh_cmd(target_host)

        for source_path in source_paths:
            normalized_source_path = self._normalize_source_path(source_path, source_host)
            try:
                desired_target_path = (
                    path_mappings.get(source_path)
                    if path_mappings and source_path in path_mappings
                    else target_path
                )

                if desired_target_path not in target_dirs_created:
                    await self._ensure_remote_directory(ssh_cmd_target, desired_target_path)
                    target_dirs_created.add(desired_target_path)

                result = await transfer_instance.transfer(
                    source_host=source_host,
                    target_host=target_host,
                    source_path=normalized_source_path,
                    target_path=desired_target_path,
                    compress=True,
                    delete=False,  # Safety: don't delete target files
                )

                result.setdefault("metadata", {})["original_source_path"] = source_path
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
            "transfer_type": transfer_type,
            "transfers": transfer_results,
            "paths_transferred": len([r for r in transfer_results if r.get("success", False)]),
            "total_paths": len(source_paths),
        }

        if not overall_success:
            # Extract first error for detailed reporting
            first_error = next(
                (r.get("error") for r in transfer_results if r.get("error")),
                "Unknown transfer error"
            )
            final_result["error"] = first_error
            final_result["message"] = f"Transfer failed: {first_error}"
        else:
            final_result["message"] = (
                f"Successfully transferred {final_result['paths_transferred']} paths via {transfer_type}"
            )

        return final_result

    async def _ensure_remote_directory(self, ssh_cmd: list[str], directory: str) -> None:
        """Ensure a remote directory exists before data transfer."""

        mkdir_cmd = ssh_cmd + [f"mkdir -p {shlex.quote(directory)}"]

        result = await asyncio.to_thread(
            subprocess.run,  # nosec B603
            mkdir_cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            error_message = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise MigrationError(
                f"Failed to prepare remote directory {directory}: {error_message}"
            )

    def _normalize_source_path(self, source_path: str, source_host: DockerHost) -> str:
        """Normalize rsync source paths to local form when prefixed with the host name.

        Rsync accepts paths such as ``host:/remote/path`` to identify remote sources. During
        migration we execute rsync *on* the source host itself, so the source segment must be a
        local filesystem path. Some call sites pass values like ``"squirts:/mnt/data"`` which
        causes rsync to treat the path as a secondary remote target. This helper strips prefixes
        that simply repeat the current source host identifier while leaving genuine remote mounts
        (e.g. an NFS path ``nfs-server:/exports``) untouched.

        Args:
            source_path: Path string that may include an SCP-style host prefix.
            source_host: Host configuration for the migration source.

        Returns:
            Sanitized local path suitable for rsync execution on the source host.
        """

        if not source_path or ":" not in source_path:
            return source_path

        prefix, remainder = source_path.split(":", 1)

        # Only normalize absolute paths (rsync remote syntax). Leave anything else untouched.
        if not remainder.startswith("/"):
            return source_path

        normalized_prefix = prefix.strip("'\"")
        if "@" in normalized_prefix:
            normalized_prefix = normalized_prefix.split("@", 1)[1]
        normalized_prefix = normalized_prefix.strip("[]")

        host_aliases = {source_host.hostname}
        if "." in source_host.hostname:
            host_aliases.add(source_host.hostname.split(".", 1)[0])
        host_aliases.update({"localhost", "127.0.0.1"})

        if normalized_prefix in host_aliases:
            return remainder

        return source_path

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

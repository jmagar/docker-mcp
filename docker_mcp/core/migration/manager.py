"""Main migration orchestrator for Docker stack transfers."""

import asyncio
import subprocess
from typing import Any

import structlog

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
        """
        Initialize a MigrationManager instance and create its component objects.
        
        Creates a component-scoped logger bound to "migration_manager" and instantiates the helper components used by the manager:
        - VolumeParser: parses compose volumes and paths.
        - MigrationVerifier: performs pre- and post-migration verification.
        - ArchiveUtils: creates, extracts, and cleans up archives used for rsync transfers.
        - RsyncTransfer: handles rsync-based transfers.
        - ZFSTransfer: handles ZFS send/receive transfers.
        """
        self.logger = logger.bind(component="migration_manager")
        
        # Initialize focused components
        self.volume_parser = VolumeParser()
        self.verifier = MigrationVerifier()
        
        # Initialize transfer methods
        self.archive_utils = ArchiveUtils()
        self.rsync_transfer = RsyncTransfer()
        self.zfs_transfer = ZFSTransfer()
    
    async def choose_transfer_method(
        self,
        source_host: DockerHost,
        target_host: DockerHost
    ) -> tuple[str, Any]:
        """
        Select the best transfer mechanism between the source and target hosts.
        
        If both hosts declare ZFS capability and dataset names, this method asynchronously validates ZFS requirements on each host and returns the ZFS transfer instance when validation succeeds. Otherwise it falls back to the rsync transfer.
        
        Returns:
            tuple[str, Any]: (transfer_type, transfer_instance) where `transfer_type` is
            either `"zfs"` (when ZFS is usable on both ends) or `"rsync"` (fallback), and
            `transfer_instance` is the corresponding transfer handler.
        """
        # Check if both hosts have ZFS capability configured
        if (source_host.zfs_capable and target_host.zfs_capable and 
            source_host.zfs_dataset and target_host.zfs_dataset):
            
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
        """
        Check whether all containers belonging to a Compose stack on a remote host are stopped; optionally force-kill running containers.
        
        Parameters:
            ssh_cmd (list[str]): SSH command parts to execute remote commands (e.g. ['ssh', 'user@host', ...]).
            stack_name (str): Compose project/stack name used to filter containers.
            force_stop (bool): If True, issue `docker kill` for any running containers, wait 10 seconds, and recheck.
        
        Returns:
            tuple[bool, list[str]]: (all_stopped, running_containers)
                - all_stopped: True if no containers are running for the stack; False otherwise or if the remote check failed.
                - running_containers: list of container names currently running (empty on success or on remote-check failure).
        
        Notes:
            - The function performs remote commands via subprocess.run executed in a thread pool executor.
            - If the initial remote check returns a non-zero exit code, the function logs a warning and returns (False, []).
            - When force_stop is True, containers are killed on the remote host and the function waits 10 seconds before re-checking.
        """
        # Check for running containers
        check_cmd = ssh_cmd + [
            f"docker ps --filter 'label=com.docker.compose.project={stack_name}' --format '{{{{.Names}}}}'"
        ]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
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
                stop_cmd = ssh_cmd + [f"docker kill {container}"]
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(  # nosec B603
                        stop_cmd, check=False, capture_output=True, text=True
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
        """
        Ensure the target host contains a stack-specific appdata directory and return its path.
        
        This runs `mkdir -p {appdata_path}/{stack_name}` on the remote host using the provided SSH command. If the remote command fails, a MigrationError is raised and includes the remote stderr.
        
        Parameters:
            appdata_path (str): Base application data directory on the target host.
            stack_name (str): Stack name used to create the subdirectory.
        
        Returns:
            str: Full path to the created stack-specific appdata directory.
        
        Raises:
            MigrationError: If the remote directory creation command returns a non-zero exit code.
        """
        # Create stack-specific directory
        stack_dir = f"{appdata_path}/{stack_name}"
        mkdir_cmd = f"mkdir -p {stack_dir}"
        full_cmd = ssh_cmd + [mkdir_cmd]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                full_cmd, check=False, capture_output=True, text=True
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
        dry_run: bool = False
    ) -> dict[str, Any]:
        """
        Transfer application data from a source host to a target host using the best available method (ZFS send/receive when both hosts support it and validate, otherwise rsync with an on-disk archive).
        
        When ZFS is chosen this calls the ZFS transfer implementation with the hosts' appdata paths and datasets. When rsync is chosen this will:
        - create a compressed archive of the provided source_paths on the source host,
        - transfer that archive to the target,
        - extract the archive into target_path on the target,
        - remove temporary archives on both source and target.
        
        Parameters that require extra context:
        - source_paths: list of filesystem paths on the source to include in the migration; if empty no transfer is performed.
        - dry_run: when True and rsync would be chosen, the function returns a successful dry-run result without creating archives or transferring.
        
        Returns:
        A dictionary describing the transfer outcome. Common keys:
        - "success" (bool): whether the operation succeeded.
        - "message" (str): human-readable status or error message.
        - "transfer_type" (str): one of "zfs", "rsync" or "none".
        Additional keys may be present as provided by the chosen transfer implementation.
        """
        if not source_paths:
            return {"success": True, "message": "No data to transfer", "transfer_type": "none"}
        
        # Choose transfer method
        transfer_type, transfer_instance = await self.choose_transfer_method(source_host, target_host)
        
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
                return {"success": True, "message": "Dry run - would transfer via rsync", "transfer_type": "rsync"}
            
            # Create archive on source
            ssh_cmd_source = self._build_ssh_cmd(source_host)
            archive_path = await self.archive_utils.create_archive(
                ssh_cmd_source, source_paths, f"{stack_name}_migration"
            )
            
            # Transfer archive to target
            transfer_result = await transfer_instance.transfer(
                source_host=source_host,
                target_host=target_host,
                source_path=archive_path,
                target_path=f"/tmp/{stack_name}_migration.tar.gz"
            )
            
            if transfer_result["success"]:
                # Extract on target
                ssh_cmd_target = self._build_ssh_cmd(target_host)
                extracted = await self.archive_utils.extract_archive(
                    ssh_cmd_target, 
                    f"/tmp/{stack_name}_migration.tar.gz",
                    target_path
                )
                
                if extracted:
                    # Cleanup archive on target
                    await self.archive_utils.cleanup_archive(
                        ssh_cmd_target, f"/tmp/{stack_name}_migration.tar.gz"
                    )
            
            # Cleanup archive on source
            await self.archive_utils.cleanup_archive(ssh_cmd_source, archive_path)
            
            return transfer_result
    
    def _build_ssh_cmd(self, host: DockerHost) -> list[str]:
        """
        Builds an SSH command argument list for connecting to the given host.
        
        Includes `-o StrictHostKeyChecking=no`, adds `-i <identity_file>` if the host provides an identity file, and adds `-p <port>` when the port is not the default 22. Returns a list of command arguments suitable for passing to subprocess calls.
        """
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        if host.identity_file:
            ssh_cmd.extend(["-i", host.identity_file])
        if host.port != 22:
            ssh_cmd.extend(["-p", str(host.port)])
        ssh_cmd.append(f"{host.user}@{host.hostname}")
        return ssh_cmd
    
    # Delegate methods to focused components
    async def parse_compose_volumes(self, compose_content: str, source_appdata_path: str = None) -> dict[str, Any]:
        """
        Parse Docker Compose content to extract volume definitions for migration.
        
        Delegates to VolumeParser.parse_compose_volumes to produce a mapping of named volumes and bind mounts found in the provided compose YAML.
        
        Parameters:
            compose_content (str): Raw Docker Compose YAML content.
            source_appdata_path (str, optional): Base application data path on the source host used to resolve relative or templated volume paths.
        
        Returns:
            dict[str, Any]: Parsed volume information suitable for migration (volume names to location/metadata).
        """
        return await self.volume_parser.parse_compose_volumes(compose_content, source_appdata_path)
    
    async def get_volume_locations(self, ssh_cmd: list[str], named_volumes: list[str]) -> dict[str, str]:
        """
        Return a mapping of named Docker volumes to their host paths on the remote host.
        
        Given an SSH command (as a list of ssh subprocess arguments) and a list of compose volume names,
        query the remote host for each volume's filesystem location and return a dict mapping
        volume name -> absolute host path.
        Parameters:
            ssh_cmd (list[str]): SSH command token list to execute remote checks (e.g. ['ssh', 'user@host', ...]).
            named_volumes (list[str]): List of volume names to resolve on the remote host.
        
        Returns:
            dict[str, str]: Mapping from each volume name in `named_volumes` to its resolved host path.
        """
        return await self.volume_parser.get_volume_locations(ssh_cmd, named_volumes)
    
    def update_compose_for_migration(
        self, 
        compose_content: str, 
        old_paths: dict[str, str], 
        new_base_path: str, 
        target_appdata_path: str = None
    ) -> str:
        """
        Update Docker Compose content so volume paths point to a new base application-data directory for migration.
        
        Rewrites host volume paths in the provided `compose_content` by replacing occurrences from `old_paths` (mapping of original volume names to their host paths) with equivalents under `new_base_path`. If `target_appdata_path` is provided, it is used as the explicit target base for reconstructed paths instead of `new_base_path`.
        
        Parameters:
            compose_content (str): Raw Docker Compose YAML/text to modify.
            old_paths (dict[str, str]): Mapping of volume identifiers to their current host paths to be rewritten.
            new_base_path (str): Base path on the target host where volumes should be relocated.
            target_appdata_path (str, optional): Explicit appdata base path for the target; when set, this overrides `new_base_path` for constructing new volume paths.
        
        Returns:
            str: The updated Compose content with volume paths adjusted for the target host.
        """
        return self.volume_parser.update_compose_for_migration(
            compose_content, old_paths, new_base_path, target_appdata_path
        )
    
    async def create_source_inventory(self, ssh_cmd: list[str], volume_paths: list[str]) -> dict[str, Any]:
        """
        Create an inventory of the given source volume paths by delegating to MigrationVerifier.
        
        Collects metadata about each path on the source host (as produced by
        MigrationVerifier.create_source_inventory) to be used during migration.
        
        Parameters:
            ssh_cmd (list[str]): SSH command list to run remote inspection commands (e.g., ['ssh', 'user@host', ...]).
            volume_paths (list[str]): Absolute paths of volumes on the source host to inventory.
        
        Returns:
            dict[str, Any]: Mapping of each inspected path to its collected metadata as returned by the verifier.
        """
        return await self.verifier.create_source_inventory(ssh_cmd, volume_paths)
    
    async def verify_migration_completeness(
        self, 
        ssh_cmd: list[str], 
        source_inventory: dict[str, Any], 
        target_path: str
    ) -> dict[str, Any]:
        """
        Verify that the migration completed successfully by comparing the source inventory with the data present on the target.
        
        This delegates to MigrationVerifier.verify_migration_completeness to perform the actual checks over SSH. It compares the files/volumes described in `source_inventory` against the contents found under `target_path` on the remote host addressed by `ssh_cmd` and returns whatever structured verification report the verifier produces.
        
        Parameters:
            ssh_cmd (list[str]): SSH command (e.g., ['ssh', 'user@host', ...]) used to run remote checks.
            source_inventory (dict[str, Any]): Inventory produced for the source describing expected files/volumes and metadata.
            target_path (str): Path on the target host where the migrated data should reside.
        
        Returns:
            dict[str, Any]: Verification report (typically includes keys such as 'success' (bool) and 'details' or similar diagnostic information).
        """
        return await self.verifier.verify_migration_completeness(
            ssh_cmd, source_inventory, target_path
        )
    
    async def verify_container_integration(
        self, 
        ssh_cmd: list[str], 
        stack_name: str, 
        expected_appdata_path: str, 
        expected_volumes: list[str]
    ) -> dict[str, Any]:
        """
        Verify that containers in the migrated stack are integrated with the expected application-data path and volumes on the target host.
        
        Parameters:
            ssh_cmd (list[str]): SSH command to execute remote inspection commands (e.g., as returned by _build_ssh_cmd).
            stack_name (str): Compose project/stack name to inspect on the target.
            expected_appdata_path (str): Expected base path for application data on the target host.
            expected_volumes (list[str]): List of volume paths expected to be mounted into containers.
        
        Returns:
            dict[str, Any]: Verification result from MigrationVerifier.verify_container_integration containing status and diagnostic details.
        """
        return await self.verifier.verify_container_integration(
            ssh_cmd, stack_name, expected_appdata_path, expected_volumes
        )
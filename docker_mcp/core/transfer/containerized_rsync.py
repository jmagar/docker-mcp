"""Containerized rsync transfer implementation using Docker containers."""

import asyncio
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import structlog

from ..config_loader import DockerHost
from ..exceptions import DockerMCPError
from ..settings import RSYNC_TIMEOUT, DOCKER_CLI_TIMEOUT, CONTAINER_PULL_TIMEOUT
from .base import BaseTransfer

logger = structlog.get_logger()


class ContainerizedRsyncError(DockerMCPError):
    """Containerized rsync transfer operation failed."""

    pass


class ContainerizedRsyncTransfer(BaseTransfer):
    """Transfer files between hosts using rsync in Docker containers.

    This implementation solves permission issues by running rsync inside
    containers with the appropriate capabilities to read all files.
    """

    def __init__(self, docker_image: str = "instrumentisto/rsync-ssh:latest"):
        super().__init__()
        self.logger = logger.bind(component="containerized_rsync")
        self.docker_image = docker_image

    def get_transfer_type(self) -> str:
        """Get the name/type of this transfer method."""
        return "containerized_rsync"

    async def validate_requirements(self, host: DockerHost) -> tuple[bool, str]:
        """Validate that Docker is available and the rsync image can be pulled.

        Args:
            host: Host configuration to validate (for local Docker daemon)

        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        # Check Docker daemon connectivity on the host via SSH
        try:
            ssh_cmd = self.build_ssh_cmd(host)
            check_cmd = ssh_cmd + ["docker", "version", "--format", "json"]

            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603 - validated SSH + Docker command
                check_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=DOCKER_CLI_TIMEOUT,
            )

            if result.returncode != 0:
                return False, f"Docker daemon not accessible: {result.stderr.strip()}"

            # Verify we can parse version info
            try:
                version_info = json.loads(result.stdout)
                if "Server" not in version_info:
                    return False, "Docker server not running or not accessible"
            except json.JSONDecodeError as e:
                return False, f"Failed to parse Docker version output: {e}"

        except subprocess.TimeoutExpired:
            return False, f"Docker version check timed out after {DOCKER_CLI_TIMEOUT}s"
        except Exception as e:
            return False, f"Docker daemon check failed: {str(e)}"

        # Check if rsync image is available or can be pulled on the host via SSH
        try:
            ssh_cmd = self.build_ssh_cmd(host)
            inspect_cmd = ssh_cmd + ["docker", "image", "inspect", self.docker_image]

            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603 - validated SSH + Docker command
                inspect_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=DOCKER_CLI_TIMEOUT,
            )

            if result.returncode == 0:
                # Image already exists locally
                return True, ""

            # Try to pull the image on the host via SSH
            self.logger.info("Pulling rsync Docker image", image=self.docker_image, host=host.hostname)
            ssh_cmd = self.build_ssh_cmd(host)
            pull_cmd = ssh_cmd + ["docker", "pull", self.docker_image]

            pull_result = await asyncio.to_thread(
                subprocess.run,  # nosec B603 - validated SSH + Docker command
                pull_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=CONTAINER_PULL_TIMEOUT,
            )

            if pull_result.returncode != 0:
                return False, f"Failed to pull rsync image: {pull_result.stderr.strip()}"

            return True, ""

        except subprocess.TimeoutExpired:
            return False, "Docker image check/pull timed out"
        except Exception as e:
            return False, f"Docker image validation failed: {str(e)}"

    def _build_docker_run_cmd(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_path: str,
        target_path: str,
        rsync_args: list[str],
    ) -> list[str]:
        """Build Docker run command with proper volume mounts and security settings.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_path: Path on source host
            target_path: Path on target host
            rsync_args: Rsync command arguments

        Returns:
            Complete Docker run command as list of strings
        """
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", "host",  # Use host networking for best performance
            "--cap-add", "DAC_OVERRIDE",  # Read files regardless of permissions
            "--cap-add", "CHOWN",  # Set proper permissions in container
        ]

        # Mount source data directory as read-only volume (CRITICAL FIX)
        docker_cmd.extend(["-v", f"{source_path}:/data/source:ro"])

        # Mount SSH keys if specified
        if source_host.identity_file is not None:
            try:
                source_key_path = Path(source_host.identity_file).expanduser().resolve()
                if not source_key_path.exists():
                    self.logger.warning(
                        "Source SSH key file not found",
                        key_path=str(source_key_path),
                        host=source_host.hostname
                    )
                docker_cmd.extend(["-v", f"{source_key_path}:/source_key:ro"])
            except (OSError, ValueError) as e:
                raise ContainerizedRsyncError(f"Invalid source SSH key path: {e}") from e

        if target_host.identity_file is not None:
            try:
                target_key_path = Path(target_host.identity_file).expanduser().resolve()
                if not target_key_path.exists():
                    self.logger.warning(
                        "Target SSH key file not found",
                        key_path=str(target_key_path),
                        host=target_host.hostname
                    )
                docker_cmd.extend(["-v", f"{target_key_path}:/target_key:ro"])
            except (OSError, ValueError) as e:
                raise ContainerizedRsyncError(f"Invalid target SSH key path: {e}") from e
        else:
            # If no specific key, mount the entire .ssh directory
            ssh_dir = Path.home() / ".ssh"
            if ssh_dir.exists():
                docker_cmd.extend(["-v", f"{ssh_dir}:/root/.ssh:ro"])

        # Set container working directory
        docker_cmd.extend(["-w", "/data"])

        # Add the Docker image
        docker_cmd.append(self.docker_image)

        # Build the command to run inside the container
        container_cmd = self._build_container_command(
            source_host, target_host, source_path, target_path, rsync_args
        )

        # Add the container command
        docker_cmd.extend(["/bin/sh", "-c", container_cmd])

        return docker_cmd

    def _build_container_command(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_path: str,
        target_path: str,
        rsync_args: list[str],
    ) -> str:
        """Build the command to run inside the container.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_path: Path on source host
            target_path: Path on target host
            rsync_args: Rsync command arguments

        Returns:
            Shell command string to execute in container
        """
        commands = []

        # Prepare shared SSH options up front so both identity branches can append
        target_ssh_opts = [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
        ]

        if target_host.port != 22:
            target_ssh_opts.extend(["-p", str(target_host.port)])

        # Set up SSH keys with proper permissions
        if source_host.identity_file is not None:
            commands.extend([
                "cp /source_key /tmp/source_key",
                "chmod 600 /tmp/source_key",
            ])

        if target_host.identity_file is not None:
            commands.extend([
                "cp /target_key /tmp/target_key",
                "chmod 600 /tmp/target_key",
            ])
        else:
            # Copy SSH directory and fix ownership/permissions for container root user
            commands.extend([
                "cp -r /root/.ssh /tmp/.ssh 2>/dev/null || mkdir -p /tmp/.ssh",
                "chown -R root:root /tmp/.ssh 2>/dev/null || true",
                "chmod 700 /tmp/.ssh",
                "chmod 600 /tmp/.ssh/* 2>/dev/null || true",
            ])
            # Use copied SSH config file instead of mounted read-only version
            target_ssh_opts.extend(["-F", "/tmp/.ssh/config"])

        # Build rsync command that runs directly in container (simplified architecture)
        target_url = f"{target_host.user}@{target_host.hostname}:{shlex.quote(target_path)}"

        # Use mounted source data instead of SSH connection to source host
        rsync_cmd = ["rsync"] + rsync_args

        # Build the rsync command with SSH options
        rsync_cmd.extend(["/data/source/", target_url])

        if target_host.identity_file is not None:
            # Use specific identity file
            target_ssh_opts.extend(["-i", "/tmp/target_key"])
            ssh_command = f"ssh {' '.join(target_ssh_opts)}"
            rsync_cmd.insert(-2, "-e")
            rsync_cmd.insert(-2, ssh_command)

            # Execute rsync directly
            commands.append(shlex.join(rsync_cmd))
        else:
            # Find available SSH key and build rsync command dynamically
            commands.append("if [ -f /tmp/.ssh/id_ed25519 ]; then SSH_KEY=/tmp/.ssh/id_ed25519; elif [ -f /tmp/.ssh/id_rsa ]; then SSH_KEY=/tmp/.ssh/id_rsa; elif [ -f /tmp/.ssh/id_ecdsa ]; then SSH_KEY=/tmp/.ssh/id_ecdsa; else echo 'No SSH key found' && exit 1; fi")

            # Build rsync command with dynamic SSH key
            target_ssh_opts_str = " ".join(target_ssh_opts)
            rsync_base_cmd = " ".join(rsync_args)
            commands.append(f'rsync {rsync_base_cmd} -e "ssh -i $SSH_KEY {target_ssh_opts_str}" /data/source/ {target_url}')

        # Join all commands with &&
        final_command = " && ".join(commands)


        return final_command

    async def transfer(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_path: str,
        target_path: str,
        compress: bool = True,
        delete: bool = False,
        dry_run: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        """Transfer files between hosts using containerized rsync.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            source_path: Path on source host
            target_path: Path on target host
            compress: Use compression during transfer
            delete: Delete files on target not in source
            dry_run: Perform dry run only
            **kwargs: Additional options (ignored)

        Returns:
            Transfer result with statistics
        """
        # Build rsync options
        rsync_opts = ["-avP", "--stats"]
        if compress:
            rsync_opts.extend(["-z", "--compress-level=6"])
            # Try to use zstd for better compression performance if available
            rsync_opts.append("--compress-choice=zstd")
        if delete:
            rsync_opts.append("--delete")
        if dry_run:
            rsync_opts.append("--dry-run")

        # Build Docker command
        docker_cmd = self._build_docker_run_cmd(
            source_host, target_host, source_path, target_path, rsync_opts
        )

        self.logger.info(
            "Starting containerized rsync transfer",
            source_host=source_host.hostname,
            source_path=source_path,
            target_host=target_host.hostname,
            target_path=target_path,
            compress=compress,
            delete=delete,
            dry_run=dry_run,
            docker_image=self.docker_image,
        )

        # Execute containerized rsync with timeout - SSH to source host to run Docker there
        try:
            # Build SSH command to execute Docker on source host
            ssh_cmd = self.build_ssh_cmd(source_host)
            full_cmd = ssh_cmd + [shlex.join(docker_cmd)]


            result = await asyncio.to_thread(
                subprocess.run,  # nosec B603 - validated SSH + Docker command
                full_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=RSYNC_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            raise ContainerizedRsyncError(f"Containerized rsync timed out after {RSYNC_TIMEOUT}s") from e

        if result.returncode != 0:
            # Don't truncate - we need full error details
            stderr_output = result.stderr or ""
            stdout_output = result.stdout or ""

            # Log full command for debugging
            self.logger.error(
                "Containerized rsync command failed",
                return_code=result.returncode,
                stderr=stderr_output[:1000],  # Log more but not everything
                stdout=stdout_output[:1000],
                source_host=source_host.hostname,
                target_host=target_host.hostname,
                source_path=source_path,
                target_path=target_path
            )

            error_msg = (
                f"Containerized rsync failed (exit {result.returncode}): "
                f"{stderr_output or stdout_output}. "
                f"Source: {source_host.hostname}:{source_path}, Target: {target_host.hostname}:{target_path}"
            )
            raise ContainerizedRsyncError(error_msg)

        # Parse rsync output for statistics
        stats = self._parse_stats(result.stdout)

        return {
            "success": True,
            "transfer_type": "containerized_rsync",
            "source": source_path,
            "source_host": source_host.hostname,
            "source_path": source_path,
            "target": f"{target_host.hostname}:{target_path}",
            "target_host": target_host.hostname,
            "target_path": target_path,
            "stats": stats,
            "dry_run": dry_run,
            "output": result.stdout,
            "docker_image": self.docker_image,
        }

    def _parse_stats(self, output: str) -> dict[str, Any]:
        """Parse rsync output for transfer statistics.

        Args:
            output: Rsync command output

        Returns:
            Dictionary with transfer statistics
        """
        stats = {
            "files_transferred": 0,
            "total_size": 0,
            "transfer_rate": "",
            "speedup": 1.0,
        }

        # Parse rsync summary statistics
        for line in output.split("\n"):
            if (
                "Number of files transferred:" in line
                or "Number of regular files transferred:" in line
            ):
                match = re.search(r"(\d+)", line)
                if match:
                    stats["files_transferred"] = int(match.group(1))
            elif "Total transferred file size:" in line:
                match = re.search(r"([\d,]+) bytes", line)
                if match:
                    stats["total_size"] = int(match.group(1).replace(",", ""))
            elif "sent" in line and "received" in line:
                # Parse transfer rate from summary line
                match = re.search(r"(\d+\.?\d*) (\w+/sec)", line)
                if match:
                    stats["transfer_rate"] = f"{match.group(1)} {match.group(2)}"
            elif "speedup is" in line:
                match = re.search(r"speedup is (\d+\.?\d*)", line)
                if match:
                    stats["speedup"] = float(match.group(1))

        return stats

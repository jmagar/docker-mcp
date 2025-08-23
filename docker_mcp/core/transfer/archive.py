"""Archive utilities for volume data compression and extraction."""

import asyncio
import subprocess
from datetime import datetime
from typing import Any

import structlog

from ..exceptions import DockerMCPError

logger = structlog.get_logger()


class ArchiveError(DockerMCPError):
    """Archive operation failed."""
    pass


class ArchiveUtils:
    """Utilities for creating and managing tar.gz archives."""
    
    # Default exclusion patterns for archiving
    DEFAULT_EXCLUSIONS = [
        "node_modules/",
        ".git/",
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
        "*.log",
        "*.tmp",
        "*.temp",
        "cache/",
        "temp/",
        "tmp/",
        ".cache/",
        "*.swp",
        "*.swo",
        ".DS_Store",
        "Thumbs.db",
        "*.pid",
        "*.lock",
        ".venv/",
        "venv/",
        "env/",
        "dist/",
        "build/",
        ".next/",
        ".nuxt/",
        "coverage/",
        ".coverage",
        "*.bak",
        "*.backup",
        "*.old",
    ]
    
    def __init__(self):
        self.logger = logger.bind(component="archive_utils")
    
    async def create_archive(
        self,
        ssh_cmd: list[str],
        volume_paths: list[str],
        archive_name: str,
        temp_dir: str = "/tmp",
        exclusions: list[str] | None = None,
    ) -> str:
        """Create tar.gz archive of volume data on remote host.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            volume_paths: List of paths to archive
            archive_name: Name for the archive file
            temp_dir: Temporary directory for archive creation
            exclusions: Additional exclusion patterns
            
        Returns:
            Path to created archive on remote host
        """
        if not volume_paths:
            raise ArchiveError("No volumes to archive")
        
        # Combine default and custom exclusions
        all_exclusions = self.DEFAULT_EXCLUSIONS.copy()
        if exclusions:
            all_exclusions.extend(exclusions)
        
        # Build exclusion flags for tar
        exclude_flags = []
        for pattern in all_exclusions:
            exclude_flags.extend(["--exclude", pattern])
        
        # Create timestamped archive name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_file = f"{temp_dir}/{archive_name}_{timestamp}.tar.gz"
        
        # Build tar command
        tar_cmd = ["tar", "czf", archive_file] + exclude_flags + volume_paths
        
        # Execute tar command on remote host
        remote_cmd = " ".join(tar_cmd)
        full_cmd = ssh_cmd + [remote_cmd]
        
        self.logger.info(
            "Creating volume archive",
            archive_file=archive_file,
            paths=volume_paths,
            exclusions=len(all_exclusions),
        )
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                full_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode != 0:
            raise ArchiveError(f"Failed to create archive: {result.stderr}")
        
        return archive_file
    
    async def verify_archive(self, ssh_cmd: list[str], archive_path: str) -> bool:
        """Verify archive integrity.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            archive_path: Path to archive file
            
        Returns:
            True if archive is valid, False otherwise
        """
        verify_cmd = ssh_cmd + [f"tar tzf {archive_path} > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                verify_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        return "OK" in result.stdout
    
    async def extract_archive(
        self,
        ssh_cmd: list[str],
        archive_path: str,
        extract_dir: str,
    ) -> bool:
        """Extract archive to specified directory.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            archive_path: Path to archive file
            extract_dir: Directory to extract to
            
        Returns:
            True if extraction successful, False otherwise
        """
        extract_cmd = ssh_cmd + [f"cd {extract_dir} && tar xzf {archive_path}"]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                extract_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode == 0:
            self.logger.info("Archive extracted successfully", archive=archive_path, destination=extract_dir)
            return True
        else:
            self.logger.error("Archive extraction failed", archive=archive_path, error=result.stderr)
            return False
    
    async def cleanup_archive(self, ssh_cmd: list[str], archive_path: str) -> None:
        """Remove archive file.
        
        Args:
            ssh_cmd: SSH command parts for remote execution
            archive_path: Path to archive file to remove
        """
        cleanup_cmd = ssh_cmd + [f"rm -f {archive_path}"]
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                cleanup_cmd, check=False, capture_output=True, text=True
            ),
        )
        
        if result.returncode == 0:
            self.logger.debug("Archive cleaned up", archive=archive_path)
        else:
            self.logger.warning("Failed to cleanup archive", archive=archive_path, error=result.stderr)
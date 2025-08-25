"""Archive utilities for volume data compression and extraction."""

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ..exceptions import DockerMCPError
from ..safety import MigrationSafety

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
        """
        Initialize ArchiveUtils.
        
        Creates a component-scoped logger (component="archive_utils") and instantiates
        a MigrationSafety object used for performing safety checks during archive cleanup.
        """
        self.logger = logger.bind(component="archive_utils")
        self.safety = MigrationSafety()
    
    def _find_common_parent(self, paths: list[str]) -> tuple[str, list[str]]:
        """
        Return a common parent directory and a list of paths relative to that parent suitable for tar'ing.
        
        Given a list of absolute filesystem paths, compute a parent directory that is the longest common path prefix and produce relative paths of each input with respect to that parent. This is intended to be used with tar's -C <parent> <relative_paths> invocation.
        
        Behavior:
        - If `paths` is empty, returns ("/", []).
        - If `paths` contains a single path, the parent is that path and the returned relative path is ["."], which archives the contents of that directory.
        - If multiple paths are provided, the parent is the longest common path prefix ("/" when there is no common directory). Relative paths are computed with respect to the parent; when the parent is root, returned relative paths have any leading "/" removed so they are suitable for tar.
        - On unexpected errors while computing the common parent, falls back to parent "/" and returns paths stripped of a leading "/" if present.
        
        Returns:
            tuple[str, list[str]]: (parent_directory, relative_paths_for_contents)
        """
        if not paths:
            return "/", []
        
        # Convert to Path objects
        path_objects = [Path(p) for p in paths]
        
        # For archiving, we want to archive the CONTENTS of directories
        # So we use the path itself as the parent and archive everything inside with "*"
        if len(path_objects) == 1:
            # Single path - use the path itself as parent, archive its contents
            parent = str(path_objects[0])
            # Use "." to archive all contents of the directory
            relative_paths = ["."]
        else:
            # Multiple paths - find their common parent
            try:
                # Find the longest common prefix
                common_parts = []
                min_parts = min(len(p.parts) for p in path_objects)
                
                for i in range(min_parts):
                    part = path_objects[0].parts[i]
                    if all(p.parts[i] == part for p in path_objects):
                        common_parts.append(part)
                    else:
                        break
                
                # Build parent from common parts
                if common_parts:
                    if len(common_parts) == 1 and common_parts[0] == "/":
                        parent = "/"
                    else:
                        parent = "/" + "/".join(common_parts[1:])
                else:
                    parent = "/"
                
                # Calculate relative paths from parent
                relative_paths = []
                parent_path = Path(parent)
                for p in path_objects:
                    try:
                        if parent_path == Path("/"):
                            # Remove leading slash for relative path from root
                            rel_path = str(p)[1:] if str(p).startswith("/") else str(p)
                        else:
                            rel_path = str(p.relative_to(parent_path))
                        relative_paths.append(rel_path)
                    except ValueError:
                        # Path is not relative to parent, use absolute
                        relative_paths.append(str(p))
                        
            except Exception:
                # Fallback to using root as parent
                parent = "/"
                relative_paths = [str(p)[1:] if str(p).startswith("/") else str(p) for p in path_objects]
        
        return parent, relative_paths
    
    async def create_archive(
        self,
        ssh_cmd: list[str],
        volume_paths: list[str],
        archive_name: str,
        temp_dir: str = "/tmp",
        exclusions: list[str] | None = None,
    ) -> str:
        """
        Create a gzip-compressed tar archive of the given volume paths on a remote host via SSH and return its remote path.
        
        The function determines a common parent directory for the provided volume paths and archives the relative paths (using tar's -C) while applying default and optional exclusion patterns. The archive is created in temp_dir with archive_name suffixed by a timestamp.
        
        Parameters:
            volume_paths (list[str]): Absolute paths to include in the archive.
            archive_name (str): Base name for the archive file (timestamp and .tar.gz are appended).
            temp_dir (str, optional): Directory on the remote host where the archive will be created. Defaults to "/tmp".
            exclusions (list[str] | None, optional): Additional tar exclude patterns to append to the module's default exclusions.
        
        Returns:
            str: Full path to the created archive on the remote host.
        
        Raises:
            ArchiveError: If volume_paths is empty or if the remote tar command fails.
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
        
        # Find common parent and convert to relative paths
        common_parent, relative_paths = self._find_common_parent(volume_paths)
        
        # Build tar command with -C to change directory
        import shlex
        tar_cmd = ["tar", "czf", archive_file, "-C", common_parent] + exclude_flags + relative_paths
        
        # Execute tar command on remote host
        remote_cmd = " ".join(map(shlex.quote, tar_cmd))
        full_cmd = ssh_cmd + [remote_cmd]
        
        self.logger.info(
            "Creating volume archive",
            archive_file=archive_file,
            parent_dir=common_parent,
            relative_paths=relative_paths,
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
        """
        Verify the integrity of a tar.gz archive on the remote host.
        
        Runs a remote `tar tzf` (list) against the specified archive via the provided SSH command and returns True if the archive can be read successfully.
        
        Parameters:
            ssh_cmd (list[str]): SSH command and arguments to run the remote check (e.g., ["ssh", "user@host"]).
            archive_path (str): Remote path to the tar.gz archive to verify.
        
        Returns:
            bool: True if the archive is valid and readable on the remote host, False otherwise.
        """
        import shlex
        verify_cmd = ssh_cmd + [f"tar tzf {shlex.quote(archive_path)} > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"]
        
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
        """
        Extract a tar.gz archive on the remote host into the specified directory.
        
        Runs `tar xzf <archive_path> -C <extract_dir>` over the provided SSH command and returns True if the remote tar command succeeds (exit code 0), otherwise False.
        
        Parameters:
            archive_path (str): Path to the archive file on the remote host.
            extract_dir (str): Target directory on the remote host where the archive will be extracted.
        
        Returns:
            bool: True if extraction succeeded, False if it failed.
        """
        import shlex
        extract_cmd = ssh_cmd + [f"tar xzf {shlex.quote(archive_path)} -C {shlex.quote(extract_dir)}"]
        
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
        """
        Safely remove a remote archive file using the configured MigrationSafety and log the outcome.
        
        Attempts to remove the archive at the given path by delegating to MigrationSafety.safe_cleanup_archive; logs whether cleanup succeeded, failed, or encountered an error. Exceptions raised during the safety check are caught and logged; the method does not raise.
        """
        try:
            success, message = await self.safety.safe_cleanup_archive(
                ssh_cmd, archive_path, "Archive cleanup after migration"
            )
            
            if success:
                self.logger.debug("Archive cleaned up safely", archive=archive_path, message=message)
            else:
                self.logger.warning("Archive cleanup failed", archive=archive_path, error=message)
                
        except Exception as e:
            self.logger.error("Archive cleanup error", archive=archive_path, error=str(e))
"""Archive utilities for volume data compression and extraction."""

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

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
        self.logger = logger.bind(component="archive_utils")
        self.safety = MigrationSafety()

    def _find_common_parent(self, paths: list[str]) -> tuple[str, list[str]]:
        """Find common parent directory and relative paths for archiving contents.

        Args:
            paths: List of absolute paths

        Returns:
            Tuple of (common_parent, relative_paths_for_contents)
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
                relative_paths = [
                    str(p)[1:] if str(p).startswith("/") else str(p) for p in path_objects
                ]

        return parent, relative_paths

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
        """Verify archive integrity.

        Args:
            ssh_cmd: SSH command parts for remote execution
            archive_path: Path to archive file

        Returns:
            True if archive is valid, False otherwise
        """
        import shlex

        verify_cmd = ssh_cmd + [
            f"tar tzf {shlex.quote(archive_path)} > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"
        ]

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
        import shlex

        extract_cmd = ssh_cmd + [
            f"tar xzf {shlex.quote(archive_path)} -C {shlex.quote(extract_dir)}"
        ]

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(  # nosec B603
                extract_cmd, check=False, capture_output=True, text=True
            ),
        )

        if result.returncode == 0:
            self.logger.info(
                "Archive extracted successfully", archive=archive_path, destination=extract_dir
            )
            return True
        else:
            self.logger.error(
                "Archive extraction failed", archive=archive_path, error=result.stderr
            )
            return False

    async def cleanup_archive(self, ssh_cmd: list[str], archive_path: str) -> None:
        """Remove archive file with safety validation.

        Args:
            ssh_cmd: SSH command parts for remote execution
            archive_path: Path to archive file to remove
        """
        try:
            success, message = await self.safety.safe_cleanup_archive(
                ssh_cmd, archive_path, "Archive cleanup after migration"
            )

            if success:
                self.logger.debug(
                    "Archive cleaned up safely", archive=archive_path, message=message
                )
            else:
                self.logger.warning("Archive cleanup failed", archive=archive_path, error=message)

        except Exception as e:
            self.logger.error("Archive cleanup error", archive=archive_path, error=str(e))

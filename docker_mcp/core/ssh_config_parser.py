"""SSH configuration file parser for importing host configurations."""

from pathlib import Path

import structlog

from .config import DockerHost

logger = structlog.get_logger()


class SSHConfigEntry:
    """Represents a single SSH host configuration entry."""

    def __init__(self, name: str):
        self.name = name
        self.hostname: str | None = None
        self.user: str | None = None
        self.port: int = 22
        self.identity_file: str | None = None
        self.other_options: dict[str, str] = {}

    def to_docker_host(self) -> DockerHost:
        """Convert SSH config entry to DockerHost configuration."""
        return DockerHost(
            hostname=self.hostname or self.name,
            user=self.user or "root",  # Default to root if not specified
            port=self.port,
            identity_file=self.identity_file,
            description="Imported from SSH config",
            tags=["imported", "ssh-config"],
            enabled=True,
        )

    def is_valid(self) -> bool:
        """Check if this SSH config entry has minimum required fields."""
        # Skip wildcard entries and entries without hostnames
        if "*" in self.name or "?" in self.name:
            return False

        # Must have a hostname (explicit or default to name)
        effective_hostname = self.hostname or self.name

        # Skip localhost and common patterns that aren't real hosts
        if effective_hostname.lower() in ["localhost", "127.0.0.1", "::1"]:
            return False

        # Must have a user (explicit or will default to root)
        return True

    def __repr__(self) -> str:
        return f"SSHConfigEntry(name='{self.name}', hostname='{self.hostname}', user='{self.user}', port={self.port})"


class SSHConfigParser:
    """Parser for SSH configuration files."""

    def __init__(self, config_path: str | Path | None = None):
        """Initialize SSH config parser.

        Args:
            config_path: Path to SSH config file. Defaults to ~/.ssh/config
        """
        if config_path is None:
            config_path = Path.home() / ".ssh" / "config"
        else:
            config_path = Path(config_path)

        self.config_path = config_path

    def parse(self) -> dict[str, SSHConfigEntry]:
        """Parse SSH config file and return dictionary of host entries.

        Returns:
            Dictionary mapping host names to SSHConfigEntry objects

        Raises:
            FileNotFoundError: If SSH config file doesn't exist
            ValueError: If config file is malformed
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"SSH config file not found: {self.config_path}")

        logger.info("Parsing SSH config file", path=str(self.config_path))

        try:
            with open(self.config_path, encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            raise ValueError(f"Failed to read SSH config file: {e}") from e

        return self._parse_content(content)

    def _parse_content(self, content: str) -> dict[str, SSHConfigEntry]:
        """Parse SSH config file content."""
        entries: dict[str, SSHConfigEntry] = {}
        current_entry: SSHConfigEntry | None = None
        line_number = 0

        for raw_line in content.splitlines():
            line_number += 1
            line = raw_line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Parse the line
            try:
                key, value = self._parse_line(line)
            except ValueError as e:
                logger.warning(
                    "Skipping malformed line in SSH config",
                    line_number=line_number,
                    line=line,
                    error=str(e),
                )
                continue

            # Handle Host directive (starts new entry)
            if key.lower() == "host":
                # Save previous entry if it exists and is valid
                if current_entry and current_entry.is_valid():
                    entries[current_entry.name] = current_entry

                # Start new entry
                current_entry = SSHConfigEntry(value)
                logger.debug("Found SSH host entry", name=value)

            # Handle other directives for current entry
            elif current_entry:
                self._apply_directive(current_entry, key, value)

        # Don't forget the last entry
        if current_entry and current_entry.is_valid():
            entries[current_entry.name] = current_entry

        logger.info(
            "SSH config parsing completed",
            total_entries=len(entries),
            valid_entries=len([e for e in entries.values() if e.is_valid()]),
        )

        return entries

    def _parse_line(self, line: str) -> tuple[str, str]:
        """Parse a single SSH config line into key-value pair."""
        # Handle various SSH config formats
        # Format: "Key Value" or "Key=Value" or "Key value1 value2..."

        # First, try to split on whitespace
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid SSH config line format: {line}")

        key, value = parts

        # Handle "Key=Value" format
        if "=" in key:
            key, value_part = key.split("=", 1)
            value = value_part + " " + value if value else value_part

        return key.strip(), value.strip()

    def _apply_directive(self, entry: SSHConfigEntry, key: str, value: str) -> None:
        """Apply a SSH config directive to the current entry."""
        key_lower = key.lower()

        if key_lower == "hostname":
            entry.hostname = value
        elif key_lower == "user":
            entry.user = value
        elif key_lower == "port":
            try:
                entry.port = int(value)
            except ValueError:
                logger.warning(
                    "Invalid port number in SSH config", host=entry.name, port_value=value
                )
        elif key_lower == "identityfile":
            # Expand ~ and environment variables
            identity_path = Path(value).expanduser()
            entry.identity_file = str(identity_path)
        else:
            # Store other options for potential future use
            entry.other_options[key] = value

    def get_importable_hosts(self) -> list[SSHConfigEntry]:
        """Get list of SSH config entries that can be imported.

        Returns:
            List of valid SSH config entries suitable for import
        """
        try:
            all_entries = self.parse()
            importable = []

            for entry in all_entries.values():
                if entry.is_valid():
                    importable.append(entry)
                else:
                    logger.debug(
                        "Skipping non-importable SSH host",
                        name=entry.name,
                        reason="Invalid or wildcard entry",
                    )

            # Sort by name for consistent ordering
            importable.sort(key=lambda x: x.name.lower())

            logger.info("Found importable SSH hosts", count=len(importable))
            return importable

        except Exception as e:
            logger.error("Failed to get importable hosts", error=str(e))
            raise

    def validate_config_file(self) -> tuple[bool, str]:
        """Validate SSH config file and return status with message.

        Returns:
            Tuple of (is_valid, status_message)
        """
        try:
            if not self.config_path.exists():
                return False, f"SSH config file not found: {self.config_path}"

            entries = self.parse()
            valid_count = len([e for e in entries.values() if e.is_valid()])

            if valid_count == 0:
                return False, "No valid SSH host entries found in config file"

            return True, f"Found {valid_count} valid SSH host entries"

        except Exception as e:
            return False, f"Error parsing SSH config: {e}"

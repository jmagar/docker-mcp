"""Abstract base class for transfer methods."""

from abc import ABC, abstractmethod
from typing import Any

import structlog

from ..config_loader import DockerHost

logger = structlog.get_logger()


class BaseTransfer(ABC):
    """Abstract base class for all transfer methods."""

    def __init__(self):
        self.logger = logger.bind(component=self.__class__.__name__.lower())

    @abstractmethod
    async def transfer(
        self,
        source_host: DockerHost,
        target_host: DockerHost,
        source_path: str,
        target_path: str,
        **kwargs
    ) -> dict[str, Any]:
        """Transfer data between hosts.
        
        Args:
            source_host: Source host configuration
            target_host: Target host configuration  
            source_path: Path on source host
            target_path: Path on target host
            **kwargs: Additional transfer-specific options
            
        Returns:
            Dictionary with transfer results and statistics
        """
        pass

    @abstractmethod
    async def validate_requirements(self, host: DockerHost) -> tuple[bool, str]:
        """Validate that this transfer method can be used on the host.
        
        Args:
            host: Host configuration to validate
            
        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        pass

    @abstractmethod
    def get_transfer_type(self) -> str:
        """Get the name/type of this transfer method.
        
        Returns:
            String identifier for this transfer method
        """
        pass

    def build_ssh_cmd(self, host: DockerHost) -> list[str]:
        """Build SSH command for a host.
        
        Args:
            host: Host configuration
            
        Returns:
            SSH command as list of strings
        """
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]

        if host.identity_file:
            ssh_cmd.extend(["-i", host.identity_file])

        if host.port != 22:
            ssh_cmd.extend(["-p", str(host.port)])

        ssh_cmd.append(f"{host.user}@{host.hostname}")

        return ssh_cmd

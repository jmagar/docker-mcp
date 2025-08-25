"""Modular migration system with focused components."""

# Re-export the main migration manager for backwards compatibility
from .manager import MigrationManager, MigrationError  # noqa: F401
from .verification import MigrationVerifier  # noqa: F401
from .volume_parser import VolumeParser  # noqa: F401

__all__ = ["MigrationManager", "MigrationError", "MigrationVerifier", "VolumeParser"]
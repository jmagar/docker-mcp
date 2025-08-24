"""Modular migration system with focused components."""

# Re-export the main migration manager for backwards compatibility
from .manager import MigrationManager
from .verification import MigrationVerifier  
from .volume_parser import VolumeParser

__all__ = ["MigrationManager", "MigrationVerifier", "VolumeParser"]
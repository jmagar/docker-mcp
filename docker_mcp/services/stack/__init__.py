"""
Stack Management Modules

Refactored stack management into focused, single-responsibility modules:
- operations: Core stack CRUD operations
- validation: All validation and checking logic
- network: Network testing and performance estimation
- risk_assessment: Migration risk analysis
- volume_utils: Volume and mount handling utilities
- migration_executor: Actual migration execution logic
- migration_orchestrator: High-level migration coordination

The main StackService acts as a facade that delegates to these specialized modules.
"""

from .migration_executor import StackMigrationExecutor
from .migration_orchestrator import StackMigrationOrchestrator
from .network import StackNetwork
from .operations import StackOperations
from .risk_assessment import StackRiskAssessment
from .validation import StackValidation
from .volume_utils import StackVolumeUtils

__all__ = [
    "StackOperations",
    "StackValidation",
    "StackNetwork",
    "StackRiskAssessment",
    "StackVolumeUtils",
    "StackMigrationExecutor",
    "StackMigrationOrchestrator",
]

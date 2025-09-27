"""
Stack Risk Assessment Module

Risk analysis and mitigation recommendations for Docker stack migrations.
Evaluates data size, downtime, critical files, and service complexity.
"""

import structlog

from ...utils import format_size


class StackRiskAssessment:
    """Risk assessment and mitigation planning for stack migrations."""

    def __init__(self):
        self.logger = structlog.get_logger()

    def assess_migration_risks(
        self,
        stack_name: str,
        data_size_bytes: int,
        estimated_downtime: float,
        source_inventory: dict = None,
        compose_content: str = "",
    ) -> dict:
        """Assess risks associated with the migration.

        Args:
            stack_name: Name of the stack being migrated
            data_size_bytes: Size of data to migrate
            estimated_downtime: Estimated downtime in seconds
            source_inventory: Source data inventory from migration manager
            compose_content: Docker Compose file content

        Returns:
            Dict with risk assessment details
        """
        risks = {
            "overall_risk": "LOW",
            "risk_factors": [],
            "warnings": [],
            "recommendations": [],
            "critical_files": [],
            "rollback_plan": [],
        }

        # Assess each risk factor
        self._assess_data_size_risk(risks, data_size_bytes)
        self._assess_downtime_risk(risks, estimated_downtime)
        self._assess_critical_files_risk(risks, source_inventory)
        self._assess_compose_complexity_risk(risks, compose_content)

        # Generate rollback plan and additional recommendations
        self._generate_rollback_plan(risks)
        self._add_risk_based_recommendations(risks)

        return risks

    def _assess_data_size_risk(self, risks: dict, data_size_bytes: int) -> None:
        """Assess risk based on data size."""
        if data_size_bytes > 50 * 1024**3:  # > 50GB
            risks["risk_factors"].append("LARGE_DATASET")
            risks["warnings"].append(
                f"Large dataset ({format_size(data_size_bytes)}) - increased transfer time and failure risk"
            )
            risks["recommendations"].append("Consider migrating during maintenance window")
            risks["overall_risk"] = "HIGH"
        elif data_size_bytes > 10 * 1024**3:  # > 10GB
            risks["risk_factors"].append("MODERATE_DATASET")
            risks["warnings"].append(
                f"Moderate dataset ({format_size(data_size_bytes)}) - plan for extended transfer time"
            )
            risks["overall_risk"] = "MEDIUM"

    def _assess_downtime_risk(self, risks: dict, estimated_downtime: float) -> None:
        """Assess risk based on estimated downtime."""
        if estimated_downtime > 3600:  # > 1 hour
            risks["risk_factors"].append("LONG_DOWNTIME")
            risks["warnings"].append(
                f"Extended downtime expected ({self._format_time(estimated_downtime)})"
            )
            risks["recommendations"].append("Schedule migration during low-usage period")
            if risks["overall_risk"] == "LOW":
                risks["overall_risk"] = "MEDIUM"
        elif estimated_downtime > 600:  # > 10 minutes
            risks["risk_factors"].append("MODERATE_DOWNTIME")
            risks["warnings"].append(
                f"Moderate downtime expected ({self._format_time(estimated_downtime)})"
            )

    def _assess_critical_files_risk(self, risks: dict, source_inventory: dict) -> None:
        """Assess risk based on critical files in source inventory."""
        if not source_inventory or not source_inventory.get("critical_files"):
            return

        critical_files = source_inventory["critical_files"]

        # Identify database files
        db_files = [
            f
            for f in critical_files.keys()
            if any(ext in f.lower() for ext in [".db", ".sql", ".sqlite", "database"])
        ]

        # Identify configuration files
        config_files = [
            f
            for f in critical_files.keys()
            if any(ext in f.lower() for ext in [".conf", ".config", ".env", ".yaml", ".json"])
        ]

        if db_files:
            risks["risk_factors"].append("DATABASE_FILES")
            risks["warnings"].append(
                f"Database files detected ({len(db_files)} files) - data corruption risk if not properly stopped"
            )
            risks["recommendations"].append(
                "Ensure all database connections are closed before migration"
            )
            risks["critical_files"].extend(db_files)
            if risks["overall_risk"] == "LOW":
                risks["overall_risk"] = "MEDIUM"

        if config_files:
            risks["critical_files"].extend(config_files)

        if len(critical_files) > 20:
            risks["risk_factors"].append("MANY_CRITICAL_FILES")
            risks["warnings"].append(
                f"Many critical files ({len(critical_files)}) - increased complexity"
            )

    def _assess_compose_complexity_risk(self, risks: dict, compose_content: str) -> None:
        """Assess risk based on Docker Compose file complexity."""
        if not compose_content:
            return

        try:
            import yaml

            compose_data = yaml.safe_load(compose_content)
            services = compose_data.get("services", {})

            # Assess different aspects of compose complexity
            self._assess_persistent_volume_risk(risks, services)
            self._assess_health_check_complexity(risks, services)

        except Exception as e:
            # Skip compose analysis if parsing fails
            self.logger.debug("Failed to analyze compose content for risks", error=str(e))

    def _assess_persistent_volume_risk(self, risks: dict, services: dict) -> None:
        """Assess risk from services with persistent volumes."""
        persistent_services = []
        for service_name, service_config in services.items():
            volumes = service_config.get("volumes", [])
            if volumes:
                persistent_services.append(service_name)

        if persistent_services:
            risks["risk_factors"].append("PERSISTENT_SERVICES")
            if len(persistent_services) > 3:
                risks["warnings"].append(
                    f"Multiple services with persistent data ({len(persistent_services)} services)"
                )
                if risks["overall_risk"] == "LOW":
                    risks["overall_risk"] = "MEDIUM"

    def _assess_health_check_complexity(self, risks: dict, services: dict) -> None:
        """Assess complexity from services with health checks."""
        health_checked_services = []
        for service_name, service_config in services.items():
            if "healthcheck" in service_config:
                health_checked_services.append(service_name)

        if health_checked_services:
            risks["recommendations"].append(
                "Monitor health checks after migration - services may need time to stabilize"
            )

    def _generate_rollback_plan(self, risks: dict) -> None:
        """Generate a rollback plan for the migration."""
        risks["rollback_plan"] = [
            "1. Stop target stack immediately if issues detected",
            "2. Verify source stack can be restarted on original host",
            "3. Restore from backup if target data was corrupted",
            "4. Update DNS/load balancer to point back to source",
            "5. Monitor source services for stability after rollback",
        ]

    def _add_risk_based_recommendations(self, risks: dict) -> None:
        """Add recommendations based on overall risk level."""
        if risks["overall_risk"] == "HIGH":
            risks["recommendations"].extend(
                [
                    "Create full backup before starting migration",
                    "Test rollback procedure in non-production environment",
                    "Have technical team available during migration",
                    "Consider incremental migration approach for large datasets",
                ]
            )
        elif risks["overall_risk"] == "MEDIUM":
            risks["recommendations"].extend(
                [
                    "Create backup before starting migration",
                    "Monitor migration progress closely",
                    "Prepare rollback steps in advance",
                ]
            )

    def _format_time(self, seconds: float) -> str:
        """Format seconds into human-readable time string."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        elif seconds < 86400:
            hours = seconds / 3600
            return f"{hours:.1f}h"
        else:
            days = seconds / 86400
            return f"{days:.1f}d"

    def calculate_risk_score(self, risks: dict) -> int:
        """Calculate a numerical risk score (0-100).

        Args:
            risks: Risk assessment dictionary

        Returns:
            Risk score from 0 (lowest risk) to 100 (highest risk)
        """
        score = 0
        risk_factors = risks.get("risk_factors", [])

        # Base score based on risk factors
        factor_scores = {
            "LARGE_DATASET": 30,
            "MODERATE_DATASET": 15,
            "LONG_DOWNTIME": 25,
            "MODERATE_DOWNTIME": 10,
            "DATABASE_FILES": 20,
            "MANY_CRITICAL_FILES": 10,
            "PERSISTENT_SERVICES": 10,
        }

        for factor in risk_factors:
            score += factor_scores.get(factor, 5)

        # Cap at 100
        return min(score, 100)

    def generate_mitigation_plan(self, risks: dict) -> dict:
        """Generate specific mitigation strategies for identified risks.

        Args:
            risks: Risk assessment dictionary

        Returns:
            Dict with mitigation strategies per risk factor
        """
        mitigation_plan = {
            "pre_migration": [],
            "during_migration": [],
            "post_migration": [],
            "contingency": [],
        }

        risk_factors = risks.get("risk_factors", [])

        for factor in risk_factors:
            if factor == "LARGE_DATASET":
                mitigation_plan["pre_migration"].extend(
                    [
                        "Schedule during off-peak hours",
                        "Verify network bandwidth between hosts",
                        "Create incremental backup strategy",
                    ]
                )
                mitigation_plan["during_migration"].extend(
                    [
                        "Monitor transfer progress every 30 minutes",
                        "Have fallback communication plan ready",
                    ]
                )

            elif factor == "DATABASE_FILES":
                mitigation_plan["pre_migration"].extend(
                    [
                        "Create database dump/export",
                        "Verify all connections are closed",
                        "Test database startup on target",
                    ]
                )
                mitigation_plan["post_migration"].extend(
                    ["Verify database integrity after migration", "Run database consistency checks"]
                )

        return mitigation_plan

    def assess_rollback_feasibility(self, risks: dict, migration_params: dict) -> dict:
        """Assess how feasible a rollback would be.

        Args:
            risks: Risk assessment dictionary
            migration_params: Migration parameters (remove_source, etc.)

        Returns:
            Dict with rollback feasibility assessment
        """
        feasibility = {
            "rollback_possible": True,
            "rollback_time_estimate": "5-15 minutes",
            "rollback_risks": [],
            "preparation_required": [],
        }

        # If source will be removed, rollback is more complex
        if migration_params.get("remove_source", False):
            feasibility["rollback_possible"] = False
            feasibility["rollback_risks"].append(
                "Source stack will be deleted - rollback requires restore from backup"
            )
            feasibility["preparation_required"].append(
                "Ensure backup is created and tested before migration"
            )

        # Large datasets make rollback slower
        if "LARGE_DATASET" in risks.get("risk_factors", []):
            feasibility["rollback_time_estimate"] = "30-60 minutes"
            feasibility["rollback_risks"].append("Large dataset restore will take significant time")

        return feasibility

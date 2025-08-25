"""
Docker Cleanup Scheduling Service

Business logic for managing scheduled Docker cleanup operations.
"""

from datetime import datetime
from typing import Any

import structlog

from ..core.config_loader import DockerMCPConfig


class ScheduleService:
    """Service for managing Docker cleanup schedules."""

    def __init__(self, config: DockerMCPConfig):
        self.config = config
        self.logger = structlog.get_logger()

    def _validate_host(self, host_id: str) -> tuple[bool, str]:
        """Validate that a host exists in configuration."""
        if host_id not in self.config.hosts:
            return False, f"Host '{host_id}' not found"
        return True, ""

    async def handle_schedule_action(
        self,
        schedule_action: str,
        host_id: str | None = None,
        cleanup_type: str | None = None,
        schedule_frequency: str | None = None,
        schedule_time: str | None = None,
        schedule_id: str | None = None,
    ) -> dict[str, Any]:
        """Handle schedule-related actions.
        
        Args:
            schedule_action: Action to perform (add, remove, list, enable, disable)
            host_id: Target Docker host identifier
            cleanup_type: Type of cleanup (safe, moderate only)
            schedule_frequency: Cleanup frequency (daily, weekly, monthly, custom)
            schedule_time: Time to run cleanup (e.g., '02:00')
            schedule_id: Schedule identifier for management
            
        Returns:
            Action result
        """
        try:
            if schedule_action == "list":
                return await self._list_schedules()
            elif schedule_action == "add":
                return await self._add_schedule(
                    host_id, cleanup_type, schedule_frequency, schedule_time
                )
            elif schedule_action == "remove":
                return await self._remove_schedule(schedule_id)
            elif schedule_action == "enable":
                return await self._toggle_schedule(schedule_id, True)
            elif schedule_action == "disable":
                return await self._toggle_schedule(schedule_id, False)
            else:
                return {
                    "success": False,
                    "error": f"Invalid schedule_action: {schedule_action}"
                }

        except Exception as e:
            self.logger.error(
                "Schedule action failed",
                action=schedule_action,
                error=str(e)
            )
            return {"success": False, "error": str(e)}

    async def _add_schedule(
        self,
        host_id: str | None,
        cleanup_type: str | None,
        schedule_frequency: str | None,
        schedule_time: str | None,
    ) -> dict[str, Any]:
        """Add a new cleanup schedule."""
        # Validate required parameters
        if not host_id:
            return {"success": False, "error": "host_id is required for schedule add"}
        if not cleanup_type:
            return {"success": False, "error": "cleanup_type is required for schedule add"}
        if not schedule_frequency:
            return {"success": False, "error": "schedule_frequency is required for schedule add"}
        if not schedule_time:
            return {"success": False, "error": "schedule_time is required for schedule add"}

        # Validate host exists
        is_valid, error_msg = self._validate_host(host_id)
        if not is_valid:
            return {"success": False, "error": error_msg}

        # Safety: Only allow safe and moderate cleanup types for scheduling
        if cleanup_type not in ["safe", "moderate"]:
            return {
                "success": False,
                "error": "Only 'safe' and 'moderate' cleanup types are allowed for scheduling"
            }

        # Validate frequency
        if schedule_frequency not in ["daily", "weekly", "monthly", "custom"]:
            return {
                "success": False,
                "error": "schedule_frequency must be one of: daily, weekly, monthly, custom"
            }

        # Validate time format (HH:MM)
        if not self._validate_time_format(schedule_time):
            return {
                "success": False,
                "error": "schedule_time must be in HH:MM format (e.g., '02:00')"
            }

        # Generate unique schedule ID
        schedule_id = f"cleanup-{host_id}-{schedule_frequency}"

        # Check if schedule already exists
        if hasattr(self.config, 'cleanup_schedules') and schedule_id in self.config.cleanup_schedules:
            return {
                "success": False,
                "error": f"Schedule '{schedule_id}' already exists"
            }

        # Create schedule configuration
        schedule_config = {
            "host_id": host_id,
            "cleanup_type": cleanup_type,
            "frequency": schedule_frequency,
            "time": schedule_time,
            "enabled": True,
            "log_path": f"/var/log/docker-cleanup/{host_id}.log",
            "created_at": datetime.now().isoformat()
        }

        # Generate cron entry
        cron_expression = self._generate_cron_expression(schedule_frequency, schedule_time)

        self.logger.info(
            "Adding cleanup schedule",
            schedule_id=schedule_id,
            host_id=host_id,
            cleanup_type=cleanup_type,
            frequency=schedule_frequency,
            time=schedule_time,
            cron_expression=cron_expression
        )

        # TODO: Add schedule to config and update crontab
        # For now, return success with the schedule info
        return {
            "success": True,
            "schedule_id": schedule_id,
            "schedule": schedule_config,
            "cron_expression": cron_expression,
            "message": f"Schedule '{schedule_id}' created successfully",
            "note": "Actual cron job creation not yet implemented"
        }

    async def _remove_schedule(self, schedule_id: str | None) -> dict[str, Any]:
        """Remove a cleanup schedule."""
        if not schedule_id:
            return {"success": False, "error": "schedule_id is required for schedule remove"}

        self.logger.info("Removing cleanup schedule", schedule_id=schedule_id)

        # TODO: Remove from config and update crontab
        return {
            "success": True,
            "schedule_id": schedule_id,
            "message": f"Schedule '{schedule_id}' removed successfully",
            "note": "Actual cron job removal not yet implemented"
        }

    async def _toggle_schedule(self, schedule_id: str | None, enabled: bool) -> dict[str, Any]:
        """Enable or disable a cleanup schedule."""
        if not schedule_id:
            return {
                "success": False,
                "error": "schedule_id is required for schedule enable/disable"
            }

        action = "enabled" if enabled else "disabled"

        self.logger.info(
            "Toggling cleanup schedule",
            schedule_id=schedule_id,
            enabled=enabled
        )

        # TODO: Update config and crontab
        return {
            "success": True,
            "schedule_id": schedule_id,
            "enabled": enabled,
            "message": f"Schedule '{schedule_id}' {action} successfully",
            "note": "Actual cron job toggle not yet implemented"
        }

    async def _list_schedules(self) -> dict[str, Any]:
        """List all cleanup schedules."""
        self.logger.info("Listing cleanup schedules")

        # TODO: Read from actual config
        # For now, return empty list
        schedules = []

        return {
            "success": True,
            "schedules": schedules,
            "count": len(schedules),
            "message": "Retrieved all cleanup schedules",
            "note": "Reading from config not yet implemented"
        }

    def _validate_time_format(self, time_str: str) -> bool:
        """Validate time format (HH:MM)."""
        try:
            parts = time_str.split(':')
            if len(parts) != 2:
                return False

            hour = int(parts[0])
            minute = int(parts[1])

            return 0 <= hour <= 23 and 0 <= minute <= 59
        except ValueError:
            return False

    def _generate_cron_expression(self, frequency: str, time: str) -> str:
        """Generate cron expression from frequency and time.
        
        Args:
            frequency: daily, weekly, monthly, or custom
            time: Time in HH:MM format
            
        Returns:
            Cron expression string
        """
        hour, minute = time.split(':')

        if frequency == "daily":
            return f"{minute} {hour} * * *"
        elif frequency == "weekly":
            # Run on Sunday (0)
            return f"{minute} {hour} * * 0"
        elif frequency == "monthly":
            # Run on 1st of every month
            return f"{minute} {hour} 1 * *"
        else:  # custom
            # For custom, user would need to provide full cron expression
            # For now, default to daily
            return f"{minute} {hour} * * *"

    async def _update_crontab(self, schedule_id: str, action: str, cron_entry: str = ""):
        """Update system crontab with schedule.
        
        Args:
            schedule_id: Unique schedule identifier
            action: 'add' or 'remove'
            cron_entry: Full cron entry for addition
        """
        # TODO: Implement actual crontab management
        # This would involve:
        # 1. Reading current crontab
        # 2. Adding/removing entries with schedule_id comments
        # 3. Writing back to crontab

        self.logger.info(
            "Crontab update requested",
            schedule_id=schedule_id,
            action=action,
            cron_entry=cron_entry
        )

    def _generate_cleanup_command(self, schedule_config: dict) -> str:
        """Generate the command that will be executed by cron.
        
        Args:
            schedule_config: Schedule configuration dictionary
            
        Returns:
            Command string for cron execution
        """
        host_id = schedule_config["host_id"]
        cleanup_type = schedule_config["cleanup_type"]
        log_path = schedule_config.get("log_path", f"/var/log/docker-cleanup/{host_id}.log")

        # This would be the actual command executed by cron
        # It should call back into our MCP server or use a CLI tool
        return (
            f"docker-mcp cleanup --host {host_id} --type {cleanup_type} "
            f">> {log_path} 2>&1"
        )

    def _format_schedule_display(self, schedule_config: dict) -> dict[str, Any]:
        """Format schedule configuration for display.
        
        Args:
            schedule_config: Raw schedule configuration
            
        Returns:
            Formatted schedule information
        """
        return {
            "host_id": schedule_config["host_id"],
            "cleanup_type": schedule_config["cleanup_type"],
            "frequency": schedule_config["frequency"],
            "time": schedule_config["time"],
            "enabled": schedule_config.get("enabled", True),
            "next_run": "TBD",  # Would calculate based on cron expression
            "created_at": schedule_config.get("created_at"),
            "log_path": schedule_config.get("log_path")
        }

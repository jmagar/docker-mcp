"""
Docker Cleanup Scheduling Service

Business logic for managing scheduled Docker cleanup operations.
"""

import asyncio
import subprocess
from datetime import datetime
from typing import Any, Optional

import structlog

from ..core.config_loader import DockerMCPConfig


class ScheduleService:
    """Service for managing Docker cleanup schedules."""

    def __init__(self, config: DockerMCPConfig):
        """
        Initialize the ScheduleService.
        
        Stores the provided DockerMCPConfig on the instance and initializes a structured logger for service operations.
        """
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
        """
        Dispatch schedule management requests to the appropriate internal handler.
        
        Supported actions:
        - "list": return all schedules.
        - "add": create a new schedule (requires host_id, cleanup_type, schedule_frequency, schedule_time).
        - "remove": remove an existing schedule (requires schedule_id).
        - "enable" / "disable": toggle an existing schedule (requires schedule_id).
        
        Parameters:
            schedule_action: One of "list", "add", "remove", "enable", "disable".
            host_id: Target Docker host identifier (required for "add").
            cleanup_type: Cleanup type, e.g., "safe" or "moderate" (required for "add").
            schedule_frequency: One of "daily", "weekly", "monthly", "custom" (required for "add").
            schedule_time: Time in "HH:MM" format (required for "add").
            schedule_id: Identifier of the schedule to manage (required for "remove", "enable", "disable").
        
        Returns:
            A dict containing the operation result. On success the dict includes success=True and action-specific data;
            on failure it includes success=False and an "error" message. Exceptions are caught and returned in the error field.
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
        """
        Create a new scheduled cleanup entry for a host.
        
        Validates inputs (host existence, allowed cleanup types, frequency values, and HH:MM time format),
        generates a schedule identifier of the form `cleanup-{host_id}-{schedule_frequency}`, builds a schedule
        configuration dict, and returns the computed cron expression and schedule data. This function performs
        validation only and does not persist the schedule to configuration or install a cron job.
        
        Parameters:
            host_id (str | None): Target host identifier (required).
            cleanup_type (str | None): Cleanup intensity; allowed values are `"safe"` and `"moderate"` (required).
            schedule_frequency (str | None): Recurrence specifier; one of `"daily"`, `"weekly"`, `"monthly"`, or `"custom"` (required).
            schedule_time (str | None): Execution time in `HH:MM` 24-hour format (required).
        
        Returns:
            dict: A result dictionary. On success:
                {
                    "success": True,
                    "schedule_id": str,           # generated schedule identifier
                    "schedule": dict,             # schedule configuration (host_id, cleanup_type, frequency, time, enabled, log_path, created_at)
                    "cron_expression": str,       # cron expression derived from frequency and time
                    "message": str,
                    "note": str                   # indicates persistence/cron installation is not implemented
                }
                On failure:
                {
                    "success": False,
                    "error": str                  # explanation of validation or existence failure
                }
        """
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
        """
        Remove a scheduled cleanup entry.
        
        Validates that a schedule_id is provided, logs the removal request, and returns a structured result.
        This function does not currently persist changes or modify the system crontab (those steps are TODO).
        
        Parameters:
            schedule_id (str | None): Identifier of the schedule to remove; required.
        
        Returns:
            dict[str, Any]: Result object with keys including:
                - success (bool): True when the request is accepted.
                - schedule_id (str): The provided schedule identifier.
                - message (str): Human-readable outcome message.
                - note (str): Indicates that actual cron removal is not yet implemented.
        """
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
        """
        Enable or disable an existing cleanup schedule.
        
        Validates that a schedule identifier is provided, logs the toggle action, and returns a structured result describing the requested change. This function does not modify persistent configuration or the system crontab (those actions are TODO).
        
        Parameters:
            schedule_id (str | None): Identifier of the schedule to toggle; required.
            enabled (bool): True to enable the schedule, False to disable it.
        
        Returns:
            dict[str, Any]: A result dictionary. On success contains at least:
                - "success" (bool): True.
                - "schedule_id" (str): The provided schedule identifier.
                - "enabled" (bool): The resulting enabled state.
                - "message" (str): Human-readable outcome.
                - "note" (str): Indicates that actual cron toggling is not implemented.
              If schedule_id is missing, returns:
                - "success": False
                - "error": str explaining the missing identifier.
        """
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
        """
        Return a list of configured cleanup schedules.
        
        Currently this is a placeholder: it always returns an empty schedule list and a note indicating that reading schedules from the configuration is not yet implemented.
        
        Returns:
            dict[str, Any]: Structured response with keys:
                - success (bool): True when the operation completed.
                - schedules (list): List of schedule objects (empty in the current implementation).
                - count (int): Number of schedules returned.
                - message (str): Human-readable status message.
                - note (str): Implementation note (explains that reading from config is TODO).
        """
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
        """
        Return True if `time_str` is a valid 24-hour time in "HH:MM" format.
        
        Accepts two numeric components separated by a colon where hour is 0–23 and minute is 0–59. Non-numeric values, missing/extra components, or out-of-range values produce False.
        """
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
        """
        Generate a cron schedule expression from a human-friendly frequency and an HH:MM time.
        
        frequency: one of "daily", "weekly", "monthly", or "custom". "weekly" produces a cron entry for Sunday (day-of-week 0); "monthly" targets the 1st day of the month. "custom" currently falls back to the same schedule as "daily".
        time: local time in 24-hour "HH:MM" format (hour and minute are placed into the cron expression as "minute hour ...").
        
        Returns:
            A cron expression string in standard 5-field format (minute hour day month weekday).
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
        """
        Request an update to the system crontab for a schedule.
        
        This is a non-blocking stub that records an intent to add or remove a cron entry for the given schedule_id.
        It currently logs the request and does not modify the system crontab. When implemented, callers can expect
        the method to read the current crontab, add or remove lines annotated with the schedule_id, and write the
        updated crontab back to the system.
        
        Parameters:
            schedule_id (str): Unique identifier for the schedule; used to tag cron lines for later removal.
            action (str): Either 'add' to insert a cron entry or 'remove' to delete entries associated with schedule_id.
            cron_entry (str): Full cron line to be added when action is 'add' (ignored for 'remove').
        
        Returns:
            None
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
        """
        Return the shell command string cron should execute to run a scheduled cleanup.
        
        The function builds a CLI invocation for the `docker-mcp cleanup` command based on
        the provided schedule configuration. Expected keys in `schedule_config`:
        - `host_id` (str): target host identifier (required).
        - `cleanup_type` (str): cleanup mode (required, e.g., "safe" or "moderate").
        - `log_path` (str, optional): path to append command output; defaults to
          `/var/log/docker-cleanup/{host_id}.log`.
        
        Returns:
            A single-line shell command that runs the cleanup for the specified host/type
            and redirects both stdout and stderr to the configured log file.
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
        """
        Format a raw schedule configuration into a display-friendly dictionary.
        
        Only surface-facing fields are returned; values are taken from schedule_config with sensible defaults:
        - host_id: schedule host identifier (required).
        - cleanup_type: cleanup strategy (e.g., "safe", "moderate").
        - frequency: schedule frequency (e.g., "daily", "weekly").
        - time: scheduled time in "HH:MM" format.
        - enabled: boolean (defaults to True if missing).
        - next_run: human-friendly next run time (currently a placeholder "TBD").
        - created_at: creation timestamp if present.
        - log_path: path to the schedule's log file if present.
        
        schedule_config: Raw schedule configuration dictionary (expected to contain at least host_id, cleanup_type, frequency, and time).
        Returns:
            A dict containing the formatted schedule fields described above.
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
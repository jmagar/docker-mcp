# Remaining Fixes from PR #12 Review

This document lists all the pending changes and suggestions from the `docker-mcp-pr12-fixes.md` review that still need to be implemented in the codebase.

---

## `docker_mcp/core/settings.py`

### Pydantic v2 Style Config
- **Status**: To Do
- **File**: `docker_mcp/core/settings.py`, lines 38-41
- **Suggestion**: Use `SettingsConfigDict` via `model_config` for consistency with Pydantic v2.
- **Diff**:
  ```diff
  -from pydantic_settings import BaseSettings
  +from pydantic_settings import BaseSettings, SettingsConfigDict
  @@
  -    class Config:
  -        env_file = ".env"
  -        extra = "ignore"
  +    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
  ```

---

## `docker_mcp/services/config.py`

### Avoid Runtime Imports
- **Status**: To Do
- **File**: `docker_mcp/services/config.py`, lines 17-21 and 27-31
- **Suggestion**: Avoid importing `DockerContextManager` at runtime to prevent potential circular dependencies. Use a `TYPE_CHECKING` block instead.
- **Diff**:
  ```diff
  -from ..core.docker_context import DockerContextManager
  ```

### Structured Logging
- **Status**: To Do
- **File**: `docker_mcp/services/config.py`, lines 129-133
- **Suggestion**: Prefer structured logging fields over formatted f-string messages.
- **Diff**:
  ```diff
  -            self.logger.info(f"Discovering compose locations for {current_host_id}")
  +            self.logger.info("Discovering compose locations", host_id=current_host_id)
  ```

---
## `docker_mcp/services/host.py`

### Action Dispatch Bug
- **Status**: To Do
- **File**: `docker_mcp/services/host.py`, lines 1258-1266 and 1289-1303
- **Suggestion**: The `handle_action` method has a bug where it looks up an enum in a dictionary using a string, which will always fail. The string should be converted to a `HostAction` enum before the lookup.
- **Diff**:
  ```diff
  -            # Normalize action to handle string inputs
  -            if isinstance(action, str):
  -                action = action.lower().strip()
  +            # Normalize action to HostAction enum when provided as string
  +            if isinstance(action, str):
  +                from ..models.enums import HostAction
  +                try:
  +                    action = HostAction(action.lower().strip())
  +                except Exception:
  +                    return {
  +                        "success": False,
  +                        "error": f"Unknown action: {action}",
  +                        "valid_actions": [a.value for a in HostAction],
  +                    }
  @@
  -            handler = handlers.get(action)
  +            handler = handlers.get(action)
  ```

### Avoid Fragile Re-exports
- **Status**: To Do
- **File**: `docker_mcp/services/host.py`, lines 1405-1417
- **Suggestion**: Import `ContainerService` from its concrete submodule to reduce coupling.
- **Diff**:
  ```diff
  -        from ..services import ContainerService
  +        from ..services.container import ContainerService
  ```

### Normalize Discovery Result Shapes
- **Status**: To Do
- **File**: `docker_mcp/services/host.py`, lines 537-556
- **Suggestion**: Add `success=True` to successful discovery results for consistency.

### Make Config Save Non-blocking
- **Status**: To Do
- **File**: `docker_mcp/services/host.py`, lines 93, 272-274, 323-325
- **Suggestion**: `save_config` performs blocking file I/O and should be made asynchronous using `asyncio.to_thread`.

### Reload Config Asynchronously
- **Status**: To Do
- **File**: `docker_mcp/services/host.py`, lines 435-437, 492-503
- **Suggestion**: `_reload_config` performs blocking file I/O and should be made asynchronous.

---
## `docker_mcp/server.py`

### Validate JSON Scope Parsing
- **Status**: To Do
- **File**: `docker_mcp/server.py`, lines 513-537
- **Suggestion**: The JSON parsing for `FASTMCP_SERVER_AUTH_GOOGLE_REQUIRED_SCOPES` is not validated and could crash if the JSON is malformed.

---
## `docker_mcp/core/safety.py`

### Whitelist Precedence Bug
- **Status**: To Do
- **File**: `docker_mcp/core/safety.py`, lines 71-101
- **Suggestion**: The `validate_deletion_path` logic has a bug where the forbidden paths are checked before the safe paths. The order of checks needs to be reversed.

### Command Injection Risk
- **Status**: To Do
- **File**: `docker_mcp/core/safety.py`, lines 169-179
- **Suggestion**: There's a command injection risk in `safe_delete_file` where `file_path` is not quoted.

---
## `docker_mcp/services/cleanup.py`

### Schedule ID Collisions
- **Status**: To Do
- **File**: `docker_mcp/services/cleanup.py`, lines 988-994
- **Suggestion**: The `schedule_id` does not include the time, which can cause collisions.

### Use Timezone-aware Timestamps
- **Status**: To Do
- **File**: `docker_mcp/services/cleanup.py`, lines 1006-1014
- **Suggestion**: The `created_at` timestamp is not timezone-aware. It should be stored in UTC.

---
## `docker_mcp/core/migration/verification.py`

### Move Bandit Nosec
- **Status**: To Do
- **File**: `docker_mcp/core/migration/verification.py`, lines 624-629, 640-646, 696-702
- **Suggestion**: The `# nosec B603` comment is misplaced and should be on the `subprocess.run` line.

### Expose Top-level Success Flag
- **Status**: To Do
- **File**: `docker_mcp/core/migration/verification.py`, lines 314-327
- **Suggestion**: Add a top-level `success` flag to the verification result for compatibility with callers.

---
## `docker_mcp/core/transfer/zfs.py`

### Quote SSH Command Safely
- **Status**: To Do
- **File**: `docker_mcp/core/transfer/zfs.py`, lines 544-561
- **Suggestion**: The `_send_receive` method joins the SSH command with spaces, which is not safe. It should use `shlex.join`.

### Quote Dataset Names
- **Status**: To Do
- **File**: `docker_mcp/core/transfer/zfs.py`, lines 188-199, 210-221, 227-241
- **Suggestion**: ZFS dataset names are not quoted in commands, which is a security risk.

### Incomplete Rollback Logic
- **Status**: To Do
- **File**: `docker_mcp/core/transfer/zfs.py`, lines 304-315
- **Suggestion**: The `create_dataset_from_directory` rollback logic is incomplete. It should also destroy the partially created ZFS dataset.

### Fragile Dataset Detection
- **Status**: To Do
- **File**: `docker_mcp/core/transfer/zfs.py`, lines 140-160
- **Suggestion**: Dataset detection in `get_dataset_for_path` is fragile and uses `grep`. It should be replaced with a more robust method.

### Dead Code
- **Status**: To Do
- **File**: `docker_mcp/core/transfer/zfs.py`, lines 976-1032
- **Suggestion**: The `_cleanup_target_snapshots` method is unused and should be removed or integrated.

---
## `docker_mcp/models/container.py`

### Type Container Actions
- **Status**: To Do
- **File**: `docker_mcp/models/container.py`, lines 83-90
- **Suggestion**: The `action` field in `ContainerActionRequest` is a string. It should be a `Literal` or an `Enum` for type safety.

### Add `host_id` to `PortConflict`
- **Status**: To Do
- **File**: `docker_mcp/models/container.py`, lines 118-126
- **Suggestion**: `PortConflict` is missing the `host_id`.

### Add `host_id` to `PortMapping`
- **Status**: To Do
- **File**: `docker_mcp/models/container.py`, lines 103-116
- **Suggestion**: `PortMapping` is missing the `host_id`.

---
## `docker_mcp/tools/containers.py`

### Return Structured Errors
- **Status**: To Do
- **File**: `docker_mcp/tools/containers.py`, lines 152-154, 873-879
- **Suggestion**: The tool raises exceptions instead of returning structured error responses.

### Include Success Flag
- **Status**: To Do
- **File**: `docker_mcp/tools/containers.py`, lines 140-150
- **Suggestion**: The `list_containers` success response is missing the `"success": True` field.

---
## `docker_mcp/core/transfer/rsync.py`

### Quote Paths in Rsync Command
- **Status**: To Do
- **File**: `docker_mcp/core/transfer/rsync.py`, lines 101-113
- **Suggestion**: The `rsync_inner_cmd` is built by joining with spaces, which will fail for paths with spaces.

---
## `docker_mcp/services/stack_service.py`

### Fix `lines` Range Message
- **Status**: To Do
- **File**: `docker_mcp/services/stack_service.py`, lines 372-374
- **Suggestion**: The `lines` parameter validation message for logs says the limit is 10000, but the code enforces 1000.

---
## `docker_mcp/services/stack/migration_executor.py`

### Quote `stack_name`
- **Status**: To Do
- **File**: `docker_mcp/services/stack/migration_executor.py`, lines 301-303
- **Suggestion**: The `stack_name` is not quoted in the `docker ps` filter, which is a security risk.

---
## Documentation and Minor Fixes

### `config/CLAUDE.md`
- **Status**: To Do
- **Suggestion**: The documentation incorrectly references `expand_env_vars` instead of `expand_yaml_config`.

### `CHANGELOG.md`
- **Status**: To Do
- **Suggestion**: There should be a blank line before the "Previous release" section.

### `fix_asyncio_formatting.py`
- **Status**: To Do
- **Suggestion**: This utility script should be added to `.gitignore`.

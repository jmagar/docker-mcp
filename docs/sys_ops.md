# sys_ops Tool Specification (System Operations)

## Overview

`sys_ops` is a consolidated MCP tool that provides host‑level system operations via SSH, designed to complement the existing Docker tools while keeping the number of tools small and the action surface rich. It follows the token‑efficient formatting system: every action returns a concise, human‑readable summary while preserving complete machine‑readable data via `ToolResult`.

- Tool id: `sys_ops`
- Title: System Operations
- Scope: OS info, services, packages, disks/filesystems
- Transport: SSH (non‑interactive), no secrets returned
- Defaults: Read‑only and non‑destructive by default; destructive operations require explicit opt‑in (`dry_run=False`)

## Design Principles

- Token‑efficient formatted `content` + complete `structured_content` (ToolResult)
- Safe defaults, explicit opt‑in for changes
- Clear, aligned tables and status icons (`●`, `○`, `◐`, `✓`, `✗`)
- Parameter validation before any remote work
- Cross‑distro support where feasible (apt/dnf/yum/pacman)

## Actions Summary

- `info`: System summary (OS, kernel, uptime, load, CPU, memory, swap, disks)
- `services`: List systemd services with filters
- `service`: Operate on a specific systemd unit (status/logs/read‑only by default)
- `packages`: Package operations (list updates/security; dry‑run upgrade/autoremove)
- `disk`: Disk/FS operations (df, mounts, inodes, du)

All actions require `host_id` and validate the host exists in configuration.

## Common Return Type (All Actions)

```python
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

# Always return ToolResult
ToolResult(
    content=[TextContent(type="text", text=formatted_summary)],
    structured_content={...full_data...}
)
```

- `content`: concise, token‑efficient summary (tables/headers/previews only)
- `structured_content`: full JSON containing all details (complete listings, raw outputs as applicable)

## Parameters and Validation

Unless specified otherwise, all string parameters are trimmed; empty strings are treated as unset. Enumerations are validated case‑insensitively by value or name.

- `host_id` (str): Required for all actions; must exist in MCP config.
- `dry_run` (bool): Defaults to `True` for any action that can mutate state. Set `False` to execute.
- Numeric ranges:
  - `limit`: 1–1000 (default varies per action)
  - `lines`: 1–10000 for log outputs (default 100)
  - `depth`: 0–10 for `du` (default 1)

Errors are returned as `ToolResult` with `structured_content` containing `{ "success": False, "error": "..." }`.

## Action: info

Show a concise system summary.

Parameters
- `host_id` (str, required)

Validation
- Host must exist in configuration

Behavior
- Executes standard read‑only commands to gather:
  - Hostname, distro (e.g., `/etc/os-release`), kernel (`uname -r`)
  - Uptime, load averages
  - CPU model/count
  - Memory/swap total/used
  - Disks summary (filesystem, size, used, avail, use%)

Formatted Output (content)
```
System Info for prod-1
OS: Ubuntu 22.04 | Kernel: 6.5.0-28 | Uptime: 3d 12h
CPU: 8 cores | Load: 0.42 0.35 0.40
Mem: 12.3 GB / 31.3 GB (39%) | Swap: 0.0 GB / 8.0 GB (0%)

Filesystem           Size    Used    Avail   Use%  Mount
-------------------  ------  ------  ------  ----  ---------
/dev/nvme0n1p2       456G    210G    228G    48%   /
...
```

Structured Content
- `system`: hostname, os, version, kernel, uptime, load, cpu, memory, swap
- `disks`: list of filesystems with sizes and usage

## Action: services

List systemd units with filters.

Parameters
- `host_id` (str, required)
- `state_filter` (str, optional): one of `all|running|failed` (default `all`)
- `include_system` (bool, optional): include system units (default `False`)
- `limit` (int, optional): 1–1000 (default 100)

Validation
- `state_filter` must be one of allowed values
- `limit` in range

Behavior
- Uses `systemctl list-units --type=service --all --no-pager --no-legend`
- Applies state filter client‑side; can include/exclude system units (e.g., `systemd-` prefixed)

Formatted Output (content)
```
Services on prod-1 (running: 37, failed: 1)
Service               State  En  Failed  Description
--------------------  -----  --  ------  --------------------------
docker.service        ●      ✓   ✗       Docker Application Container Engine
traefik.service       ●      ✓   ✗       A modern reverse proxy
fail2ban.service      ○      ✓   ✗       Ban hosts that cause problems
...
```

Structured Content
- `services`: list of objects with `name`, `state`, `enabled`, `failed`, `description`
- `counts`: by state

## Action: service

Operate on a specific systemd unit. Read‑only by default.

Parameters
- `host_id` (str, required)
- `name` (str, required): unit name (e.g., `docker.service`)
- `op` (str, required): one of `status|logs|start|stop|restart|enable|disable`
- `lines` (int, optional): number of log lines for `logs` (default 100)
- `follow` (bool, optional): stream logs for `logs` (default False)
- `dry_run` (bool, optional): default True for mutating ops; ignored for read‑only ops

Validation
- `name` non‑empty
- `op` in allowed set
- `lines` in 1–10000
- For `start|stop|restart|enable|disable`: require `dry_run=False` to execute; with `True`, return planned action

Behavior
- `status`: `systemctl is-active/is-enabled`, recent failure from `systemctl status`
- `logs`: `journalctl -u <unit> -n <lines>`; token‑efficient preview (head/tail)
- `start/stop/restart/enable/disable`: guarded by `dry_run`

Formatted Output (examples)
```
service status docker.service on prod-1
State: ● active | Enabled: ✓ | Failed: ✗

service logs traefik.service on prod-1
Lines returned: 100 (requested: 100)
Preview (first 5):
  ...
Preview (last 5):
  ...

service restart traefik.service on prod-1 (dry run)
Would run: systemctl restart traefik.service
```

Structured Content
- `status`: state, enabled, failed, timestamps
- `logs`: `lines`, `lines_returned`, `follow`, `logs: list[str]`
- `action`: op, dry_run, executed, message

## Action: packages

Cross‑distro package operations. Read‑only by default.

Parameters
- `host_id` (str, required)
- `op` (str, required): `list_updates|list_security|upgrade|autoremove`
- `dry_run` (bool, optional): default True for `upgrade|autoremove`
- `limit` (int, optional): 1–1000 (default 200) for list views

Validation
- `op` in allowed set
- `limit` in range
- Detect supported backend: one of `apt|dnf|yum|pacman`; otherwise return error

Behavior
- `list_updates`: distro‑specific query (e.g., `apt list --upgradable`, `dnf check-update`)
- `list_security`: distro‑specific security updates (e.g., `apt-get --just-print upgrade` with security origin filter; `dnf updateinfo list security`)
- `upgrade|autoremove`: preview when `dry_run=True`; execute command when `False`

Formatted Output (content)
```
Package Updates on prod-1 (backend: apt)
Total updates: 14 (security: 3)
Name                Current        Candidate      Security
------------------  -------------  -------------  --------
libssl3             3.0.2-0        3.0.2-1        ✓
...
```

Structured Content
- `backend`: `apt|dnf|yum|pacman`
- `updates`: list of packages with fields (name, current, candidate, security)
- For actions: `planned_changes` (when dry run) or `changes_applied`

## Action: disk

Disk and filesystem operations.

Parameters
- `host_id` (str, required)
- `op` (str, required): `df|mounts|inodes|du`
- `path` (str, optional): for `du` (default `/`)
- `depth` (int, optional): for `du` depth 0–10 (default 1)
- `limit` (int, optional): number of entries for `du` (default 20)

Validation
- `op` in allowed set
- For `du`: validate `path` not empty, `depth` and `limit` in range

Behavior
- `df`: summarize filesystems (size/used/avail/use%/mountpoint)
- `mounts`: list mount points (fs type, options)
- `inodes`: `df -i` results (inode usage)
- `du`: top N directories/files by size under `path` with max `depth`

Formatted Output (examples)
```
DF on prod-1
Filesystem           Size    Used    Avail   Use%  Mount
-------------------  ------  ------  ------  ----  ---------
/dev/nvme0n1p2       456G    210G    228G    48%   /
...

Top Disk Usage under /var (depth=1, top=10)
Path                           Size
-----------------------------  --------
/var/lib/docker                82.4 GB
/var/log                       2.1 GB
...
```

Structured Content
- `df`: list of filesystems
- `mounts`: list of mounts
- `inodes`: list with inode usage
- `du`: list of paths with sizes

## Safety and Permissions

- All actions are read‑only by default; mutation requires `dry_run=False`
- Commands executed via SSH using non‑interactive options
- No secrets are returned; log outputs are previewed, full logs in `structured_content`
- Destructive operations (e.g., service stop, package upgrade) require explicit confirmation (dry run off)

## Token‑Efficient Formatting

- Headers with counts and context
- Aligned tables with fixed‑width columns
- Status indicators:
  - `status_symbol(state)`: running `●`, stopped `○`, partial/restarting `◐`, unknown `?`
  - `icon_ok(bool)`: `✓` for True, `✗` for False
- Previews for long outputs (logs) with head/tail; full details preserved in `structured_content`

## Error Handling

Errors produce ToolResult with clear summary and machine‑parsable content:

```
❌ Failed to list services: systemd not available
```

```json
{
  "success": false,
  "error": "systemd not available",
  "host_id": "prod-1",
  "action": "services"
}
```

## Usage Examples

```
# System summary
sys_ops info prod-1

# Services (running only)
sys_ops services prod-1 --state_filter running --limit 50

# Inspect a service (status, then logs)
sys_ops service prod-1 docker.service status
sys_ops service prod-1 traefik.service logs --lines 200

# Dry-run a restart (explicitly show what would run)
sys_ops service prod-1 traefik.service restart --dry-run

# Package updates (list)
sys_ops packages prod-1 list_updates --limit 100

# Disk summary and top consumers
sys_ops disk prod-1 df
sys_ops disk prod-1 du --path /var --depth 1 --limit 10
```

## Future Extensions

- Users/processes (top/ps summaries)
- Firewall summaries (ufw/iptables‑nft)
- Network diagnostics (ping/dns/route summaries)
- Certificate scanner (TLS cert expiry)

This specification defines the full contract for implementing `sys_ops` with the same patterns and guarantees as our existing Docker tools: minimal tool surface, rich actions, safe defaults, and token‑efficient formatting.


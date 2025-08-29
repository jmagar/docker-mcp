#!/usr/bin/env python3
"""
Dynamic Ansible inventory script for Docker MCP.
Converts hosts.yml configuration to Ansible inventory format.
"""

import json
import sys
from pathlib import Path
from typing import Any

import yaml


def load_docker_mcp_config(config_path: str = None) -> dict[str, Any]:
    """Load Docker MCP hosts configuration."""

    # Default config paths to check
    config_paths = []
    if config_path:
        config_paths.append(config_path)

    config_paths.extend([
        "../config/hosts.yml",
        "config/hosts.yml",
        "/home/jmagar/code/docker-mcp/config/hosts.yml",
        "~/.docker-mcp/config/hosts.yml"
    ])

    for path in config_paths:
        expanded_path = Path(path).expanduser()
        if expanded_path.exists():
            with open(expanded_path) as f:
                return yaml.safe_load(f)

    return {"hosts": {}}


def build_ansible_inventory(config: dict[str, Any]) -> dict[str, Any]:
    """Build Ansible inventory from Docker MCP config."""

    inventory = {
        "_meta": {
            "hostvars": {}
        }
    }

    # Group hosts by tags and other criteria
    groups = {
        "all": {"children": []},
        "docker_hosts": {"hosts": []},
        "enabled_hosts": {"hosts": []},
        "disabled_hosts": {"hosts": []},
        "zfs_capable": {"hosts": []},
        "production": {"hosts": []},
        "staging": {"hosts": []},
        "development": {"hosts": []}
    }

    hosts_config = config.get("hosts", {})

    for host_id, host_config in hosts_config.items():
        if not host_config:
            continue

        hostname = host_config.get("hostname", host_id)
        user = host_config.get("user", "root")
        port = host_config.get("port", 22)
        identity_file = host_config.get("identity_file")
        enabled = host_config.get("enabled", True)
        tags = host_config.get("tags", [])

        # Add to basic groups
        groups["docker_hosts"]["hosts"].append(host_id)

        if enabled:
            groups["enabled_hosts"]["hosts"].append(host_id)
        else:
            groups["disabled_hosts"]["hosts"].append(host_id)

        # Group by ZFS capability
        if host_config.get("zfs_capable", False) or "zfs" in tags:
            groups["zfs_capable"]["hosts"].append(host_id)

        # Group by environment tags
        for tag in tags:
            tag = tag.lower()
            if tag in groups:
                groups[tag]["hosts"].append(host_id)
            else:
                # Create dynamic group for tag
                if tag not in groups:
                    groups[tag] = {"hosts": []}
                groups[tag]["hosts"].append(host_id)

        # Set host variables
        hostvars = {
            "ansible_host": hostname,
            "ansible_user": user,
            "ansible_port": port,
            "ansible_ssh_common_args": "-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null",
            "docker_mcp_enabled": enabled,
            "docker_mcp_tags": tags,
        }

        if identity_file:
            hostvars["ansible_ssh_private_key_file"] = identity_file

        # Docker MCP specific variables
        if host_config.get("compose_path"):
            hostvars["docker_compose_path"] = host_config["compose_path"]

        if host_config.get("appdata_path"):
            hostvars["docker_appdata_path"] = host_config["appdata_path"]

        if host_config.get("zfs_capable"):
            hostvars["zfs_capable"] = True
            hostvars["zfs_dataset"] = host_config.get("zfs_dataset", "")

        if host_config.get("description"):
            hostvars["description"] = host_config["description"]

        inventory["_meta"]["hostvars"][host_id] = hostvars

    # Remove empty groups and add to inventory
    for group_name, group_data in groups.items():
        if group_data.get("hosts") or group_data.get("children"):
            inventory[group_name] = group_data

    # Set up group relationships
    inventory["all"]["children"] = ["docker_hosts"]
    inventory["docker_hosts"]["children"] = ["enabled_hosts", "disabled_hosts"]

    return inventory


def main():
    """Main entry point for dynamic inventory."""

    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        # Return full inventory
        config = load_docker_mcp_config()
        inventory = build_ansible_inventory(config)
        print(json.dumps(inventory, indent=2))

    elif len(sys.argv) == 3 and sys.argv[1] == "--host":
        # Return host-specific vars (handled by _meta, so return empty)
        print(json.dumps({}))

    else:
        print("Usage: dynamic_inventory.py --list")
        print("       dynamic_inventory.py --host <hostname>")
        sys.exit(1)


if __name__ == "__main__":
    main()

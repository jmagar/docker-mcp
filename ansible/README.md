# Docker MCP Ansible Automation

This directory contains Ansible playbooks and configuration for automating Docker MCP operations across multiple hosts.

## Overview

The Ansible integration provides idempotent, parallel, and robust automation for:
- Host setup and SSH key distribution
- Docker cleanup operations
- Stack migrations between hosts
- Health monitoring and alerting
- Volume backups

## Directory Structure

```
ansible/
├── ansible.cfg              # Ansible configuration
├── inventory/
│   └── dynamic_inventory.py  # Dynamic inventory from hosts.yml
├── playbooks/
│   ├── host-setup.yml       # Host discovery and SSH setup
│   ├── docker-cleanup.yml   # Docker cleanup operations
│   ├── migrate-stack.yml    # Stack migration between hosts
│   ├── health-check.yml     # Health monitoring
│   └── backup-volumes.yml   # Volume backup operations
├── templates/
│   └── hosts.yml.j2         # Template for generating hosts.yml
├── group_vars/
│   ├── all.yml              # Global variables
│   └── production.yml       # Production-specific settings
├── host_vars/               # Host-specific variables
├── roles/                   # Custom Ansible roles
└── logs/                    # Ansible execution logs
```

## Quick Start

### 1. Host Setup (replaces setup-ssh-keys.sh)

```bash
# Auto-discover and setup hosts from SSH config
ansible-playbook playbooks/host-setup.yml

# Setup specific hosts
ansible-playbook playbooks/host-setup.yml -l "host1,host2"

# Dry run to see what would be done
ansible-playbook playbooks/host-setup.yml --check
```

### 2. Docker Cleanup

```bash
# Check cleanup opportunities (no changes made)
ansible-playbook playbooks/docker-cleanup.yml -e cleanup_level=check

# Safe cleanup (containers, networks, cache)
ansible-playbook playbooks/docker-cleanup.yml -e cleanup_level=safe

# Moderate cleanup (adds unused images)
ansible-playbook playbooks/docker-cleanup.yml -e cleanup_level=moderate

# Aggressive cleanup (adds volumes - DANGEROUS!)
ansible-playbook playbooks/docker-cleanup.yml -e cleanup_level=aggressive

# Parallel cleanup on specific hosts
ansible-playbook playbooks/docker-cleanup.yml -l "production" -e cleanup_level=safe
```

### 3. Stack Migration

```bash
# Dry run migration
ansible-playbook playbooks/migrate-stack.yml \
  -e source_host=old-server \
  -e target_host=new-server \
  -e stack=my-app \
  -e dry_run_mode=true

# Execute migration
ansible-playbook playbooks/migrate-stack.yml \
  -e source_host=old-server \
  -e target_host=new-server \
  -e stack=my-app

# Migration with custom options
ansible-playbook playbooks/migrate-stack.yml \
  -e source_host=old-server \
  -e target_host=new-server \
  -e stack=my-app \
  -e skip_stop=false \
  -e start_after_migration=true \
  -e cleanup_source=true
```

### 4. Health Monitoring

```bash
# Basic health check
ansible-playbook playbooks/health-check.yml

# Detailed health check with performance metrics
ansible-playbook playbooks/health-check.yml -e detailed=true

# Check specific hosts
ansible-playbook playbooks/health-check.yml -l "production"
```

### 5. Volume Backups

```bash
# Backup all volumes
ansible-playbook playbooks/backup-volumes.yml

# Backup specific stack
ansible-playbook playbooks/backup-volumes.yml -e stack=my-app

# ZFS-based backup (on ZFS-capable hosts)
ansible-playbook playbooks/backup-volumes.yml -e backup_method=zfs

# Custom backup location and retention
ansible-playbook playbooks/backup-volumes.yml \
  -e backup_path=/mnt/backups \
  -e retention=30
```

## Inventory Management

The dynamic inventory automatically converts your Docker MCP `hosts.yml` to Ansible inventory:

```bash
# View current inventory
ansible-inventory --list

# Test inventory and connectivity
ansible all -m ping

# Run commands on specific groups
ansible production -m shell -a "docker ps"
ansible zfs_capable -m shell -a "zfs list"
```

## Host Groups

Hosts are automatically grouped by:
- `all`: All configured hosts
- `enabled_hosts`: Only enabled hosts
- `disabled_hosts`: Disabled hosts
- `zfs_capable`: Hosts with ZFS support
- `production`: Hosts tagged as production
- `staging`: Hosts tagged as staging
- `development`: Hosts tagged as development
- Custom groups based on host tags

## Configuration

### Global Settings

Edit `group_vars/all.yml` for global defaults:

```yaml
# Cleanup thresholds
cleanup_thresholds:
  disk_usage_warning: 80
  disk_usage_critical: 90

# Health check settings
health_check:
  disk_warning_threshold: 80
  memory_warning_threshold: 85
```

### Environment-Specific Settings

Create `group_vars/[environment].yml` for environment-specific overrides:

```yaml
# group_vars/production.yml
cleanup_thresholds:
  disk_usage_warning: 70  # Stricter in production
  
backup:
  default_retention_days: 30  # Longer retention in production
```

### Host-Specific Settings

Create `host_vars/[hostname].yml` for host-specific overrides:

```yaml
# host_vars/web-server-1.yml
docker_compose_path: "/custom/compose/path"
docker_appdata_path: "/custom/data/path"
```

## Advanced Usage

### Running Playbooks with Extra Variables

```bash
# Override default settings
ansible-playbook playbooks/docker-cleanup.yml \
  -e cleanup_level=safe \
  -e parallel=true \
  -e dry_run_mode=false

# Use JSON for complex variables
ansible-playbook playbooks/migrate-stack.yml \
  -e '{"source_host":"old","target_host":"new","stack":"app","migration":{"timeout_minutes":180}}'
```

### Limiting Execution

```bash
# Run on specific hosts
ansible-playbook playbooks/health-check.yml -l "web-server-1,web-server-2"

# Run on host groups
ansible-playbook playbooks/docker-cleanup.yml -l "production"

# Skip specific hosts
ansible-playbook playbooks/health-check.yml -l "all:!staging"
```

### Parallel Execution

Ansible runs tasks in parallel by default (controlled by `forks` in ansible.cfg):

```bash
# Override parallel execution
ansible-playbook playbooks/docker-cleanup.yml -f 20  # 20 parallel processes
```

## Integration with Docker MCP Services

While these playbooks work standalone, they're designed to integrate with the Docker MCP Python services for:
- Centralized logging and monitoring
- Results aggregation
- Error handling and alerting
- Scheduled execution

## Troubleshooting

### Common Issues

1. **SSH Connection Issues**
   ```bash
   # Test SSH connectivity
   ansible all -m ping
   
   # Debug SSH issues
   ansible-playbook playbooks/host-setup.yml -vvv
   ```

2. **Inventory Not Loading**
   ```bash
   # Test dynamic inventory
   ./inventory/dynamic_inventory.py --list
   
   # Check hosts.yml format
   ansible-inventory --list --yaml
   ```

3. **Permission Issues**
   ```bash
   # Run with sudo (if needed)
   ansible-playbook playbooks/docker-cleanup.yml -b
   
   # Specify different user
   ansible-playbook playbooks/health-check.yml -u docker-user
   ```

### Debugging

```bash
# Verbose output
ansible-playbook playbooks/health-check.yml -v    # verbose
ansible-playbook playbooks/health-check.yml -vv   # more verbose  
ansible-playbook playbooks/health-check.yml -vvv  # debug

# Check mode (dry run)
ansible-playbook playbooks/docker-cleanup.yml --check

# Step through playbook
ansible-playbook playbooks/migrate-stack.yml --step

# Start at specific task
ansible-playbook playbooks/migrate-stack.yml --start-at-task "Create archive"
```

### Logs

Ansible logs are written to `logs/ansible.log` (configured in `ansible.cfg`).

## Best Practices

1. **Always test with --check first**
2. **Use dry runs for migrations**
3. **Monitor logs during execution**  
4. **Keep group_vars updated with your environment**
5. **Regular health checks to catch issues early**
6. **Backup before major operations**
7. **Use tags to organize hosts properly**

## Security Considerations

- SSH keys are managed securely through host configurations
- Playbooks run with minimal privileges (no sudo by default)
- Sensitive data should use Ansible Vault
- Regular audit of SSH access
- Logs contain operational data but no secrets

## Performance Optimization

- Adjust `forks` in ansible.cfg for your environment
- Use fact caching to reduce connection overhead
- Enable pipelining for faster SSH operations
- Group related tasks to minimize round trips
- Use async tasks for long-running operations

## Contributing

When adding new playbooks:
1. Follow existing naming conventions
2. Add appropriate error handling
3. Include dry-run support where applicable
4. Document variables in playbook header
5. Test with various host configurations
6. Update this README with usage examples
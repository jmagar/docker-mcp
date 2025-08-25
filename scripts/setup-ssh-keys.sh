#!/bin/bash

# Docker Manager MCP - SSH Key Distribution Script
# Robust, standalone SSH key setup with automatic host discovery
# Usage: ./scripts/setup-ssh-keys.sh [options]

set -Eeuo pipefail
# Minimal ERR trap (don't rely on functions not yet defined here)
trap 'echo "[ERROR] Unexpected failure at line $LINENO (exit=$?): ${BASH_COMMAND}" >&2' ERR

# Configuration (matching install.sh conventions)
DOCKER_MCP_DIR="${HOME}/.docker-mcp"
SSH_KEY_NAME="docker-mcp-key"
SSH_KEY_PATH="${DOCKER_MCP_DIR}/ssh/${SSH_KEY_NAME}"
CONFIG_DIR="${DOCKER_MCP_DIR}/config"
DATA_DIR="${DOCKER_MCP_DIR}/data"

# Additional settings
SSH_CONFIG="${SSH_CONFIG:-${HOME}/.ssh/config}"
PARALLEL_JOBS="${PARALLEL_JOBS:-10}"
# (removed) SCRIPT_DIR was unused

# Color codes (matching install.sh)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Command line options
BATCH_MODE=false
DRY_RUN=false
VERIFY_AFTER=false
CUSTOM_KEY=""
HOST_FILTER=""
VERBOSE=false

# print_header prints a colored banner header for the script (title and separators) to stdout.
print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Docker MCP SSH Key Distribution${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo
}

# print_success prints a green checkmark and the given message to stdout.
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

# print_error prints an error message prefixed with a red "✗" and resets terminal color.
print_error() {
    echo -e "${RED}✗${NC} $1"
}

# print_warning prints a warning message prefixed with a yellow warning icon and resets terminal color.
print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# print_info prints an informational message prefixed with a blue "ℹ" symbol and resets color; accepts a single string argument to display.
print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# print_verbose prints MESSAGE to stdout prefixed with a `[DEBUG]` tag when VERBOSE is true; no output is produced otherwise.
print_verbose() {
    if [ "$VERBOSE" = true ]; then
        echo -e "${BLUE}[DEBUG]${NC} $1"
    fi
}

# show_usage displays usage information, supported command-line options, and example invocations for setup-ssh-keys.sh.
show_usage() {
    cat << 'EOF'
Usage: setup-ssh-keys.sh [OPTIONS]

Automatically discover SSH hosts and distribute Docker MCP SSH keys.

OPTIONS:
    -h, --help              Show this help message
    -c, --config PATH       Use custom SSH config file (default: ~/.ssh/config)
    -k, --key PATH          Use existing SSH key instead of generating new one
    -b, --batch             Batch mode - no interactive prompts
    -d, --dry-run           Show what would be done without making changes
    -v, --verify            Verify SSH connectivity after setup
    -f, --filter PATTERN    Filter hosts by pattern (supports wildcards)
    -j, --jobs N            Number of parallel jobs (default: 10)
    --verbose               Enable verbose logging
    
EXAMPLES:
    # Basic usage - auto-discover and setup
    ./setup-ssh-keys.sh
    
    # Use custom SSH config
    ./setup-ssh-keys.sh --config /path/to/ssh/config
    
    # Use existing SSH key
    ./setup-ssh-keys.sh --key ~/.ssh/id_ed25519
    
    # Batch mode with host filtering
    ./setup-ssh-keys.sh --batch --filter "prod-*"
    
    # Test what would be done
    ./setup-ssh-keys.sh --dry-run
EOF
}

# parse_arguments parses command-line options and updates global flags and variables used by the script.
# Supports: -h|--help, -c|--config <file>, -k|--key <path>, -b|--batch, -d|--dry-run, -v|--verify,
# -f|--filter <pattern>, -j|--jobs <n>, and --verbose. On `--help` it prints usage and exits; on unknown
# options it prints an error, shows usage, and exits with non-zero status.
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_usage
                exit 0
                ;;
            -c|--config)
                SSH_CONFIG="$2"
                shift 2
                ;;
            -k|--key)
                CUSTOM_KEY="$2"
                shift 2
                ;;
            -b|--batch)
                BATCH_MODE=true
                shift
                ;;
            -d|--dry-run)
                DRY_RUN=true
                shift
                ;;
            -v|--verify)
                VERIFY_AFTER=true
                shift
                ;;
            -f|--filter)
                HOST_FILTER="$2"
                shift 2
                ;;
            -j|--jobs)
                PARALLEL_JOBS="$2"
                shift 2
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            *)
                print_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

# check_prerequisites verifies required CLI tools and environment before proceeding.
# It ensures `ssh`, `ssh-keygen`, and `ssh-keyscan` are installed (exits with non‑zero status if any are missing),
# warns if optional utilities (`ssh-copy-id`, `timeout`) are absent, detects GNU `parallel` and sets HAS_PARALLEL=true/false,
# and prints human-friendly status messages.
check_prerequisites() {
    echo -e "${BLUE}Checking prerequisites...${NC}"
    echo
    
    local missing_deps=0
    
    if ! command -v ssh &> /dev/null; then
        print_error "ssh is not installed"
        missing_deps=1
    else
        print_success "ssh is available"
    fi
    
    if ! command -v ssh-keygen &> /dev/null; then
        print_error "ssh-keygen is not installed"
        missing_deps=1
    else
        print_success "ssh-keygen is available"
    fi
    
    if ! command -v ssh-copy-id &> /dev/null; then
        print_warning "ssh-copy-id not found - will use manual method"
    else
        print_success "ssh-copy-id is available"
    fi
    
    if ! command -v ssh-keyscan &> /dev/null; then
        print_error "ssh-keyscan is not installed"
        missing_deps=1
    else
        print_success "ssh-keyscan is available"
    fi
    
    if ! command -v timeout &> /dev/null; then
        print_warning "timeout command not found - ssh-keyscan may hang on unreachable hosts"
    else
        print_success "timeout is available"
    fi
    
    # Check for GNU parallel (optional)
    if command -v parallel &> /dev/null; then
        print_success "GNU parallel is available (will use for faster distribution)"
        HAS_PARALLEL=true
    else
        print_info "GNU parallel not found - will use background jobs"
        HAS_PARALLEL=false
    fi
    
    if [ $missing_deps -eq 1 ]; then
        print_error "Missing required dependencies. Please install them and run this script again."
        exit 1
    fi
    
    print_success "Prerequisites check passed!"
    echo
}

# create_directories creates the Docker MCP directory layout (base dir, ssh, config, and data/logs) and reports actions.
# If the user's ~/.ssh/config exists, it symlinks it into the MCP ssh directory for host resolution. Respects DRY_RUN by only printing intended actions when set.
create_directories() {
    echo -e "${BLUE}Creating directory structure...${NC}"
    echo
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would create directories at ${DOCKER_MCP_DIR}"
        return
    fi
    
    mkdir -p "${DOCKER_MCP_DIR}"
    mkdir -p "${DOCKER_MCP_DIR}/ssh"
    mkdir -p "${CONFIG_DIR}"
    mkdir -p "${DATA_DIR}/logs"
    
    # Create symlink to SSH config if it exists (for host resolution)
    if [ -f "${HOME}/.ssh/config" ]; then
        ln -sf "${HOME}/.ssh/config" "${DOCKER_MCP_DIR}/ssh/config" 2>/dev/null || true
        print_verbose "Linked SSH config for host resolution"
    fi
    
    print_success "Created directory structure at ${DOCKER_MCP_DIR}"
    echo
}

# parse_ssh_config parses the SSH config file, extracts Host blocks (HostName, User, Port), filters out invalid or incomplete entries, applies an optional HOST_FILTER, and populates the DISCOVERED_HOSTS array with entries formatted as `host|effective_hostname|user|port`; returns non-zero if the config file is missing or no valid hosts are found.
parse_ssh_config() {
    echo -e "${BLUE}Parsing SSH configuration...${NC}"
    echo
    
    if [ ! -f "$SSH_CONFIG" ]; then
        print_error "SSH config file not found: $SSH_CONFIG"
        return 1
    fi
    
    print_info "Parsing SSH config: $SSH_CONFIG"
    
    local hosts=()
    local current_host=""
    local hostname=""
    local user=""
    local port="22"
    
    while IFS= read -r line; do
        # Skip empty lines and comments
        if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
            continue
        fi
        
        # Parse Host entries
        if [[ $line =~ ^Host[[:space:]]+(.+)$ ]]; then
            # Capture the new host name IMMEDIATELY before BASH_REMATCH gets overwritten
            local new_host="${BASH_REMATCH[1]}"
            
            # Process previous host if valid
            if [[ -n "$current_host" && -n "$user" ]]; then
                # Use hostname or fall back to host name (like Python parser)
                local effective_hostname="${hostname:-$current_host}"
                if is_valid_host "$current_host" "$effective_hostname"; then
                    hosts+=("$current_host|$effective_hostname|$user|$port")
                    print_verbose "Found valid host: $current_host ($effective_hostname)"
                else
                    print_verbose "Skipping invalid host: $current_host (hostname: '$hostname', user: '$user')"
                fi
            elif [[ -n "$current_host" ]]; then
                print_verbose "Skipping incomplete host: $current_host (hostname: '$hostname', user: '$user')"
            fi
            
            # Start new host
            current_host="$new_host"
            hostname=""
            user=""
            port="22"
            
        elif [[ $line =~ ^[[:space:]]*[Hh]ost[Nn]ame[[:space:]]+(.+)$ ]]; then
            hostname="${BASH_REMATCH[1]}"
            print_verbose "  Found hostname for $current_host: $hostname"
        elif [[ $line =~ ^[[:space:]]*[Uu]ser[[:space:]]+(.+)$ ]]; then
            user="${BASH_REMATCH[1]}"
            print_verbose "  Found user for $current_host: $user"
        elif [[ $line =~ ^[[:space:]]*[Pp]ort[[:space:]]+(.+)$ ]]; then
            port="${BASH_REMATCH[1]}"
            print_verbose "  Found port for $current_host: $port"
        fi
    done < "$SSH_CONFIG"
    
    # Don't forget the last host
    if [[ -n "$current_host" && -n "$user" ]]; then
        # Use hostname or fall back to host name (like Python parser)
        local effective_hostname="${hostname:-$current_host}"
        if is_valid_host "$current_host" "$effective_hostname"; then
            hosts+=("$current_host|$effective_hostname|$user|$port")
            print_verbose "Found valid host: $current_host ($effective_hostname)"
        else
            print_verbose "Skipping invalid host: $current_host (hostname: '$hostname', user: '$user')"
        fi
    elif [[ -n "$current_host" ]]; then
        print_verbose "Skipping incomplete host: $current_host (hostname: '$hostname', user: '$user')"
    fi
    
    if [ ${#hosts[@]} -eq 0 ]; then
        print_warning "No valid hosts found in SSH config"
        return 1
    fi
    
    # Apply host filter if specified
    if [ -n "$HOST_FILTER" ]; then
        local filtered_hosts=()
        for host_entry in "${hosts[@]}"; do
            local host_name=$(echo "$host_entry" | cut -d'|' -f1)
            if [[ "$host_name" == $HOST_FILTER ]]; then
                filtered_hosts+=("$host_entry")
            fi
        done
        hosts=("${filtered_hosts[@]}")
        print_info "Applied filter '$HOST_FILTER': ${#hosts[@]} hosts match"
    fi
    
    print_success "Found ${#hosts[@]} valid host(s) for SSH key distribution"
    echo
    
    # Export for use in other functions
    DISCOVERED_HOSTS=("${hosts[@]}")
}

# is_valid_host returns success (0) if the given host should be considered for SSH key distribution; it filters out wildcard host patterns, localhost variants (localhost, 127.0.0.1, ::1), and common VCS hosts (github.com, gitlab.com, bitbucket.org).
is_valid_host() {
    local host_name="$1"
    local hostname="$2"
    
    # Skip wildcards
    if [[ "$host_name" =~ [*?] ]]; then
        print_verbose "Skipping wildcard host: $host_name"
        return 1
    fi
    
    # Skip localhost variants
    if [[ "$hostname" =~ ^(localhost|127\.0\.0\.1|::1)$ ]]; then
        print_verbose "Skipping localhost: $host_name ($hostname)"
        return 1
    fi
    
    # Skip common VCS hosts
    if [[ "$hostname" =~ (github\.com|gitlab\.com|bitbucket\.org)$ ]]; then
        print_verbose "Skipping VCS host: $host_name ($hostname)"
        return 1
    fi
    
    return 0
}

# generate_or_find_key selects or creates the SSH key to use: it prefers a provided CUSTOM_KEY, falls back to an existing Docker MCP key, or (unless in DRY_RUN) generates a new ed25519 key, verifies the public key exists, exports the chosen path as ACTIVE_SSH_KEY, and returns non-zero on failure.
generate_or_find_key() {
    echo -e "${BLUE}Managing SSH key...${NC}"
    echo
    
    local key_to_use=""
    
    if [ -n "$CUSTOM_KEY" ]; then
        if [ -f "$CUSTOM_KEY" ]; then
            key_to_use="$CUSTOM_KEY"
            print_info "Using custom SSH key: $CUSTOM_KEY"
        else
            print_error "Custom key not found: $CUSTOM_KEY"
            return 1
        fi
    elif [ -f "$SSH_KEY_PATH" ]; then
        key_to_use="$SSH_KEY_PATH"
        print_info "Using existing Docker MCP key: $SSH_KEY_PATH"
    else
        if [ "$DRY_RUN" = true ]; then
            print_info "[DRY RUN] Would generate new SSH key at $SSH_KEY_PATH"
            key_to_use="$SSH_KEY_PATH"
        else
            print_info "Generating new Docker MCP SSH key..."
            chmod 700 "$(dirname "$SSH_KEY_PATH")" || true
            ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "docker-mcp:$(hostname -f 2>/dev/null || hostname)"
            chmod 600 "$SSH_KEY_PATH"
            chmod 644 "$SSH_KEY_PATH.pub"
            key_to_use="$SSH_KEY_PATH"
            print_success "Generated new SSH key: $SSH_KEY_PATH"
        fi
    fi
    
    # Verify public key exists
    if [ "$DRY_RUN" != true ] && [ ! -f "${key_to_use}.pub" ]; then
        print_error "Public key not found: ${key_to_use}.pub"
        return 1
    fi
    
    # Export for use in distribution
    ACTIVE_SSH_KEY="$key_to_use"
    echo
}

# scan_host_keys scans SSH host keys for every entry in DISCOVERED_HOSTS and appends them to the user's ~/.ssh/known_hosts to pre-populate host keys (skips actual scanning when DRY_RUN=true). It uses a 10-second timeout per host, respects per-host ports, reports per-host success/failure, and leaves failed hosts to be confirmed interactively during key distribution.
scan_host_keys() {
    echo -e "${BLUE}Scanning SSH host keys...${NC}"
    echo
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would scan host keys for ${#DISCOVERED_HOSTS[@]} hosts"
        return 0
    fi
    
    local scanned=0
    local failed=0
    
    for host_entry in "${DISCOVERED_HOSTS[@]}"; do
        IFS='|' read -r host_name hostname user port <<< "$host_entry"
        
        # Use appropriate port option
        local scan_opts=""
        if [ "$port" != "22" ]; then
            scan_opts="-p $port"
        fi
        
        echo -n "Scanning keys for $host_name ($hostname:$port)... "
        print_verbose "Running: timeout 10 ssh-keyscan -H ${scan_opts:-} $hostname"
        
        # Scan and add to known_hosts (with 10 second timeout)
        if timeout 10 ssh-keyscan -H $scan_opts "$hostname" >> ~/.ssh/known_hosts 2>/dev/null; then
            print_success "OK"
            : $((scanned++))
        else
            print_warning "Failed (will prompt during distribution)"
            : $((failed++))
        fi
        
        print_verbose "Completed scan for $host_name"
    done
    
    echo
    print_success "Scanned $scanned host(s) successfully"
    
    if [ $failed -gt 0 ]; then
        print_warning "$failed host(s) could not be scanned"
        print_info "You'll be prompted to accept keys for these hosts during distribution"
    fi
    
    echo
}

# show_distribution_plan displays the SSH key to be distributed, the list of target hosts (formatted as `user@hostname:port`), and the computed number of parallel jobs.
show_distribution_plan() {
    echo -e "${BLUE}Distribution Plan${NC}"
    echo "=================="
    echo "SSH Key: $ACTIVE_SSH_KEY"
    echo "Hosts to configure:"
    echo
    
    for host_entry in "${DISCOVERED_HOSTS[@]}"; do
        IFS='|' read -r host_name hostname user port <<< "$host_entry"
        echo "  • $host_name ($user@$hostname:$port)"
    done
    echo
    local actual_jobs=$((${#DISCOVERED_HOSTS[@]} < PARALLEL_JOBS ? ${#DISCOVERED_HOSTS[@]} : PARALLEL_JOBS))
    echo "Parallel jobs: $actual_jobs (max: $PARALLEL_JOBS)"
    echo
}

# confirm_distribution prompts the user to confirm proceeding with SSH key distribution unless BATCH_MODE is true; returns 0 when confirmed or in batch mode, 1 when cancelled.
confirm_distribution() {
    if [ "$BATCH_MODE" = true ]; then
        print_info "Batch mode enabled - proceeding with key distribution"
        return 0
    fi
    
    read -p "Do you want to proceed with SSH key distribution? (y/N): " -n 1 -r
    echo
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "SSH key distribution cancelled by user"
        return 1
    fi
    
    return 0
}

# distribute_keys_parallel distributes the active SSH public key to all discovered hosts, in parallel (GNU parallel if available, otherwise background jobs).
# 
# Performs a dry-run when DRY_RUN=true (prints planned actions and exits). For each host it first attempts `ssh-copy-id` and, if unavailable or failing, falls back to appending the public key to the remote `~/.ssh/authorized_keys`. On success each host is recorded for later config generation (SUCCESSFUL_HOSTS). Prints per-host results and a final summary of successes and failures and suggests a manual `ssh-copy-id` command for failures.
# 
# Side effects:
# - Uses ACTIVE_SSH_KEY and its public key (`${ACTIVE_SSH_KEY}.pub`) to install keys on remote hosts.
# - May create and remove a temporary directory for tracking results.
# - Exports SUCCESSFUL_HOSTS list of hosts that succeeded.
# - Emits colored status output to stdout/stderr.
# 
# Notes:
# - Respects PARALLEL_JOBS for concurrency.
# - Requires network access and valid SSH credentials to perform installations.
distribute_keys_parallel() {
    echo -e "${BLUE}Distributing SSH keys...${NC}"
    echo
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would distribute keys to ${#DISCOVERED_HOSTS[@]} hosts"
        for host_entry in "${DISCOVERED_HOSTS[@]}"; do
            IFS='|' read -r host_name hostname user port <<< "$host_entry"
            print_info "[DRY RUN] Would copy key to $user@$hostname:$port"
        done
        return 0
    fi
    
    local success_count=0
    local failure_count=0
    local failed_hosts=()
    
    # Create temporary files for tracking
    local temp_dir=$(mktemp -d)
    local success_file="$temp_dir/success"
    local failure_file="$temp_dir/failure"
    
    # Function to distribute key to a single host
    distribute_to_host() {
        local host_entry="$1"
        IFS='|' read -r host_name hostname user port <<< "$host_entry"
        
        local ssh_target="$user@$hostname"
        local ssh_opts=(-o BatchMode=yes)
        if [ "$port" != "22" ]; then
            ssh_opts+=(-p "$port")
        fi
        
        echo -n "Distributing to $host_name... "
        
        # Try ssh-copy-id first
        if command -v ssh-copy-id &> /dev/null; then
            if ssh-copy-id "${ssh_opts[@]}" -i "${ACTIVE_SSH_KEY}" "$ssh_target" >/dev/null 2>&1; then
                echo "$host_entry" >> "$success_file"
                print_success "Success"
                return 0
            fi
        fi
        
        # Fallback to manual method
        if cat "${ACTIVE_SSH_KEY}.pub" | ssh "${ssh_opts[@]}" "$ssh_target" "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" >/dev/null 2>&1; then
            echo "$host_entry" >> "$success_file"
            print_success "Success"
            return 0
        else
            echo "$host_entry" >> "$failure_file"
            print_error "Failed"
            return 1
        fi
    }
    
    # Export function for parallel execution
    export -f distribute_to_host
    export -f print_success
    export -f print_error
    export ACTIVE_SSH_KEY
    export GREEN RED NC
    
    if [ "$HAS_PARALLEL" = true ]; then
        # Use GNU parallel for distribution
        printf '%s\n' "${DISCOVERED_HOSTS[@]}" | parallel -j "$PARALLEL_JOBS" distribute_to_host
    else
        # Use background jobs for parallel execution
        local pids=()
        for host_entry in "${DISCOVERED_HOSTS[@]}"; do
            # Limit concurrent jobs
            while [ ${#pids[@]} -ge $PARALLEL_JOBS ]; do
                for i in "${!pids[@]}"; do
                    if ! kill -0 "${pids[$i]}" 2>/dev/null; then
                        unset "pids[$i]"
                    fi
                done
                pids=("${pids[@]}") # Re-index array
                sleep 0.1
            done
            
            distribute_to_host "$host_entry" &
            pids+=($!)
        done
        
        # Wait for all background jobs to complete
        for pid in "${pids[@]}"; do
            wait "$pid"
        done
    fi
    
    # Count results
    if [ -f "$success_file" ]; then
        success_count=$(wc -l < "$success_file")
    fi
    
    if [ -f "$failure_file" ]; then
        failure_count=$(wc -l < "$failure_file")
        while IFS= read -r host_entry; do
            failed_hosts+=("$host_entry")
        done < "$failure_file"
    fi
    
    echo
    print_success "Successfully distributed keys to $success_count host(s)"
    
    if [ $failure_count -gt 0 ]; then
        print_warning "Failed to distribute keys to $failure_count host(s):"
        for host_entry in "${failed_hosts[@]}"; do
            IFS='|' read -r host_name hostname user port <<< "$host_entry"
            echo "  • $host_name ($user@$hostname:$port)"
        done
        echo
        print_info "You can manually copy the key using:"
        echo "  ssh-copy-id -i ${ACTIVE_SSH_KEY} user@host"
    fi
    
    # Export results for hosts config generation BEFORE cleanup
    SUCCESSFUL_HOSTS=()
    if [ -f "$success_file" ]; then
        while IFS= read -r host_entry; do
            SUCCESSFUL_HOSTS+=("$host_entry")
        done < "$success_file"
    fi
    
    # Cleanup
    rm -rf "$temp_dir"
    
    echo
}

generate_hosts_config() {
    echo -e "${BLUE}Generating Docker MCP hosts configuration...${NC}"
    echo
    
    local config_file="${CONFIG_DIR}/hosts.yml"
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would generate hosts config at $config_file"
        return 0
    fi
    
    # Create header
    cat > "$config_file" << 'EOF'
# Docker Manager MCP Configuration
# Auto-generated from SSH config by setup-ssh-keys.sh

hosts:
EOF
    
    if [ ${#SUCCESSFUL_HOSTS[@]} -eq 0 ]; then
        print_warning "No successful hosts to add to configuration"
        return 1
    fi
    
    # Add each successful host
    for host_entry in "${SUCCESSFUL_HOSTS[@]}"; do
        IFS='|' read -r host_name hostname user port <<< "$host_entry"
        
        cat >> "$config_file" << EOF
  ${host_name}:
    hostname: ${hostname}
    user: ${user}
    port: ${port}
    identity_file: ${SSH_KEY_PATH}
    description: "Auto-imported from SSH config"
    tags: ["auto-imported", "ssh-config"]
    enabled: true
    
EOF
    done
    
    print_success "Generated hosts configuration with ${#SUCCESSFUL_HOSTS[@]} host(s)"
    print_info "Configuration saved to: $config_file"
    echo
}

verify_connectivity() {
    echo -e "${BLUE}Verifying SSH connectivity...${NC}"
    echo
    
    if [ ${#SUCCESSFUL_HOSTS[@]} -eq 0 ]; then
        print_warning "No hosts to verify"
        return 0
    fi
    
    local verified=0
    local failed=0
    
    for host_entry in "${SUCCESSFUL_HOSTS[@]}"; do
        IFS='|' read -r host_name hostname user port <<< "$host_entry"
        
        echo -n "Testing $host_name... "
        
        local ssh_opts=(-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no)
        if [ "$port" != "22" ]; then
            ssh_opts+=(-p "$port")
        fi
        
        if ssh "${ssh_opts[@]}" -i "$ACTIVE_SSH_KEY" "$user@$hostname" "echo 'Connection successful'" >/dev/null 2>&1; then
            print_success "OK"
            : $((verified++))
        else
            print_error "Failed"
            : $((failed++))
        fi
    done
    
    echo
    print_success "Verified connectivity to $verified host(s)"
    
    if [ $failed -gt 0 ]; then
        print_warning "$failed host(s) failed connectivity test"
    fi
    
    echo
}

print_completion() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}SSH Key Distribution Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo
    echo "Summary:"
    echo "  SSH Key: $ACTIVE_SSH_KEY"
    echo "  Hosts configured: ${#SUCCESSFUL_HOSTS[@]}"
    echo "  Configuration: ${CONFIG_DIR}/hosts.yml"
    echo
    echo "Next steps:"
    echo "  1. Run Docker MCP installer: ./install.sh"
    echo "  2. Or start Docker MCP manually:"
    echo "     cd ~/.docker-mcp && docker compose up -d"
    echo
    echo "Useful commands:"
    echo "  Test SSH access: ssh -i $ACTIVE_SSH_KEY user@host"
    echo "  View hosts config: cat ${CONFIG_DIR}/hosts.yml"
    echo
}

main() {
    parse_arguments "$@"
    print_header
    
    # Special case for verify-only mode
    if [ "$VERIFY_ONLY" = true ]; then
        if [ -f "${CONFIG_DIR}/hosts.yml" ]; then
            # Parse existing config and verify
            print_info "Verifying existing configuration..."
            # Implementation would parse existing config
            print_warning "Verify-only mode not fully implemented yet"
            exit 0
        else
            print_error "No existing configuration found to verify"
            exit 1
        fi
    fi
    
    check_prerequisites
    create_directories
    
    if ! parse_ssh_config; then
        print_error "Failed to parse SSH configuration"
        exit 1
    fi
    
    if ! generate_or_find_key; then
        print_error "Failed to manage SSH key"
        exit 1
    fi
    
    scan_host_keys
    show_distribution_plan
    
    if ! confirm_distribution; then
        print_info "Exiting without making changes"
        exit 0
    fi
    
    distribute_keys_parallel
    generate_hosts_config
    
    # Verify connectivity unless in batch mode
    if [ "$BATCH_MODE" != true ]; then
        verify_connectivity
    fi
    
    print_completion
}

# Run main function with all arguments
main "$@"
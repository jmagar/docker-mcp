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
VERIFY_ONLY=false
CUSTOM_KEY=""
HOST_FILTER=""
VERBOSE=false

# Functions (matching install.sh style)
print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Docker MCP SSH Key Distribution${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_verbose() {
    if [ "$VERBOSE" = true ]; then
        echo -e "${BLUE}[DEBUG]${NC} $1"
    fi
}

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
    -V, --verify-only       Only verify existing configuration, don't set up keys
    -f, --filter PATTERN    Filter hosts by pattern (supports wildcards)
    -j, --jobs N            Number of parallel jobs (default: 10)
    --verbose               Enable verbose logging
    
EXAMPLES:
    # Basic usage - auto-discover and setup
    ./setup-ssh-keys.sh
    
    # Verify existing configuration only
    ./setup-ssh-keys.sh --verify-only
    
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
            -V|--verify-only)
                VERIFY_ONLY=true
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
    
    # Prefer GNU timeout; fall back to gtimeout (macOS coreutils)
    if command -v timeout >/dev/null 2>&1; then
        TIMEOUT_CMD="timeout"
        print_success "timeout is available"
    elif command -v gtimeout >/dev/null 2>&1; then
        TIMEOUT_CMD="gtimeout"
        print_success "gtimeout is available (using as timeout)"
    else
        TIMEOUT_CMD=""
        print_warning "timeout/gtimeout not found - ssh-keyscan may hang on unreachable hosts"
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

create_directories() {
    echo -e "${BLUE}Creating directory structure...${NC}"
    echo
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would create directories at ${DOCKER_MCP_DIR}"
        return
    fi
    
    # Create directories with error checking
    local dirs=(
        "${DOCKER_MCP_DIR}"
        "${DOCKER_MCP_DIR}/ssh"
        "${CONFIG_DIR}"
        "${DATA_DIR}/logs"
        "${HOME}/.ssh"
    )
    
    for dir in "${dirs[@]}"; do
        if ! mkdir -p "$dir" 2>/dev/null; then
            print_error "Failed to create directory: $dir"
            return 1
        fi
        print_verbose "Created directory: $dir"
    done

    # Set proper permissions with verification
    local secure_dirs=(
        "${DOCKER_MCP_DIR}/ssh:700"
        "${HOME}/.ssh:700"
        "${DOCKER_MCP_DIR}:755"
        "${CONFIG_DIR}:755"
        "${DATA_DIR}:755"
    )
    
    for dir_perm in "${secure_dirs[@]}"; do
        local dir
        dir="${dir_perm%:*}"
        local perm
        perm="${dir_perm##*:}"
        
        if [ -d "$dir" ]; then
            if chmod "$perm" "$dir" 2>/dev/null; then
                print_verbose "Set permissions $perm on $dir"
            else
                print_warning "Failed to set permissions $perm on $dir"
            fi
            
            # Verify permissions were set correctly
            if [ "$VERBOSE" = true ]; then
                local actual_perm
                actual_perm=$(stat -c "%a" "$dir" 2>/dev/null || stat -f "%Lp" "$dir" 2>/dev/null || echo "unknown")
                if [ "$actual_perm" != "$perm" ] && [ "$actual_perm" != "unknown" ]; then
                    print_warning "Directory $dir has permissions $actual_perm, expected $perm"
                fi
            fi
        fi
    done

    # Create symlink to SSH config if it exists (for host resolution)
    if [ -f "${HOME}/.ssh/config" ]; then
        if ln -sf "${HOME}/.ssh/config" "${DOCKER_MCP_DIR}/ssh/config" 2>/dev/null; then
            print_verbose "Linked SSH config for host resolution"
        else
            print_warning "Failed to link SSH config"
        fi
    fi
    
    print_success "Created directory structure at ${DOCKER_MCP_DIR}"
    echo
}

get_ssh_options() {
    local purpose="${1:-default}"  # default, verification, key_distribution
    local port="${2:-22}"
    local -a ssh_opts
    
    # Common base options for all SSH operations
    ssh_opts=(
        -o BatchMode=yes
        -o ConnectTimeout=10
        -o LogLevel=ERROR
        -o UserKnownHostsFile="${HOME}/.ssh/known_hosts"
        -o IdentitiesOnly=yes
    )
    
    # StrictHostKeyChecking - prefer accept-new where supported; fall back to no
    if ssh -G localhost 2>/dev/null | grep -qi 'stricthostkeychecking.*accept-new'; then
        ssh_opts+=(-o StrictHostKeyChecking=accept-new)
    else
        ssh_opts+=(-o StrictHostKeyChecking=no)
    fi
    
    # Port-specific options
    if [ "$port" != "22" ]; then
        ssh_opts+=(-p "$port")
    fi
    
    # Purpose-specific options
    case "$purpose" in
        "verification")
            # Additional options for connectivity verification
            ssh_opts+=(-o PasswordAuthentication=no)
            ssh_opts+=(-o PubkeyAuthentication=yes)
            ;;
        "key_distribution")
            # More permissive for initial key setup
            ssh_opts+=(-o PreferredAuthentications=password,publickey)
            ;;
        *)
            # Default SSH options
            ;;
    esac
    
    printf '%s\n' "${ssh_opts[@]}"
}

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
            local host_name
            host_name="$(echo "$host_entry" | cut -d'|' -f1)"
            # Intentionally unquoted to allow wildcard matching in --filter (SC2053)
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
            
            # Ensure parent directory exists with proper permissions
            if ! mkdir -p "$(dirname "$SSH_KEY_PATH")" 2>/dev/null; then
                print_error "Failed to create SSH key directory: $(dirname "$SSH_KEY_PATH")"
                return 1
            fi
            
            if ! chmod 700 "$(dirname "$SSH_KEY_PATH")"; then
                print_error "Failed to set permissions on SSH key directory"
                return 1
            fi
            
            # Generate key with enhanced security
            local hostname_info
            hostname_info="$(hostname -f 2>/dev/null || hostname || echo "unknown")"
            local key_comment="docker-mcp:${hostname_info}:$(date +%Y%m%d)"
            
            print_verbose "Generating Ed25519 SSH key with comment: $key_comment"
            
            if ! ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "$key_comment" -q; then
                print_error "Failed to generate SSH key"
                return 1
            fi
            
            # Set strict permissions on both private and public key
            if ! chmod 600 "$SSH_KEY_PATH"; then
                print_error "Failed to set permissions on private key"
                return 1
            fi
            
            if ! chmod 644 "$SSH_KEY_PATH.pub"; then
                print_error "Failed to set permissions on public key"
                return 1
            fi
            
            # Validate the generated key
            if ! ssh-keygen -l -f "$SSH_KEY_PATH" >/dev/null 2>&1; then
                print_error "Generated SSH key validation failed"
                return 1
            fi
            
            # Get key fingerprint for verification
            local key_fingerprint
            key_fingerprint=$(ssh-keygen -l -f "$SSH_KEY_PATH" 2>/dev/null | awk '{print $2}')
            
            key_to_use="$SSH_KEY_PATH"
            print_success "Generated new SSH key: $SSH_KEY_PATH"
            print_verbose "Key fingerprint: $key_fingerprint"
            print_verbose "Key comment: $key_comment"
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
        
        # Build ssh-keyscan args
        local -a scan_cmd=(ssh-keyscan -H)
        if [ "$port" != "22" ]; then
            scan_cmd+=(-p "$port")
        fi
        
        echo -n "Scanning keys for $host_name ($hostname:$port)... "
        print_verbose "Running: ssh-keyscan with 10s timeout if available"
        
        # Ensure ~/.ssh exists and create known_hosts if needed
        mkdir -p "${HOME}/.ssh"
        touch "${HOME}/.ssh/known_hosts"
        chmod 600 "${HOME}/.ssh/known_hosts"
        
        # Remove any existing entries for this host
        if [ "$port" = "22" ]; then
            ssh-keygen -R "$hostname" >/dev/null 2>&1 || true
        else
            ssh-keygen -R "[$hostname]:$port" >/dev/null 2>&1 || true
        fi

        # Scan and add to known_hosts with enhanced timeout and validation
        local scan_success=false
        local scan_output
        local temp_scan_file
        temp_scan_file=$(mktemp)
        
        # Try scan with timeout (up to 2 retries)
        for attempt in 1 2; do
            if [ -n "${TIMEOUT_CMD:-}" ]; then
                if scan_output=$("${TIMEOUT_CMD}" 10 "${scan_cmd[@]}" "$hostname" 2>&1); then
                    scan_success=true
                    break
                fi
            else
                if scan_output=$("${scan_cmd[@]}" "$hostname" 2>&1); then
                    scan_success=true
                    break
                fi
            fi
            
            # Brief pause before retry
            [ "$attempt" = "1" ] && sleep 1
        done
        
        if [ "$scan_success" = "true" ] && [ -n "$scan_output" ]; then
            # Validate scan output contains key data
            if echo "$scan_output" | grep -q "ssh-"; then
                # Add to known_hosts and remove duplicates
                echo "$scan_output" >> "${HOME}/.ssh/known_hosts"
                
                # Deduplicate known_hosts file
                if [ -f "${HOME}/.ssh/known_hosts" ]; then
                    sort "${HOME}/.ssh/known_hosts" | uniq > "$temp_scan_file"
                    mv "$temp_scan_file" "${HOME}/.ssh/known_hosts"
                    chmod 600 "${HOME}/.ssh/known_hosts"
                fi
                
                print_success "OK"
                : $((scanned++))
                
                print_verbose "Added $(echo "$scan_output" | wc -l) key(s) for $hostname"
            else
                print_warning "Invalid key data returned"
                : $((failed++))
            fi
        else
            print_warning "Failed (will prompt during distribution)"
            print_verbose "Scan error: ${scan_output:-timeout or connection failed}"
            : $((failed++))
        fi
        
        # Clean up temp file
        rm -f "$temp_scan_file"
        
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
    local actual_jobs
    actual_jobs=$((${#DISCOVERED_HOSTS[@]} < PARALLEL_JOBS ? ${#DISCOVERED_HOSTS[@]} : PARALLEL_JOBS))
    echo "Parallel jobs: $actual_jobs (max: $PARALLEL_JOBS)"
    echo
}

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
    local temp_dir
    temp_dir=$(mktemp -d)
    local success_file="$temp_dir/success"
    local failure_file="$temp_dir/failure"
    
    # Function to distribute key to a single host
    distribute_to_host() {
        local host_entry="$1"
        IFS='|' read -r host_name hostname user port <<< "$host_entry"
        
        local ssh_target="$user@$hostname"
        # Get standardized SSH options for key distribution
        local -a ssh_opts
        mapfile -t ssh_opts < <(get_ssh_options "key_distribution" "$port")
        
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
        if ssh "${ssh_opts[@]}" "$ssh_target" "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" < "${ACTIVE_SSH_KEY}.pub" >/dev/null 2>&1; then
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
    
    # Add each successful host with proper YAML quoting
    for host_entry in "${SUCCESSFUL_HOSTS[@]}"; do
        IFS='|' read -r host_name hostname user port <<< "$host_entry"
        
        # Escape and quote values for YAML safety
        local safe_host_name
        local safe_hostname
        local safe_user
        local safe_identity_file
        local safe_description
        
        # Quote host name if it contains special characters
        if [[ "$host_name" =~ [[:space:][:punct:]] ]]; then
            safe_host_name="\"$host_name\""
        else
            safe_host_name="$host_name"
        fi
        
        # Always quote hostname, user, and paths for safety
        safe_hostname="\"${hostname//\"/\\\"}\""
        safe_user="\"${user//\"/\\\"}\""
        safe_identity_file="\"${ACTIVE_SSH_KEY//\"/\\\"}\""
        safe_description="\"Auto-imported from SSH config on $(date '+%Y-%m-%d %H:%M:%S')\""
        
        cat >> "$config_file" << EOF
  ${safe_host_name}:
    hostname: ${safe_hostname}
    user: ${safe_user}
    port: ${port}
    identity_file: ${safe_identity_file}
    description: ${safe_description}
    tags: ["auto-imported", "ssh-config"]
    enabled: true

EOF
    done
    
    print_success "Generated hosts configuration with ${#SUCCESSFUL_HOSTS[@]} host(s)"
    print_info "Configuration saved to: $config_file"
    echo
}

load_hosts_from_config() {
    local config_file="${CONFIG_DIR}/hosts.yml"
    local -a config_hosts=()
    
    if [ ! -f "$config_file" ]; then
        print_error "No existing configuration found at $config_file"
        return 1
    fi
    
    print_info "Loading hosts from existing configuration..."
    
    # Simple YAML parsing for hosts (assumes basic structure)
    while IFS= read -r line; do
        # Skip empty lines and comments
        [[ -z "${line// }" || "$line" =~ ^[[:space:]]*# ]] && continue
        
        # Look for host entries (indented, followed by colon)
        if [[ "$line" =~ ^[[:space:]]+([^:[:space:]]+):[[:space:]]*$ ]]; then
            local host_name="${BASH_REMATCH[1]}"
            local hostname="" user="" port="22"
            
            # Read the next few lines to get hostname, user, port
            while IFS= read -r subline; do
                [[ -z "${subline// }" ]] && break
                [[ ! "$subline" =~ ^[[:space:]]+ ]] && break
                
                if [[ "$subline" =~ ^[[:space:]]+hostname:[[:space:]]*(.+)$ ]]; then
                    hostname="${BASH_REMATCH[1]// }"
                elif [[ "$subline" =~ ^[[:space:]]+user:[[:space:]]*(.+)$ ]]; then
                    user="${BASH_REMATCH[1]// }"
                elif [[ "$subline" =~ ^[[:space:]]+port:[[:space:]]*([0-9]+)$ ]]; then
                    port="${BASH_REMATCH[1]}"
                fi
            done <<< "$(tail -n +$(($(grep -n "^[[:space:]]*${host_name}:" "$config_file" | cut -d: -f1) + 1)) "$config_file")"
            
            if [ -n "$hostname" ] && [ -n "$user" ]; then
                config_hosts+=("${host_name}|${hostname}|${user}|${port}")
            fi
        fi
    done < "$config_file"
    
    if [ ${#config_hosts[@]} -eq 0 ]; then
        print_warning "No valid host configurations found"
        return 1
    fi
    
    # Copy to SUCCESSFUL_HOSTS for verification
    SUCCESSFUL_HOSTS=("${config_hosts[@]}")
    print_info "Loaded ${#SUCCESSFUL_HOSTS[@]} host(s) from configuration"
    return 0
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
    local ssh_key_to_use="$ACTIVE_SSH_KEY"
    
    # For verify-only mode, try to find the SSH key from config
    if [ "$VERIFY_ONLY" = true ] && [ -z "$CUSTOM_KEY" ]; then
        if [ -f "$SSH_KEY_PATH" ]; then
            ssh_key_to_use="$SSH_KEY_PATH"
            print_info "Using SSH key: $ssh_key_to_use"
        else
            print_error "SSH key not found at $SSH_KEY_PATH"
            return 1
        fi
    fi
    
    for host_entry in "${SUCCESSFUL_HOSTS[@]}"; do
        IFS='|' read -r host_name hostname user port <<< "$host_entry"
        
        echo -n "Testing $host_name ($user@$hostname:$port)... "
        
        # Get standardized SSH options for verification
        local -a ssh_opts
        mapfile -t ssh_opts < <(get_ssh_options "verification" "$port")
        
        if ssh "${ssh_opts[@]}" -i "$ssh_key_to_use" "$user@$hostname" "echo 'Connection successful'" >/dev/null 2>&1; then
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
        return 1
    fi
    
    echo
    return 0
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
        print_info "Verify-only mode: checking existing configuration..."
        
        if ! load_hosts_from_config; then
            exit 1
        fi
        
        if verify_connectivity; then
            print_success "All configured hosts are accessible!"
            exit 0
        else
            print_error "Some hosts failed connectivity test"
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
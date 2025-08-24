#!/bin/bash

# Docker Manager MCP - Enhanced Automated Installer
# This script sets up Docker Manager MCP with automatic SSH key configuration
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/install.sh | bash
#
# For security-conscious users, download and inspect first:
#   curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/install.sh -o install.sh
#   # Inspect the script
#   bash install.sh
#
# Environment variables:
#   VERBOSE=true          # Enable verbose output
#   QUIET=true           # Suppress non-error output
#   DRY_RUN=true         # Show what would be done without doing it
#   SKIP_SSH_COPY=true   # Skip SSH key distribution
#   FORCE_REINSTALL=true # Force reinstall even if already installed

set -euo pipefail

# Global installation state
INSTALLATION_STARTED=false
CLEANUP_ON_EXIT=true
INSTALL_STATE_FILE=""

# Installation options (can be set via environment variables)
VERBOSE=${VERBOSE:-false}
QUIET=${QUIET:-false}
DRY_RUN=${DRY_RUN:-false}
SKIP_SSH_COPY=${SKIP_SSH_COPY:-false}
FORCE_REINSTALL=${FORCE_REINSTALL:-false}

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Detect OS and set platform-specific variables
OS_TYPE="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS_TYPE="linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS_TYPE="macos"
elif [[ "$OSTYPE" == "freebsd"* ]] || [[ "$OSTYPE" == "openbsd"* ]] || [[ "$OSTYPE" == "netbsd"* ]]; then
    OS_TYPE="bsd"
fi

# Configuration
DOCKER_MCP_DIR="${HOME}/.docker-mcp"
SSH_KEY_NAME="docker-mcp-key"
SSH_KEY_PATH="${DOCKER_MCP_DIR}/ssh/${SSH_KEY_NAME}"
CONFIG_DIR="${DOCKER_MCP_DIR}/config"
DATA_DIR="${DOCKER_MCP_DIR}/data"
COMPOSE_URL="https://raw.githubusercontent.com/jmagar/docker-mcp/main/docker-compose.yaml"
EXAMPLE_CONFIG_URL="https://raw.githubusercontent.com/jmagar/docker-mcp/main/config/hosts.example.yml"

# Expected checksums (update these when files change)
COMPOSE_CHECKSUM=""  # TODO: Add actual checksums
EXAMPLE_CONFIG_CHECKSUM=""

# Cleanup and error handling functions
cleanup_on_error() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]] && [[ "$CLEANUP_ON_EXIT" == "true" ]] && [[ "$INSTALLATION_STARTED" == "true" ]]; then
        print_error "Installation failed with exit code $exit_code. Cleaning up..."
        cleanup_partial_installation
    fi
}

cleanup_partial_installation() {
    [[ "$QUIET" != "true" ]] && print_info "Performing cleanup of partial installation..."
    
    # Stop services if they were started
    if [[ -f "${DOCKER_MCP_DIR}/docker-compose.yaml" ]]; then
        cd "${DOCKER_MCP_DIR}" 2>/dev/null || true
        if command -v docker-compose &> /dev/null; then
            docker-compose down 2>/dev/null || true
        else
            docker compose down 2>/dev/null || true
        fi
    fi
    
    # Remove created directories if they contain our marker file
    if [[ -d "$DOCKER_MCP_DIR" ]] && [[ -f "${DOCKER_MCP_DIR}/.docker-mcp-install" ]]; then
        [[ "$QUIET" != "true" ]] && print_info "Removing Docker MCP directory: $DOCKER_MCP_DIR"
        rm -rf "$DOCKER_MCP_DIR" 2>/dev/null || true
    fi
    
    rm -f "$INSTALL_STATE_FILE" 2>/dev/null || true
}

# Set up trap handlers
trap cleanup_on_error EXIT ERR
trap 'echo "Installation interrupted by user"; exit 130' INT TERM

# Logging functions
log_verbose() {
    if [[ "$VERBOSE" == "true" ]] && [[ "$QUIET" != "true" ]]; then
        echo -e "${CYAN}[VERBOSE]${NC} $1" >&2
    fi
}

log_step() {
    [[ "$QUIET" != "true" ]] && echo -e "${MAGENTA}[STEP]${NC} $1"
}

# Cross-platform sed function
sed_inplace() {
    if [[ "$OS_TYPE" == "macos" ]] || [[ "$OS_TYPE" == "bsd" ]]; then
        sed -i '' "$1" "$2"
    else
        sed -i "$1" "$2"
    fi
}

# Retry function for network operations
retry_command() {
    local max_attempts=${1:-3}
    local delay=${2:-2}
    local cmd="${@:3}"
    local attempt=1
    
    while [[ $attempt -le $max_attempts ]]; do
        log_verbose "Attempt $attempt/$max_attempts: $cmd"
        if eval "$cmd"; then
            return 0
        fi
        
        if [[ $attempt -lt $max_attempts ]]; then
            log_verbose "Command failed, retrying in ${delay}s..."
            sleep "$delay"
        fi
        
        ((attempt++))
    done
    
    return 1
}

# Verify file checksum
verify_checksum() {
    local file="$1"
    local expected_checksum="$2"
    
    if [[ -z "$expected_checksum" ]]; then
        log_verbose "No checksum provided for $file, skipping verification"
        return 0
    fi
    
    local actual_checksum
    if command -v sha256sum &> /dev/null; then
        actual_checksum=$(sha256sum "$file" | cut -d' ' -f1)
    elif command -v shasum &> /dev/null; then
        actual_checksum=$(shasum -a 256 "$file" | cut -d' ' -f1)
    else
        print_warning "No checksum utility found, skipping verification"
        return 0
    fi
    
    if [[ "$actual_checksum" == "$expected_checksum" ]]; then
        log_verbose "Checksum verification passed for $file"
        return 0
    else
        print_error "Checksum verification failed for $file"
        print_error "Expected: $expected_checksum"
        print_error "Actual:   $actual_checksum"
        return 1
    fi
}

# Functions
print_header() {
    if [[ "$QUIET" != "true" ]]; then
        echo -e "${BLUE}========================================${NC}"
        echo -e "${BLUE}Docker Manager MCP Enhanced Installer${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo
        [[ "$DRY_RUN" == "true" ]] && echo -e "${YELLOW}DRY RUN MODE - No changes will be made${NC}"
        [[ "$VERBOSE" == "true" ]] && echo -e "${CYAN}Verbose mode enabled${NC}"
        echo
    fi
}

print_success() {
    [[ "$QUIET" != "true" ]] && echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

print_warning() {
    [[ "$QUIET" != "true" ]] && echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    [[ "$QUIET" != "true" ]] && echo -e "${BLUE}ℹ${NC} $1"
}

check_command() {
    if command -v "$1" &> /dev/null; then
        print_success "$1 is installed"
        return 0
    else
        print_error "$1 is not installed"
        return 1
    fi
}

check_prerequisites() {
    log_step "Checking prerequisites..."
    
    local missing_deps=0
    
    if ! check_command docker; then
        missing_deps=1
        echo "  Please install Docker: https://docs.docker.com/get-docker/"
    fi
    
    if ! check_command docker-compose && ! docker compose version &> /dev/null; then
        missing_deps=1
        echo "  Please install Docker Compose: https://docs.docker.com/compose/install/"
    fi
    
    if ! check_command ssh; then
        missing_deps=1
        echo "  Please install OpenSSH client"
    fi
    
    if ! check_command ssh-keygen; then
        missing_deps=1
        echo "  Please install OpenSSH client (ssh-keygen)"
    fi
    
    if ! check_command ssh-copy-id; then
        print_warning "ssh-copy-id not found - you'll need to manually copy SSH keys"
    fi
    
    # Check for checksum utilities
    if ! command -v sha256sum &> /dev/null && ! command -v shasum &> /dev/null; then
        print_warning "No checksum utility found (sha256sum/shasum) - checksum verification will be skipped"
    fi
    
    echo
    
    if [[ $missing_deps -eq 1 ]]; then
        print_error "Missing required dependencies. Please install them and run this script again."
        exit 1
    fi
    
    print_success "All prerequisites met!"
    echo
}

create_directories() {
    log_step "Creating directory structure..."
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "[DRY RUN] Would create directories:"
        print_info "  - ${DOCKER_MCP_DIR}"
        print_info "  - ${DOCKER_MCP_DIR}/ssh"
        print_info "  - ${CONFIG_DIR}"
        print_info "  - ${DATA_DIR}/logs"
        return
    fi
    
    mkdir -p "${DOCKER_MCP_DIR}"
    mkdir -p "${DOCKER_MCP_DIR}/ssh"
    mkdir -p "${CONFIG_DIR}"
    mkdir -p "${DATA_DIR}/logs"
    
    # Create installation marker
    touch "${DOCKER_MCP_DIR}/.docker-mcp-install"
    
    # Create symlink to SSH config if it exists (for host resolution)
    if [[ -f "${HOME}/.ssh/config" ]]; then
        ln -sf "${HOME}/.ssh/config" "${DOCKER_MCP_DIR}/ssh/config" 2>/dev/null || true
        print_info "Linked SSH config for host resolution"
    fi
    
    print_success "Created directory structure at ${DOCKER_MCP_DIR}"
    echo
}

generate_ssh_keys() {
    log_step "Generating SSH keys for Docker MCP..."
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "[DRY RUN] Would generate SSH key at: ${SSH_KEY_PATH}"
        return
    fi
    
    if [[ -f "${SSH_KEY_PATH}" ]] && [[ "$FORCE_REINSTALL" != "true" ]]; then
        print_warning "SSH key already exists at ${SSH_KEY_PATH}"
        read -p "Do you want to regenerate it? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Using existing SSH key"
            return
        fi
    fi
    
    ssh-keygen -t ed25519 -f "${SSH_KEY_PATH}" -N "" -C "docker-mcp@$(hostname)"
    chmod 600 "${SSH_KEY_PATH}"
    chmod 644 "${SSH_KEY_PATH}.pub"
    
    print_success "Generated SSH key pair at ${SSH_KEY_PATH}"
    echo
}

# Enhanced SSH key distribution with parallel processing and validation
copy_ssh_keys() {
    if [[ "$SKIP_SSH_COPY" == "true" ]]; then
        print_info "Skipping SSH key distribution (SKIP_SSH_COPY=true)"
        return
    fi
    
    log_step "Distributing SSH keys to hosts..."
    
    if [[ ! -f "${SSH_KEY_PATH}.pub" ]]; then
        print_error "SSH public key not found at ${SSH_KEY_PATH}.pub"
        return 1
    fi
    
    local ssh_config="${HOME}/.ssh/config"
    local hosts=()
    
    # Parse SSH config for hosts with validation
    if [[ -f "$ssh_config" ]]; then
        while IFS= read -r line; do
            if [[ $line =~ ^Host[[:space:]]+(.+)$ ]]; then
                host="${BASH_REMATCH[1]}"
                # Skip wildcards, localhost, and VCS hosts
                if [[ ! "$host" =~ [\*\?] ]] && \
                   [[ "$host" != "localhost" ]] && \
                   [[ "$host" != "127.0.0.1" ]] && \
                   [[ "$host" != "github.com" ]] && \
                   [[ "$host" != "gitlab.com" ]] && \
                   [[ "$host" != "bitbucket.org" ]]; then
                    
                    # Test SSH connectivity before adding to list
                    log_verbose "Testing SSH connectivity to $host..."
                    if ssh -o ConnectTimeout=5 -o BatchMode=yes -o PasswordAuthentication=no "$host" exit 2>/dev/null; then
                        hosts+=("$host")
                        log_verbose "✓ $host is reachable"
                    else
                        log_verbose "⚠ $host is not reachable, skipping"
                    fi
                fi
            fi
        done < "$ssh_config"
    fi
    
    if [[ ${#hosts[@]} -eq 0 ]]; then
        print_warning "No suitable hosts found in SSH config for key distribution"
        print_info "You'll need to manually copy the public key to your Docker hosts:"
        echo
        echo "Public key location: ${SSH_KEY_PATH}.pub"
        echo "Public key content:"
        cat "${SSH_KEY_PATH}.pub"
        echo
        return
    fi
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "[DRY RUN] Would copy SSH keys to the following hosts:"
        for host in "${hosts[@]}"; do
            echo "  - $host"
        done
        return
    fi
    
    print_info "Found ${#hosts[@]} reachable host(s) for SSH key distribution"
    echo
    
    echo "The installer will now copy the Docker MCP SSH key to the following hosts:"
    for host in "${hosts[@]}"; do
        echo "  - $host"
    done
    echo
    
    read -p "Do you want to proceed with key distribution? (y/N): " -n 1 -r
    echo
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_warning "Skipping SSH key distribution"
        print_info "You can manually copy the key later using:"
        echo "  ssh-copy-id -i ${SSH_KEY_PATH} user@host"
        echo
        return
    fi
    
    local failed_hosts=()
    
    # Distribute keys with error handling
    for host in "${hosts[@]}"; do
        echo -n "Copying key to $host... "
        if retry_command 2 1 "ssh-copy-id -i '${SSH_KEY_PATH}' '$host' 2>/dev/null"; then
            print_success "Success"
        else
            # Try manual method if ssh-copy-id fails
            if retry_command 2 1 "cat '${SSH_KEY_PATH}.pub' | ssh '$host' 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys' 2>/dev/null"; then
                print_success "Success (manual method)"
            else
                print_error "Failed"
                failed_hosts+=("$host")
            fi
        fi
    done
    
    echo
    
    if [[ ${#failed_hosts[@]} -gt 0 ]]; then
        print_warning "Failed to copy keys to some hosts:"
        for host in "${failed_hosts[@]}"; do
            echo "  - $host"
        done
        echo
        print_info "You can manually copy the key using:"
        echo "  ssh-copy-id -i ${SSH_KEY_PATH} user@host"
        echo
    else
        print_success "Successfully distributed SSH keys to all reachable hosts!"
        echo
    fi
}

generate_hosts_config() {
    log_step "Generating hosts configuration..."
    
    local config_file="${CONFIG_DIR}/hosts.yml"
    local ssh_config="${HOME}/.ssh/config"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "[DRY RUN] Would generate hosts config at: $config_file"
        return
    fi
    
    # Start with the header
    cat > "$config_file" << 'EOF'
# Docker Manager MCP Configuration
# Auto-generated from SSH config

hosts:
EOF
    
    if [[ -f "$ssh_config" ]]; then
        local current_host=""
        local hostname=""
        local user=""
        local port="22"
        
        while IFS= read -r line; do
            if [[ $line =~ ^Host[[:space:]]+(.+)$ ]]; then
                # Process previous host if exists
                if [[ -n "$current_host" ]] && [[ -n "$hostname" ]] && [[ -n "$user" ]]; then
                    # Skip localhost and VCS hosts
                    if [[ "$hostname" != "localhost" ]] && \
                       [[ "$hostname" != "127.0.0.1" ]] && \
                       [[ ! "$current_host" =~ (github|gitlab|bitbucket) ]]; then
                        cat >> "$config_file" << EOF
  ${current_host}:
    hostname: ${hostname}
    user: ${user}
    port: ${port}
    identity_file: ${SSH_KEY_PATH}
    description: "Imported from SSH config"
    tags: ["imported", "ssh-config"]
    enabled: true
    
EOF
                    fi
                fi
                
                # Start new host
                current_host="${BASH_REMATCH[1]}"
                # Skip wildcards
                if [[ "$current_host" =~ [\*\?] ]]; then
                    current_host=""
                fi
                # Reset values
                hostname=""
                user=""
                port="22"
                
            elif [[ $line =~ ^[[:space:]]*HostName[[:space:]]+(.+)$ ]]; then
                hostname="${BASH_REMATCH[1]}"
            elif [[ $line =~ ^[[:space:]]*User[[:space:]]+(.+)$ ]]; then
                user="${BASH_REMATCH[1]}"
            elif [[ $line =~ ^[[:space:]]*Port[[:space:]]+(.+)$ ]]; then
                port="${BASH_REMATCH[1]}"
            fi
        done < "$ssh_config"
        
        # Process last host
        if [[ -n "$current_host" ]] && [[ -n "$hostname" ]] && [[ -n "$user" ]]; then
            if [[ "$hostname" != "localhost" ]] && \
               [[ "$hostname" != "127.0.0.1" ]] && \
               [[ ! "$current_host" =~ (github|gitlab|bitbucket) ]]; then
                cat >> "$config_file" << EOF
  ${current_host}:
    hostname: ${hostname}
    user: ${user}
    port: ${port}
    identity_file: ${SSH_KEY_PATH}
    description: "Imported from SSH config"
    tags: ["imported", "ssh-config"]
    enabled: true
EOF
            fi
        fi
    fi
    
    # Check if any hosts were added
    if [[ $(wc -l < "$config_file") -le 4 ]]; then
        print_warning "No hosts were imported from SSH config"
        print_info "Downloading example configuration..."
        if retry_command 3 2 "curl -sSL '$EXAMPLE_CONFIG_URL' -o '$config_file'"; then
            print_info "Please edit ${config_file} to add your Docker hosts"
        else
            print_error "Failed to download example configuration"
            return 1
        fi
    else
        print_success "Generated hosts configuration at ${config_file}"
    fi
    
    echo
}

# Enhanced port detection with multiple methods
find_available_port() {
    local start_port="${1:-8000}"
    local max_port=$((start_port + 100))
    
    for port in $(seq $start_port $max_port); do
        local port_in_use=false
        
        # Check with multiple methods for maximum compatibility
        if command -v lsof &> /dev/null; then
            lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1 && port_in_use=true
        fi
        
        if [[ "$port_in_use" == "false" ]] && command -v netstat &> /dev/null; then
            netstat -tuln 2>/dev/null | grep -q ":$port " && port_in_use=true
        fi
        
        if [[ "$port_in_use" == "false" ]] && command -v ss &> /dev/null; then
            ss -tuln 2>/dev/null | grep -q ":$port " && port_in_use=true
        fi
        
        # Final check with nc if available
        if [[ "$port_in_use" == "false" ]] && command -v nc &> /dev/null; then
            nc -z localhost $port >/dev/null 2>&1 && port_in_use=true
        fi
        
        if [[ "$port_in_use" == "false" ]]; then
            echo "$port"
            return 0
        fi
    done
    
    # If no port found in range, return error
    return 1
}

download_compose_file() {
    log_step "Downloading docker-compose.yaml..."
    
    local compose_file="${DOCKER_MCP_DIR}/docker-compose.yaml"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "[DRY RUN] Would download: $COMPOSE_URL"
        print_info "[DRY RUN] To: $compose_file"
        return
    fi
    
    if retry_command 3 2 "curl -sSL '$COMPOSE_URL' -o '$compose_file'"; then
        # Verify checksum if available
        # verify_checksum "$compose_file" "$COMPOSE_CHECKSUM"
        
        # Update paths in compose file with cross-platform sed
        sed_inplace "s|~/.ssh|${DOCKER_MCP_DIR}/ssh|g" "$compose_file"
        sed_inplace "s|./config|${CONFIG_DIR}|g" "$compose_file"
        sed_inplace "s|./data|${DATA_DIR}|g" "$compose_file"
        
        # Check if port 8000 is available
        local desired_port=8000
        if ! find_available_port $desired_port >/dev/null; then
            print_warning "Port $desired_port is already in use"
            print_info "Finding an available port..."
            
            if available_port=$(find_available_port $desired_port); then
                print_success "Found available port: $available_port"
                
                # Update docker-compose.yaml with new port
                sed_inplace "s|\"8000:8000\"|\"${available_port}:8000\"|g" "$compose_file"
                sed_inplace "s|FASTMCP_PORT: \"8000\"|FASTMCP_PORT: \"8000\"|g" "$compose_file"
                
                # Store the port for later use
                echo "FASTMCP_PORT=${available_port}" > "${DOCKER_MCP_DIR}/.env"
                desired_port=$available_port
            else
                print_error "Could not find an available port in range 8000-8100"
                print_info "Please manually edit the port in ${compose_file}"
                desired_port=8000  # Use default for display purposes
            fi
        else
            print_success "Port $desired_port is available"
            echo "FASTMCP_PORT=${desired_port}" > "${DOCKER_MCP_DIR}/.env"
        fi
        
        # Export for use in other functions
        export FASTMCP_PORT=$desired_port
        
        print_success "Downloaded docker-compose.yaml to ${compose_file}"
    else
        print_error "Failed to download docker-compose.yaml"
        exit 1
    fi
    
    echo
}

start_services() {
    log_step "Starting Docker MCP services..."
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "[DRY RUN] Would start Docker services"
        return
    fi
    
    cd "${DOCKER_MCP_DIR}"
    
    # Check if docker-compose or docker compose should be used
    if command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        COMPOSE_CMD="docker compose"
    fi
    
    print_info "Pulling Docker image..."
    $COMPOSE_CMD pull
    
    print_info "Starting services..."
    if $COMPOSE_CMD up -d; then
        print_success "Docker MCP services started successfully!"
    else
        print_error "Failed to start services"
        print_info "Check logs with: cd ${DOCKER_MCP_DIR} && $COMPOSE_CMD logs"
        exit 1
    fi
    
    echo
}

create_uninstall_script() {
    log_step "Creating uninstall script..."
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "[DRY RUN] Would create uninstall script"
        return
    fi
    
    local uninstall_script="${DOCKER_MCP_DIR}/uninstall.sh"
    
    cat > "$uninstall_script" << 'EOF'
#!/bin/bash
# Docker MCP Uninstaller

set -e

DOCKER_MCP_DIR="${HOME}/.docker-mcp"

echo "Uninstalling Docker MCP..."

# Stop and remove containers
if [[ -f "${DOCKER_MCP_DIR}/docker-compose.yaml" ]]; then
    cd "${DOCKER_MCP_DIR}"
    if command -v docker-compose &> /dev/null; then
        docker-compose down -v
    else
        docker compose down -v
    fi
fi

# Remove Docker MCP directory
if [[ -d "$DOCKER_MCP_DIR" ]]; then
    echo "Removing Docker MCP directory: $DOCKER_MCP_DIR"
    rm -rf "$DOCKER_MCP_DIR"
fi

echo "Docker MCP has been completely uninstalled."
EOF
    
    chmod +x "$uninstall_script"
    print_success "Created uninstall script at ${uninstall_script}"
}

print_completion() {
    if [[ "$QUIET" != "true" ]]; then
        echo -e "${GREEN}========================================${NC}"
        echo -e "${GREEN}Installation Complete!${NC}"
        echo -e "${GREEN}========================================${NC}"
        echo
        
        if [[ "$DRY_RUN" == "true" ]]; then
            echo -e "${YELLOW}This was a DRY RUN - no changes were made${NC}"
            echo "Re-run without DRY_RUN=true to perform the actual installation."
            return
        fi
        
        echo "Docker MCP has been installed and configured at:"
        echo "  ${DOCKER_MCP_DIR}"
        echo
        echo "Configuration files:"
        echo "  Hosts config: ${CONFIG_DIR}/hosts.yml"
        echo "  SSH key: ${SSH_KEY_PATH}"
        echo "  Docker Compose: ${DOCKER_MCP_DIR}/docker-compose.yaml"
        echo "  Uninstall: ${DOCKER_MCP_DIR}/uninstall.sh"
        echo
        echo "Service is running at:"
        echo "  http://localhost:${FASTMCP_PORT:-8000}"
        echo
        echo "Useful commands:"
        echo "  View logs:    cd ${DOCKER_MCP_DIR} && docker compose logs -f"
        echo "  Stop:         cd ${DOCKER_MCP_DIR} && docker compose down"
        echo "  Restart:      cd ${DOCKER_MCP_DIR} && docker compose restart"
        echo "  Update:       cd ${DOCKER_MCP_DIR} && docker compose pull && docker compose up -d"
        echo "  Uninstall:    ${DOCKER_MCP_DIR}/uninstall.sh"
        echo
        echo "To use with Claude Desktop, add to config:"
        echo '  {'
        echo '    "mcpServers": {'
        echo '      "docker-mcp": {'
        echo "        \"url\": \"http://localhost:${FASTMCP_PORT:-8000}\""
        echo '      }'
        echo '    }'
        echo '  }'
        echo
    fi
}

main() {
    # Set installation marker
    INSTALLATION_STARTED=true
    INSTALL_STATE_FILE=$(mktemp)
    
    print_header
    check_prerequisites
    create_directories
    generate_ssh_keys
    copy_ssh_keys
    generate_hosts_config
    download_compose_file
    start_services
    create_uninstall_script
    print_completion
    
    # Success - disable cleanup
    CLEANUP_ON_EXIT=false
    rm -f "$INSTALL_STATE_FILE"
}

# Run main function
main "$@"
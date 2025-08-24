#!/bin/bash

# Docker Manager MCP - Automated Installer
# This script sets up Docker Manager MCP with automatic SSH key configuration
# Usage: curl -sSL https://raw.githubusercontent.com/jmagar/docker-mcp/main/install.sh | bash

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
DOCKER_MCP_DIR="${HOME}/.docker-mcp"
SSH_KEY_NAME="docker-mcp-key"
SSH_KEY_PATH="${DOCKER_MCP_DIR}/ssh/${SSH_KEY_NAME}"
CONFIG_DIR="${DOCKER_MCP_DIR}/config"
DATA_DIR="${DOCKER_MCP_DIR}/data"
COMPOSE_URL="https://raw.githubusercontent.com/jmagar/docker-mcp/main/docker-compose.yaml"
EXAMPLE_CONFIG_URL="https://raw.githubusercontent.com/jmagar/docker-mcp/main/config/hosts.example.yml"

# Functions
print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Docker Manager MCP Installer${NC}"
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
    echo -e "${BLUE}Checking prerequisites...${NC}"
    echo
    
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
    
    echo
    
    if [ $missing_deps -eq 1 ]; then
        print_error "Missing required dependencies. Please install them and run this script again."
        exit 1
    fi
    
    print_success "All prerequisites met!"
    echo
}

create_directories() {
    echo -e "${BLUE}Creating directory structure...${NC}"
    echo
    
    mkdir -p "${DOCKER_MCP_DIR}"
    mkdir -p "${DOCKER_MCP_DIR}/ssh"
    mkdir -p "${CONFIG_DIR}"
    mkdir -p "${DATA_DIR}/logs"
    
    # Create symlink to SSH config if it exists (for host resolution)
    if [ -f "${HOME}/.ssh/config" ]; then
        ln -sf "${HOME}/.ssh/config" "${DOCKER_MCP_DIR}/ssh/config" 2>/dev/null || true
        print_info "Linked SSH config for host resolution"
    fi
    
    print_success "Created directory structure at ${DOCKER_MCP_DIR}"
    echo
}

generate_ssh_keys() {
    echo -e "${BLUE}Generating SSH keys for Docker MCP...${NC}"
    echo
    
    if [ -f "${SSH_KEY_PATH}" ]; then
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

parse_ssh_config() {
    local ssh_config="${HOME}/.ssh/config"
    local hosts=()
    
    if [ ! -f "$ssh_config" ]; then
        print_warning "No SSH config file found at $ssh_config"
        return 1
    fi
    
    echo -e "${BLUE}Parsing SSH config for hosts...${NC}"
    echo
    
    # Parse SSH config for Host entries
    while IFS= read -r line; do
        if [[ $line =~ ^Host[[:space:]]+(.+)$ ]]; then
            host="${BASH_REMATCH[1]}"
            # Skip wildcards and special entries
            if [[ ! "$host" =~ [\*\?] ]] && [[ "$host" != "github.com" ]] && [[ "$host" != "gitlab.com" ]]; then
                hosts+=("$host")
            fi
        fi
    done < "$ssh_config"
    
    if [ ${#hosts[@]} -eq 0 ]; then
        print_warning "No suitable hosts found in SSH config"
        return 1
    fi
    
    print_success "Found ${#hosts[@]} host(s) in SSH config"
    echo
    
    echo "Hosts found:"
    for host in "${hosts[@]}"; do
        echo "  - $host"
    done
    echo
    
    echo "$hosts"
}

copy_ssh_keys() {
    echo -e "${BLUE}Distributing SSH keys to hosts...${NC}"
    echo
    
    if [ ! -f "${SSH_KEY_PATH}.pub" ]; then
        print_error "SSH public key not found at ${SSH_KEY_PATH}.pub"
        return 1
    fi
    
    local ssh_config="${HOME}/.ssh/config"
    local hosts=()
    
    # Parse SSH config for hosts
    if [ -f "$ssh_config" ]; then
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
                    hosts+=("$host")
                fi
            fi
        done < "$ssh_config"
    fi
    
    if [ ${#hosts[@]} -eq 0 ]; then
        print_warning "No suitable hosts found in SSH config for key distribution"
        print_info "You'll need to manually copy the public key to your Docker hosts:"
        echo
        echo "Public key location: ${SSH_KEY_PATH}.pub"
        echo "Public key content:"
        cat "${SSH_KEY_PATH}.pub"
        echo
        return
    fi
    
    print_info "Found ${#hosts[@]} host(s) for SSH key distribution"
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
    
    for host in "${hosts[@]}"; do
        echo -n "Copying key to $host... "
        if command -v ssh-copy-id &> /dev/null; then
            if ssh-copy-id -i "${SSH_KEY_PATH}" "$host" 2>/dev/null; then
                print_success "Success"
            else
                print_error "Failed"
                failed_hosts+=("$host")
            fi
        else
            # Manual method if ssh-copy-id is not available
            if cat "${SSH_KEY_PATH}.pub" | ssh "$host" "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" 2>/dev/null; then
                print_success "Success"
            else
                print_error "Failed"
                failed_hosts+=("$host")
            fi
        fi
    done
    
    echo
    
    if [ ${#failed_hosts[@]} -gt 0 ]; then
        print_warning "Failed to copy keys to some hosts:"
        for host in "${failed_hosts[@]}"; do
            echo "  - $host"
        done
        echo
        print_info "You can manually copy the key using:"
        echo "  ssh-copy-id -i ${SSH_KEY_PATH} user@host"
        echo
    else
        print_success "Successfully distributed SSH keys to all hosts!"
        echo
    fi
}

generate_hosts_config() {
    echo -e "${BLUE}Generating hosts configuration...${NC}"
    echo
    
    local config_file="${CONFIG_DIR}/hosts.yml"
    local ssh_config="${HOME}/.ssh/config"
    
    # Start with the header
    cat > "$config_file" << 'EOF'
# Docker Manager MCP Configuration
# Auto-generated from SSH config

hosts:
EOF
    
    if [ -f "$ssh_config" ]; then
        local current_host=""
        local hostname=""
        local user=""
        local port="22"
        local identity_file=""
        
        while IFS= read -r line; do
            if [[ $line =~ ^Host[[:space:]]+(.+)$ ]]; then
                # Process previous host if exists
                if [ -n "$current_host" ] && [ -n "$hostname" ] && [ -n "$user" ]; then
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
                identity_file="${SSH_KEY_PATH}"
                
            elif [[ $line =~ ^[[:space:]]*HostName[[:space:]]+(.+)$ ]]; then
                hostname="${BASH_REMATCH[1]}"
            elif [[ $line =~ ^[[:space:]]*User[[:space:]]+(.+)$ ]]; then
                user="${BASH_REMATCH[1]}"
            elif [[ $line =~ ^[[:space:]]*Port[[:space:]]+(.+)$ ]]; then
                port="${BASH_REMATCH[1]}"
            fi
        done < "$ssh_config"
        
        # Process last host
        if [ -n "$current_host" ] && [ -n "$hostname" ] && [ -n "$user" ]; then
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
    if [ $(wc -l < "$config_file") -le 4 ]; then
        print_warning "No hosts were imported from SSH config"
        print_info "Downloading example configuration..."
        curl -sSL "$EXAMPLE_CONFIG_URL" -o "$config_file"
        print_info "Please edit ${config_file} to add your Docker hosts"
    else
        print_success "Generated hosts configuration at ${config_file}"
    fi
    
    echo
}

find_available_port() {
    local start_port="${1:-8000}"
    local max_port=$((start_port + 100))
    
    for port in $(seq $start_port $max_port); do
        # Check if port is in use
        if ! lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1 && \
           ! netstat -tuln 2>/dev/null | grep -q ":$port " && \
           ! ss -tuln 2>/dev/null | grep -q ":$port "; then
            echo "$port"
            return 0
        fi
    done
    
    # If no port found in range, return error
    return 1
}

download_compose_file() {
    echo -e "${BLUE}Downloading docker-compose.yaml...${NC}"
    echo
    
    local compose_file="${DOCKER_MCP_DIR}/docker-compose.yaml"
    
    if curl -sSL "$COMPOSE_URL" -o "$compose_file"; then
        # Update paths in compose file
        sed -i.bak "s|~/.ssh|${DOCKER_MCP_DIR}/ssh|g" "$compose_file"
        sed -i.bak "s|./config|${CONFIG_DIR}|g" "$compose_file"
        sed -i.bak "s|./data|${DATA_DIR}|g" "$compose_file"
        rm -f "${compose_file}.bak"
        
        # Check if port 8000 is available
        local desired_port=8000
        if lsof -Pi :$desired_port -sTCP:LISTEN -t >/dev/null 2>&1 || \
           netstat -tuln 2>/dev/null | grep -q ":$desired_port " || \
           ss -tuln 2>/dev/null | grep -q ":$desired_port "; then
            print_warning "Port $desired_port is already in use"
            print_info "Finding an available port..."
            
            if available_port=$(find_available_port $desired_port); then
                print_success "Found available port: $available_port"
                
                # Update docker-compose.yaml with new port
                sed -i.bak "s|\"8000:8000\"|\"${available_port}:8000\"|g" "$compose_file"
                sed -i.bak "s|FASTMCP_PORT: \"8000\"|FASTMCP_PORT: \"8000\"|g" "$compose_file"
                rm -f "${compose_file}.bak"
                
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
    echo -e "${BLUE}Starting Docker MCP services...${NC}"
    echo
    
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

print_completion() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Installation Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo
    echo "Docker MCP has been installed and configured at:"
    echo "  ${DOCKER_MCP_DIR}"
    echo
    echo "Configuration files:"
    echo "  Hosts config: ${CONFIG_DIR}/hosts.yml"
    echo "  SSH key: ${SSH_KEY_PATH}"
    echo "  Docker Compose: ${DOCKER_MCP_DIR}/docker-compose.yaml"
    echo
    echo "Service is running at:"
    echo "  http://localhost:${FASTMCP_PORT:-8000}"
    echo
    echo "Useful commands:"
    echo "  View logs:    cd ${DOCKER_MCP_DIR} && docker compose logs -f"
    echo "  Stop:         cd ${DOCKER_MCP_DIR} && docker compose down"
    echo "  Restart:      cd ${DOCKER_MCP_DIR} && docker compose restart"
    echo "  Update:       cd ${DOCKER_MCP_DIR} && docker compose pull && docker compose up -d"
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
}

setup_ssh_with_standalone_script() {
    echo -e "${BLUE}Setting up SSH keys...${NC}"
    echo
    
    local script_path="$(dirname "$0")/scripts/setup-ssh-keys.sh"
    
    if [ -f "$script_path" ]; then
        print_info "Using standalone SSH setup script"
        if "$script_path" --batch; then
            print_success "SSH key distribution completed successfully"
        else
            print_warning "SSH setup script encountered issues, falling back to embedded functions"
            generate_ssh_keys
            copy_ssh_keys
            generate_hosts_config
        fi
    else
        print_info "Standalone script not found, using embedded SSH setup"
        generate_ssh_keys
        copy_ssh_keys
        generate_hosts_config
    fi
    
    echo
}

main() {
    print_header
    check_prerequisites
    create_directories
    setup_ssh_with_standalone_script
    download_compose_file
    start_services
    print_completion
}

# Run main function
main "$@"
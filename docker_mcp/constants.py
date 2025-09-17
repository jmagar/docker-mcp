"""Centralized constants for Docker MCP to eliminate duplicate strings."""

# SSH Configuration Options
SSH_NO_HOST_CHECK = "StrictHostKeyChecking=no"
SSH_NO_KNOWN_HOSTS = "UserKnownHostsFile=/dev/null"
SSH_ERROR_LOG_LEVEL = "LogLevel=ERROR"

# Docker Labels
DOCKER_COMPOSE_PROJECT = "com.docker.compose.project"
DOCKER_COMPOSE_SERVICE = "com.docker.compose.service"
DOCKER_COMPOSE_CONFIG_FILES = "com.docker.compose.project.config_files"
DOCKER_COMPOSE_WORKING_DIR = "com.docker.compose.project.working_dir"

# Common Field Names
COMPOSE_FILE = "compose_file"
CONTAINER_ID = "container_id"
HOST_ID = "host_id"
APPDATA_PATH = "appdata_path"
COMPOSE_PATH = "compose_path"
IDENTITY_FILE = "identity_file"

# Container Fields
TOTAL_CONTAINERS = "total_containers"
TOTAL_PORTS = "total_ports"
PORT_MAPPINGS = "port_mappings"
BIND_MOUNTS = "bind_mounts"
COMPOSE_PROJECT = "compose_project"
COMPOSE_SERVICE = "compose_service"
MEMORY_USAGE = "memory_usage"
MEMORY_LIMIT = "memory_limit"
HEALTH_STATUS = "health_status"
CONTAINER_NAME = "container_name"

# Docker Stats Fields
TOTAL_USAGE = "total_usage"
SYSTEM_CPU_USAGE = "system_cpu_usage"
PRECPU_STATS = "precpu_stats"
MEMORY_STATS = "memory_stats"
NETWORK_SETTINGS = "NetworkSettings"
DESTINATION = "Destination"

# Migration & Transfer Fields
TRANSFER_TYPE = "transfer_type"
NAMED_VOLUMES = "named_volumes"
BIND_MOUNTS = "bind_mounts"
FILES_TRANSFERRED = "files_transferred"
TRANSFER_RATE = "transfer_rate"
TOTAL_FILES = "total_files"
CRITICAL_FILES = "critical_files"
DATA_TRANSFER = "data_transfer"
FILES_FOUND = "files_found"
CRITICAL_FILES_VERIFIED = "critical_files_verified"
CONTAINER_INTEGRATION = "container_integration"
CONTAINER_RUNNING = "container_running"
CONTAINER_HEALTHY = "container_healthy"
MOUNT_PATHS_CORRECT = "mount_paths_correct"
DATA_ACCESSIBLE = "data_accessible"
FILES_EXPECTED = "files_expected"
FILE_MATCH_PERCENTAGE = "file_match_percentage"
SIZE_MATCH_PERCENTAGE = "size_match_percentage"

# Backup Fields
BACKUP_PATH = "backup_path"
BACKUP_SIZE = "backup_size"
BACKUP_SIZE_HUMAN = "backup_size_human"
SNAPSHOT_NAME = "snapshot_name"

# Date/Time Formats
BACKUP_DATE_FORMAT = "%Y%m%d_%H%M%S"
ISO_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Docker Context Prefix
DOCKER_CONTEXT_PREFIX = "docker-mcp-"

# Common Docker Commands
DOCKER_PS_PROJECT_FILTER = (
    "docker ps --filter 'label=com.docker.compose.project={0}' --format '{{{{.Names}}}}'"
)
DOCKER_COMPOSE_PROJECT_FILTER = " -type f 2>/dev/null | wc -l"

# SSH Command Templates
SSH_RSYNC_KEY_TEMPLATE = " -e 'ssh -i {0}'"
SSH_DEFAULT_PATH = "/opt/docker-appdata"

# Error Messages
NOT_FOUND_SUFFIX = "' not found"
INVALID_ACTION_PREFIX = "Invalid action '"
VALID_ACTIONS_SUFFIX = "'. Valid actions: "
UNKNOWN_ACTION_PREFIX = "Unknown action: "
CONTAINER_PREFIX = "Container '"
SAFETY_BLOCK_PREFIX = "SAFETY BLOCK: "

# Resource Types
RESOURCE_TYPE = "resource_type"
RESOURCE_URI = "resource_uri"

# HTTP
CONTENT_TYPE_JSON = "application/json"
STRUCTURED_CONTENT = "structured_content"

# Environment Variables
ENV_FASTMCP_HOST = "FASTMCP_HOST"
ENV_FASTMCP_PORT = "FASTMCP_PORT"
ENV_DOCKER_HOSTS_CONFIG = "DOCKER_HOSTS_CONFIG"

# Logging
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_INIT_MESSAGE = "Logging system initialized"

# Common Descriptions (for parameter models)
DESC_ACTION_DEFAULT = "Action to perform (defaults to list if not provided)"
DESC_HOST_ID = "Host identifier"
DESC_SSH_HOST = "SSH hostname or IP address"
DESC_SSH_USER = "SSH username"
DESC_SSH_PORT = "SSH port number"
DESC_SSH_KEY_PATH = "Path to SSH private key file"
DESC_COMPOSE_PATH = "Docker Compose file path"
DESC_APPDATA_PATH = "Application data storage path"
DESC_HOST_ENABLED = "Whether host is enabled"
DESC_SSH_CONFIG_PATH = "Path to SSH config file"
DESC_SELECTED_HOSTS = "Comma-separated list of hosts to select"
DESC_CLEANUP_TYPE = "Type of cleanup to perform"
DESC_CLEANUP_FREQUENCY = "Cleanup schedule frequency"
DESC_ACTION_PERFORM = "Action to perform"
DESC_CONTAINER_ID = "Container identifier"
DESC_COMPOSE_CONTENT = "Docker Compose file content"
DESC_ENVIRONMENT_VARS = "Environment variables"
DESC_PULL_IMAGES = "Pull images before deploying"
DESC_RECREATE_CONTAINERS = "Recreate containers"
DESC_DRY_RUN = "Perform a dry run without making changes"
DESC_ADDITIONAL_OPTIONS = "Additional options for the operation"
DESC_TARGET_HOST_ID = "Target host ID for migration operations"
DESC_REMOVE_SOURCE = "Remove source stack after migration"
DESC_SKIP_STOP_SOURCE = "Skip stopping source stack before migration"
DESC_START_TARGET = "Start target stack after migration"
DESC_MAX_RESULTS = "Maximum number of results to return"
DESC_SKIP_RESULTS = "Number of results to skip"
DESC_FOLLOW_LOGS = "Follow log output"
DESC_LOG_LINES = "Number of log lines to retrieve"
DESC_FORCE_OPERATION = "Force the operation"
DESC_OPERATION_TIMEOUT = "Operation timeout in seconds"

# Port Ranges
PORT_RANGE_EPHEMERAL = "49152-65535"

# Service Names
RECOMMENDATIONS = "recommendations"
TOTAL_HOSTS = "total_hosts"
DISCOVERIES = "discoveries"
COMPOSE_DISCOVERY = "compose_discovery"
APPDATA_DISCOVERY = "appdata_discovery"
IMPORTED_HOSTS = "imported_hosts"
VALID_ACTIONS = "valid_actions"
STACKS_FOUND = "stacks_found"
COMPOSE_LOCATIONS = "compose_locations"
SUGGESTED_PATH = "suggested_path"
RUNNING_CONTAINERS = "running_containers"
TOTAL_REQUESTS = "total_requests"
PROTOCOL_COUNTS = "protocol_counts"
PORT_RANGE_USAGE = "port_range_usage"
ENVIRONMENT = "environment"
MESSAGE_TYPE = "message_type"

# Security-related field names for filtering
SECURITY_FIELDS = [
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "api_token",
    "key",
    "api_key",
    "private_key",
    "secret_key",
    "ssh_key",
    "secret",
    "client_secret",
    "auth_secret",
    "credential",
    "auth",
    "authorization",
    "certificate",
]

# Common Status Messages
STATUS_CONTAINER = "\n    Status: "
BUILD_SSH_DOCSTRING = "Build SSH command for a host."
FORMAT_BYTES_DOCSTRING = "Format bytes into human-readable string."
VALIDATE_HOST_DOCSTRING = "Validate host exists in configuration."
SET_CACHE_MANAGER_DOCSTRING = "Set the cache manager after initialization."
GET_TRANSFER_TYPE_DOCSTRING = "Get the name/type of this transfer method."

# Migration file suffix
MIGRATION_SUFFIX = "_migration_"

# Action constants
TEST_CONNECTION = "test_connection"

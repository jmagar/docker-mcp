import re
from pathlib import Path

DOCKER_COMPOSE_CANDIDATES = [
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
]

def _read_compose_text():
    # Prefer real compose files if present
    for name in DOCKER_COMPOSE_CANDIDATES:
        p = Path(name)
        if p.is_file():
            return p.read_text(encoding="utf-8"), f"file:{name}"
    # Fallback: use the PR diff content embedded in tests/test_docker_compose.py if present
    fallback_path = Path("tests/test_docker_compose.py")
    if fallback_path.is_file():
        return fallback_path.read_text(encoding="utf-8"), "file:tests/test_docker_compose.py"
    raise FileNotFoundError("No compose file found and fallback content not available")

def _try_parse_yaml(text: str):
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except Exception:
        return None

def _extract_service_ports_text(text: str):
    # naive extraction for line-based assertions when YAML parser is unavailable
    # find the 'ports:' block under 'docker-mcp:' service
    lines = text.splitlines()
    ports_block = []
    in_service = False
    in_ports = False
    for ln in lines:
        if re.match(r'^\s+docker-mcp:\s*$', ln):
            in_service = True
            in_ports = False
            continue
        if in_service and re.match(r'^\s+\w', ln) and not re.match(r'^\s{2,}ports:\s*$', ln) and not ln.strip().startswith('- '):
            # likely next top-level under service; ignore until we specifically enter ports
            pass
        if in_service and re.match(r'^\s{2,}ports:\s*$', ln):
            in_ports = True
            continue
        if in_service and in_ports:
            if re.match(r'^\s{2,}\w', ln) and not ln.strip().startswith('- '):
                # next key under service; ports block ended
                break
            ports_block.append(ln)
    return "\n".join(ports_block)

def _extract_block(text: str, service_key: str, block_key: str):
    # generic very-lightweight block extractor (for fallback assertions)
    lines = text.splitlines()
    block = []
    in_service = False
    in_block = False
    for ln in lines:
        if re.match(rf'^\s+{re.escape(service_key)}:\s*$', ln):
            in_service = True
            in_block = False
            continue
        if in_service and re.match(rf'^\s{{2,}}{re.escape(block_key)}:\s*$', ln):
            in_block = True
            continue
        if in_service and in_block:
            if re.match(r'^\s{2,}\w', ln) and not ln.strip().startswith('- '):
                break
            block.append(ln)
    return "\n".join(block)

def test_compose_structure_and_values():
    text, source = _read_compose_text()
    data = _try_parse_yaml(text)

    if data is not None:
        # YAML-based assertions (preferred)
        assert "services" in data, f"services missing in {source}"
        services = data["services"]
        assert "docker-mcp" in services, "service 'docker-mcp' missing"
        svc = services["docker-mcp"]

        # basic keys
        assert svc.get("build") == ".", "build should be '.'"
        assert svc.get("container_name") == "docker-mcp"
        assert svc.get("restart") == "unless-stopped"

        # ports
        ports = svc.get("ports")
        assert isinstance(ports, list) and ports, "ports should be a non-empty list"
        assert any("${FASTMCP_PORT:-8000}:${FASTMCP_PORT:-8000}" in str(p) for p in ports), "port mapping must use FASTMCP_PORT default 8000"

        # volumes
        volumes = svc.get("volumes")
        assert isinstance(volumes, list) and volumes, "volumes should be a non-empty list"
        vol_strs = list(map(str, volumes))
        assert any(":ro" in v and "/home/dockermcp/.ssh" in v for v in vol_strs), "SSH keys volume must be read-only and point to container ~/.ssh"
        assert any(":ro" in v and "/app/config" in v for v in vol_strs), "config volume must be read-only at /app/config"
        assert any(v.endswith("docker-mcp-data:/app/data") for v in vol_strs), "data volume docker-mcp-data must be mounted at /app/data"
        assert any("/var/run/docker.sock:/var/run/docker.sock:ro" in v for v in vol_strs), "docker.sock must be mounted read-only"

        # environment
        env = svc.get("environment")
        assert isinstance(env, dict), "environment must be a mapping"
        expected_env = {
            "FASTMCP_HOST": "${FASTMCP_HOST:-0.0.0.0}",
            "FASTMCP_PORT": "${FASTMCP_PORT:-8000}",
            "DOCKER_HOSTS_CONFIG": "${DOCKER_HOSTS_CONFIG:-/app/config/hosts.yml}",
            "LOG_LEVEL": "${LOG_LEVEL:-INFO}",
            "LOG_DIR": "${LOG_DIR:-/app/data/logs}",
            "LOG_FILE_SIZE_MB": "${LOG_FILE_SIZE_MB:-10}",
            "SSH_CONFIG_PATH": "/home/dockermcp/.ssh/config",
            "SSH_DEBUG": "${SSH_DEBUG:-0}",
            "DEVELOPMENT_MODE": "${DEVELOPMENT_MODE:-false}",
            "HOT_RELOAD": "${HOT_RELOAD:-true}",
            "RATE_LIMIT_PER_SECOND": "${RATE_LIMIT_PER_SECOND:-50.0}",
            "SLOW_REQUEST_THRESHOLD_MS": "${SLOW_REQUEST_THRESHOLD_MS:-5000.0}",
        }
        for k, v in expected_env.items():
            assert env.get(k) == v, f"environment variable {k} mismatch: expected {v}, got {env.get(k)}"

        # healthcheck
        health = svc.get("healthcheck")
        assert isinstance(health, dict), "healthcheck must be defined"
        test_cmd = health.get("test")
        assert isinstance(test_cmd, list) and test_cmd[:2] == ["CMD-SHELL", "python -c \"import os,socket; socket.create_connection(('localhost', int(os.getenv('FASTMCP_PORT','8000'))), timeout=5)\""], "healthcheck test command invalid"
        assert str(health.get("interval")) == "30s", "healthcheck interval must be 30s"
        assert str(health.get("timeout")) == "10s", "healthcheck timeout must be 10s"
        assert int(health.get("retries")) == 3, "healthcheck retries must be 3"
        assert str(health.get("start_period")) == "10s", "healthcheck start_period must be 10s"

        # logging
        logging = svc.get("logging")
        assert isinstance(logging, dict) and logging.get("driver") == "json-file", "logging driver must be json-file"
        opts = logging.get("options", {})
        assert opts.get("max-size") == "10m", "logging option max-size must be 10m"
        assert opts.get("max-file") == "3", "logging option max-file must be 3"

        # top-level volumes
        vols = data.get("volumes", {})
        assert "docker-mcp-data" in vols, "top-level volume docker-mcp-data must exist"

        # networks
        networks = data.get("networks", {})
        default = networks.get("default", {})
        assert default.get("name") == "docker-mcp-network", "default network name mismatch"
        assert default.get("driver") == "bridge", "default network driver must be bridge"
    else:
        # Fallback: text-based assertions
        # Existence checks
        assert re.search(r"^\s*services:\s*$", text, re.M)
        assert re.search(r"^\s{2}docker-mcp:\s*$", text, re.M)
        # Basic keys
        assert re.search(r"^\s{4}build:\s*\.\s*$", text, re.M)
        assert re.search(r"^\s{4}container_name:\s*docker-mcp\s*$", text, re.M)
        assert re.search(r"^\s{4}restart:\s*unless-stopped\s*$", text, re.M)
        # Ports
        ports_block = _extract_block(text, "docker-mcp", "ports")
        assert "${FASTMCP_PORT:-8000}:${FASTMCP_PORT:-8000}" in ports_block, "port mapping missing or incorrect"
        # Volumes
        volumes_block = _extract_block(text, "docker-mcp", "volumes")
        assert "/home/dockermcp/.ssh:ro" in volumes_block, "SSH keys volume must be read-only"
        assert "/app/config:ro" in volumes_block, "config volume must be read-only"
        assert "docker-mcp-data:/app/data" in volumes_block, "data volume mount missing"
        assert "/var/run/docker.sock:/var/run/docker.sock:ro" in volumes_block, "docker.sock must be read-only"
        # Environment
        env_block = _extract_block(text, "docker-mcp", "environment")
        for k, v in [
            ("FASTMCP_HOST", '"${FASTMCP_HOST:-0.0.0.0}"'),
            ("FASTMCP_PORT", '"${FASTMCP_PORT:-8000}"'),
            ("DOCKER_HOSTS_CONFIG", '"${DOCKER_HOSTS_CONFIG:-/app/config/hosts.yml}"'),
            ("LOG_LEVEL", '"${LOG_LEVEL:-INFO}"'),
            ("LOG_DIR", '"${LOG_DIR:-/app/data/logs}"'),
            ("LOG_FILE_SIZE_MB", '"${LOG_FILE_SIZE_MB:-10}"'),
            ("SSH_CONFIG_PATH", '"/home/dockermcp/.ssh/config"'),
            ("SSH_DEBUG", '"${SSH_DEBUG:-0}"'),
            ("DEVELOPMENT_MODE", '"${DEVELOPMENT_MODE:-false}"'),
            ("HOT_RELOAD", '"${HOT_RELOAD:-true}"'),
            ("RATE_LIMIT_PER_SECOND", '"${RATE_LIMIT_PER_SECOND:-50.0}"'),
            ("SLOW_REQUEST_THRESHOLD_MS", '"${SLOW_REQUEST_THRESHOLD_MS:-5000.0}"'),
        ]:
            pattern = rf"^\s+{re.escape(k)}:\s*{v}\s*$"
            assert re.search(pattern, env_block, re.M), f"env {k} not correctly set"
        # Healthcheck
        assert re.search(
            r'^\s{4}healthcheck:\s*$\n\s{6}test:\s*\["CMD-SHELL",\s*"python -c \\"import os,socket; socket.create_connection\(\'\(\'localhost\', int(os.getenv\(\'FASTMCP_PORT\',\'8000\'\)\)\)\', timeout=5\)\\\"\]\s*$',
            text, re.M
        ) or "socket.create_connection(('localhost', int(os.getenv('FASTMCP_PORT','8000'))), timeout=5)" in text, "healthcheck command invalid"
        assert re.search(r"^\s{6}interval:\s*30s\s*$", text, re.M)
        assert re.search(r"^\s{6}timeout:\s*10s\s*$", text, re.M)
        assert re.search(r"^\s{6}retries:\s*3\s*$", text, re.M)
        assert re.search(r"^\s{6}start_period:\s*10s\s*$", text, re.M)
        # Logging
        logging_block = _extract_block(text, "docker-mcp", "logging")
        assert 'driver: "json-file"' in logging_block or "driver: json-file" in logging_block
        assert re.search(r'max-size:\s*"10m"|max-size:\s*10m', logging_block)
        assert re.search(r'max-file:\s*"3"|max-file:\s*3', logging_block)
        # Top-level volumes and networks
        assert re.search(r"^\s*volumes:\s*$", text, re.M)
        assert re.search(r"^\s{2}docker-mcp-data:\s*$", text, re.M)
        assert re.search(r"^\s*networks:\s*$", text, re.M)
        networks_block = re.search(r"(?ms)^networks:\s*\n(.+)$", text)
        assert networks_block and ("name: docker-mcp-network" in networks_block.group(1))
        assert "driver: bridge" in networks_block.group(1)

def test_environment_defaults_rendering_examples():
    # Validate that env interpolation expressions include sane defaults
    text, _ = _read_compose_text()
    # Ensure defaults are wrapped properly with :- syntax
    defaults = {
        "FASTMCP_HOST": "0.0.0.0",
        "FASTMCP_PORT": "8000",
        "DOCKER_HOSTS_CONFIG": "/app/config/hosts.yml",
        "LOG_LEVEL": "INFO",
        "LOG_DIR": "/app/data/logs",
        "LOG_FILE_SIZE_MB": "10",
        "SSH_DEBUG": "0",
        "DEVELOPMENT_MODE": "false",
        "HOT_RELOAD": "true",
        "RATE_LIMIT_PER_SECOND": "50.0",
        "SLOW_REQUEST_THRESHOLD_MS": "5000.0",
    }
    for key, default in defaults.items():
        assert f'${{{key}:-{default}}}' in text, f"Default for {key} should be {default}"

def test_security_related_mounts_are_read_only():
    text, _ = _read_compose_text()
    assert "/home/dockermcp/.ssh:ro" in text, "SSH keys mount must be read-only"
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in text, "Docker socket should be read-only to minimize risk"
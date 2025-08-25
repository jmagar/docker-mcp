# AI Review Content from PR #7

- [ ] [COPILOT REVIEW - copilot-pull-request-reviewer[bot]]
## Pull Request Overview

This PR enhances the Docker MCP installer with improved container environment detection, dynamic path management, and comprehensive SSH key distribution capabilities. It replaces the basic embedded SSH setup with a standalone, feature-rich script while maintaining backward compatibility.

- Implements container runtime detection using the `DOCKER_CONTAINER` environment variable
- Adds dynamic data/config directory resolution for both container and local environments  
- Introduces a comprehensive standalone SSH key distribution script with parallel processing and host discovery

### Reviewed Changes

Copilot reviewed 6 out of 6 changed files in this pull request and generated 5 comments.

<details>
<summary>Show a summary per file</summary>

| File | Description |
|---

- [x] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:42-46]
> -    # Create formatters
> -    json_formatter = structlog.testing.LogCapture()
> +    # Create formatters
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:42-46]
> -    structlog.configure(
> -        processors=[
> -            structlog.contextvars.merge_contextvars,
> -            structlog.processors.add_log_level,
> -            structlog.processors.StackInfoRenderer(),
> -            structlog.dev.set_exc_info,
> -            structlog.processors.TimeStamper(fmt="iso"),
> -            structlog.dev.ConsoleRenderer() if sys.stdout.isatty() else structlog.processors.JSONRenderer(),
> -        ],
> -        wrapper_class=structlog.make_filtering_bound_logger(log_level_num),
> -        logger_factory=structlog.WriteLoggerFactory(),
> -        cache_logger_on_first_use=True,
> -    )
> +    from structlog.stdlib import LoggerFactory, BoundLogger, ProcessorFormatter
> +    renderer = structlog.dev.ConsoleRenderer() if sys.stdout.isatty() else structlog.processors.JSONRenderer()
> +    # Integrate with stdlib handlers so file and console both receive events
> +    structlog.configure(
> +        processors=[
> +            structlog.contextvars.merge_contextvars,
> +            structlog.processors.add_log_level,
> +            structlog.processors.StackInfoRenderer(),
> +            structlog.processors.TimeStamper(fmt="iso"),
> +            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
> +        ],
> +        logger_factory=LoggerFactory(),
> +        wrapper_class=BoundLogger,
> +        cache_logger_on_first_use=True,
> +    )
> +    # Apply structlog formatting via ProcessorFormatter on each handler
> +    console_handler.setFormatter(ProcessorFormatter(processor=renderer))
> +    server_file_handler.setFormatter(ProcessorFormatter(processor=structlog.processors.JSONRenderer()))
> +    middleware_file_handler.setFormatter(ProcessorFormatter(processor=structlog.processors.JSONRenderer()))
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:42-46]
> -      test: ["CMD", "python", "-c", "import socket; socket.create_connection(('localhost', 8000), timeout=5)"]
> +      test: ["CMD-SHELL", "python -c \"import os,socket; socket.create_connection(('localhost', int(os.getenv('FASTMCP_PORT','8000'))), timeout=5)\""]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:42-46]
> -RUN mkdir -p /home/dockermcp/.ssh /app/data /app/logs && \
> -    chown -R dockermcp:dockermcp /home/dockermcp/.ssh /app/data /app/logs /app && \
> +RUN mkdir -p /home/dockermcp/.ssh /app/data && \
> +    chown -R dockermcp:dockermcp /home/dockermcp/.ssh /app/data /app && \
>      chmod 700 /home/dockermcp/.ssh
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:42-46]
> --- a/docker_mcp/server.py
> +++ b/docker_mcp/server.py
> @@ def main() -> None:
> -    # Setup logging
> -    structlog.configure(
> -        processors=[
> -            structlog.stdlib.filter_by_level,
> -            structlog.stdlib.add_logger_name,
> -            structlog.stdlib.add_log_level,
> -            structlog.stdlib.PositionalArgumentsFormatter(),
> -            structlog.processors.TimeStamper(fmt="iso"),
> -            structlog.processors.StackInfoRenderer(),
> -            structlog.processors.format_exc_info,
> -            structlog.processors.UnicodeDecoder(),
> -            structlog.processors.JSONRenderer(),
> -        ],
> -        context_class=dict,
> -        logger_factory=structlog.stdlib.LoggerFactory(),
> -        wrapper_class=structlog.stdlib.BoundLogger,
> -        cache_logger_on_first_use=True,
> -    )
> -
> -    logger = structlog.get_logger()
> -    logger.setLevel(args.log_level)
> +    # Setup unified logging (console + files)
> +    from docker_mcp.core.logging_config import setup_logging, get_server_logger
> +    setup_logging(
> +        log_dir=os.getenv("LOG_DIR", str(get_data_dir() / "logs")),
> +        log_level=args.log_level,
> +        max_file_size_mb=int(os.getenv("LOG_FILE_SIZE_MB", "10"))
> +    )
> +    logger = get_server_logger()
> @@ class DockerMCPServer:
> -        # Setup dual logging system first (before any logging)
> -        setup_logging(
> -            log_dir=os.getenv("LOG_DIR", str(get_data_dir() / "logs")),
> -            log_level=os.getenv("LOG_LEVEL"),
> -            max_file_size_mb=int(os.getenv("LOG_FILE_SIZE_MB", "10"))
> -        )
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:42-46]
> -                # Update docker-compose.yaml with new port
> -                sed -i.bak "s|\"8000:8000\"|\"${available_port}:8000\"|g" "$compose_file"
> -                sed -i.bak "s|FASTMCP_PORT: \"8000\"|FASTMCP_PORT: \"8000\"|g" "$compose_file"
> -                rm -f "${compose_file}.bak"
> -                
>                  # Store the port for later use
>                  echo "FASTMCP_PORT=${available_port}" > "${DOCKER_MCP_DIR}/.env"
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -[dependency-groups]
> -dev = [
> -    "pytest-asyncio>=1.1.0",
> -    "pytest-cov>=6.2.1",
> -    "pytest-stub>=1.1.0",
> -    "pytest-xdist>=3.8.0",
> -    "pytest-timeout>=2.3.0",
> -    "types-pyyaml>=6.0.12.20250809",
> -    "types-requests>=2.32.4.20250809",
> -]
> + # Dependency groups removed to avoid duplication with [project.optional-dependencies].dev
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -        if result.returncode != 0:
> -            raise RsyncError(f"Rsync failed: {result.stderr}")
> +        if result.returncode != 0:
> +            snippet = (result.stdout or "")[:500]
> +            raise RsyncError(f"Rsync failed: {result.stderr or snippet}")
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -        tar_cmd = ["tar", "czf", archive_file, "-C", common_parent] + exclude_flags + relative_paths
> +        import shlex
> +        tar_cmd = ["tar", "czf", archive_file, "-C", common_parent] + exclude_flags + relative_paths
> @@
> -        remote_cmd = " ".join(tar_cmd)
> +        remote_cmd = " ".join(map(shlex.quote, tar_cmd))
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -        verify_cmd = ssh_cmd + [f"tar tzf {archive_path} > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"]
> +        import shlex
> +        verify_cmd = ssh_cmd + [f"tar tzf {shlex.quote(archive_path)} > /dev/null 2>&1 && echo 'OK' || echo 'FAILED'"]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -        extract_cmd = ssh_cmd + [f"cd {extract_dir} && tar xzf {archive_path}"]
> +        import shlex
> +        extract_cmd = ssh_cmd + [f"tar xzf {shlex.quote(archive_path)} -C {shlex.quote(extract_dir)}"]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
>  import structlog
> +import shlex
> 
>      # Before
> -    df_cmd = ssh_cmd + [f"df -T {path} | tail -1"]
> +    df_cmd = ssh_cmd + [f"df -T {shlex.quote(path)} | tail -1"]
> 
>      # Before
> -   snap_cmd = ssh_cmd + [f"zfs snapshot {snap_flags} {full_snapshot}".strip()]
> +   quoted_snapshot = shlex.quote(f"{dataset}@{snapshot_name}")
> +   snap_cmd = ssh_cmd + [f"zfs snapshot {snap_flags} {quoted_snapshot}".strip()]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -            # Build SSH command for source
> -            ssh_cmd_source = ["ssh", "-o", "StrictHostKeyChecking=no"]
> -            if source_host.identity_file:
> -                ssh_cmd_source.extend(["-i", source_host.identity_file])
> -            ssh_cmd_source.append(f"{source_host.user}@{source_host.hostname}")
> +            # Build SSH command for source (reuses port/identity handling)
> +            ssh_cmd_source = self._build_ssh_cmd(source_host)
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -            result = subprocess.run(read_cmd, capture_output=True, text=True, check=False)  # nosec B603
> +            loop = asyncio.get_running_loop()
> +            result = await loop.run_in_executor(
> +                None, lambda: subprocess.run(read_cmd, capture_output=True, text=True, check=False)  # nosec B603
> +            )
> @@
> -                verify_result = subprocess.run(verify_cmd, capture_output=True, text=True, check=False)  # nosec B603
> +                verify_result = await loop.run_in_executor(
> +                    None, lambda: subprocess.run(verify_cmd, capture_output=True, text=True, check=False)  # nosec B603
> +                )
> @@
> -                subprocess.run(sync_cmd, capture_output=True, check=False)  # nosec B603
> +                await loop.run_in_executor(
> +                    None, lambda: subprocess.run(sync_cmd, capture_output=True, check=False)  # nosec B603
> +                )
> @@
> -                result = subprocess.run(extract_cmd, capture_output=True, text=True, check=False)  # nosec B603
> +                result = await loop.run_in_executor(
> +                    None, lambda: subprocess.run(extract_cmd, capture_output=True, text=True, check=False)  # nosec B603
> +                )
> @@
> -                    fallback_result = subprocess.run(fallback_cmd, capture_output=True, text=True, check=False)  # nosec B603
> +                    fallback_result = await loop.run_in_executor(
> +                        None, lambda: subprocess.run(fallback_cmd, capture_output=True, text=True, check=False)  # nosec B603
> +                    )
> @@
> -                subprocess.run(remove_cmd, check=False)  # nosec B603
> +                await loop.run_in_executor(None, lambda: subprocess.run(remove_cmd, check=False))  # nosec B603
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
>     -    read_cmd = ssh_cmd_source + [f"cat {compose_file_path}"]
>     +    read_cmd = ssh_cmd_source + [f"cat {shlex.quote(compose_file_path)}"]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
>     -    verify_cmd = ssh_cmd_source + [f"docker ps --filter 'label=com.docker.compose.project={stack_name}' --format '{{{{.Names}}}}'"]
>     +    verify_cmd = ssh_cmd_source + [(
>     +        f"docker ps --filter "
>     +        f"'label=com.docker.compose.project={shlex.quote(stack_name)}' "
>     +        f"--format '{{{{.Names}}}}'"
>     +    )]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
>      extract_cmd = self._build_ssh_cmd(target_host) + [
>     -    f"mkdir -p {target_stack_dir}.tmp && "
>     -    f"tar xzf /tmp/{stack_name}_migration.tar.gz -C {target_stack_dir}.tmp && "
>     -    f"rm -rf {target_stack_dir}.old && "
>     -    f"test -d {target_stack_dir} && mv {target_stack_dir} {target_stack_dir}.old || true && "
>     -    f"mv {target_stack_dir}.tmp {target_stack_dir} && "
>     -    f"rm -rf {target_stack_dir}.old && "
>     +    f"mkdir -p {shlex.quote(target_stack_dir)}.tmp && "
>     +    f"tar xzf /tmp/{shlex.quote(stack_name)}_migration.tar.gz "
>     +    f"-C {shlex.quote(target_stack_dir)}.tmp && "
>     +    f"rm -rf {shlex.quote(target_stack_dir)}.old && "
>     +    f"test -d {shlex.quote(target_stack_dir)} "
>     +    f"&& mv {shlex.quote(target_stack_dir)} {shlex.quote(target_stack_dir)}.old || true && "
>     +    f"mv {shlex.quote(target_stack_dir)}.tmp {shlex.quote(target_stack_dir)} && "
>     +    f"rm -rf {shlex.quote(target_stack_dir)}.old && "
>      ]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
>     -    fallback_cmd = self._build_ssh_cmd(target_host) + [
>     -        f"cd {target_stack_dir} && "
>     -        f"tar xzf /tmp/{stack_name}_migration.tar.gz --overwrite --no-same-owner"
>     -    ]
>     +    fallback_cmd = self._build_ssh_cmd(target_host) + [
>     +        f"cd {shlex.quote(target_stack_dir)} && "
>     +        f"tar xzf /tmp/{shlex.quote(stack_name)}_migration.tar.gz "
>     +        f"--overwrite --no-same-owner"
>     +    ]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
>     -    remove_cmd = ssh_cmd_source + [f"rm -f {compose_file_path}"]
>     +    remove_cmd = ssh_cmd_source + [f"rm -f {shlex.quote(compose_file_path)}"]
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -        parts = expanded_volume_str.split(":")
> +        # Split into at most [source, dest, mode]; don't explode extra colons
> +        parts = expanded_volume_str.split(":", 2)
> @@
> -        if parts[0].startswith("/") or parts[0].startswith("./") or parts[0].startswith("~"):
> +        if parts[0].startswith(("/", "./", "~")):
>              return {
>                  "type": "bind",
>                  "source": parts[0],
>                  "destination": parts[1] if len(parts) > 1 else "",
>                  "mode": parts[2] if len(parts) > 2 else "rw",
>                  "original": volume_str,  # Keep original for path mapping
>              }
>          else:
>              # Named volume
>              return {
>                  "type": "named",
>                  "name": parts[0],
>                  "destination": parts[1] if len(parts) > 1 else "",
>                  "mode": parts[2] if len(parts) > 2 else "rw",
> +                "original": volume_str,
>              }
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -    def update_compose_for_migration(...):
> -        updated_content = compose_content
> -        if target_appdata_path:
> -            updated_content = updated_content.replace("${APPDATA_PATH}", target_appdata_path)
> -            ...
> -        for old_path in old_paths.values():
> -            if old_path in updated_content:
> -                ...
> -                updated_content = updated_content.replace(old_path, new_path)
> -        return updated_content
> +    def update_compose_for_migration(...):
> +        try:
> +            data = yaml.safe_load(compose_content) or {}
> +            for svc in (data.get("services") or {}).values():
> +                vols = svc.get("volumes") or []
> +                new_vols = []
> +                for v in vols:
> +                    if isinstance(v, str):
> +                        host, sep, rest = v.partition(":")
> +                        if sep:
> +                            if target_appdata_path and "${APPDATA_PATH}" in host:
> +                                host = host.replace("${APPDATA_PATH}", target_appdata_path)
> +                            for old in old_paths.values():
> +                                if host == old or host.startswith(old.rstrip("/") + "/"):
> +                                    rel = host[len(old):].lstrip("/")
> +                                    host = f"{new_base_path}/{rel}" if rel else f"{new_base_path}/{old.rsplit('/',1)[-1]}"
> +                            new_vols.append(f"{host}:{rest}")
> +                        else:
> +                            new_vols.append(v)  # named volume
> +                    elif isinstance(v, dict) and v.get("type") == "bind":
> +                        host = v.get("source", "")
> +                        if target_appdata_path and "${APPDATA_PATH}" in host:
> +                            host = host.replace("${APPDATA_PATH}", target_appdata_path)
> +                        for old in old_paths.values():
> +                            if host == old or host.startswith(old.rstrip('/') + "/"):
> +                                rel = host[len(old):].lstrip("/")
> +                                host = f"{new_base_path}/{rel}" if rel else f"{new_base_path}/{old.rsplit('/',1)[-1]}"
> +                        v["source"] = host
> +                        new_vols.append(v)
> +                    else:
> +                        new_vols.append(v)
> +                svc["volumes"] = new_vols
> +            return yaml.safe_dump(data, sort_keys=False)
> +        except Exception as e:
> +            self.logger.error("Failed updating compose for migration", error=str(e))
> +            raise MigrationError(f"Failed to update compose for migration: {e}")
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> -        check_cmd = ssh_cmd + [
> -            f"docker ps --filter 'label=com.docker.compose.project={stack_name}' --format '{{{{.Names}}}}'"
> -        ]
> +        import shlex
> +        label = shlex.quote(f"com.docker.compose.project={stack_name}")
> +        check_cmd = ssh_cmd + [f"docker ps --filter label={label} --format '{{{{.Names}}}}'"]
> @@
> -            # Force stop each container
> +            # Force stop each container
>              for container in running_containers:
> -                stop_cmd = ssh_cmd + [f"docker kill {container}"]
> +                stop_cmd = ssh_cmd + [f"docker kill {shlex.quote(container)}"]
>                  await asyncio.get_event_loop().run_in_executor(
>                      None,
>                      lambda: subprocess.run(  # nosec B603
>                          stop_cmd, check=False, capture_output=True, text=True
>                      ),
>                  )
> -            # Wait for containers to stop and processes to fully terminate
> -            await asyncio.sleep(10)  # Increased from 3s to ensure complete shutdown
> -            # Re-check
> -            return await self.verify_containers_stopped(ssh_cmd, stack_name, force_stop=False)
> +            # Poll until stopped (max 20s)
> +            for _ in range(20):
> +                await asyncio.sleep(1)
> +                all_stopped, still_running = await self.verify_containers_stopped(ssh_cmd, stack_name, force_stop=False)
> +                if all_stopped:
> +                    return True, []
> +            return False, still_running
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> - self.logger.info(
> -     "Parsed compose volumes",
> -     named_volumes=len(...),
> -     bind_mounts=len(...),
> - )
> + self.logger.info(
> +     "Parsed compose volumes",
> +     named_volumes=len(volumes_info["named_volumes"]),
> +     bind_mounts=len(volumes_info["bind_mounts"]),
> +     services=list((compose_data or {}).get("services", {}).keys())[:8],
> + )
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
> --- a/docker_mcp/core/migration.py
> +++ b/docker_mcp/core/migration.py
> @@ -31,8 +31,17 @@ class Migration:
> -    async def parse_compose_volumes(self, compose_content: str, source_appdata_path: str = None) -> dict[str, Any]:
> -        """Parse Docker Compose file to extract volume information.
> +    async def parse_compose_volumes(self, compose_content: str, source_appdata_path: str | None = None) -> dict[str, Any]:
> +        """Delegate to shared VolumeParser to ensure a single source of truth."""
>          try:
> -            compose_data = yaml.safe_load(compose_content)
> -            ...  # inlined parsing logic
> -            return volumes_info
> -        except yaml.YAMLError as e:
> -            raise MigrationError(f"Failed to parse compose file: {e}")
> -        except Exception as e:
> -            raise MigrationError(f"Error extracting volumes: {e}")
> +            from .migration.volume_parser import VolumeParser  # avoid import cycles
> +            parser = VolumeParser()
> +            return await parser.parse_compose_volumes(compose_content, source_appdata_path)
> +        except Exception as e:
> +            raise MigrationError(f"Error parsing compose volumes: {e}")
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
>  def main() -> None:
> @@
> -    # Setup logging
> -    structlog.configure(
> -        processors=[
> -            structlog.stdlib.filter_by_level,
> -            structlog.stdlib.add_logger_name,
> -            structlog.stdlib.add_log_level,
> -            structlog.stdlib.PositionalArgumentsFormatter(),
> -            structlog.processors.TimeStamper(fmt="iso"),
> -            structlog.processors.StackInfoRenderer(),
> -            structlog.processors.format_exc_info,
> -            structlog.processors.UnicodeDecoder(),
> -            structlog.processors.JSONRenderer(),
> -        ],
> -        context_class=dict,
> -        logger_factory=structlog.stdlib.LoggerFactory(),
> -        wrapper_class=structlog.stdlib.BoundLogger,
> -        cache_logger_on_first_use=True,
> -    )
> -
> -    logger = structlog.get_logger()
> -    logger.setLevel(args.log_level)
> +    # Ensure our setup_logging (in DockerMCPServer.__init__) sees CLI log level
> +    os.environ["LOG_LEVEL"] = args.log_level
> +    logger = structlog.get_logger()
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
-def get_data_dir() -> Path:
-    """Get data directory based on environment."""
-    if os.getenv("DOCKER_CONTAINER"):
-        return Path("/app/data")
-    else:
-        # Local development path
-        return Path.home() / ".docker-mcp" / "data"
+def get_data_dir() -> Path:
+    """Get data directory based on environment."""
+    override = os.getenv("FASTMCP_DATA_DIR")
+    if override:
+        return Path(override)
+    if os.getenv("DOCKER_CONTAINER", "").lower() in ("1", "true", "yes", "y", "on"):
+        return Path("/app/data")
+    # Local development
+    return Path.home() / ".docker-mcp" / "data"---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:28-42]
-def get_config_dir() -> Path:
-    """Get config directory based on environment.""" 
-    if os.getenv("DOCKER_CONTAINER"):
-        return Path("/app/config")
-    else:
-        # Local development - use project config dir
-        return Path("config")
+def get_config_dir() -> Path:
+    """Get config directory based on environment."""
+    override = os.getenv("FASTMCP_CONFIG_DIR")
+    if override:
+        return Path(override)
+    if os.getenv("DOCKER_CONTAINER", "").lower() in ("1", "true", "yes", "y", "on"):
+        return Path("/app/config")
+    # Local development - use project config dir
+    return Path("config")---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
> -        """Consolidated Docker container management tool.
> +        """Consolidated Docker container management tool.
>  ...
> -        - logs: Get container logs (requires: host_id, container_id)
> +        - logs: Get container logs (requires: host_id, container_id)
> +        - pull: Pull an image on the host (requires: host_id, container_id as image name)
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
> -                if limit < 1 or limit > 100:
> -                    return {"success": False, "error": "limit must be between 1 and 100"}
> +                if limit < 1 or limit > 1000:
> +                    return {"success": False, "error": "limit must be between 1 and 1000"}
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
> -                if lines < 1 or lines > 1000:
> -                    return {"success": False, "error": "lines must be between 1 and 1000"}
> +                if lines < 1 or lines > 10000:
> +                    return {"success": False, "error": "lines must be between 1 and 10000"}
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
> -                if lines < 1 or lines > 1000:
> -                    return {"success": False, "error": "lines must be between 1 and 1000"}
> +                if lines < 1 or lines > 10000:
> +                    return {"success": False, "error": "lines must be between 1 and 10000"}
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
> -    default_config = os.getenv("DOCKER_HOSTS_CONFIG", "config/hosts.yml")
> +    default_config = os.getenv("DOCKER_HOSTS_CONFIG", str(get_config_dir() / "hosts.yml"))
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
> -        config_path_for_reload = args.config or os.getenv("DOCKER_HOSTS_CONFIG", "config/hosts.yml")
> +        config_path_for_reload = args.config
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
>     - logs_result = await self.log_tools.get_container_logs(
>     -     host_id, container_id, lines, None, False
>     - )
>     + logs_result = await self.log_tools.get_container_logs(
>     +     host_id=host_id,
>     +     container_id=container_id,
>     +     lines=lines,
>     +     since=None,
>     +     timestamps=False,
>     +     follow=follow,
>     + )
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-set -e
+set -Eeuo pipefail
+# Minimal ERR trap (don't rely on functions not yet defined here)
+trap 'echo "[ERROR] Unexpected failure at line $LINENO (exit=$?): ${BASH_COMMAND}" >&2' ERR---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
+# (removed) SCRIPT_DIR was unused---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
@@
-BATCH_MODE=false
-DRY_RUN=false
-VERIFY_ONLY=false
+BATCH_MODE=false
+DRY_RUN=false
+VERIFY_AFTER=false
@@
-            -v|--verify)
-                VERIFY_ONLY=true
+            -v|--verify)
+                VERIFY_AFTER=true
                 shift
                 ;;
@@
-    # Special case for verify-only mode
-    if [ "$VERIFY_ONLY" = true ]; then
-        if [ -f "${CONFIG_DIR}/hosts.yml" ]; then
-            # Parse existing config and verify
-            print_info "Verifying existing configuration..."
-            # Implementation would parse existing config
-            print_warning "Verify-only mode not fully implemented yet"
-            exit 0
-        else
-            print_error "No existing configuration found to verify"
-            exit 1
-        fi
-    fi
+    # No verify-only short-circuit; verification can run after distribution with --verify
@@
-    # Verify connectivity unless in batch mode
-    if [ "$BATCH_MODE" != true ]; then
-        verify_connectivity
-    fi
+    # Verify connectivity if requested
+    if [ "$VERIFY_AFTER" = true ]; then
+        verify_connectivity
+    fi---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
@@
-    if ! command -v timeout &> /dev/null; then
-        print_warning "timeout command not found - ssh-keyscan may hang on unreachable hosts"
-    else
-        print_success "timeout is available"
-    fi
+    # Prefer GNU timeout; fall back to gtimeout (macOS coreutils)
+    if command -v timeout >/dev/null 2>&1; then
+        TIMEOUT_CMD="timeout"
+        print_success "timeout is available"
+    elif command -v gtimeout >/dev/null 2>&1; then
+        TIMEOUT_CMD="gtimeout"
+        print_success "gtimeout is available (using as timeout)"
+    else
+        TIMEOUT_CMD=""
+        print_warning "timeout/gtimeout not found - ssh-keyscan may hang on unreachable hosts"
+    fi---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
@@
-    mkdir -p "${DOCKER_MCP_DIR}"
-    mkdir -p "${DOCKER_MCP_DIR}/ssh"
+    mkdir -p "${DOCKER_MCP_DIR}"
+    mkdir -p "${DOCKER_MCP_DIR}/ssh"
     mkdir -p "${CONFIG_DIR}"
     mkdir -p "${DATA_DIR}/logs"
+    mkdir -p "${HOME}/.ssh"
+
+    chmod 700 "${DOCKER_MCP_DIR}/ssh" || true
+    chmod 700 "${HOME}/.ssh" || true
@@
-    if [ -f "${HOME}/.ssh/config" ]; then
+    if [ -f "${HOME}/.ssh/config" ]; then
         ln -sf "${HOME}/.ssh/config" "${DOCKER_MCP_DIR}/ssh/config" 2>/dev/null || true
         print_verbose "Linked SSH config for host resolution"
     fi---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-        for host_entry in "${hosts[@]}"; do
-            local host_name=$(echo "$host_entry" | cut -d'|' -f1)
-            if [[ "$host_name" == $HOST_FILTER ]]; then
+        for host_entry in "${hosts[@]}"; do
+            local host_name
+            host_name="$(echo "$host_entry" | cut -d'|' -f1)"
+            # Intentionally unquoted to allow wildcard matching in --filter (SC2053)
+            if [[ "$host_name" == $HOST_FILTER ]]; then
                 filtered_hosts+=("$host_entry")
             fi
         done---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-            print_info "Generating new Docker MCP SSH key..."
-            ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "docker-mcp@$(hostname)"
+            print_info "Generating new Docker MCP SSH key..."
+            chmod 700 "$(dirname "$SSH_KEY_PATH")" || true
+            ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "docker-mcp:$(hostname -f 2>/dev/null || hostname)"
             chmod 600 "$SSH_KEY_PATH"
             chmod 644 "$SSH_KEY_PATH.pub"---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
@@
-        # Use appropriate port option
-        local scan_opts=""
-        if [ "$port" != "22" ]; then
-            scan_opts="-p $port"
-        fi
+        # Build ssh-keyscan args
+        local -a scan_cmd=(ssh-keyscan -H)
+        if [ "$port" != "22" ]; then
+            scan_cmd+=(-p "$port")
+        fi
@@
-        print_verbose "Running: timeout 10 ssh-keyscan -H ${scan_opts:-} $hostname"
+        print_verbose "Running: ssh-keyscan with 10s timeout if available"
@@
-        # Scan and add to known_hosts (with 10 second timeout)
-        if timeout 10 ssh-keyscan -H $scan_opts "$hostname" >> ~/.ssh/known_hosts 2>/dev/null; then
+        # Ensure ~/.ssh exists and dedupe old entries
+        mkdir -p "${HOME}/.ssh"
+        if [ "$port" = "22" ]; then
+            ssh-keygen -R "$hostname" >/dev/null 2>&1 || true
+        else
+            ssh-keygen -R "[$hostname]:$port" >/dev/null 2>&1 || true
+        fi
+
+        # Scan and add to known_hosts (10s timeout if available)
+        if [ -n "${TIMEOUT_CMD:-}" ]; then
+            if "${TIMEOUT_CMD}" 10 "${scan_cmd[@]}" "$hostname" >> "${HOME}/.ssh/known_hosts" 2>/dev/null; then
+                print_success "OK"
+                : $((scanned++))
+            else
+                print_warning "Failed (will prompt during distribution)"
+                : $((failed++))
+            fi
+        else
+            if "${scan_cmd[@]}" "$hostname" >> "${HOME}/.ssh/known_hosts" 2>/dev/null; then
+                print_success "OK"
+                : $((scanned++))
+            else
+                print_warning "Failed (will prompt during distribution)"
+                : $((failed++))
+            fi
+        fi
-            print_success "OK"
-            : $((scanned++))
-        else
-            print_warning "Failed (will prompt during distribution)"
-            : $((failed++))
-        fi---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
@@
-        local ssh_target="$user@$hostname"
-        local ssh_opts=(-o BatchMode=yes)
+        local ssh_target="$user@$hostname"
+        local -a ssh_opts=(-o BatchMode=yes)
+        # Prefer accept-new where supported; fall back to no
+        if ssh -G localhost 2>/dev/null | grep -qi '^stricthostkeychecking'; then
+            ssh_opts+=(-o StrictHostKeyChecking=accept-new)
+        else
+            ssh_opts+=(-o StrictHostKeyChecking=no)
+        fi
         if [ "$port" != "22" ]; then
             ssh_opts+=(-p "$port")
         fi
@@
-        # Fallback to manual method
-        if cat "${ACTIVE_SSH_KEY}.pub" | ssh "${ssh_opts[@]}" "$ssh_target" "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" >/dev/null 2>&1; then
+        # Fallback to manual method
+        if ssh "${ssh_opts[@]}" "$ssh_target" "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" < "${ACTIVE_SSH_KEY}.pub" >/dev/null 2>&1; then
             echo "$host_entry" >> "$success_file"
             print_success "Success"
             return 0
         else
             echo "$host_entry" >> "$failure_file"
             print_error "Failed"
             return 1
         fi---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-        cat >> "$config_file" << EOF
-  ${host_name}:
-    hostname: ${hostname}
-    user: ${user}
-    port: ${port}
-    identity_file: ${SSH_KEY_PATH}
-    description: "Auto-imported from SSH config"
-    tags: ["auto-imported", "ssh-config"]
-    enabled: true
-    
-EOF
+        cat >> "$config_file" << EOF
+  "${host_name}":
+    hostname: "${hostname}"
+    user: "${user}"
+    port: ${port}
+    identity_file: "${ACTIVE_SSH_KEY}"
+    description: "Auto-imported from SSH config"
+    tags: ["auto-imported", "ssh-config"]
+    enabled: true
+
+EOF---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-from .models.params import DockerHostsParams, DockerContainerParams, DockerComposeParams
+from .models.params import DockerHostsParams, DockerContainerParams, DockerComposeParams  # re-exported for public API  # noqa: F401---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-from docker_mcp.models.params import DockerHostsParams, DockerContainerParams, DockerComposeParams
+from docker_mcp.models.params import DockerHostsParams, DockerContainerParams, DockerComposeParams  # re-exported for public API  # noqa: F401---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-from typing import Any, Annotated, Optional, List, Dict
+from typing import Any, Annotated---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-from ..config_loader import DockerHost
 from ..exceptions import DockerMCPError---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
import asyncio
 import json
 import subprocess
 import time
+import shlex
 from typing import Any

 # ... in create_source_inventory method ...

 for path in volume_paths:
+    qpath = shlex.quote(path)
     path_inventory = {}
     
     # Get file count
-    file_count_cmd = ssh_cmd + [f"find {path} -type f 2>/dev/null | wc -l"]
+    file_count_cmd = ssh_cmd + [f"find {qpath} -type f 2>/dev/null | wc -l"]
     result = await asyncio.get_event_loop().run_in_executor(
         None,
-        lambda: subprocess.run(file_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
+        lambda cmd=file_count_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
     )---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
# Use provided target path
+qtarget = shlex.quote(target_path)
 # Get target inventory using same methods as source
 # File count
-file_count_cmd = ssh_cmd + [f"find {target_path} -type f 2>/dev/null | wc -l"]
+file_count_cmd = ssh_cmd + [f"find {qtarget} -type f 2>/dev/null | wc -l"]
 result = await asyncio.get_event_loop().run_in_executor(
     None,
-    lambda: subprocess.run(file_count_cmd, capture_output=True, text=True, check=False)  # nosec B603
+    lambda cmd=file_count_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
 )---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
for rel_path, source_checksum in source_inventory["critical_files"].items():
-    target_file_path = f"{target_path}/{rel_path}"
-    checksum_cmd = ssh_cmd + [f"md5sum {target_file_path} 2>/dev/null | cut -d' ' -f1"]
+    target_file_path = f"{target_path.rstrip('/')}/{rel_path.lstrip('/')}"
+    qfile = shlex.quote(target_file_path)
+    # Try SHA256 first, fallback to MD5
+    checksum_cmd = ssh_cmd + [
+        f"if command -v sha256sum >/dev/null 2>&1; then "
+        f"  sha256sum {qfile} 2>/dev/null | cut -d' ' -f1; "
+        f"else "
+        f"  md5sum {qfile} 2>/dev/null | cut -d' ' -f1; "
+        f"fi"
+    ]
     
     result = await asyncio.get_event_loop().run_in_executor(
         None,
-        lambda: subprocess.run(checksum_cmd, capture_output=True, text=True, check=False)  # nosec B603
+        lambda cmd=checksum_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
     )---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-inspect_cmd = ssh_cmd + [f"docker inspect {stack_name} 2>/dev/null || echo 'NOT_FOUND'"]
+qname = shlex.quote(stack_name)
+inspect_cmd = ssh_cmd + [f"docker inspect {qname} 2>/dev/null || echo 'NOT_FOUND'"]
 result = await asyncio.get_event_loop().run_in_executor(
     None,
-    lambda: subprocess.run(inspect_cmd, capture_output=True, text=True, check=False)  # nosec B603
+    lambda cmd=inspect_cmd: subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
 )---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-migration_steps.append(f"💾 Creating backup of existing target data...")
+migration_steps.append("💾 Creating backup of existing target data...")

-migration_steps.append(f"✅ Split-phase extraction completed successfully:")
+migration_steps.append("✅ Split-phase extraction completed successfully:")

-migration_steps.append(f"   • Phase 2: ✓ Staging directory verified")
+migration_steps.append("   • Phase 2: ✓ Staging directory verified")

-migration_steps.append(f"   • Phase 3: ✓ Atomic move to final location")
+migration_steps.append("   • Phase 3: ✓ Atomic move to final location")

-migration_steps.append(f"🔍 Verifying split-phase extraction success...")
+migration_steps.append("🔍 Verifying split-phase extraction success...")

-f"Split-phase extraction verification failed",
+"Split-phase extraction verification failed",

-migration_steps.append(f"✅ Verification PASSED: Split-phase extraction successful")
+migration_steps.append("✅ Verification PASSED: Split-phase extraction successful")

-migration_steps.append(f"❌ Verification FAILED: Split-phase extraction incomplete")
+migration_steps.append("❌ Verification FAILED: Split-phase extraction incomplete")

-migration_steps.append(f"⚠️  Skipping source removal - migration verification failed")
+migration_steps.append("⚠️  Skipping source removal - migration verification failed")---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
+def _normalize_volume_entry(self, volume, target_appdata: str, stack_name: str) -> str | None:
+    """Normalize a single volume entry to source:destination format."""
+    if isinstance(volume, str) and ":" in volume:
+        parts = volume.split(":", 2)
+        if len(parts) >= 2:
+            source_path = parts[0]
+            container_path = parts[1]
+            
+            # Convert relative paths to absolute
+            if source_path.startswith("."):
+                source_path = f"{target_appdata}/{stack_name}/{source_path[2:]}"
+            elif not source_path.startswith("/"):
+                # Named volume - needs resolution
+                source_path = f"{target_appdata}/{stack_name}"
+            
+            return f"{source_path}:{container_path}"
+    
+    elif isinstance(volume, dict) and volume.get("type") == "bind":
+        source = volume.get("source", "")
+        target = volume.get("target", "")
+        if source and target:
+            if not source.startswith("/"):
+                source = f"{target_appdata}/{stack_name}/{source}"
+            return f"{source}:{target}"
+    
+    return None

 def _extract_expected_mounts(self, compose_content: str, target_appdata: str, stack_name: str) -> list[str]:
     # ... existing setup ...
-    for service_name, service_config in services.items():
+    for _service_name, service_config in services.items():
         volumes = service_config.get("volumes", [])
         for volume in volumes:
-            # ... existing complex logic ...
+            mount = self._normalize_volume_entry(volume, target_appdata, stack_name)
+            if mount and mount not in expected_mounts:
+                expected_mounts.append(mount)---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
-for service_name, service_config in services.items():
+for _service_name, service_config in services.items():---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
for container in running_containers:
     stop_cmd = ssh_cmd + [f"docker kill {container}"]
     await asyncio.get_event_loop().run_in_executor(
         None,
-        lambda: subprocess.run(  # nosec B603
-            stop_cmd, check=False, capture_output=True, text=True
-        ),
+        lambda cmd=stop_cmd: subprocess.run(  # nosec B603
+            cmd, check=False, capture_output=True, text=True
+        ),
     )---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
+import shlex
 # Create stack-specific directory
 stack_dir = f"{appdata_path}/{stack_name}"
-mkdir_cmd = f"mkdir -p {stack_dir}"
+mkdir_cmd = f"mkdir -p {shlex.quote(stack_dir)}"
 full_cmd = ssh_cmd + [mkdir_cmd]---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
+import tempfile
+import os
 # In transfer_data method
-target_path=f"/tmp/{stack_name}_migration.tar.gz"
+temp_suffix = os.urandom(8).hex()[:8]
+target_path=f"/tmp/{stack_name}_migration_{temp_suffix}.tar.gz"---

- [ ] [PYTHON BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
>     async def get_container_logs(
>         self,
>         host_id: str,
>         container_id: str,
>         lines: int = 100,
>         since: str | None = None,
>         timestamps: bool = False,
>         follow: bool = False,
>     ) -> dict[str, Any]:
>---

- [ ] [PYTHON BLOCK - coderabbitai[bot] - docker_mcp/server.py:289-299]
>     if follow:
>         cmd += " --follow"
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
> -            # Test connection if requested
> -            if test_connection:
> -                # Basic validation - in real implementation would test SSH
> -                pass
> +            # Test connection if requested
> +            if test_connection:
> +                # TODO: Implement real SSH check (e.g., async subprocess 'ssh -o BatchMode=yes ... echo ok')
> +                self.logger.warning(
> +                    "SSH connection test not implemented; skipping",
> +                    host_id=host_id, hostname=ssh_host, user=ssh_user, port=ssh_port
> +                )
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
> -from ..core.config_loader import DockerHost, DockerMCPConfig
> +from ..core.config_loader import DockerHost, DockerMCPConfig, save_config
> @@
> -    async def add_docker_host(
> +    async def add_docker_host(
>          self,
>          host_id: str,
>          ssh_host: str,
>          ssh_user: str,
>          ssh_port: int = 22,
>          ssh_key_path: str | None = None,
>          description: str = "",
>          tags: list[str] | None = None,
>          test_connection: bool = True,
>          compose_path: str | None = None,
>          enabled: bool = True,
> +        persist: bool = False,
>      ) -> dict[str, Any]:
> @@
> -            self.config.hosts[host_id] = host_config
> +            self.config.hosts[host_id] = host_config
> +            if persist:
> +                # Use configured path or default
> +                save_config(self.config, getattr(self.config, "config_file", None))
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
>  def _load_config_file(config: DockerMCPConfig, config_path: Path) -> None:
> @@
>      yaml_config = _load_yaml_config(config_path)
>      _apply_host_config(config, yaml_config)
>      _apply_server_config(config, yaml_config)
> +    _apply_cleanup_schedules(config, yaml_config)
> +
> +
> +def _apply_cleanup_schedules(config: DockerMCPConfig, yaml_config: dict[str, Any]) -> None:
> +    """Apply cleanup schedules from YAML data."""
> +    schedules = yaml_config.get("cleanup_schedules")
> +    if not schedules:
> +        return
> +    config.cleanup_schedules = {
> +        schedule_id: CleanupSchedule(**sched_data)
> +        for schedule_id, sched_data in schedules.items()
> +    }
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
>  def save_config(config: DockerMCPConfig, config_path: str | None = None) -> None:
> @@
> -        # Build YAML structure
> -        yaml_data = _build_yaml_data(config)
> +        # Build YAML structure
> +        yaml_data = _build_yaml_data(config)
> @@
> -        # Write YAML file with proper formatting
> +        # Write YAML file with proper formatting
>          with open(config_path, "w", encoding="utf-8") as f:
>              _write_yaml_header(f)
>              _write_hosts_section(f, yaml_data["hosts"])
> +            _write_cleanup_schedules_section(f, yaml_data["cleanup_schedules"])
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
>  def _write_hosts_section(f, hosts_data: dict[str, Any]) -> None:
> @@
>      for host_id, host_data in hosts_data.items():
>          f.write(f"  {host_id}:\n")
>          for key, value in host_data.items():
>              _write_yaml_value(f, key, value)
>          f.write("\n")
> +
> +
> +def _write_cleanup_schedules_section(f, schedules: dict[str, Any]) -> None:
> +    """Write cleanup_schedules section to YAML file."""
> +    f.write("cleanup_schedules:\n")
> +    if not schedules:
> +        f.write("  {}\n")
> +        return
> +    # Use safe_dump for nested mapping serialization
> +    dumped = yaml.safe_dump(
> +        schedules, default_flow_style=False, sort_keys=False, indent=2
> +    )
> +    # Indent by two spaces under the section key
> +    indented = "".join(f"  {line}" for line in dumped.splitlines(True))
> +    f.write(indented)
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
>  def _build_yaml_data(config: DockerMCPConfig) -> dict[str, Any]:
>      """Build YAML data structure from configuration."""
> -    yaml_data: dict[str, Any] = {"hosts": {}}
> +    yaml_data: dict[str, Any] = {"hosts": {}, "cleanup_schedules": {}}
> @@
> -    return yaml_data
> +    # Persist schedules as plain dicts
> +    if getattr(config, "cleanup_schedules", None):
> +        for sched_id, sched in config.cleanup_schedules.items():
> +            yaml_data["cleanup_schedules"][sched_id] = (
> +                sched.model_dump() if hasattr(sched, "model_dump") else dict(sched)
> +            )
> +    return yaml_data
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
>  def _build_host_data(host_config: DockerHost) -> dict[str, Any]:
> @@
>      if host_config.compose_path:
>          host_data["compose_path"] = host_config.compose_path
> +    if host_config.docker_context:
> +        host_data["docker_context"] = host_config.docker_context
> +    if host_config.appdata_path:
> +        host_data["appdata_path"] = host_config.appdata_path
> +    if host_config.zfs_capable:
> +        host_data["zfs_capable"] = host_config.zfs_capable
> +    if host_config.zfs_dataset:
> +        host_data["zfs_dataset"] = host_config.zfs_dataset
> @@
>      return host_data
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
> -    default_config = os.getenv("DOCKER_HOSTS_CONFIG", "config/hosts.yml")
> +    default_config = os.getenv("DOCKER_HOSTS_CONFIG", str(get_config_dir() / "hosts.yml"))
>---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
-    action: str = Field(
+    action: Literal["list", "add", "ports", "compose_path", "import_ssh", "cleanup", "disk_usage", "schedule"] = Field(
         ..., 
-        description="Action to perform (list, add, ports, compose_path, import_ssh, cleanup, disk_usage, schedule)"
+        description="Action to perform"
     )---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
+    @computed_field(return_type=list[str])
+    @property
+    def selected_hosts_list(self) -> list[str]:
+        if not self.selected_hosts:
+            return []
+        return [h.strip() for h in self.selected_hosts.split(",") if h.strip()]---

- [ ] [DIFF BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
-from typing import Dict, List, Optional
+from typing import Any, Literal
-
-from pydantic import BaseModel, Field
+from pydantic import BaseModel, Field, computed_field---

- [ ] [PYTHON BLOCK - coderabbitai[bot] - docker_mcp/server.py:65-69]
> def _to_dict(self, result: Any, fallback_msg: str = "No structured content") -> dict[str, Any]:
>     if hasattr(result, "structured_content"):
>         return result.structured_content or {"success": True, "data": fallback_msg}
>     return result
>---

- [ ] [AI PROMPT - docker-compose.yaml:8]
In docker-compose.yaml around line 8, the ports mapping uses
${FASTMCP_PORT:-8011}:${FASTMCP_PORT:-8011} while the environment block and
Dockerfile expect 8000, causing the container to map 8011 but the app to listen
on 8000; change the ports mapping to ${FASTMCP_PORT:-8000}:${FASTMCP_PORT:-8000}
(or otherwise set FASTMCP_PORT to 8000 consistently), and verify the env block
on line 26 and the Dockerfile EXPOSE also use 8000 so defaults are unified and
service/healthchecks reach the container.---

- [ ] [AI PROMPT - install.sh:535]
In install.sh around lines 511 to 535, the standalone script path is declared
and assigned in one line (SC2155) and uses dirname in a way that can break when
the file is sourced; also the script is executed directly which fails if not
executable. Fix by first declare the variable and then assign it, compute a
robust absolute script directory using BASH_SOURCE fallback (e.g. cd to dirname
of "${BASH_SOURCE[0]:-$0}" and pwd) to support sourcing, build script_path from
that directory, and invoke the standalone script with "bash" (bash
"$script_path" --batch) to avoid relying on the executable bit; keep the
existing fallback behavior unchanged.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:7]
In scripts/setup-ssh-keys.sh around line 7, the script currently uses only "set
-e" which is insufficient for robust failure handling when manipulating SSH
keys; update the shell options to enable errexit, nounset and pipefail (e.g.,
set -euo pipefail) and add an ERR trap that logs an error message (including
exit code and the failing command) and exits nonzero to ensure any runtime or
unset-variable failures are caught and reported; ensure the trap is defined
immediately after the shell options so it applies to the whole script.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:19]
In scripts/setup-ssh-keys.sh around line 19, the SCRIPT_DIR variable is declared
but never used (SC2034); either remove the assignment entirely to eliminate the
unused-variable warning, or use SCRIPT_DIR when referencing files relative to
the script (e.g., replace any hard-coded or pwd-based relative paths with
"$SCRIPT_DIR/<file>" or cd "$SCRIPT_DIR" before path operations) so the variable
is actually used.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:35]
In scripts/setup-ssh-keys.sh around lines 28-35 (and also update related logic
at 124-127, 720-732, 758-762), the current VERIFY_ONLY flag is used as a
short-circuit that exits early; change semantics so -v/--verify sets a boolean
(e.g., RUN_VERIFY_AFTER_DISTRIBUTION=true) that does not short-circuit
distribution but instead causes the script to run the verification step after
keys are distributed; remove the early-exit branch that returns/exit when
VERIFY_ONLY is set, update option parsing to set the new flag name, and ensure
all references at the other mentioned line ranges check the new flag to run
verification after distribution rather than before/early exiting.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:205]
In scripts/setup-ssh-keys.sh around lines 149 to 205, the prerequisites check
only detects a GNU timeout named "timeout" but on macOS the GNU coreutils
timeout is often installed as "gtimeout"; detect and prefer "timeout" then
"gtimeout", set a TIMEOUT_CMD variable accordingly (export it if other
functions/scripts need it), update the existing timeout check to use this
variable and adjust warning messages when neither is found so macOS users get
the gtimeout hint; ensure HAS_PARALLEL logic remains unchanged and exit behavior
for missing required deps is preserved.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:228]
In scripts/setup-ssh-keys.sh around lines 206 to 228, the directory creation
does not ensure ~/.ssh exists and does not enforce restrictive permissions for
SSH-related directories and files; update create_directories to create
${HOME}/.ssh if missing (mkdir -p), set strict permissions (700) on ~/.ssh and
${DOCKER_MCP_DIR}/ssh and 600 on any private key files copied or created, and
only attempt to link ${HOME}/.ssh/config after ensuring the source exists;
finally ensure any created directories use mkdir -p and apply chmod immediately
after creation to enforce secure layout.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:320]
In scripts/setup-ssh-keys.sh around lines 311-320, avoid SC2155 by declaring the
local variable before using command substitution and add a clarifying comment
for the intentional unquoted glob match: change the single-line local+assignment
to two statements (declare local host_name first, then assign host_name=$(echo
...)), keep the RHS of the == unquoted to allow wildcard matching but add a
short inline comment like "# intentional: unquoted to allow glob/wildcard
matches (SC2053)" to silence future warnings; apply the same change to the
corresponding occurrences on lines 313-315.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:394]
In scripts/setup-ssh-keys.sh around lines 355 to 394, before generating a new
key ensure the parent directory of $SSH_KEY_PATH exists and has 0700 permissions
and, when not in DRY_RUN, set those perms prior to calling ssh-keygen; also
replace the unstable comment that uses hostname with a stable, consistent key
comment (e.g. "docker-mcp") so generated keys are traceable. Ensure the
directory creation/chmod step runs only when actually generating the key (skip
on DRY_RUN), then proceed with existing keygen and file-mode adjustments.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:423]
In scripts/setup-ssh-keys.sh around lines 396-423, the host key scanning is
fragile: it assumes the timeout binary exists (causing “command not found”),
writes noisy stderr, and can create duplicate known_hosts entries for host:port
combos. Fix by ensuring ~/.ssh exists with correct perms before scanning, detect
if timeout is available and fall back to running ssh-keyscan without it, run
ssh-keyscan while redirecting stderr to /dev/null and only append its stdout if
non-empty, and before appending remove any existing entry for that host/port
using ssh-keygen -R with the proper "[hostname]:port" bracket form for
non-default ports so entries are deduplicated.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:567]
In scripts/setup-ssh-keys.sh around lines 540 to 567, parallel background jobs
call distribute_to_host which may concurrently append to the same output file
causing interleaved writes; change the implementation so each host writes to its
own per-host temporary file (e.g., in TMPDIR with a sanitized hostname-based
name) and after all jobs complete aggregate those temp files into the final file
with a single serial operation (cat or mv) or use flock around the final append
to ensure atomicity; ensure temp files are cleaned up and handle failures by
skipping absent temp files.---

- [ ] [AI PROMPT - scripts/setup-ssh-keys.sh:647]
In scripts/setup-ssh-keys.sh around lines 632 to 647, the generated YAML still
writes identity_file: ${SSH_KEY_PATH} and leaves values unquoted; update the
loop to read the actual key path from each host_entry (e.g., IFS='|' read -r
host_name hostname user port key_path <<< "$host_entry"), then emit the YAML
using the per-host key and quote all values and the mapping key to be YAML-safe
(e.g., '"${host_name}":', hostname: '"${hostname}"', user: '"${user}"', port:
'"${port}"', identity_file: '"${key_path}"', description: '"Auto-imported from
SSH config"', tags: '["auto-imported","ssh-config"]', enabled: true). Ensure
quoting uses double quotes around interpolated variables so IPv6 and special
characters are preserved.---

- [ ] [AI PROMPT - docker_mcp/core/migration/__init__.py:8]
In docker_mcp/core/migration/__init__.py around lines 5 to 8, remove the
trailing whitespace at the end of the "from .verification import
MigrationVerifier  " line and ensure the file ends with a single trailing
newline; trim any other extraneous trailing spaces on these lines and save the
file with a newline at EOF to satisfy linters.---

- [ ] [AI PROMPT - docker_mcp/core/migration/verification.py:8]
In docker_mcp/core/migration/verification.py around lines 3-8 (and similarly for
lines 55-85), the review flags two issues: unquoted remote path/stack variables
used in shell commands (injection risk) and lambdas that capture loop variables
(late-binding). Fix by importing shlex and wrap any path/stack/remote variables
passed into shell commands with shlex.quote (or avoid shell=True and pass args
list), and replace lambdas that reference loop variables with functions that
bind current values via default arguments (e.g., lambda x=val: ...) or use
functools.partial so each executor task gets the correct captured value. Ensure
no unquoted interpolation into subprocess calls and avoid late-binding by
binding loop variables at lambda creation.---

- [ ] [AI PROMPT - docker_mcp/core/migration/verification.py:13]
In docker_mcp/core/migration/verification.py around lines 11 to 13, the imported
symbol DockerHost is unused; remove the unused import (delete "DockerHost" from
the from ..config_loader import line) so only DockerMCPError is imported, and
run linting to ensure no other unused imports remain.---

- [ ] [AI PROMPT - docker_mcp/core/migration/verification.py:241]
In docker_mcp/core/migration/verification.py around lines 220 to 245, the
checksum command currently injects the unquoted target path and always uses
md5sum; update it to securely quote the remote path (use shlex.quote or
equivalent) when building the ssh command and prefer sha256sum with a fallback
to md5sum if sha256sum is not available. Build a remote command string that
first tries "sha256sum <quoted_path> 2>/dev/null | cut -d' ' -f1" and if that
returns empty/non-zero, try "md5sum <quoted_path> 2>/dev/null | cut -d' ' -f1";
capture which algorithm produced the checksum and store it in the verification
dict (e.g., algorithm: "sha256" or "md5"), and when both fail mark verified
False with an appropriate error message. Use subprocess.run safely via the
executor as before but pass a single remote command string to ssh, and ensure
stdout is checked/stripped before comparing to the source checksum.---

- [ ] [AI PROMPT - docker_mcp/core/migration/verification.py:312]
In docker_mcp/core/migration/verification.py around lines 284-316,
verify_container_integration is doing too many things (inspecting container,
extracting mounts, checking in-container data access, and collecting startup
logs) which makes it hard to read and test; refactor by extracting these
responsibilities into four helpers: _inspect_container(ssh_cmd, stack_name) ->
dict to run the remote inspect and return parsed container info,
_collect_mounts(container_info) -> list[str] to derive actual mount strings from
the inspect output, _check_in_container_access(ssh_cmd, stack_name) -> bool to
run the in-container data access checks, and _collect_startup_errors(ssh_cmd,
stack_name) -> list[str] to gather startup logs/errors; update
verify_container_integration to call these helpers and compose the verification
dict (apply same extraction pattern to the similar logic at lines 403-423).---

- [ ] [AI PROMPT - docker_mcp/core/migration/verification.py:328]
In docker_mcp/core/migration/verification.py around lines 318 to 332 (and also
apply the same fix to lines 378-397), the docker command is vulnerable to shell
word-splitting and glob expansion for stack_name and the lambda captures the
outer variable late; fix by constructing commands safely using
shlex.quote(stack_name) (or otherwise wrap/escape the stack_name in single
quotes) when building the docker inspect/run strings so special characters and
spaces are preserved, and bind the command into the lambda to avoid late-binding
(e.g., use lambda cmd=inspect_cmd: subprocess.run(cmd, ...)) when calling
run_in_executor.---

- [ ] [AI PROMPT - docker_mcp/core/migration/volume_parser.py:26]
In docker_mcp/core/migration/volume_parser.py around line 26, the
parse_compose_volumes method is too complex (exceeds Ruff's C901); refactor by
extracting two helpers: one to walk and collect all service volume entries
(e.g., iterate services, expand short/long forms, yield raw entries and service
context) and another to normalize a single volume entry into the final dict
structure (handle bind/volume/short syntax, parse source:target:mode, resolve
relative host paths against source_appdata_path, and apply defaults). Replace
the in-method loops with calls to these helpers, keep behavior and tests
unchanged, and ensure proper typing and docstrings for each helper so
parse_compose_volumes becomes a high-level orchestration function.---

- [ ] [AI PROMPT - docker_mcp/core/migration/volume_parser.py:107]
In docker_mcp/core/migration/volume_parser.py around lines 105 to 107, the call
to expanded_volume_str.split(":") can over-split volume strings that include a
third "mode" segment; change it to expanded_volume_str.split(":", 2) so only the
first two ":" are split into at most three parts, preserving the mode portion,
and update any subsequent logic that relies on parts length accordingly.---

- [ ] [AI PROMPT - docker_mcp/core/migration/volume_parser.py:149]
In docker_mcp/core/migration/volume_parser.py around lines 147-149 (and also
apply the same change to lines 150-155), the constructed inspect command must
quote the volume_name to prevent shell interpretation and the per-iteration
command must be frozen when passed to the executor to avoid late-binding: change
inspect_cmd to use a quoted volume_name (e.g. f"docker volume inspect
'{volume_name}' --format '{{{{.Mountpoint}}}}'") and when creating the lambda
for execution capture the current command as a default argument or use
functools.partial (e.g. lambda cmd=full_cmd: run(cmd) or partial(run, full_cmd))
so each task uses its own frozen command.
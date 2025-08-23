## `docker_hosts`
## Combine the following tools into one tool: `docker_hosts`

`list_host_ports` - List all port mappings and detect conflicts
  Becomes:
  - `docker_hosts ports` req: host
  Usage:
  - `docker_hosts ports TOOTIE`

`list_docker_hosts` - List all configured Docker hosts
  Becomes:
  - `docker_hosts list` req: host
  Usage:
  - `docker_hosts list TOOTIE`

`add_docker_host` - Add a remote Docker host for management
  Becomes:
  - `docker_hosts add` 
  Required: host_id, ssh_host, ssh_user
  Optional: ssh_port (default: 22), ssh_key_path, description, tags, compose_path, enabled (default: true)
  Usage:
  - docker_hosts add TOOTIE tootie.example.com root
  - docker_hosts add PROD prod.example.com deploy 2222

`update_host_config` - Update host compose path configuration
  Becomes:
  - `docker_hosts compose path` req: host
  Usage:
  - `docker_hosts compose path TOOTIE /new/compose/path`

`import_ssh_config` - Import hosts from SSH configuration
  Becomes:
  - `docker_hosts import ssh`
  Usage:
  - `docker_hosts import ssh`


## `docker_container`
## Combine the following tools into one tool: `docker_container`

`list_containers` - List all containers with compose file information
  Becomes:
  - `docker_container list` req: host
  Usage:
  - `docker_container list TOOTIE`

`get_container_info` - Get detailed information about a specific container
  Becomes:
  - `docker_container info` req: container + host
  Usage:
  - `docker_container info plex TOOTIE`

`manage_container` - Unified container management (start, stop, restart)
  Becomes:
  - `docker_container [start/stop/restart/build] req: container + host
  Usage:
  - `docker_container stop plex TOOTIE`

`get_container_logs` - Get recent logs from a container
  Becomes:
  - `docker_container logs` req: container + host
  Usage:
  - `docker_container logs plex TOOTIE`


## `docker_compose`
## Combine the following tools into one tool: `docker_compose`

| `deploy_stack` | Deploy a Docker Compose stack with persistent files | 
  Becomes:
  - `docker_compose deploy` req: stack name + host + full docker-compose
  Usage:
  - `docker_compose deploy plex TOOTIE
  """
  insert 
  PLEX
  docker
  compose
  yaml
  here
  """`

`manage_stack` - Manage stack lifecycle (up, down, restart, etc.)
  Becomes:
  - `docker_compose [up/down/restart/build]`
  Usage:
  - `docker_compose restart plex TOOTIE`

`list_stacks` - List all Docker Compose stacks on a host
  Becomes:
  - `docker_compose list` req: host
  Usage:
  - `docker_compose list TOOTIE`

`discover_compose_paths` - Auto-discover compose file locations
  Becomes:
  - `docker_compose discover` req: host
  Usage:
  - `docker_compose discover TOOTIE`

We can also easily add more capabilities to the server without bloating context with a tool for each little thing.
For example, let's enhance the docker_compose tool with the ability to check logs for a stack on a specific host, lets implent:
  - `docker_compose logs`
  Usage:
  - `docker_compose logs plex TOOTIE`

Let's be extra certain we check all of the current tools required params to make sure we properly migrate the tools.
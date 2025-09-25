# Docker MCP Test Prompts
You are to create a TODO for all of the prompts below to test the Docker MCP project.
Pretend I sent you each of the prompts below one at a time, and then execute the corresponding action in the Docker MCP project.
Execute each action for each of the prompts below. 
It doesn't matter if they succeed or fail.
Your sole job is to execute each action for each of the prompts below.
Once you've executed all the actions, respond with "DONE".
Then create a detailed report of what happened for each action, including any errors encountered.

## docker_hosts Actions

### list
List all docker hosts.

### add
Add a new Docker host with host_id "test-server", SSH host "192.168.1.100", SSH user "docker", using SSH key "~/.ssh/id_rsa", description "Test server", and tags "test,development".

### ports
Show port usage on the "squirts" host.

### import_ssh
Import Docker hosts from the SSH config file.

### cleanup
Perform a cleanup check on the "squirts" host to see what can be cleaned up.

### test_connection
Test the SSH connection to the "squirts" host.

### discover
Discover Docker paths and capabilities on the "squirts" host.

### edit
Edit the "squirts" host to change its description to "Production server" and add tags "production,critical".

### remove
Remove the Docker host with ID "test-server".

## docker_container Actions

### list
List all containers on the "squirts" host.

### info
Get detailed information about the "opengist" container on the "squirts" host.

### stop
Stop the "opengist" container on the "squirts" host.

### start
Start the "opengist" container on the "squirts" host.

### restart
Restart the "opengist" container on the "squirts" host.

### logs
Get the last 50 lines of logs from the "opengist" container on the "squirts" host.

### remove
Remove the stopped "test-container" container from the "squirts" host.

## docker_compose Actions

### list
List all Docker Compose stacks on the "squirts" host.

### discover
Discover Docker Compose paths on the "squirts" host.

### view
View the compose file for the "opengist" stack on the "squirts" host.

### deploy
Deploy a new stack called "nginx-test" on the "squirts" host with this compose content:
```yaml
version: '3.8'
services:
  nginx:
    image: nginx:latest
    ports:
      - "8080:80"
```
### down
Bring down the "opengist" stack on the "squirts" host.

### up
Bring up the "opengist" stack on the "squirts" host.

### restart
Restart the "opengist" stack on the "squirts" host.

### build
Build the "swag-mcp" stack on the "squirts" host.

### logs
Get logs from the "opengist" stack on the "squirts" host.

### ps
Show the status of services in the "opengist" stack on the "squirts" host.

### migrate
Migrate the "test-app" stack from "squirts" host to "shart" host.

### pull
Pull the latest images for the "opengist" stack on the "squirts" host.

## Complex Test Scenarios

### Scenario 1: Full Host Setup
1. Add a new Docker host "staging" at "staging.example.com" with user "deploy"
2. Test the connection to the new host
3. Discover paths on the new host
4. List containers on the new host

### Scenario 2: Stack Migration
1. List stacks on source host "squirts"
2. View the compose file for stack "authelia"
3. Migrate the "authelia" stack from "squirts" to "shart" with dry run enabled
4. Check the status of the migrated stack on "shart"

### Scenario 3: Container Management
1. List all running containers on "squirts"
2. Stop container "overseerr"
3. Get logs from the stopped container
4. Start the container again
5. Verify it's running with container info

### Scenario 4: Port Management
1. Check port usage on "squirts"
2. Check if port 8080 is available on "squirts"
3. Deploy a test service on port 8080
4. Verify the port is now in use

### Scenario 5: Cleanup Operations
1. Run cleanup check on "squirts" to see what can be cleaned
2. Perform safe cleanup on "squirts"
3. Verify disk space was freed

## Edge Cases and Error Testing

### Invalid Host
Try to list containers on non-existent host "fake-host".

### Invalid Container
Get info for non-existent container "fake-container" on "squirts".

### Port Conflict
Deploy a stack using port 443 which is already in use by opengist.

### SSH Connection Failure
Add a host with invalid SSH credentials: host "unreachable" at "999.999.999.999".

### Empty Stack Deploy
Try to deploy a stack with empty compose content.

### Migration Without Stop
Migrate a running stack "opengist" from "squirts" to "shart" with skip_stop_source=true.

## Notes for Testing

- Replace host IDs with actual configured hosts from your setup
- Adjust container and stack names based on what's actually running
- Use dry_run=true for potentially destructive operations during testing
- Monitor logs for any errors or warnings
- Test both success and failure paths for each action
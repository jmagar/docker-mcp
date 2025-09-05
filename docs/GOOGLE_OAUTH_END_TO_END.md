# Google OAuth — End‑to‑End Guide for Claude History MCP

This guide documents a complete, production‑ready Google OAuth setup for the Claude History MCP server running at `https://cc-history.tootie.tv`.

It covers credentials, environment configuration, server wiring, reverse proxy, verification, headless login, client usage, troubleshooting, and security hardening. It reflects the implementation in this repository as of FastMCP 2.12.x.

## Overview

- Server: Claude History MCP (`claude_history_mcp.server`), HTTP transport at `/mcp`.
- Auth: FastMCP `GoogleProvider` (built on OAuth Proxy) mounts full OAuth endpoints on your server and proxies to Google (no DCR support needed upstream).
- Reverse proxy: SWAG/Nginx terminates TLS and forwards both MCP and OAuth endpoints to the backend MCP server.
- Clients: MCP clients (e.g., Claude, FastMCP Python Client) use `auth="oauth"` and auto‑negotiate the flow; tokens are cached locally for future sessions.

## Prerequisites

- A Google Cloud project with OAuth 2.0 Client ID (Web application)
  - Authorized Redirect URI: `https://cc-history.tootie.tv/auth/callback`
- Public HTTPS domain: `cc-history.tootie.tv` pointing at your reverse proxy
- This repo deployed with Python 3.12+ and FastMCP 2.12.2+

## Environment Configuration

FastMCP reads server auth configuration from environment variables at startup. This project loads `.env` before constructing the server application, so these values must be present and correct.

Required

- `FASTMCP_SERVER_AUTH=fastmcp.server.auth.providers.google.GoogleProvider`
- `FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID` — Google OAuth Client ID (…apps.googleusercontent.com)
- `FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET` — Google OAuth Client Secret (starts with `GOCSPX-`)
- `FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL=https://cc-history.tootie.tv`
- `FASTMCP_SERVER_AUTH_GOOGLE_REQUIRED_SCOPES=openid,https://www.googleapis.com/auth/userinfo.email`

Recommended (hardening)

- `FASTMCP_SERVER_AUTH_GOOGLE_ALLOWED_CLIENT_REDIRECT_URIS`: comma‑separated list of allowed client redirect patterns; e.g. `http://localhost:*,http://127.0.0.1:*`
  - This restricts which loopback URLs MCP clients can use.

Notes

- The `GoogleProvider` internally defaults `redirect_path` to `/auth/callback`. If you change it in Google Console, set `FASTMCP_SERVER_AUTH_GOOGLE_REDIRECT_PATH` to match.
- Scopes can be extended (e.g., add `https://www.googleapis.com/auth/userinfo.profile`).

## Server Wiring (what this repo does)

File: `claude_history_mcp/server.py`

- Loads `.env` before importing/constructing FastMCP so env‑based auth is available immediately.
- Constructs the app with an explicit Google provider if GOOGLE env is present to guarantee OAuth routes mount:

```python
from fastmcp.server.auth.providers.google import GoogleProvider
app = FastMCP("claude-history", auth=GoogleProvider(), mask_error_details=True)
```

- Adds two diagnostic tools protected by OAuth:
  - `whoami` — returns standard identity claims (iss, sub, email, name, picture)
  - `get_user_info` — simplified wrapper that returns `email`, `name`, `google_id`, `picture`

## Reverse Proxy (SWAG/Nginx)

You must forward both the MCP path and the OAuth endpoints to the backend server (default backend: `http://100.122.19.93:8080`).

Key locations to forward

- `/mcp` — MCP endpoint (SSE/Streamable HTTP)
- `/.well-known/oauth-authorization-server` — discovery
- `/.well-known/openid-configuration` — discovery (OIDC shim)
- `/register` — dynamic client registration
- `/authorize` — authorization endpoint (redirects to Google)
- `/token` — token exchange
- `/auth/` — Google → server callback path (and any subpaths)
- `/revoke` — optional token revocation
- `/health` — health check

Minimal SWAG snippet (server block only)

```nginx
server {
  listen 443 ssl;
  listen [::]:443 ssl;
  server_name cc-history.tootie.tv;
  include /config/nginx/ssl.conf;

  # Upstream target
  set $upstream_app  "100.122.19.93";
  set $upstream_port "8080";
  set $upstream_proto "http";

  # MCP endpoint
  location /mcp {
    include /config/nginx/resolver.conf;
    proxy_http_version 1.1;
    proxy_buffering off; proxy_cache off; proxy_request_buffering off;
    proxy_set_header Connection '';
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_pass $upstream_proto://$upstream_app:$upstream_port;
  }

  # OAuth discovery
  location = /.well-known/oauth-authorization-server { proxy_pass $upstream_proto://$upstream_app:$upstream_port; }
  location = /.well-known/openid-configuration          { proxy_pass $upstream_proto://$upstream_app:$upstream_port; }

  # OAuth endpoints
  location = /register  { proxy_pass $upstream_proto://$upstream_app:$upstream_port; }
  location = /authorize { proxy_pass $upstream_proto://$upstream_app:$upstream_port; }
  location = /token     { proxy_pass $upstream_proto://$upstream_app:$upstream_port; }
  location = /revoke    { proxy_pass $upstream_proto://$upstream_app:$upstream_port; }
  location /auth/       { proxy_pass $upstream_proto://$upstream_app:$upstream_port; }

  # Health
  location /health { proxy_pass $upstream_proto://$upstream_app:$upstream_port; }
}
```

After editing

- Validate and reload SWAG:
  - `docker exec -it swag nginx -t`
  - `docker exec -it swag s6-svc -h /var/run/s6/services/nginx`

## Running the Server

Recommended (uses this repo’s startup/shutdown and HTTP transport):

```bash
uv run python -m claude_history_mcp.server --host 0.0.0.0 --port 8080
```

This initializes DB + TEI, loads `.env`, attaches Google OAuth, and serves at `/mcp`.

## Verification

Check discovery endpoints

- `curl -sS https://cc-history.tootie.tv/.well-known/oauth-authorization-server | jq .`
- `curl -sS https://cc-history.tootie.tv/.well-known/openid-configuration | jq .`

You should see JSON with keys like `authorization_endpoint`, `token_endpoint`, `registration_endpoint`, `issuer`, and scopes.

Smoke test registration and authorize

- `curl -i -X POST https://cc-history.tootie.tv/register -H 'Content-Type: application/json' -d '{}'` (expect JSON/validation error, not 404)
- `curl -i 'https://cc-history.tootie.tv/authorize?client_id=x&redirect_uri=http://localhost:1234/cb&response_type=code&scope=openid&code_challenge=abc&code_challenge_method=S256&state=xyz'` (expect 3xx redirect)

## Client Usage

Examples (this repo)

- `examples/client_google_oauth.py`

```bash
# Production
python examples/client_google_oauth.py

# Local (if running on localhost:8080)
python examples/client_google_oauth.py --local

# Call different tool (optional)
python examples/client_google_oauth.py --tool whoami
```

Minimal snippet

```python
from fastmcp import Client
import asyncio

async def main():
    async with Client("https://cc-history.tootie.tv/mcp/", auth="oauth") as client:
        info = await client.call_tool("get_user_info")
        print(info)

asyncio.run(main())
```

## Headless Login Options

1) SSH Port Forwarding (recommended)

- Use a fixed callback port so you can forward it:

```python
from fastmcp import Client
from fastmcp.client.auth import OAuth

oauth = OAuth("https://cc-history.tootie.tv/mcp/", callback_port=54321)
async with Client("https://cc-history.tootie.tv/mcp/", auth=oauth) as client:
    print(await client.call_tool("get_user_info"))
```

- From your local machine: `ssh -L 54321:127.0.0.1:54321 user@HEADLESS_HOST`
- Start the headless script; copy/open the authorization URL in your local browser.
- Tokens are cached at `~/.fastmcp/oauth-mcp-client-cache` on the headless host.

2) Pre‑authenticate and copy cache

- Authenticate once on a machine with a browser, then copy these files to the headless machine:
  - `~/.fastmcp/oauth-mcp-client-cache/https_cc-history_tootie_tv_client_info.json`
  - `~/.fastmcp/oauth-mcp-client-cache/https_cc-history_tootie_tv_tokens.json`

## Troubleshooting

- `404 Not Found` on `/.well-known/...` or `/register`
  - Cause: OAuth routes not mounted or not forwarded by proxy.
  - Fixes:
    - Ensure `.env` is loaded before FastMCP construction (already handled in this repo).
    - Ensure app is constructed with `GoogleProvider()` so routes mount.
    - Add Nginx locations for discovery and OAuth endpoints (see above) and reload SWAG.

- No browser opens / client fails before authorization
  - Headless: use fixed `callback_port` + SSH port forwarding (see Headless).
  - Cached invalid client: clear cache (`~/.fastmcp/oauth-mcp-client-cache/*_client_info.json` and `*_tokens.json`).

- `invalid_client` or `redirect_uri_mismatch`
  - Ensure Google Console has exact Redirect URI: `https://cc-history.tootie.tv/auth/callback`.
  - If you customized `redirect_path`, ensure both Google and env match.

- Stale credentials after server redeploy
  - The client may have cached a registration tied to prior server state. Clear client cache and retry; the provider will issue fresh registration.

## Security Hardening

- Restrict client redirect URIs with `FASTMCP_SERVER_AUTH_GOOGLE_ALLOWED_CLIENT_REDIRECT_URIS` (e.g., `http://localhost:*,http://127.0.0.1:*`).
- Use the minimum required scopes (currently `openid` and `userinfo.email`).
- Keep TLS termination correct and forward `X-Forwarded-*` headers so the server advertises HTTPS URLs.
- Rotate Google client secret periodically; store in secrets manager for production.
- Keep FastMCP updated (2.12.x+); review CHANGELOG for auth updates.

## Useful Commands

```bash
# Run server
uv run python -m claude_history_mcp.server --host 0.0.0.0 --port 8080

# Check discovery
curl -sS https://cc-history.tootie.tv/.well-known/oauth-authorization-server | jq .
curl -sS https://cc-history.tootie.tv/.well-known/openid-configuration | jq .

# Test client (prod)
python examples/client_google_oauth.py

# Clear FastMCP OAuth client cache (local machine)
rm -f ~/.fastmcp/oauth-mcp-client-cache/*_{client_info,tokens}.json
```

## References

- Project docs: `docs/GOOGLE_OAUTH.md`, `docs/OAUTH_PROXY.md`, `docs/AUTHENTICATION.md`
- FastMCP docs: https://gofastmcp.com (Authentication, OAuth Proxy, Google Provider)
- Google OAuth: https://console.cloud.google.com/apis/credentials

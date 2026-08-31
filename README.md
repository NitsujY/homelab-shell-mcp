# homelab-shell-mcp

Security-first shell execution MCP server. Lets a remote LLM client (e.g. a
Perplexity custom connector) run a tightly whitelisted set of shell commands
on a Debian 12 LXC, over HTTPS. TLS is terminated by Tailscale Funnel — the
app itself is plain HTTP on 127.0.0.1:8080.

## Tools

| Tool | Description |
|------|-------------|
| `run_command(command)` | Run a whitelisted command. Returns `{exit_code, stdout, stderr, duration_ms, truncated}`. |
| `list_allowed_commands()` | Current whitelist + hard denylist. |
| `get_recent_commands(limit)` | Recent audit log entries (max 100). |

## Security model

- Input parsed with `shlex.split`, executed via `subprocess` argv array — never `shell=True`.
- Whitelist match on basename of `argv[0]` only; absolute paths and traversal rejected.
- Hard denylist (overrides whitelist): `rm sudo su dd mkfs shutdown reboot chmod chown`, plus curl/wget pipe constructs.
- 60s per-command timeout (`MCP_CMD_TIMEOUT`); the whole process group is SIGKILLed on timeout.
- stdout/stderr truncated at 50KB each (`truncated: true` when applied).
- Bearer auth on every request (`Authorization: Bearer ${MCP_AUTH_TOKEN}`); the server refuses to start without a token.
- Every invocation (including blocked ones) is appended as a JSON line to the audit log.
- Runs as dedicated non-root user `mcpshell` under systemd with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`.

## Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_AUTH_TOKEN` | *(required)* | Bearer token; server refuses to start without it. |
| `MCP_ALLOWED_COMMANDS` | `ls,cat,df,free,uptime,ps,docker,systemctl,journalctl,tailscale,ping` | Comma-separated whitelist. |
| `MCP_CMD_TIMEOUT` | `60` | Per-command timeout in seconds. |
| `MCP_AUDIT_LOG` | `/var/log/homelab-shell-mcp/audit.jsonl` | Audit log path. |

---

## Step-by-step: Proxmox LXC → Tailscale Funnel → Perplexity

### One-liner (on the Proxmox host, as root)

Creates the LXC (unprivileged Debian 12, 1 core / 512 MB / 4 GB) and installs
everything — Tailscale, the app, the systemd service:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/NitsujY/homelab-shell-mcp/main/install-proxmox.sh)"
```

Override defaults via env: `CTID=200 MEMORY=1024 BRIDGE=vmbr1 bash -c "$(...)"`.
When it finishes it prints the root password and `MCP_AUTH_TOKEN`, then run
`pct enter <CTID>` → `tailscale up` → `tailscale funnel 8080 on` (steps 4–5 below).

### Already have an LXC? One-liner inside the CT console (as root)

Works on both Debian and Alpine (auto-detects, no bash needed):

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/NitsujY/homelab-shell-mcp/main/install-lxc.sh)"
```

Then `tailscale up` → `tailscale funnel 8080 on` (steps 4–5 below).

### Quick install (one paste, inside the LXC console as root)

After creating the LXC (step 1 below), this single block installs Tailscale,
the app, and starts the service:

```bash
apt update && apt install -y curl git && \
curl -fsSL https://tailscale.com/install.sh | sh && \
git clone https://github.com/NitsujY/homelab-shell-mcp.git /opt/src/homelab-shell-mcp && \
cd /opt/src/homelab-shell-mcp && ./install.sh && \
systemctl enable --now homelab-shell-mcp && systemctl status homelab-shell-mcp --no-pager
```

Then:

```bash
tailscale up                      # open the printed login URL
cat /etc/homelab-shell-mcp.env    # copy MCP_AUTH_TOKEN for the connector
tailscale funnel 8080 on
tailscale funnel status           # copy the https://<host>.<tailnet>.ts.net URL
```

The MCP endpoint for the Perplexity connector is `https://<host>.<tailnet>.ts.net/mcp`
with header `Authorization: Bearer <MCP_AUTH_TOKEN>`.

### Alpine variant (lighter than Debian)

The server is pure Python and runs fine on Alpine 3.21+ LXC (~100 MB smaller
template, lower RAM). In the Proxmox web UI pick the `alpine-3.2x-default` CT
template instead of Debian, then paste this in the LXC console as root:

```bash
apk add --no-cache curl git tailscale && \
git clone https://github.com/NitsujY/homelab-shell-mcp.git /opt/src/homelab-shell-mcp && \
cd /opt/src/homelab-shell-mcp && sh ./install-alpine.sh
```

Then `tailscale up`, `tailscale funnel 8080 on` (if Funnel commands are missing,
`rc-service tailscale start` first), and copy the token from
`/etc/homelab-shell-mcp.env`.

Caveat: Alpine uses OpenRC, not systemd — the service still runs as the
non-root `mcpshell` user, but the systemd sandboxing directives
(`ProtectSystem=strict` etc.) have no OpenRC equivalent.

### 1. Create the LXC in Proxmox

In the Proxmox web UI:

1. **Create CT** → General: hostname `homelab-shell-mcp`, set a root password, **tick "Unprivileged container"** (default).
2. Template: pick **Debian 12 (bookworm)**. If not listed, first download it: local storage → *CT Templates* → *Templates* → `debian-12-standard`.
3. Disks: 4 GB is plenty. CPU: **1 vCPU**. Memory: **512 MB** (swap 512 MB).
4. Network: DHCP on your LAN bridge (usually `vmbr0`) is fine — Tailscale handles inbound, so no port forwarding is needed.
5. Confirm → Finish → Start the container, then open its **Console**.

### 2. Install Tailscale in the LXC

In the container console (as root):

```bash
apt update && apt install -y curl git
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up        # follow the login URL to join your tailnet
```

> If Tailscale fails with `/dev/net/tun` errors: on the Proxmox **host**, edit
> `/etc/pve/lxc/<CTID>.conf` and append:
> ```
> lxc.cgroup2.devices.allow: c 10:200 rwm
> lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
> ```
> then restart the container.

### 3. Install homelab-shell-mcp

Still in the container:

```bash
git clone https://github.com/NitsujY/homelab-shell-mcp.git
cd homelab-shell-mcp
./install.sh        # creates user mcpshell, generates MCP_AUTH_TOKEN
systemctl enable --now homelab-shell-mcp
systemctl status homelab-shell-mcp   # should be active (running)
```

The installer prints and saves a generated token in `/etc/homelab-shell-mcp.env`.
**Copy this token — you need it in step 5.** To view it again later:

```bash
cat /etc/homelab-shell-mcp.env
```

Quick local check (should return 401 without a token, non-401 with it):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/mcp
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/mcp \
  -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

### 4. Expose with Tailscale Funnel

```bash
tailscale funnel 8080 on
tailscale funnel status    # note the https://<host>.<tailnet>.ts.net URL
```

The MCP endpoint is `https://<host>.<tailnet>.ts.net/mcp`.

> Funnel requires it to be enabled in your tailnet ACLs (Tailscale admin
> console → DNS/HTTPS must be on, and ACL `nodeAttrs` must include `funnel`).

### 5. Connect Perplexity

1. Perplexity → **Account settings → Connectors → + Custom connector → Remote**.
2. URL: `https://<host>.<tailnet>.ts.net/mcp`
3. Add header: `Authorization: Bearer <token from step 3>`.
4. Save, then ask Perplexity e.g. *"use homelab-shell-mcp to run `uptime`"*.

---

## Container image

CI (`.github/workflows/ci.yml`) runs pytest on every push/PR, then builds and
pushes `ghcr.io/nitsujy/homelab-shell-mcp:latest` to GHCR on pushes to main.

Run locally:

```bash
docker build -t homelab-shell-mcp .
docker run --rm -p 8080:8080 -e MCP_AUTH_TOKEN=changeme homelab-shell-mcp
```

Note: inside a container most whitelisted commands (`systemctl`, `docker`,
`tailscale`) don't exist — the image is for CI/dev; production deployment is
the LXC + systemd path above.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests/
```

Auth API verified against FastMCP 3.4.7 (`StaticTokenVerifier`,
`FastMCP(auth=...)`, `http_app()`); the dependency is pinned to `>=3.4,<4`.

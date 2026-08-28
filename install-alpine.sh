#!/bin/sh
# Install homelab-shell-mcp on Alpine Linux 3.21+ (Proxmox LXC). Run as root.
# Ponytail: no uv needed — Alpine's apk ships python >= 3.12 directly.
set -eu

APP_DIR=/opt/homelab-shell-mcp
LOG_DIR=/var/log/homelab-shell-mcp
ENV_FILE=/etc/homelab-shell-mcp.env

apk add --no-cache python3 py3-pip
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "need python >= 3.12")'

# busybox adduser does not create a matching group, so do it explicitly
addgroup -S mcpshell 2>/dev/null || true
id mcpshell >/dev/null 2>&1 || \
    adduser -S -H -h "$APP_DIR" -s /sbin/nologin -G mcpshell mcpshell

install -d -o mcpshell -g mcpshell "$APP_DIR" "$APP_DIR/src" "$LOG_DIR"
install -m 644 pyproject.toml "$APP_DIR/"
install -m 644 src/homelab_shell_mcp.py "$APP_DIR/src/"

su -s /bin/sh mcpshell -c "python3 -m venv '$APP_DIR/.venv'"
su -s /bin/sh mcpshell -c "'$APP_DIR/.venv/bin/pip' install --quiet '$APP_DIR'"

if [ ! -f "$ENV_FILE" ]; then
    echo "MCP_AUTH_TOKEN=$(head -c 32 /dev/urandom | base64)" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Generated MCP_AUTH_TOKEN in $ENV_FILE -- save it for the connector config."
fi

# OpenRC service (Alpine has no systemd). Runs as non-root mcpshell; the
# systemd sandboxing directives from the Debian path have no OpenRC equivalent.
cat > /etc/init.d/homelab-shell-mcp <<'EOF'
#!/sbin/openrc-run
name="homelab-shell-mcp"
command="/opt/homelab-shell-mcp/.venv/bin/homelab-shell-mcp"
command_user="mcpshell:mcpshell"
command_background=true
pidfile="/run/${RC_SVCNAME}.pid"
output_log="/var/log/homelab-shell-mcp/service.log"
error_log="/var/log/homelab-shell-mcp/service.log"

start_pre() {
    set -a
    . /etc/homelab-shell-mcp.env
    set +a
}
EOF
chmod +x /etc/init.d/homelab-shell-mcp

rc-update add homelab-shell-mcp default
rc-service homelab-shell-mcp restart
echo "Done. Token: $ENV_FILE — endpoint listens on 127.0.0.1:8080"

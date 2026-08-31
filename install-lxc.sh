#!/bin/sh
# Run INSIDE an existing Proxmox LXC, as root:
#   sh -c "$(curl -fsSL https://raw.githubusercontent.com/NitsujY/homelab-shell-mcp/main/install-lxc.sh)"
# Detects Alpine vs Debian and runs the right installer. POSIX sh (no bash needed).
set -eu

REPO=https://github.com/NitsujY/homelab-shell-mcp.git
SRC=/opt/src/homelab-shell-mcp

if command -v apk >/dev/null; then
    apk add --no-cache curl git tailscale
    rc-update add tailscale default
    rc-service tailscale start || echo "WARN: tailscaled failed to start — check /dev/net/tun passthrough in the CT config on the Proxmox host."
elif command -v apt-get >/dev/null; then
    apt-get update -qq && apt-get install -y -qq curl git
    curl -fsSL https://tailscale.com/install.sh | sh
else
    echo "Unsupported distro: need apk (Alpine) or apt (Debian)." >&2
    exit 1
fi

cd /        # leave $SRC first in case the script was launched from inside it
rm -rf "$SRC"
git clone --depth 1 "$REPO" "$SRC"
cd "$SRC"

if command -v apk >/dev/null; then
    sh ./install-alpine.sh
else
    bash ./install.sh
    systemctl enable --now homelab-shell-mcp
fi

grep -q '^MCP_AUTH_TOKEN=' /etc/homelab-shell-mcp.env
TOKEN=$(cut -d= -f2 /etc/homelab-shell-mcp.env)

cat <<EOF

=== Done ===
MCP_AUTH_TOKEN: $TOKEN
Next:
  tailscale up            # open the login URL
  tailscale funnel 8080 on
Endpoint: https://$(hostname).<tailnet>.ts.net/mcp  (Authorization: Bearer <token>)
EOF

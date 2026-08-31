#!/usr/bin/env bash
# Run on the Proxmox HOST as root:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/NitsujY/homelab-shell-mcp/main/install-proxmox.sh)"
# Creates an unprivileged Debian 12 LXC and installs homelab-shell-mcp + Tailscale in it.
set -euo pipefail

CTID=${CTID:-$(pvesh get /cluster/nextid)}
HOSTNAME=${HOSTNAME:-homelab-shell-mcp}
STORAGE=${STORAGE:-local-lvm}
TEMPLATE_STORAGE=${TEMPLATE_STORAGE:-local}
BRIDGE=${BRIDGE:-vmbr0}

command -v pct >/dev/null || { echo "Run this on the Proxmox host."; exit 1; }

TEMPLATE=$(pveam list "$TEMPLATE_STORAGE" | awk '/debian-12-standard/{print $1}' | sort -V | tail -1)
if [ -z "$TEMPLATE" ]; then
    pveam update
    pveam download "$TEMPLATE_STORAGE" "$(pveam available --section system | awk '/debian-12-standard/{print $2}' | sort -V | tail -1)"
    TEMPLATE=$(pveam list "$TEMPLATE_STORAGE" | awk '/debian-12-standard/{print $1}' | sort -V | tail -1)
fi

ROOT_PW=$(head -c 12 /dev/urandom | base64)
pct create "$CTID" "$TEMPLATE" \
    --hostname "$HOSTNAME" --unprivileged 1 \
    --cores 1 --memory 512 --swap 512 \
    --rootfs "$STORAGE:4" --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
    --password "$ROOT_PW" --start 1

# Tailscale needs /dev/net/tun inside the unprivileged CT.
cat >> "/etc/pve/lxc/$CTID.conf" <<EOF
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF
pct reboot "$CTID"

# Single source of truth for in-CT setup: push install-lxc.sh and run it.
curl -fsSL https://raw.githubusercontent.com/NitsujY/homelab-shell-mcp/main/install-lxc.sh -o /tmp/install-lxc.sh
pct push "$CTID" /tmp/install-lxc.sh /root/install-lxc.sh
rm -f /tmp/install-lxc.sh
pct exec "$CTID" -- env TS_AUTHKEY="${TS_AUTHKEY:-}" sh /root/install-lxc.sh

IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
cat <<EOF

=== CT ready ===
CT $CTID ($HOSTNAME) at $IP   root password: $ROOT_PW
EOF

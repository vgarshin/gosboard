#!/usr/bin/env bash
# Run as root on the dedicated Ubuntu VM. Installs Docker only when absent.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
    echo 'Run with sudo' >&2
    exit 1
fi
. /etc/os-release
if [ "${ID}" != ubuntu ]; then
    echo 'This helper supports Ubuntu only' >&2
    exit 1
fi
if ! command -v docker >/dev/null; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod 0644 /etc/apt/keyrings/docker.asc
    printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
        "${UBUNTU_CODENAME:-$VERSION_CODENAME}" "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/docker.sources
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
install -d -m 0755 /opt/aitalks/app
install -d -m 0700 /etc/aitalks /var/backups/aitalks
docker --version
docker compose version

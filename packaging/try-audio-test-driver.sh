#!/usr/bin/env bash
# Keep desktop audio recoverable when an explicit module test fails.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-}"
if [[ "${mode}" != load && "${mode}" != restore ]]; then
    printf 'Usage: bash %s {load|restore}\n' "$0" >&2
    exit 1
fi
if [[ "${EUID}" -eq 0 ]]; then
    printf 'Run this wrapper as your desktop user, without sudo.\n' >&2
    exit 1
fi

# Authenticate while audio is still running, before stopping any services.
sudo -v
restart_audio() {
    systemctl --user start pipewire.socket pipewire-pulse.socket \
        pipewire.service pipewire-pulse.service wireplumber.service
}
trap restart_audio EXIT
systemctl --user stop wireplumber.service pipewire-pulse.service \
    pipewire-pulse.socket pipewire.service pipewire.socket
sudo bash "${project_dir}/packaging/load-audio-test-driver.sh" "${mode}"

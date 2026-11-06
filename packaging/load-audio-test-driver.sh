#!/usr/bin/env bash
# Load a test module for this boot only; the installed driver stays on disk.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-}"
module="${project_dir}/build/driver-audio-test/sc0710.ko"

if [[ "${mode}" != load && "${mode}" != restore ]]; then
    printf 'Usage: sudo bash %s {load|restore}\n' "$0" >&2
    exit 1
fi
if [[ "${EUID}" -ne 0 ]]; then
    printf 'Loading a kernel module needs sudo.\n' >&2
    exit 1
fi
if [[ "${mode}" == load ]]; then
    [[ -f "${module}" ]] || { printf 'Build the test module first.\n' >&2; exit 1; }
    [[ "$(modinfo -F version "${module}")" == *-audiofix2 ]] || exit 1
    [[ "$(modinfo -F vermagic "${module}")" == "$(uname -r) "* ]] || {
        printf 'Test module was built for another kernel.\n' >&2
        exit 1
    }
fi

# Do not force-unload or kill clients: open video/audio handles must be closed first.
if ! modprobe -r sc0710; then
    printf 'Close OBS, quadcap and PipeDeck, and stop PipeWire before retrying.\n' >&2
    exit 1
fi

restore_on_failure() {
    local status=$?
    if [[ "${status}" -ne 0 ]]; then
        printf 'Loading failed; restoring the installed driver.\n' >&2
        modprobe sc0710 || true
    fi
}
trap restore_on_failure EXIT

if [[ "${mode}" == load ]]; then
    # Load normal dependencies and module options before substituting the test .ko.
    # options used by the installed driver are preserved by modprobe's dry run.
    python3 - "${module}" <<'PY'
import shlex
import subprocess
import sys
from pathlib import Path

plan = subprocess.check_output(["modprobe", "--show-depends", "sc0710"], text=True)
loaded = False
for line in plan.splitlines():
    args = shlex.split(line)
    if args and args[0] == "insmod":
        if args[1].endswith("/sc0710.ko") or "/sc0710.ko." in args[1]:
            subprocess.run(["insmod", sys.argv[1], *args[2:]], check=True)
            loaded = True
        else:
            name = subprocess.check_output(["modinfo", "-F", "name", args[1]],
                                           text=True).strip()
            if not (Path("/sys/module") / name.replace("-", "_")).exists():
                subprocess.run(["modprobe", name], check=True)
if not loaded:
    raise SystemExit("Installed sc0710 module was not found in the dependency plan")
PY
else
    modprobe sc0710
fi

trap - EXIT
printf 'Loaded sc0710 version: '
cat /sys/module/sc0710/version

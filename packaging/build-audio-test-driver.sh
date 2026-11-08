#!/usr/bin/env bash
# Build a temporary module; never install it or modify DKMS registrations.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
driver_source="${1:-${project_dir}/third_party/sc0710}"
build_dir="${project_dir}/build/driver-audio-test"
patch_files=(
    "${project_dir}/packaging/patches/sc0710-per-device-irq.patch"
    "${project_dir}/packaging/patches/sc0710-audio-lifecycle.patch"
    "${project_dir}/packaging/patches/sc0710-audio-silence-clock.patch"
)

for required in lib/sc0710-audio.c lib/sc0710.h Makefile version; do
    if [[ ! -f "${driver_source}/${required}" ]]; then
        printf 'Missing driver source: %s/%s\n' "${driver_source}" "${required}" >&2
        exit 1
    fi
done

mkdir -p "${build_dir}"
stage="$(mktemp -d "${build_dir}/source.XXXXXX")"
cp -a "${driver_source}/lib" "${stage}/lib"
cp "${driver_source}/Makefile" "${driver_source}/version" "${stage}/"

# Normalize an already patched source copy, newest patch first. Only the private
# staging directory is modified; a mismatching source fails the forward checks.
for ((i=${#patch_files[@]}-1; i>=0; i--)); do
    patch_file="${patch_files[i]}"
    if patch --batch --force --fuzz=0 --dry-run --silent --reverse -d "${stage}" -p1 \
            < "${patch_file}" > /dev/null 2>&1; then
        patch --batch --force --fuzz=0 --silent --reverse -d "${stage}" -p1 < "${patch_file}"
    fi
done
for patch_file in "${patch_files[@]}"; do
    if ! patch --batch --fuzz=0 --forward --dry-run --silent -d "${stage}" -p1 \
            < "${patch_file}" > /dev/null 2>&1; then
        printf 'Driver does not match %s; refusing a partial build.\n' "${patch_file}" >&2
        exit 1
    fi
    patch --batch --fuzz=0 --forward --silent -d "${stage}" -p1 < "${patch_file}"
done

python3 "${project_dir}/tests/driver/irq_policy.py" "${stage}"
python3 "${project_dir}/tests/driver/audio_lifecycle.py" "${stage}"
python3 "${project_dir}/tests/driver/silence_timing.py" "${stage}"
printf '%s-audiofix2\n' "$(tr -d '[:space:]' < "${stage}/version")" > "${stage}/version"
# A copied generated header may otherwise be newer than the version file.
rm -f "${stage}/lib/sc0710-version.h"
make -C "${stage}"
ln -sfn "${stage}/build/sc0710.ko" "${build_dir}/sc0710.ko"
printf '\nTest module: %s/sc0710.ko\n' "${build_dir}"
modinfo -F version "${build_dir}/sc0710.ko"

# Restore Gabriel's capture and audio setup

This branch keeps the tested desktop audio profile, the optional experimental
sc0710 patches, and the OBS format-recovery helper together. No settings need
to be reconstructed from a chat transcript after reinstalling Linux.

## Fresh-system setup

Install PipeWire, WirePlumber 0.5+, ALSA utilities (`amixer` and `alsactl`),
Python 3, and the sc0710 driver using the distribution's normal package or
upstream installation process. Confirm that the MK.2 is detected. This profile
was tested on PikaOS, Linux 7.3.1, PipeWire 1.6.11 and WirePlumber 0.5.17; a new
kernel or audio-stack version still needs a playback and HDMI reconnect check.

Clone the saved branch from Gabriel's fork, then apply the profile as the
normal desktop user:

```bash
git clone --branch fix/capture-audio-recovery https://github.com/gabrielbaez/quadcap.git
cd quadcap
python3 packaging/configure-capture-audio.py --apply-alsa
```

The script installs five PipeWire/WirePlumber drop-ins plus two derived ALSA
profile files. The ALC1220 line-input path holds Capture, Line Boost and Line
controls at 0 dB, so volume requests use software gain instead of boosting the
analog preamps. `--apply-alsa` also immediately sets the detected Realtek
ALC1220 Line Boost and Capture controls to 0 dB. It uses sudo only to save ALSA
state. It refuses ambiguous or missing codecs and does not assume a fixed ALSA
card number. If this machine has no ALC1220, omit `--apply-alsa` and configure
that machine's capture gains separately. Existing changed files are backed up
under `~/.local/state/quadcap/audio-profile-backups/`. Rerunning is safe.

To preview the changes:

```bash
python3 packaging/configure-capture-audio.py --dry-run --apply-alsa
```

Log out and back in, or restart audio after finishing recordings and closing
capture applications:

```bash
systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service
```

This installs a default/minimum quantum of 1024 frames, 2048 frames of MK.2
capture headroom, the RODECaster playback scheduling workaround, and a higher
RODECaster System driver priority. The Elgato rule matches driver/card identity
rather than a PCI address. A device rule selects the neutral input path for the tested AMD Ryzen controller
(PCI vendor/product 1022:15e3). Other capture paths and output mappings remain
from the distribution profile. This hardware-specific profile is intended for
this ALC1220 setup; inspect compatibility before using it on different hardware.
The added buffering trades latency for stability;
it is a reproducible device profile, not proof of a repaired kernel defect.

For OBS video recovery, follow [the helper instructions](packaging/obs/README.md)
and add `packaging/obs/sc0710-format-recovery.lua` through Tools → Scripts.
Select the Elgato source. This step is per OBS scene collection.

## Verification

Play PS5 audio with OBS, PipeDeck, and browser playback active. Confirm clean
sound for several minutes, turn the PS5 off and back on, and check video/audio
return. Inspect recent errors and export the graph if there is a problem:

```bash
journalctl --user --since '5 minutes ago' -u pipewire -u pipewire-pulse
pw-dump > ~/pipedeck-audio.json
cat /proc/sc0710-state
```

The successful session used a temporary runtime `clock.force-quantum=1024` and
runtime Elgato headroom=2048. The persistent profile enforces a 1024 minimum so
normal clients cannot shrink it back to 256, while allowing larger requests.
A stock-driver restart test on 2026-11-06 confirmed the minimum quantum and
headroom load from the persistent files without a forced runtime quantum.
The neutral-input profile also survived two WirePlumber restarts and software
volume changes from 50% to 100%, with both hardware gains staying at 0 dB.
A fresh boot and a full stock-driver HDMI off/on test are still pending.

## Upstream driver baseline

The driver pin now includes [sc0710 PR #89](https://github.com/Nakildias/sc0710/pull/89)
(commit `5564ede7fb888b1448c9d8f140e98a1cdc7f7485`). This merged fix paces
silence by elapsed time during audio-delivery gaps, rather than emitting a fixed
480 frames after each delayed 10 ms wake. It addresses clock drift during gaps;
it does not establish a fix for jitter while HDMI audio is arriving normally.
There were no open sc0710 pull requests when this update was prepared.

GitHub's fork status compares quadcap with quadcap upstream; it does not compare
this separately pinned driver with sc0710 upstream. The unchanged driver version
string is also insufficient to identify whether PR #89 is present.

Verify the upstream timing regression with a clean checkout at the driver pin:

```bash
python3 tests/driver/upstream_silence_timing.py third_party/sc0710
```

Updating this repository does not change the running kernel module. Existing
`third_party/sc0710` source copies are not automatically refreshed by the installer,
and an already-installed DKMS version is skipped. Refresh the source to the pin
and plan a separate driver rebuild/reload after closing capture clients; do not
assume rerunning the installer upgrades an existing module with the same version.
The experimental patches below are rebased onto the new pin and remain opt-in.

## Experimental driver: separate validation

The audiofix2 module is temporary and disappears at reboot. The installed DKMS
module has not been replaced. The current clean session therefore does not
prove whether both driver patches are needed or whether the device profile
alone is sufficient. The next controlled comparison should use the installed
driver with the same quantum/headroom, followed by off/on and recording tests.
Use the normal driver long term if that passes; retain experimental changes
only with measured evidence.

The exact pinned source revision and build/load/restore procedure are in
[the driver test notes](tests/driver/README.md). Neither this setup command nor
the normal application installer installs the experimental patches. A kernel
upgrade requires rebuilding any experimental module for that kernel; the
profile does not pin a kernel or module binary.

Before an upstream timing change, measure audio-delivery gaps, video-service
time, DMA interrupt/poll latency and sample-pointer progress. The current
source services video and audio in one DMA thread, video first, so video work
is a plausible source of jitter, not an established cause. Regressions must
cover signal loss, restoration, 4K capture, OBS recording, and other supported
cards. The OBS geometry problem is distinct from these audio measurements.

## Why saving ALSA state alone was insufficient

The default `analog-input-linein` mixer path merges Capture and Line Boost into
its volume control. On the test machine, the gains returned to +30 dB after an
audio restart. Merely setting and storing them once did not prevent later
volume handling from reapplying boost. With the neutral path, both hardware
controls stay at 0 dB while software volume remains adjustable.

The installer derives the path from the distribution's installed profile,
changes only the three line-input gain policies from `merge` to `zero`, and
references it from a copy of the stock profile set. It does not edit packaged
system files or replace the Ryzen output path. The profile files live under
`~/.config/alsa-card-profile/mixer/`. Unknown source layouts fail validation
before installation. Regenerate the profile by rerunning the setup command
following a major ALSA card-profile package change, then verify input/output.

For rollback of this input quirk, remove
`~/.config/wireplumber/wireplumber.conf.d/55-alc1220-neutral-line-input.conf`
and restart WirePlumber. Existing installer backups retain any overwritten
configuration. The generated ALSA files are unused without the device rule.

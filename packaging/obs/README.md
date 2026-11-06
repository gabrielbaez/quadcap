# OBS sc0710 format recovery

On the MK.2 test machine, OBS opened `/dev/video0` at 1920x1080 while the PS5
was off (2026-11-06 17:13:34). The PS5 returned at 3840x2160 at 17:14:31.
The driver restarted DMA and queued SOURCE_CHANGE, but OBS kept its old
capture buffers and repeatedly timed out. Manually switching away and back
reopened the source at 3840x2160 at 17:18:22.

The MK.2 driver's frame distributor skips clients whose negotiated dimensions
differ from the live source. This protects the smaller buffers. The upstream
[OBS V4L2 capture loop](https://github.com/obsproject/obs-studio/blob/master/plugins/linux-v4l2/v4l2-input.c)
does not handle SOURCE_CHANGE, and its update callback restarts capture only
when relevant settings change. Reapplying identical settings is insufficient.

`sc0710-format-recovery.lua` is a local OBS workaround for this interaction.
It polls the selected device once per second with QUERYCAP and G_FMT, checks
that the driver identifies as sc0710, and compares the reported dimensions
with OBS's actual source dimensions. After two consistent observations it
updates that source's resolution setting, triggering V4L2 reinitialization.
Other source settings and scene transforms are preserved. Capture may briefly
interrupt and its framing may change when the source resolution changes.

No external executable, shell command, kernel modification or OBS rebuild is
needed. The script targets the 64-bit Linux V4L2 ABI and checks its layout
before querying a device. It should work inside OBS Flatpak with the same
video-device permission already required by the capture source.

## Load into OBS

Finish a recording before the first test. In OBS:

1. Open **Tools → Scripts**.
2. Click **+** and select `sc0710-format-recovery.lua`.
3. Select the existing Elgato V4L2 source in the script's dropdown.
4. Keep **Enable format recovery** checked.

The local test copy is also installed at
`~/.var/app/com.obsproject.Studio/config/obs-studio/scripts/sc0710-format-recovery.lua`.
OBS loads scripts per scene collection. Add it only once in a collection.
Remove or disable it in Tools → Scripts to stop automatic updates. The most
recent resolution remains in the source settings; select the preferred
resolution in the source properties if needed.

For the hardware regression, start with the PS5 off and open the capture
source. Turn on the PS5 without switching sources. If the live format differs
from the buffered format, the script should log `Reopening ... after a capture
format change` and the image should return after about two seconds plus the
device's restart time. Repeat off/on after the initial success.

## Verification and limits

Run `python3 tests/obs/format_recovery.py` from the checkout. It runs the actual
Lua script under LuaJIT with mocked OBS and device operations, including
1080p-to-4K and smaller-format transitions, debounce, unchanged geometry,
query failures, unrelated cameras, invalid dimensions, missing/wrong sources,
disabled operation, bounded restart behaviour and lifecycle callbacks. A small
C program checks the FFI sizes, offsets and request constants against the host
kernel headers. Both checks pass locally.

Hardware recovery with the helper has not yet been verified. It handles
geometry changes, not a same-size DMA stall, FPS-only changes, audio clock
errors, or reconnects where the requested resolution already matches but OBS
has failed to start. It attempts one update per stable mismatch, avoiding an
endless restart loop on failure. The driver's audiofix2 experiment is separate.

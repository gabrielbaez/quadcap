# sc0710 audio regression tests

The application and its out-of-tree GPL driver are separate projects.
The pin includes upstream PR #89; `upstream_silence_timing.py` exercises its
clock pacing independently of the optional patches. The patches in
`packaging/patches/sc0710-audio-lifecycle.patch` and
`packaging/patches/sc0710-audio-silence-clock.patch` target the driver commit pinned
by `third_party/sc0710.pin`. They are explicit test patches, not installer updates.
The normal quadcap installer and DKMS source are unchanged.

## Observed hardware behaviour

The affected setup uses an Elgato 4K60 Pro MK.2, Linux `7.3.1-pikaos`, PipeWire
`1.6.11`, WirePlumber `0.5.17`, PipeDeck `0.2.1`, and a RODECaster Pro II.
With the Elgato linked into PipeDeck's Personal Mix, PipeWire repeatedly reports
`snd_pcm_mmap_commit ... Broken pipe` and capture follower resyncs. Selecting the
RODECaster as graph driver moves the timing errors to the Elgato capture node.
Switching Elgato capture to ALSA read mode removes the mmap errors but does not
remove the follower resyncs. These observations do not establish that the
lifecycle defect is the cause of the audible crackling.

The pinned driver's sample delivery runs while video DMA is active, even after
an ALSA capture STOP. The ring writer does not check `running`, and `prepare()`
resets the pointers without serializing with the writer. The patch gates delivery
on the capture state and protects state and ring publication with a spinlock.
ALSA period notification occurs after releasing that lock, because ALSA's stream
lock is already held when trigger acquires the capture lock. The channel mutex
keeps the substream alive across notification. Each delivered batch notifies once,
as required by the ALSA driver documentation, even if it crossed multiple periods.

The test compiles the actual ring writer extracted from the selected driver
source with small ALSA stubs. It checks stopped DMA and watchdog delivery, stereo
stride and ring wrap, partial periods, silence, and batch notification. It does
not emulate kernel concurrency, DMA scheduling, or physical audio quality.

## Build and regression checks

From the quadcap checkout, with the pinned driver available in `third_party/sc0710`:

```bash
bash packaging/build-audio-test-driver.sh
```

The build stages a private source copy, applies both patches, runs six ring-writer cases and ten silence-clock
cases, and builds for the running kernel. The module version ends in `-audiofix2`.
It neither installs nor loads the module. An alternate source directory can be
passed as the first argument. No download or package-manager changes are made.

To reproduce the regression against pristine driver source:

```bash
python3 tests/driver/audio_lifecycle.py /path/to/pristine/sc0710
```

The stopped-delivery and multiple-period cases fail before the patch. Against
the staged patched source all six pass. The standard application checks remain:

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build --output-on-failure
```

## Temporary hardware test

Finish any recording first. Quit OBS, quadcap, and PipeDeck completely, including
tray/background instances. This test temporarily interrupts all desktop audio.

The desktop-user wrapper authenticates first and restarts audio on exit, even if
the module refuses to unload or load:

```bash
bash packaging/try-audio-test-driver.sh load
```

Its individual steps, if needed for diagnosis, are:

```bash
systemctl --user stop wireplumber.service pipewire-pulse.service \
    pipewire-pulse.socket pipewire.service pipewire.socket
sudo bash packaging/load-audio-test-driver.sh load
systemctl --user start pipewire.socket pipewire-pulse.socket \
    pipewire.service pipewire-pulse.service wireplumber.service
```

The loader refuses to force-unload a module with open clients. If loading fails,
it attempts to restore the installed module. Secure Boot or kernel policy may
reject an unsigned module; the test does not disable those protections. The
installed module options are carried into the test load. Confirm the active version:

```bash
cat /sys/module/sc0710/version
```

Reopen OBS and PipeDeck. Test YouTube and Elgato audio together, then HDMI signal
off/on, OBS stop/start, and PipeDeck restart. Record whether there is audible
crackling and capture the associated PipeWire and kernel logs. Do not describe the
patch as a crackling fix until those tests pass on real hardware. Other supported
cards (4K Pro and Cam Link Pro) have not been tested.

## Restore

After quitting capture clients, the desktop-user wrapper restores the installed
driver and restarts audio:

```bash
bash packaging/try-audio-test-driver.sh restore
```

Quit capture clients and stop the audio services as above, then run:

```bash
sudo bash packaging/load-audio-test-driver.sh restore
systemctl --user start pipewire.socket pipewire-pulse.socket \
    pipewire.service pipewire-pulse.service wireplumber.service
```

A normal reboot also returns to the installed module, because the test never
changes the installed module, initramfs, or DKMS registration. Existing WirePlumber
diagnostic settings are independent of this module test and are not changed by it.

## Local verification

On the development machine, the patched module builds for `7.3.1-pikaos` and the
six ring-writer tests pass; four cases fail against the pristine pinned driver.
Kernel `checkpatch.pl --no-tree --no-signoff` reports no errors or warnings.
The patch has no upstream sign-off because it has not been submitted upstream.
The full application builds. Device, pipeline and flashback CTest suites pass.
The single-instance suite fails in both the original checkout and the test
worktree: this sandbox rejects Unix socket binding with `Operation not permitted`.
That suite still needs to run outside the sandbox. Kernel concurrency and audible
results remain unverified until the temporary module is loaded on hardware.

### First hardware run

On 2026-11-06, the MK.2 successfully loaded `2026.09.02-1-audiofix1`.
Capture remained RUNNING at S16_LE stereo, 48 kHz, with a 1024-frame period
and 32768-frame buffer. A five-second sample of ALSA hardware pointers advanced
by 48128 frames in each approximately one-second observation interval; these
coarse snapshots do not establish precise hardware clock accuracy. The driver
status reported 48128 delivered frames/s. At 3840x2160p60, with PipeDeck and
OBS in use, PipeWire continued reporting mmap commit failures and follower
resyncs after the test module loaded. No BUG/Oops/lockdep diagnostic appeared
in the inspected post-load kernel log. The lifecycle patch therefore has not
resolved the reported steady-state timing errors. Audible quality and a direct
OBS-versus-PipeDeck comparison still require user confirmation.

### HDMI shutdown reproduction

The user reports that shutting down the PS5 triggers the audible crack. At
16:43:40–16:43:46 on 2026-11-06 the kernel repeatedly logged a stalled video
DMA watchdog followed by "No format detected, skipping DMA reset". The driver
status then reported "HDMI: no signal" while ALSA capture remained RUNNING.
The silence worker still waits 100 ms after the last real delivery and then
adds a fixed 480 frames per delayed-work invocation. Its cadence is not
compensated for late work scheduling. The startup starvation window and
long-term silence cadence need separate tests; the lifecycle patch does not
change either. A fixed no-signal frame and clocked silence are expected recovery
behaviours to validate, but are not established as fixed by this test module.

### Second test build: clocked silence

The second patch samples monotonic nanoseconds and accounts for 48 kHz elapsed
frames. While the input reports no HDMI lock it starts filling silence on the
next 10 ms worker tick, rather than waiting for the 100 ms live-signal grace
period. While HDMI is locked that grace period still protects normal DMA jitter.
Real delivery resets the silence clock. Cam Link Pro uses the selected input's
lock hint; MK.2 and 4K Pro use the device lock hint.

Catch-up is bounded to half the ALSA ring and at most 4800 frames (100 ms), with
excess backlog discarded while retaining clock phase. This avoids wrapping the
ring after a severely delayed wakeup. It does not prevent underruns under arbitrary
scheduler delays, guarantee prompt HDMI lock detection, or implement video
recovery. It may need further tuning if audio resumes before the lock hint updates.

`silence_timing.py` compiles the actual worker and timing helper with a deterministic
clock and capture stubs. Ten cases cover prompt no-signal silence, live-signal
jitter, delayed cadence, fractional frame timing, bounded catch-up, small rings,
stopped capture, live-signal gaps, per-input lock hints and clock rebasing. All ten
pass with the second patch; eight fail with the first test build's worker. The
six ring-writer cases continue to pass. The tests do not emulate the kernel's
scheduler, lock races, actual HDMI polling or audio hardware.

The second module builds for `7.3.1-pikaos`; kernel checkpatch reports no errors
or warnings. Shell syntax checks pass. Application code is unchanged, so the
application validation and sandbox limitation described above still apply.

For the next hardware run, load the module with the wrapper above and confirm
`2026.09.02-1-audiofix2` in `/sys/module/sc0710/version`. Start OBS and PipeDeck with
YouTube playing, then shut down the PS5. Check whether desktop audio stays clean
and repeat power-on to check audio returns. Report a frozen image separately;
this patch does not change the video watchdog. No hardware result is claimed for
this second build until that run is completed.

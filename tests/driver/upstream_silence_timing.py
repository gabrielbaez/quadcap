#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the pinned upstream silence worker with delayed wakeups."""
import argparse
import os
import resource
from pathlib import Path
import subprocess
import tempfile
from silence_timing import SHIM, extract

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
args = parser.parse_args()
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
source = (args.source / "lib/sc0710-audio.c").read_text()
shim = SHIM.replace("#define SC0710_AUDIO_MAX_SILENCE_FRAMES 4800",
    "#define SC0710_AUDIO_SILENCE_MAX_BURST 2400\n#define div_u64(a,b) ((a)/(b))")
shim = shim.replace("u64 last_sample_ns, silence_frames;",
    "u64 last_sample_ns, silence_frames, silence_start_ns; bool silence_active;")
helper = ""
if "static unsigned int sc0710_audio_silence_owed(" in source:
    helper = extract(source, "sc0710_audio_silence_owed")
cases = r"""
int main(void) {
    struct sc0710_dev dev = {0};
    struct snd_pcm_substream stream = {0};
    struct sc0710_audio_dev chip = {.dev=&dev, .substream=&stream, .running=true};
    /* 100 late wakes span 1089 ms plus the first 10 ms: 52752 frames.
     * The old worker emits just 48000, slowing the graph during gaps. */
    for (unsigned i=0; i<100; i++) {
        clock_ns = (200ULL + i*11ULL)*NSEC_PER_MSEC;
        sc0710_audio_silence_work_fn(&chip.silence_work.work);
    }
    assert(published == 52752);
    /* A long scheduler stall must not flood the ring with catch-up. */
    unsigned before = published;
    clock_ns += NSEC_PER_SEC;
    sc0710_audio_silence_work_fn(&chip.silence_work.work);
    assert(published-before == 480);
    /* Real delivery resets the gap clock without adding silence. */
    before = published;
    chip.last_sample_jiffies = jiffies;
    sc0710_audio_silence_work_fn(&chip.silence_work.work);
    assert(published == before && !chip.silence_active);
    chip.running = false;
    unsigned armed = rearms;
    sc0710_audio_silence_work_fn(&chip.silence_work.work);
    assert(published == before && rearms == armed);
    return 0;
}
"""
with tempfile.TemporaryDirectory(prefix="sc0710-upstream-timing-") as temporary:
    unit = Path(temporary)/"timing.c"
    binary = Path(temporary)/"timing"
    unit.write_text(shim+helper+extract(source, "sc0710_audio_silence_work_fn")+cases)
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
                    "-Werror", str(unit), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("PASS: delayed cadence, bounded catch-up, real delivery and stopped capture")

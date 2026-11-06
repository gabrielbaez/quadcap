#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile the actual silence worker with a deterministic clock and ALSA stubs."""

import argparse
import os
from pathlib import Path
import re
import resource
import subprocess
import tempfile
import unittest


def extract(source, name):
    start = re.search(r"static (?:void|unsigned int) " + name + r"\(", source).start()
    body = source.index("{", start)
    depth, end = 1, body + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


SHIM = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef uint64_t u64;
#define NSEC_PER_MSEC 1000000ULL
#define NSEC_PER_SEC 1000000000ULL
#define SC0710_AUDIO_RATE_HZ 48000
#define SC0710_AUDIO_GAP_MS 100
#define SC0710_AUDIO_SILENCE_MS 10
#define SC0710_AUDIO_SILENCE_SAMPLES 480
#define SC0710_AUDIO_MAX_SILENCE_FRAMES 4800
#define SC0710_BOARD_ELGATO_CAMLINK_PRO 1
#define READ_ONCE(x) (x)
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define mul_u64_u32_div(a,b,c) ((u64)((__uint128_t)(a)*(b)/(c)))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define spin_lock_irqsave(lock,flags) do { (void)(lock); (flags)=0; } while (0)
#define spin_unlock_irqrestore(lock,flags) do { (void)(lock); (void)(flags); } while (0)
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define msecs_to_jiffies(n) (n)
#define time_after(a,b) ((long)((b)-(a)) < 0)
#define jiffies ((unsigned long)(clock_ns / NSEC_PER_MSEC))
#define ktime_get_ns() clock_ns
#define KERN_INFO ""
#define audio_debug 0
#define printk_ratelimited(...) ((void)0)
#define system_wq 0
#define mod_delayed_work(q,w,n) do { (void)(q); (void)(w); (void)(n); rearms++; } while (0)
struct work_struct { int dummy; };
struct delayed_work { struct work_struct work; };
struct snd_pcm_runtime { unsigned long buffer_size; };
struct snd_pcm_substream { struct snd_pcm_runtime *runtime; };
struct sc0710_dma_channel { int lock; };
struct sc0710_dev {
    struct sc0710_dma_channel channel[2];
    int board, locked;
    struct { int locked; } clp_input[4];
};
struct sc0710_audio_dev {
    struct delayed_work silence_work;
    struct sc0710_dev *dev;
    struct snd_pcm_substream *substream;
    bool running;
    int capture_lock, input;
    unsigned long last_sample_jiffies;
    u64 last_sample_ns, silence_frames;
};
static u64 clock_ns;
static unsigned int published, rearms;
static void sc0710_audio_push_frames(struct sc0710_audio_dev *chip,
    unsigned int frames, const void *src, unsigned int stride, bool silence)
{
    (void)chip; (void)src; (void)stride;
    assert(silence);
    published += frames;
}
"""

CASES = r"""
static void tick(struct sc0710_audio_dev *chip, u64 ns)
{
    clock_ns = ns;
    sc0710_audio_silence_work_fn(&chip->silence_work.work);
}
int main(int argc, char **argv)
{
    struct snd_pcm_runtime runtime = {.buffer_size = 32768};
    struct snd_pcm_substream stream = {&runtime};
    struct sc0710_dev dev = {0};
    struct sc0710_audio_dev chip = {.dev=&dev, .substream=&stream, .running=true};
    assert(argc == 2);
    if (!strcmp(argv[1], "prompt")) {
        tick(&chip, 10 * NSEC_PER_MSEC);
        assert(published == 480 && rearms == 1);
    } else if (!strcmp(argv[1], "live-jitter")) {
        dev.locked = 1;
        tick(&chip, 30 * NSEC_PER_MSEC);
        assert(published == 0 && rearms == 1);
    } else if (!strcmp(argv[1], "late-cadence")) {
        for (unsigned int i=1; i<=20; i++) tick(&chip, i*15*NSEC_PER_MSEC);
        assert(published == 14400);
    } else if (!strcmp(argv[1], "fraction")) {
        tick(&chip, 10500000);
        assert(published == 504);
        tick(&chip, 21000000);
        assert(published == 1008);
    } else if (!strcmp(argv[1], "bounded")) {
        tick(&chip, 500 * NSEC_PER_MSEC);
        assert(published == 4800);
        tick(&chip, 510 * NSEC_PER_MSEC);
        assert(published == 5280);
    } else if (!strcmp(argv[1], "small-ring")) {
        runtime.buffer_size = 128;
        tick(&chip, 500 * NSEC_PER_MSEC);
        assert(published == 64);
    } else if (!strcmp(argv[1], "stopped")) {
        chip.running = false;
        tick(&chip, 500 * NSEC_PER_MSEC);
        assert(published == 0 && rearms == 0);
    } else if (!strcmp(argv[1], "live-gap")) {
        dev.locked = 1;
        tick(&chip, 120 * NSEC_PER_MSEC);
        assert(published == 4800);
    } else if (!strcmp(argv[1], "camlink")) {
        dev.board = SC0710_BOARD_ELGATO_CAMLINK_PRO;
        dev.locked = 1;
        chip.input = 2;
        tick(&chip, 10 * NSEC_PER_MSEC);
        assert(published == 480);
        dev.clp_input[2].locked = 1;
        tick(&chip, 20 * NSEC_PER_MSEC);
        assert(published == 480);
    } else if (!strcmp(argv[1], "rebase")) {
        tick(&chip, 10 * NSEC_PER_MSEC);
        chip.last_sample_ns = 20 * NSEC_PER_MSEC;
        chip.last_sample_jiffies = 20;
        chip.silence_frames = 0;
        tick(&chip, 30 * NSEC_PER_MSEC);
        assert(published == 960);
    } else return 2;
    return 0;
}
"""


class SilenceTiming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        source = (SOURCE / "lib/sc0710-audio.c").read_text()
        cls.temp = tempfile.TemporaryDirectory(prefix="sc0710-silence-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        unit = SHIM
        if "static unsigned int sc0710_audio_silence_due(" in source:
            unit += extract(source, "sc0710_audio_silence_due")
        unit += extract(source, "sc0710_audio_silence_work_fn") + CASES
        (directory / "audio.c").write_text(unit)
        cls.binary = directory / "audio-test"
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
                        "-Werror", str(directory / "audio.c"), "-o", str(cls.binary)],
                       check=True)

    def test_silence_clock(self):
        for case in ["prompt", "live-jitter", "late-cadence", "fraction", "bounded",
                     "small-ring", "stopped", "live-gap", "camlink", "rebase"]:
            with self.subTest(case=case):
                result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    SOURCE = parser.parse_args().source
    unittest.main(argv=[__file__], verbosity=2)

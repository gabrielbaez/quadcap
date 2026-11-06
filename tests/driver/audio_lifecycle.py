#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the driver's real ring writer without a capture card or kernel load."""

import argparse
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import unittest


def extract_function(source, name):
    start = source.index("static void " + name + "(")
    body = source.index("{", start)
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


SHIM = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef unsigned long snd_pcm_uframes_t;
struct snd_pcm_runtime {
    u8 *dma_area;
    unsigned long buffer_size, period_size;
};
struct snd_pcm_substream { struct snd_pcm_runtime *runtime; };
struct sc0710_audio_dev {
    struct snd_pcm_substream *substream;
    unsigned long buffer_ptr, period_pos;
    bool running;
    int capture_lock;
};
static unsigned int notifications;
static bool lock_held;
#define spin_lock_irqsave(lock, flags) do { \
    (void)(lock); (flags) = 0; assert(!lock_held); lock_held = true; \
} while (0)
#define spin_unlock_irqrestore(lock, flags) do { \
    (void)(lock); (void)(flags); assert(lock_held); lock_held = false; \
} while (0)
static void snd_pcm_period_elapsed(struct snd_pcm_substream *stream)
{
    (void)stream;
    /* ALSA may acquire its stream lock, which trigger already holds. */
    assert(!lock_held);
    notifications++;
}
"""

CASES = r"""
int main(int argc, char **argv)
{
    u32 buffer[8];
    u32 input[] = {11, 0, 0, 0, 22, 0, 0, 0, 33, 0, 0, 0,
                   44, 0, 0, 0, 55, 0, 0, 0};
    struct snd_pcm_runtime runtime = {(u8 *)buffer, 8, 2};
    struct snd_pcm_substream stream = {&runtime};
    struct sc0710_audio_dev chip = {.substream = &stream};
    assert(argc == 2);
    memset(buffer, 0x5a, sizeof(buffer));
    if (!strcmp(argv[1], "stopped")) {
        sc0710_audio_push_frames(&chip, 4, (const u8 *)input, 16, false);
        assert(chip.buffer_ptr == 0 && chip.period_pos == 0);
        assert(notifications == 0 && buffer[0] == 0x5a5a5a5a);
    } else if (!strcmp(argv[1], "stopped-silence")) {
        sc0710_audio_push_frames(&chip, 4, NULL, 0, true);
        assert(chip.buffer_ptr == 0 && notifications == 0);
        assert(buffer[0] == 0x5a5a5a5a);
    } else if (!strcmp(argv[1], "wrap")) {
        chip.running = true;
        chip.buffer_ptr = 6;
        sc0710_audio_push_frames(&chip, 4, (const u8 *)input, 16, false);
        assert(chip.buffer_ptr == 2 && chip.period_pos == 0);
        assert(buffer[6] == 11 && buffer[7] == 22);
        assert(buffer[0] == 33 && buffer[1] == 44);
        assert(notifications == 1);
    } else if (!strcmp(argv[1], "partial-period")) {
        chip.running = true;
        sc0710_audio_push_frames(&chip, 1, (const u8 *)input, 16, false);
        assert(chip.buffer_ptr == 1 && chip.period_pos == 1);
        assert(notifications == 0 && buffer[0] == 11);
    } else if (!strcmp(argv[1], "batch")) {
        chip.running = true;
        sc0710_audio_push_frames(&chip, 5, (const u8 *)input, 16, false);
        assert(chip.buffer_ptr == 5 && chip.period_pos == 1);
        assert(notifications == 1 && buffer[4] == 55);
    } else if (!strcmp(argv[1], "silence")) {
        chip.running = true;
        sc0710_audio_push_frames(&chip, 2, NULL, 0, true);
        assert(chip.buffer_ptr == 2 && notifications == 1);
        assert(buffer[0] == 0 && buffer[1] == 0);
    } else {
        return 2;
    }
    return 0;
}
"""


class CaptureLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        source = (SOURCE / "lib/sc0710-audio.c").read_text()
        cls.temp = tempfile.TemporaryDirectory(prefix="sc0710-audio-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        unit = directory / "audio.c"
        unit.write_text(SHIM + extract_function(source, "sc0710_audio_push_frames") + CASES)
        cls.binary = directory / "audio-test"
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
                        "-Werror", str(unit), "-o", str(cls.binary)], check=True)

    def check_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stopped_capture_does_not_publish_dma_samples(self):
        self.check_case("stopped")

    def test_stopped_capture_does_not_publish_watchdog_silence(self):
        self.check_case("stopped-silence")

    def test_ring_wrap_preserves_interleaved_stereo_frames(self):
        self.check_case("wrap")

    def test_partial_period_does_not_notify_alsa(self):
        self.check_case("partial-period")

    def test_batch_notifies_once_after_publishing_all_frames(self):
        self.check_case("batch")

    def test_running_watchdog_publishes_silence(self):
        self.check_case("silence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="sc0710 driver source directory")
    args = parser.parse_args()
    SOURCE = args.source
    unittest.main(argv=[__file__], verbosity=2)

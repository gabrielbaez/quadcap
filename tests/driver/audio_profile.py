#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check repeatable installation, backups, and hardware gain targeting."""
import importlib.util
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('profile_setup',
                                             ROOT / 'packaging/configure-capture-audio.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class AudioProfile(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        self.mixer = Path(self.fixture.name)
        (self.mixer / 'paths').mkdir()
        (self.mixer / 'profile-sets').mkdir()
        (self.mixer / 'paths/analog-input-linein.conf').write_text(
            '[General]\npriority = 81\n'
            '[Element Capture]\nswitch = mute\nvolume = merge\n'
            '[Element Line Boost]\nvolume = merge\n'
            '[Element Line]\nvolume = merge\n'
            '[Element Rear Mic]\nvolume = off\n')
        (self.mixer / 'profile-sets/default.conf').write_text(
            '[Mapping analog-stereo]\npaths-input = analog-input-linein analog-input-mic\n'
            'paths-output = analog-output-lineout analog-output-headphones\n')
        override = patch.object(profile, 'find_mixer_root', return_value=self.mixer)
        override.start()
        self.addCleanup(override.stop)

    def test_only_line_input_gain_is_fixed_at_unity(self):
        result = profile.neutral_profile_files(self.mixer)
        path = result[Path('alsa-card-profile/mixer/paths/quadcap-alc1220-linein.conf')].decode()
        self.assertEqual(path.count('volume = zero'), 3)
        self.assertIn('[Element Rear Mic]\nvolume = off', path)
        self.assertIn('switch = mute', path)
        mapping = result[Path('alsa-card-profile/mixer/profile-sets/quadcap-alc1220.conf')].decode()
        self.assertIn('paths-input = quadcap-alc1220-linein analog-input-mic', mapping)
        self.assertIn('paths-output = analog-output-lineout analog-output-headphones', mapping)

    def test_unknown_profile_layout_fails_before_any_installation(self):
        (self.mixer / 'paths/analog-input-linein.conf').write_text('[General]\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RuntimeError):
                profile.install_profile(root / 'config', root / 'state')
            self.assertEqual(list(root.iterdir()), [])

    def test_install_preserves_other_configuration_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, state = root / 'config', root / 'state'
            other = config / 'pipewire/pipewire.conf.d/other.conf'
            other.parent.mkdir(parents=True)
            other.write_text('untouched')
            self.assertEqual(len(profile.install_profile(config, state)), 7)
            self.assertEqual(profile.install_profile(config, state), [])
            self.assertEqual(other.read_text(), 'untouched')
            self.assertFalse(state.exists())

    def test_changed_configuration_gets_a_recoverable_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, state = root / 'config', root / 'state'
            target = config / 'pipewire/pipewire.conf.d/60-capture-buffering.conf'
            target.parent.mkdir(parents=True)
            target.write_text('previous settings')
            profile.install_profile(config, state)
            backups = list(state.rglob(target.name))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), 'previous settings')
            self.assertIn('default.clock.min-quantum = 1024', target.read_text())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile.install_profile(root / 'config', root / 'state', True)
            self.assertEqual(list(root.iterdir()), [])

    def test_card_number_is_discovered_from_codec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            card = root / 'card7'
            card.mkdir()
            (card / 'codec#0').write_text('Codec: Realtek ALC1220\nAddress: 0\n')
            self.assertEqual(profile.find_line_in_card(root), 7)
            (root / 'card1').mkdir()
            (root / 'card1/codec#0').write_text('Codec: Another device\n')
            self.assertEqual(profile.find_line_in_card(root), 7)
            (root / 'card1/codec#0').write_text('Codec: Realtek ALC1220\n')
            with self.assertRaises(RuntimeError):
                profile.find_line_in_card(root)

    def test_missing_codec_does_not_guess_a_card(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                profile.find_line_in_card(Path(directory))

    def test_gain_controls_are_neutral_and_stored_for_the_detected_card(self):
        with patch.object(profile.subprocess, 'run') as run:
            profile.set_line_in_gain(7)
            self.assertEqual(run.call_args_list[0].args[0],
                             ['amixer', '-c', '7', 'sset', 'Line Boost', '0dB'])
            self.assertEqual(run.call_args_list[1].args[0],
                             ['amixer', '-c', '7', 'sset', 'Capture', '0dB'])
            self.assertEqual(run.call_args_list[2].args[0],
                             ['sudo', 'alsactl', 'store', '7'])
            run.reset_mock()
            profile.set_line_in_gain(7, True)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Restore the tested MK.2 / RODECaster / ALC1220 desktop audio profile."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PROFILE = Path(__file__).resolve().parent / 'audio-profile'


def find_line_in_card(proc_root=Path('/proc/asound')):
    """Only the tested Realtek codec may receive the hardware gain changes."""
    cards = []
    for card in proc_root.glob('card[0-9]*'):
        if any('Codec: Realtek ALC1220\n' in codec.read_text()
               for codec in card.glob('codec#*')):
            cards.append(int(card.name[4:]))
    if len(cards) != 1:
        raise RuntimeError('Expected exactly one Realtek ALC1220 card; found '
                           f'{len(cards)}. No hardware controls were changed.')
    return cards[0]


def install_profile(config_home, state_home, dry_run=False):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup_root = state_home / 'quadcap/audio-profile-backups' / stamp
    changed = []
    for service, dropin in [('pipewire', 'pipewire.conf.d'),
                            ('wireplumber', 'wireplumber.conf.d')]:
        for source in sorted((PROFILE / service).glob('*.conf')):
            target = config_home / service / dropin / source.name
            content = source.read_bytes()
            if target.exists() and target.read_bytes() == content:
                print(f'Unchanged: {target}')
                continue
            print(f'{"Would install" if dry_run else "Installing"}: {target}')
            changed.append(target)
            if dry_run:
                continue
            if target.exists():
                backup = backup_root / target.relative_to(config_home)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                print(f'Backup: {backup}')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                temporary.write(content)
                staged = Path(temporary.name)
            try:
                staged.chmod(0o644)
                staged.replace(target)
            finally:
                staged.unlink(missing_ok=True)
    return changed


def set_line_in_gain(card, dry_run=False):
    commands = [
        ['amixer', '-c', str(card), 'sset', 'Line Boost', '0dB'],
        ['amixer', '-c', str(card), 'sset', 'Capture', '0dB'],
        ['sudo', 'alsactl', 'store', str(card)],
    ]
    for command in commands:
        print(' '.join(command))
        if not dry_run:
            subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='show changes without writing')
    parser.add_argument('--apply-alsa', action='store_true',
                        help='set the detected ALC1220 line/capture gain to 0 dB and save it')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as your desktop user, without sudo. Only saving ALSA state uses sudo.')
    card = None
    if args.apply_alsa:
        try:
            card = find_line_in_card()
        except RuntimeError as error:
            parser.error(str(error))
        for program in ['amixer', 'alsactl', 'sudo']:
            if shutil.which(program) is None:
                parser.error(f'Missing {program}; install ALSA utilities before applying gains.')
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    state_home = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    install_profile(config_home, state_home, args.dry_run)
    if card is not None:
        set_line_in_gain(card, args.dry_run)
    print('Dry run complete.' if args.dry_run else
          'Profile ready. Configuration applies at the next audio restart or login.')
    print('No services, kernel modules, packages, or OBS settings were changed by this command.')


if __name__ == '__main__':
    main()

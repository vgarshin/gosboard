"""Copy the latest verified backup off the VM; destination must be outside the synced project."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ssh-config', required=True)
    parser.add_argument('--known-hosts')
    parser.add_argument('--host', default='aitalks-test-01')
    parser.add_argument('--kind', choices=['daily', 'predeploy'], default='predeploy')
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.is_relative_to(Path(__file__).resolve().parents[1]):
        raise SystemExit('Backup destination must be outside the synced project')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    ssh = ['ssh', '-F', args.ssh_config, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    if args.known_hosts:
        ssh += ['-o', 'UserKnownHostsFile=' + args.known_hosts]
    ssh += [args.host]
    status = subprocess.run(ssh + ['sudo -n cat /var/backups/aitalks/status-' + args.kind + '.json'], check=True, capture_output=True)
    status = json.loads(status.stdout)
    name = status.get('archive', '')
    if not status.get('local_verified') or not re.fullmatch(r'aitalks-\d{8}T\d{12}Z\.tar\.gz', name):
        raise SystemExit('No verified backup is available')
    remote = '/var/backups/aitalks/' + args.kind + '/' + name
    expected = subprocess.run(ssh + ['sudo -n sha256sum ' + shlex.quote(remote)], check=True, capture_output=True).stdout.decode().split()[0]
    target = destination / name
    if target.exists():
        raise SystemExit('Refusing to overwrite an existing backup')
    partial = target.with_suffix('.partial')
    created = False
    try:
        descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(descriptor, 'wb') as output:
            subprocess.run(ssh + ['sudo -n cat ' + shlex.quote(remote)], check=True, stdout=output, stderr=subprocess.PIPE)
        with partial.open('rb') as downloaded:
            actual = hashlib.file_digest(downloaded, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Off-VM backup checksum mismatch')
        partial.replace(target)
    finally:
        if created:
            partial.unlink(missing_ok=True)
    print(json.dumps({'backup': str(target), 'sha256_verified': True, 'off_vm_copy': True}))


if __name__ == '__main__':
    main()

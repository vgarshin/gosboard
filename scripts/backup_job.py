"""Daily VM snapshot, isolated restore rehearsal, and optional verified private-S3 upload.
Run from the host with an external systemd EnvironmentFile; never logs credentials.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def sha256(path):
    checksum = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def run(command, **kwargs):
    try:
        return subprocess.run(command, check=True, capture_output=True, **kwargs)
    except subprocess.CalledProcessError:
        phase = 'isolated restore' if command[:2] == ['docker', 'run'] else 'snapshot/export'
        raise ValueError('Backup command failed during ' + phase) from None


def s3_upload(client, archive, bucket, prefix, retain=7):
    """Upload and download-verify before rotating only this job's exact remote prefix."""
    key = prefix.rstrip('/') + '/' + archive.name
    checksum = sha256(archive)
    client.upload_file(str(archive), bucket, key, ExtraArgs={'Metadata': {'sha256': checksum}, 'ContentType': 'application/gzip'})
    head = client.head_object(Bucket=bucket, Key=key)
    metadata = {key.lower(): value for key, value in head.get('Metadata', {}).items()}
    if metadata.get('sha256') != checksum or head['ContentLength'] != archive.stat().st_size:
        raise ValueError('Remote backup metadata verification failed')
    with tempfile.TemporaryDirectory(prefix='aitalks-download-') as directory:
        downloaded = Path(directory) / 'roundtrip.tar.gz'
        client.download_file(bucket, key, str(downloaded))
        if sha256(downloaded) != checksum:
            raise ValueError('Remote backup download verification failed')
    # Delete only older files with our generated naming scheme, after verified success.
    objects = []
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix.rstrip('/') + '/'):
        for item in page.get('Contents', []):
            relative = item['Key'][len(prefix.rstrip('/')) + 1:]
            if '/' not in relative and relative.startswith('aitalks-') and relative.endswith('.tar.gz'):
                objects.append(item)
    objects.sort(key=lambda item: item['LastModified'], reverse=True)
    for item in objects[retain:]:
        if item['Key'] != key:
            client.delete_object(Bucket=bucket, Key=item['Key'])
    return {'uploaded': True, 'download_sha256_verified': True}


def restore_image(compose, root):
    """Resolve Docker index churn without testing against different application code."""
    cid = run(compose + ['ps', '-q', 'backend'], cwd=root).stdout.decode().strip()
    if not cid or '\n' in cid:
        raise ValueError('Cannot identify the running backend container')
    details = run(['docker', 'inspect', '--format', '{{.Image}}\n{{.Config.Image}}', cid]).stdout.decode().splitlines()
    image, reference = details
    available = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True)
    if available.returncode == 0:
        return image
    # Rebuilding an unchanged image can replace only its attestation/index digest.
    # Accept the replacement only when both source code and dependency lock match.
    candidate = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', reference]).stdout.decode().strip()
    fingerprint = (
        "import hashlib,pathlib; root=pathlib.Path('/app'); h=hashlib.sha256(); "
        "files=[root/'requirements.lock',*sorted((root/'app').rglob('*.py'))]; "
        "[(h.update(str(p.relative_to(root)).encode()),h.update(p.read_bytes())) for p in files]; "
        "print(h.hexdigest())"
    )
    live = run(compose + ['exec', '-T', 'backend', 'python', '-c', fingerprint], cwd=root).stdout.strip()
    built = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop=ALL',
                 '--security-opt=no-new-privileges', '--entrypoint', 'python', candidate, '-c', fingerprint]).stdout.strip()
    if not live or live != built:
        raise ValueError('Restore image does not match running backend code/dependencies')
    return candidate


def create_backup(root, output, kind):
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    name = 'aitalks-' + timestamp
    compose = ['docker', 'compose', '--env-file', '/etc/aitalks/production.env']
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as temporary:
        stage = Path(temporary)
        source_name = f'/data/backup-staging/{name}.sqlite3'
        command = compose + ['exec', '-T', 'backend', 'python', '-', '/data/gosboard.sqlite3', source_name]
        run(command, cwd=root, input=(root / 'scripts/backup.py').read_bytes())
        try:
            run(compose + ['cp', 'backend:' + source_name, str(stage / 'database.sqlite3')], cwd=root)
            (stage / 'database.sqlite3').chmod(0o600)
        finally:
            run(compose + ['exec', '-T', 'backend', 'python', '-c',
                            'import os,sys; os.unlink(sys.argv[1])', source_name], cwd=root)
        (stage / 'config').mkdir(mode=0o700)
        shutil.copy2(root / 'config/app.json', stage / 'config/app.json')
        image = restore_image(compose, root)
        # No production volume, credentials, network, or sockets inside the restore container.
        result = run(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0', '--read-only',
                      '--cap-drop=ALL', '--security-opt=no-new-privileges', '--memory=512m', '-e', 'PYTHONPATH=/app',
                      '--tmpfs', '/tmp:rw,size=128m', '-v', f'{stage}:/backup:ro',
                      '-v', f'{root / "scripts"}:/checks:ro', '--entrypoint', 'python', image,
                      '/checks/verify_restore.py', '/backup/database.sqlite3', '/backup/config/app.json'])
        verified = json.loads(result.stdout)
        manifest = {'created_utc': timestamp, 'kind': kind, 'restore_check': verified,
                    'sha256': {path: sha256(stage / path) for path in ('database.sqlite3', 'config/app.json')}}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        partial = output / (name + '.partial')
        archive = output / (name + '.tar.gz')
        try:
            with tarfile.open(partial, 'w:gz') as tar:
                for path in ('database.sqlite3', 'config/app.json', 'manifest.json'):
                    tar.add(stage / path, arcname=path)
            partial.chmod(0o600)
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    return archive, verified


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-only', action='store_true', help='Explicitly create a local backup without claiming off-VM protection')
    parser.add_argument('--kind', choices=['daily', 'predeploy'], default='daily')
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(os.getenv('APP_DIRECTORY', '/opt/aitalks/app')).resolve()
    directory = Path(os.getenv('BACKUP_DIRECTORY', '/var/backups/aitalks')).resolve()
    output = directory / args.kind
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    lockfile = (directory / 'backup.lock').open('a')
    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    status = {'time_utc': datetime.now(timezone.utc).isoformat(), 'off_vm': False}
    try:
        archive, verified = create_backup(root, output, args.kind)
        status.update(archive=archive.name, local_verified=True, restore_check=verified)
        # Bound local disk usage even if remote delivery fails; the newest verified snapshot remains.
        for old in sorted(output.glob('aitalks-*.tar.gz'), reverse=True)[7:]:
            old.unlink()
        local_only = args.local_only or os.getenv('BACKUP_LOCAL_ONLY', 'false').lower() == 'true'
        if not local_only:
            bucket = os.getenv('BACKUP_S3_BUCKET', '')
            if not bucket:
                raise ValueError('BACKUP_S3_BUCKET is not configured; local snapshot retained')
            endpoint = os.getenv('BACKUP_S3_ENDPOINT', '')
            if endpoint and not endpoint.startswith('https://'):
                raise ValueError('S3 endpoint must use HTTPS')
            import boto3
            from botocore.config import Config
            client = boto3.client('s3', endpoint_url=endpoint or None,
                                  region_name=os.getenv('AWS_DEFAULT_REGION', 'ru-central1'),
                                  config=Config(connect_timeout=10, read_timeout=60,
                                                retries={'max_attempts': 3, 'mode': 'standard'}))
            prefix = os.getenv('BACKUP_S3_PREFIX', 'aitalks/backups').strip('/') + '/' + args.kind
            if not prefix or prefix.startswith('/'):
                raise ValueError('An application-specific S3 prefix is required')
            status['remote'] = s3_upload(client, archive, bucket, prefix)
            status['off_vm'] = True
        status['status'] = 'success' if status['off_vm'] else 'local-only'
    except Exception as error:
        # Raw subprocess/SDK errors can contain endpoints/identifiers: report safe type only.
        status.update(status='failed', error_type=type(error).__name__)
        if isinstance(error, ValueError):
            status['reason'] = str(error)
        raise
    finally:
        target = directory / ('status-' + args.kind + '.json')
        partial = target.with_suffix('.partial')
        partial.write_text(json.dumps(status, indent=2) + '\n')
        partial.replace(target)
        print(json.dumps(status), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit(1)

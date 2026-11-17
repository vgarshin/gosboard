from datetime import datetime, timedelta, timezone
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

spec = spec_from_file_location('backup_job', Path(__file__).resolve().parents[1] / 'scripts/backup_job.py')
backup_job = module_from_spec(spec)
spec.loader.exec_module(backup_job)


class FakeS3:
    def __init__(self, corrupt=False, metadata_case='lower'):
        self.data = b''
        self.metadata = {}
        self.deleted = []
        self.corrupt = corrupt
        self.metadata_case = metadata_case

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.data = Path(path).read_bytes()
        self.metadata = ExtraArgs['Metadata']
        self.key = key

    def head_object(self, **kwargs):
        return {'ContentLength': len(self.data), 'Metadata': {getattr(key, self.metadata_case)(): value for key, value in self.metadata.items()}}

    def download_file(self, bucket, key, path):
        Path(path).write_bytes(b'corrupt' if self.corrupt else self.data)

    def get_paginator(self, name):
        return self

    def paginate(self, **kwargs):
        prefix = kwargs['Prefix']
        now = datetime.now(timezone.utc)
        return [{'Contents': [{'Key': self.key, 'LastModified': now},
                             *[{'Key': prefix + f'aitalks-old{i}.tar.gz', 'LastModified': now - timedelta(days=i+1)} for i in range(8)],
                             {'Key': prefix+'unrelated.txt', 'LastModified': now}]}]

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs['Key'])


@pytest.mark.parametrize('metadata_case', ['lower', 'title', 'upper'])
def test_remote_roundtrip_verified_before_rotation(tmp_path, metadata_case):
    archive = tmp_path / 'aitalks-current.tar.gz'
    archive.write_bytes(b'archive content')
    client = FakeS3(metadata_case=metadata_case)
    assert backup_job.s3_upload(client, archive, 'bucket', 'aitalks/backups/daily')['download_sha256_verified']
    assert client.metadata['sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert len(client.deleted) == 2
    assert all('old' in key for key in client.deleted)


def test_remote_corruption_prevents_rotation(tmp_path):
    archive = tmp_path / 'aitalks-current.tar.gz'
    archive.write_bytes(b'archive content')
    client = FakeS3(corrupt=True)
    with pytest.raises(ValueError, match='download verification'):
        backup_job.s3_upload(client, archive, 'bucket', 'aitalks/backups/daily')
    assert client.deleted == []


def test_missing_s3_config_fails_honestly_and_bounds_local_retention(tmp_path, monkeypatch):
    import json
    import sys
    output = tmp_path / 'daily'
    output.mkdir()
    for i in range(9):
        (output / f'aitalks-2020010{i}T000000000000Z.tar.gz').write_bytes(b'old')
    unrelated = output / 'keep-me.txt'
    unrelated.write_text('unrelated')
    def created(root, target, kind):
        archive = target / 'aitalks-20990101T000000000000Z.tar.gz'
        archive.write_bytes(b'verified archive')
        return archive, {'integrity': 'ok'}
    monkeypatch.setattr(backup_job, 'create_backup', created)
    monkeypatch.setenv('BACKUP_DIRECTORY', str(tmp_path))
    monkeypatch.setenv('BACKUP_S3_BUCKET', '')
    monkeypatch.setenv('BACKUP_LOCAL_ONLY', 'false')
    monkeypatch.setattr(sys, 'argv', ['backup_job.py'])
    with pytest.raises(ValueError, match='not configured'):
        backup_job.main()
    state = json.loads((tmp_path / 'status-daily.json').read_text())
    assert state['status'] == 'failed' and state['off_vm'] is False
    assert state['local_verified'] is True
    assert len(list(output.glob('aitalks-*.tar.gz'))) == 7
    assert unrelated.read_text() == 'unrelated'


def test_concurrent_backup_pull_preserves_existing_partial(tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace
    spec = spec_from_file_location('pull_backup', Path(__file__).resolve().parents[1] / 'scripts/pull_backup.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    name = 'aitalks-20261113T111741252228Z.tar.gz'
    partial = (tmp_path / name).with_suffix('.partial')
    partial.write_bytes(b'other active transfer')
    monkeypatch.setattr(sys, 'argv', ['pull_backup.py', '--ssh-config', '/unused', '--destination', str(tmp_path)])
    def response(command, **kwargs):
        if 'sha256sum' in command[-1]:
            return SimpleNamespace(stdout=b'abc  filename')
        return SimpleNamespace(stdout=json.dumps({'archive': name, 'local_verified': True}).encode())
    monkeypatch.setattr(module.subprocess, 'run', response)
    with pytest.raises(FileExistsError):
        module.main()
    assert partial.read_bytes() == b'other active transfer'


@pytest.mark.parametrize('same_code', [True, False])
def test_restore_image_handles_retagged_index_but_rejects_changed_code(tmp_path, monkeypatch, same_code):
    from types import SimpleNamespace
    monkeypatch.setattr(backup_job.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    def output(command, **kwargs):
        if 'ps' in command:
            return SimpleNamespace(stdout=b'container-id\n')
        if command[:2] == ['docker', 'inspect']:
            return SimpleNamespace(stdout=b'sha256:old-index\naitalks-backend\n')
        if command[:3] == ['docker', 'image', 'inspect']:
            return SimpleNamespace(stdout=b'sha256:new-index\n')
        if 'exec' in command:
            return SimpleNamespace(stdout=b'source-and-lock-hash\n')
        return SimpleNamespace(stdout=b'source-and-lock-hash\n' if same_code else b'different-source\n')
    monkeypatch.setattr(backup_job, 'run', output)
    if same_code:
        assert backup_job.restore_image(['docker', 'compose'], tmp_path) == 'sha256:new-index'
    else:
        with pytest.raises(ValueError, match='does not match'):
            backup_job.restore_image(['docker', 'compose'], tmp_path)

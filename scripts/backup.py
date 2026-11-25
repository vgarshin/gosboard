"""Consistent compact snapshot without sessions/caches. Usage: backup.py SOURCE TARGET."""
import os
from pathlib import Path
import sqlite3
import sys


def snapshot(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or target.exists():
        raise ValueError('Refusing to overwrite a database or existing backup')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            for table in ('cache', 'sessions', 'oauth', 'vtb_edu_oauth', 'requests'):
                dst.execute(f'DELETE FROM {table}')
            dst.commit()
            dst.execute('VACUUM')
            dst.execute('PRAGMA journal_mode=DELETE')
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot integrity check failed')
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


if __name__ == '__main__':
    snapshot(*sys.argv[1:])
    print('Backup complete')

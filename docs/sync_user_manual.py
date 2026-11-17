"""Copy the canonical Russian manual into the frontend bundle input; stdlib only."""
import argparse
from pathlib import Path

root = Path(__file__).resolve().parent.parent
source = root / 'docs/guides/2026-11-15-ai-talks-user-manual-ru.html'
target = root / 'frontend/src/user-manual-ru.html'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check', action='store_true', help='Fail when the bundled copy differs')
args = parser.parse_args()
content = source.read_bytes()
if args.check:
    if not target.exists() or target.read_bytes() != content:
        raise SystemExit('Manual copy is stale: run python3 docs/sync_user_manual.py')
    print('Manual source and bundled copy match.')
else:
    target.write_bytes(content)
    print('Updated frontend manual from docs/guides/.')

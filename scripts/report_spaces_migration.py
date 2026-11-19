"""Read aggregate counts for the Spaces migration from an offline SQLite snapshot."""
import argparse
import json
import sqlite3
from pathlib import Path


def report(snapshot):
    location = snapshot.resolve().as_uri() + '?mode=ro&immutable=1'
    with sqlite3.connect(location, uri=True) as db:
        tables = {name for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        accounts = db.execute("""
            SELECT role, CASE enabled WHEN 1 THEN 'active' WHEN -1 THEN 'pending' ELSE 'disabled' END, count(*)
            FROM access_accounts GROUP BY role,enabled ORDER BY role,enabled
        """).fetchall() if 'access_accounts' in tables else []
        sources = db.execute("""
            SELECT count(*),sum(CASE WHEN index_revision>0 AND rebuilding=0 THEN 1 ELSE 0 END)
            FROM rag_bases WHERE deleted=0
        """).fetchone() if 'rag_bases' in tables else (0, 0)
        settings = db.execute("""
            SELECT coalesce(json_extract(settings,'$.communication_mode'),
                      CASE WHEN json_extract(settings,'$.auto_speech')=1 THEN 'voice' ELSE 'text' END),
                   coalesce(json_extract(settings,'$.rag_enabled'),0),
                   coalesce(json_extract(settings,'$.web_search_enabled'),0),
                   count(*)
            FROM chat_settings GROUP BY 1,2,3 ORDER BY 1,2,3
        """).fetchall() if 'chat_settings' in tables else []
        return {
            'accounts_by_role_and_status': [
                {'role': role, 'status': status, 'count': count} for role,status,count in accounts
            ],
            'chats': db.execute('SELECT count(*) FROM chats').fetchone()[0],
            'chat_settings_by_mode_and_sources': [
                {'mode': mode, 'rag': bool(rag), 'web': bool(web), 'count': count}
                for mode,rag,web,count in settings
            ],
            'knowledge_bases': {'total': sources[0], 'indexed': sources[1] or 0},
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    print(json.dumps(report(parser.parse_args().snapshot), ensure_ascii=False, indent=2))

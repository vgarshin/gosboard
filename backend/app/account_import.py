"""Validate CSV before a separate, atomic administrative import."""
import csv
import io
import time

from fastapi import HTTPException

from .access import AccountInput


MAX_CSV_BYTES = 1024 * 1024
MAX_ROWS = 1000


def preview_accounts(db, content, default_spaces):
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig'), newline=''), strict=True)
        headers = reader.fieldnames or []
        if 'email' not in headers or len(set(headers)) != len(headers) or set(headers) - {'email', 'name', 'role', 'space_ids'}:
            raise HTTPException(422, 'Используйте поля email, name, role, space_ids')
        active_spaces = {row['id'] for row in db.execute('SELECT id FROM spaces WHERE archived IS NULL')}
        if len(default_spaces) != len(set(default_spaces)) or set(default_spaces) - active_spaces:
            raise HTTPException(422, 'Проверьте общие пространства')
        accounts = {row['email']: row['enabled'] for row in db.execute('SELECT email,enabled FROM access_accounts')}
        seen, rows = set(), []
        for idx, raw in enumerate(reader, 1):
            if idx > MAX_ROWS:
                raise HTTPException(413, 'Максимум 1000 строк')
            row = {'line': reader.line_num}
            try:
                if None in raw or raw['email'] is None:
                    raise ValueError('Проверьте число полей')
                fields = AccountInput(email=raw['email'], role=(raw.get('role') or 'user').strip())
                if fields.email in seen:
                    raise ValueError('Повтор email')
                seen.add(fields.email)
                space_ids = [sid.strip() for sid in (raw.get('space_ids') or '').split(';') if sid.strip()] or default_spaces
                if len(space_ids) != len(set(space_ids)) or set(space_ids) - active_spaces:
                    raise ValueError('Проверьте пространства')
                name = (raw.get('name') or '').strip()
                if len(name) > 200 or any(ord(ch) < 32 for ch in name):
                    raise ValueError('Имя должно занимать до 200 символов')
                row.update(email=fields.email, name=name, role=fields.role, space_ids=space_ids,
                           existing=accounts.get(fields.email))
            except ValueError as error:
                row['error'] = str(error) if type(error) is ValueError else 'Проверьте email и роль'
            rows.append(row)
        return rows
    except (UnicodeError, csv.Error):
        raise HTTPException(422, 'Загрузите корректный CSV в UTF-8') from None


def apply_accounts(db, batch_id, rows, actor, access, approve, approve_existing):
    active_spaces = {row['id'] for row in db.execute('SELECT id FROM spaces WHERE archived IS NULL')}
    result = {'created': 0, 'admitted': 0, 'skipped': 0}
    for row in rows:
        if 'error' in row:
            result['skipped'] += 1
            continue
        email = row['email']
        current = db.execute('SELECT enabled FROM access_accounts WHERE email=?', (email,)).fetchone()
        status = current['enabled'] if current else None
        if status != row['existing'] or (current and not (status == -1 and approve and approve_existing)):
            result['skipped'] += 1
            continue
        if set(row['space_ids']) - active_spaces:
            raise HTTPException(409, 'Пространства изменились. Проверьте список заново')
        enabled = 1 if approve else -1
        now = time.time()
        db.execute("""
            INSERT INTO access_accounts(email,role,enabled,created,updated,import_name) VALUES (?,?,?,?,?,?)
            ON CONFLICT(email) DO UPDATE SET role=excluded.role,enabled=excluded.enabled,updated=excluded.updated,import_name=coalesce(excluded.import_name,import_name)
        """, (email, row['role'], enabled, now, now, row['name'] or None))
        db.executemany('''
            INSERT INTO space_memberships(email,space_id) VALUES (?,?)
            ON CONFLICT(email,space_id) DO UPDATE SET active=1,revision=revision+1 WHERE active=0
        ''', ((email, sid) for sid in row['space_ids']))
        result['created'] += int(not current)
        result['admitted'] += int(approve)
        access.audit(actor, 'account.import', email, {'batch': batch_id, 'active': approve, 'role': row['role']}, db)
    return result

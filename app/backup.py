"""Private backups, consistent SQLite snapshots and offline, validated restore."""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.database import run_lock


def allowed(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        return False
    if name == 'config/watchlist.json':
        return True
    # Include current state and reconciliation history, not generated pages,
    # temporary tests, heartbeat files, logs, credentials or earlier backups.
    return (len(path.parts) >= 2 and path.parts[0] == 'data'
            and all(not p.startswith(('test-', 'backup', '.', 'runtime')) for p in path.parts[1:])
            and '-backup-' not in name and path.suffix in {'.json', '.jsonl', '.sqlite3'})


def files(root):
    root = Path(root).resolve()
    result = []
    for folder in ('data', 'config'):
        for path in (root / folder).rglob('*'):
            if path.is_file() and not path.is_symlink() and allowed(path.relative_to(root).as_posix()):
                if path.resolve().is_relative_to(root):
                    result.append(path)
    return sorted(result)


def create_backup(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('ไฟล์ชื่อนี้มีอยู่แล้ว กรุณาเลือกชื่อใหม่')
    with tempfile.TemporaryDirectory(prefix='stock-backup-') as temporary:
        stage = Path(temporary)
        entries = []
        for source in files(root):
            name = source.relative_to(root).as_posix()
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == '.sqlite3':
                with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as src:
                    with closing(sqlite3.connect(target)) as dst:
                        src.backup(dst)
            else:
                shutil.copyfile(source, target)
            blob = target.read_bytes()
            entries.append(dict(path=name, size=len(blob), sha256=hashlib.sha256(blob).hexdigest()))
        manifest = dict(version=1, at=datetime.now(UTC).isoformat(), files=entries,
                        secrets_included=False, note='Private portfolio data. Keep this archive private.')
        archive = stage / 'archive.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as out:
            out.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
            for item in entries:
                out.write(stage / item['path'], item['path'])
        # No partially written archive remains on failure; do not overwrite one.
        with destination.open('xb') as handle, archive.open('rb') as source:
            shutil.copyfileobj(source, handle)
    return len(entries)


def validate_archive(archive):
    names = archive.namelist()
    if len(names) != len(set(names)) or len(names) > 3000:
        raise ValueError('รายการไฟล์สำรองซ้ำหรือมากเกินไป')
    if sum(i.file_size for i in archive.infolist()) > 1_000_000_000:
        raise ValueError('ไฟล์สำรองมีขนาดใหญ่เกินไป')
    if archive.getinfo('manifest.json').file_size > 1_000_000:
        raise ValueError('ข้อมูลกำกับไฟล์มีขนาดใหญ่เกินไป')
    manifest = json.loads(archive.read('manifest.json'))
    if manifest.get('version') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('ไม่รองรับรุ่นของไฟล์สำรองนี้')
    entries = manifest['files']
    listed = [item['path'] for item in entries]
    if len(listed) != len(set(listed)) or set(names) != {'manifest.json', *listed}:
        raise ValueError('รายการไฟล์ไม่ตรงกับข้อมูลกำกับ')
    if not entries:
        raise ValueError('ไฟล์สำรองไม่มีข้อมูลให้กู้คืน')
    for item in entries:
        if not allowed(item['path']):
            raise ValueError('พบเส้นทางไฟล์ที่ไม่ปลอดภัย')
        info = archive.getinfo(item['path'])
        if info.file_size != item['size'] or info.file_size > 300_000_000:
            raise ValueError('ขนาดไฟล์ไม่ถูกต้อง')
        if hashlib.sha256(archive.read(info)).hexdigest() != item['sha256']:
            raise ValueError('ไฟล์สำรองเสียหายหรือถูกแก้ไข')
    return entries


def preserve_side_effects(staged, current):
    """Restoring holdings must not reset API/AI quotas or re-send delivered work."""
    if staged.suffix != '.sqlite3' or not current.exists():
        return
    with closing(sqlite3.connect(current.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        if current.name == 'market-api.sqlite3':
            with closing(sqlite3.connect(staged)) as dst:
                src.backup(dst)
            return
        with closing(sqlite3.connect(staged)) as dst, dst:
            target_tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            source_tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('notifications', 'events', 'ai_calls', 'inbox', 'outbox', 'briefs', 'research_events', 'research_batches'):
                if table not in target_tables or table not in source_tables:
                    continue
                source_columns = {r[1] for r in src.execute(f'PRAGMA table_info({table})')}
                columns = [r[1] for r in dst.execute(f'PRAGMA table_info({table})') if r[1] in source_columns]
                names = ','.join('"' + c.replace('"', '""') + '"' for c in columns)
                operation = 'IGNORE' if table == 'ai_calls' else 'REPLACE'
                dst.executemany(f'INSERT OR {operation} INTO {table} ({names}) VALUES ({",".join("?" for _ in columns)})',
                                src.execute(f'SELECT {names} FROM {table}'))


def cancel_replayed_work(path):
    """Old pending work must not be pushed to LINE again after restoring."""
    if path.suffix != '.sqlite3':
        if path.suffix == '.json':
            json.loads(path.read_text(encoding='utf-8-sig'))
        return
    with closing(sqlite3.connect(path)) as db, db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('ฐานข้อมูลสำรองเสียหาย')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'notifications' in tables:
            db.execute("UPDATE notifications SET status='cancelled' WHERE status IN ('pending','working')")
        for table in ('inbox', 'outbox', 'research_events'):
            if table in tables:
                db.execute(f"UPDATE {table} SET state='cancelled' WHERE state IN ('pending','working')")


def restore_backup(root, archive_path):
    """Workers and editors must be closed. A safety backup precedes replacement."""
    root = Path(root).resolve()
    from app.system_status import local_paths, workers
    if any(w['state'] in {'running', 'stale'} for w in workers(local_paths(root).parent).values()):
        raise ValueError('หยุดระบบเบื้องหลังและปิดหน้าต่างแก้พอร์ตก่อนกู้คืน')
    with ExitStack() as locks:
        known_locks = set((root / 'data').rglob('*.lock'))
        known_locks.update(root / 'data' / name for name in ('line-manager.lock', 'live.market-daemon.lock',
            'mock.market-daemon.lock', 'live.lock', 'mock.lock', 'portfolios.lock', 'portfolio-profile.edit.lock'))
        for path in sorted(known_locks):
            locks.enter_context(run_lock(path))
        with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory(prefix='stock-restore-') as temporary:
            entries = validate_archive(archive)
            stage = Path(temporary)
            for item in entries:
                target = root / item['path']
                if not target.resolve().is_relative_to(root) or target.is_symlink():
                    raise ValueError('เส้นทางปลายทางออกนอกโปรเจกต์')
                staged = stage / item['path']
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(archive.read(item['path']))
                preserve_side_effects(staged, target)
                cancel_replayed_work(staged)
            safety = root / 'data/backups' / ('before-restore-' + datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f') + '.zip')
            create_backup(root, safety)
            replaced = []
            try:
                with zipfile.ZipFile(safety) as old:
                    for item in entries:
                        target = root / item['path']
                        target.parent.mkdir(parents=True, exist_ok=True)
                        # Roll back from the consistent snapshot, not a raw DB
                        # file that may still depend on uncheckpointed WAL pages.
                        previous = old.read(item['path']) if item['path'] in old.namelist() else None
                        for suffix in ('-wal', '-shm') if target.suffix == '.sqlite3' else ():
                            sidecar = Path(str(target) + suffix)
                            if sidecar.exists():
                                sidecar.unlink()
                        replaced.append((target, previous))
                        local = target.with_suffix(target.suffix + '.restore.tmp')
                        shutil.copyfile(stage / item['path'], local)
                        os.replace(local, target)
            except Exception:
                for target, previous in reversed(replaced):
                    if previous is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.write_bytes(previous)
                raise
    return safety

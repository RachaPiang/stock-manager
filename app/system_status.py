"""Private, local worker heartbeats. Reading status never calls a remote API."""
import ctypes
import json
import os
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

SERVICES = {'line': 'บอต LINE', 'market': 'ติดตามราคาหุ้น'}


def process_identity(pid):
    """Creation time guards against Windows reusing the PID of a dead worker."""
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return str(pid)
        except ProcessLookupError:
            return None
        except PermissionError:
            return 'unknown'
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return 'unknown' if ctypes.get_last_error() == 5 else None
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return 'unknown'
        if exit_code.value != 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return 'unknown'
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


class Heartbeat:
    def __init__(self, directory, service):
        self.path = Path(directory) / 'runtime' / (service + '.json')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.value = dict(pid=os.getpid(), identity=process_identity(os.getpid()),
                          started=time.time(), state='running', jobs={})

    def write(self):
        with self.lock:
            self.value['updated'] = time.time()
            temporary = self.path.with_suffix('.tmp')
            try:
                temporary.write_text(json.dumps(self.value, ensure_ascii=False), encoding='utf-8')
                temporary.replace(self.path)
            except OSError:
                # A transient Windows file-sharing conflict must not stop market
                # data checks. The next heartbeat retries; GUI marks old data stale.
                import logging
                logging.getLogger(__name__).warning('Cannot update local heartbeat; retry next pulse')

    def mark(self, job, state, detail=''):
        # Only fixed labels / counts belong here, never messages or credentials.
        with self.lock:
            self.value['jobs'][job] = dict(state=state, at=time.time(), detail=detail[:160])
        self.write()

    def loop(self):
        while not self.stop.wait(10):
            self.write()


@contextmanager
def heartbeat(directory, service):
    pulse = Heartbeat(directory, service)
    pulse.write()
    thread = threading.Thread(target=pulse.loop, daemon=True, name=service + '-heartbeat')
    thread.start()
    try:
        yield pulse
    finally:
        pulse.stop.set()
        thread.join(timeout=2)
        pulse.value['state'] = 'stopped'
        pulse.write()


def local_paths(root):
    from dotenv import dotenv_values
    env = dotenv_values(Path(root) / '.env', interpolate=False)
    mock = str(env.get('MOCK_MODE', 'true')).lower() == 'true'
    path = Path(env.get('MOCK_DATABASE_PATH' if mock else 'LIVE_DATABASE_PATH') or
                ('data/mock.sqlite3' if mock else 'data/live.sqlite3'))
    return path if path.is_absolute() else Path(root) / path


def workers(directory, now=None):
    now = time.time() if now is None else now
    result = {}
    for service, label in SERVICES.items():
        value = {}
        try:
            value = json.loads((Path(directory) / 'runtime' / (service + '.json')).read_text(encoding='utf-8'))
            identity = process_identity(int(value['pid']))
            if identity == 'unknown':
                state = 'stale'
            elif identity is None or identity != value['identity'] or value['state'] == 'stopped':
                state = 'stopped'
            elif now - float(value['updated']) > 45:
                state = 'stale'
            else:
                state = 'running'
        except (OSError, ValueError, TypeError, KeyError):
            state = 'unknown'
        result[service] = dict(label=label, state=state, jobs=value.get('jobs', {}))
    return result


def read_summary(database_path):
    path = Path(database_path)
    result = dict(workers=workers(path.parent), last_run=None, pending=0, failed=0,
                  last_quote=None, manager_pending=0, manager_failed=0)
    for file, manager in [(path, False), (path.parent / 'line-manager.sqlite3', True)]:
        if not file.exists():
            continue
        try:
            with closing(sqlite3.connect(file.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                if manager:
                    counts = dict(db.execute('SELECT state,count(*) FROM outbox GROUP BY state'))
                    result['manager_pending'] = counts.get('pending', 0)
                    result['manager_failed'] = counts.get('failed', 0)
                    research = db.execute("SELECT value FROM state WHERE key='research-status'").fetchone()
                    if research:
                        result['research'] = json.loads(research[0])
                else:
                    counts = dict(db.execute('SELECT status,count(*) FROM notifications GROUP BY status'))
                    result['pending'] = counts.get('pending', 0)
                    result['failed'] = counts.get('failed', 0)
                    row = db.execute('SELECT finished_at,status,checked,errors FROM runs ORDER BY id DESC LIMIT 1').fetchone()
                    result['last_run'] = row
                    result['last_quote'] = db.execute('SELECT max(as_of) FROM quotes').fetchone()[0]
        except (sqlite3.Error, ValueError, TypeError):
            result['read_warning'] = True
    return result


def thai_time(value):
    if not value:
        return 'ยังไม่มีข้อมูล'
    from zoneinfo import ZoneInfo
    try:
        at = datetime.fromtimestamp(value, UTC) if isinstance(value, (float, int)) else datetime.fromisoformat(value)
        return at.astimezone(ZoneInfo('Asia/Bangkok')).strftime('%d/%m %H:%M')
    except (ValueError, TypeError):
        return 'ไม่ทราบเวลา'

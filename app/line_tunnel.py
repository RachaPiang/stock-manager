"""Keep the authorized Windows test tunnel connected when the bot starts."""
import logging
import os
import re
import subprocess
import time

import requests

from app.config import ROOT
from app.database import run_lock, AlreadyRunning

log = logging.getLogger(__name__)
API = 'https://api.line.me/v2/bot/channel/webhook/endpoint'
URL = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com')


def alive(endpoint):
    if not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com/webhook', endpoint):
        return False
    try:
        r = requests.get(endpoint.removesuffix('/webhook')+'/health', timeout=10)
        return r.status_code == 200 and r.json().get('version') == 'close-briefs-v2'
    except (requests.RequestException, ValueError, AttributeError):
        return False


def maintain(settings, stop, pulse=None):
    def mark(state, detail):
        if pulse:
            pulse.mark('ช่องทาง HTTPS ของ LINE', state, detail)
    executable = ROOT/'data/tools/cloudflared.exe'
    if os.name != 'nt' or not executable.is_file():
        mark('waiting', 'ใช้ endpoint ที่ตั้งไว้ · ยังไม่ตรวจการรับส่งผ่านอินเทอร์เน็ต')
        return  # Ubuntu uses its separately configured permanent HTTPS endpoint.
    child = None
    try:
        with run_lock(ROOT/'data/line-tunnel.lock'):
            headers = {'Authorization': 'Bearer '+settings.line_token}
            endpoint, logfile = '', None
            registered = False
            while not stop.is_set():
                try:
                    if not endpoint:
                        r = requests.get(API, headers=headers, timeout=15)
                        r.raise_for_status()
                        existing = r.json().get('endpoint', '')
                        # Preserve user-managed permanent endpoints.
                        if existing and not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com/webhook', existing):
                            mark('waiting', 'ใช้ endpoint ถาวร · ยังไม่ตรวจการรับส่งผ่านอินเทอร์เน็ต')
                            log.info('Permanent LINE endpoint retained; test tunnel not started')
                            return
                        if existing and alive(existing):
                            endpoint, registered = existing, True
                    if registered and alive(endpoint):
                        mark('ok', 'tunnel ตอบสนองผ่านอินเทอร์เน็ต')
                        stop.wait(60)
                        continue
                    if child is None or child.poll() is not None:
                        mark('busy', 'กำลังเปิด tunnel เบื้องหลัง')
                        logfile = ROOT/'logs'/f'tunnel-auto-{time.time_ns()}.log'
                        with logfile.open('w', encoding='utf-8') as output:
                            child = subprocess.Popen([str(executable), 'tunnel', '--no-autoupdate', '--url',
                                'http://127.0.0.1:8787'], cwd=ROOT, stdout=output, stderr=output,
                                creationflags=subprocess.CREATE_NO_WINDOW)
                        endpoint, registered = '', False
                        if stop.wait(15):
                            break
                    if not endpoint:
                        match = URL.search(logfile.read_text(encoding='utf-8', errors='replace'))
                        if match:
                            endpoint = match.group()+'/webhook'
                    if endpoint and not registered and alive(endpoint):
                        r = requests.put(API, headers=headers, json={'endpoint': endpoint}, timeout=15)
                        r.raise_for_status()
                        registered = True
                        mark('ok', 'เชื่อม tunnel และตั้ง webhook แล้ว')
                        log.info('LINE temporary tunnel connected and webhook updated')
                except (requests.RequestException, ValueError, OSError):
                    mark('error', 'tunnel ไม่พร้อม จะลองใหม่ในหนึ่งนาที')
                    log.warning('LINE tunnel connection unavailable; retrying in one minute')
                stop.wait(60)
    except AlreadyRunning:
        log.info('Existing test tunnel manager retained')
    finally:
        if child and child.poll() is None:
            child.terminate()

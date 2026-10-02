"""A small Windows home for the existing portfolio tools. No network on launch."""
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from dotenv import dotenv_values

from app.config import ROOT
from app.desktop_theme import apply_theme
from app.setup import save_environment

PYTHON = ROOT / '.venv/Scripts/python.exe'
PYTHONW = ROOT / '.venv/Scripts/pythonw.exe'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def run_process(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', creationflags=HIDDEN,
                          **kwargs)


def validate_changes(changes):
    """Validate in a fresh process; never load keys into the GUI environment."""
    env = os.environ.copy()
    env.update({k: v or '' for k, v in dotenv_values(ROOT / '.env', interpolate=False).items()})
    env.update(changes)
    # Error text from numeric parsing can contain a value, so only expose its type.
    code = ('from app.config import Settings\n'
            'try:\n Settings.from_env()\n'
            'except Exception:\n raise SystemExit(2)\n')
    result = run_process([str(PYTHON), '-c', code], env=env, timeout=15)
    if result.returncode:
        raise ValueError('ค่าบางรายการไม่ถูกต้อง ตรวจตัวเลข โหมด และช่วง RSI ก่อนบันทึก')


LABELS = {
    'MOCK_MODE': 'ใช้ข้อมูลจำลอง', 'ANALYST_MODE': 'ผู้ช่วย AI ที่ใช้',
    'NOTIFIER_MODE': 'ส่งแจ้งเตือนผ่าน', 'MANAGER_DELIVERY_HOUR': 'เวลาสรุปประจำวัน (ชั่วโมงไทย 0–23)',
    'MANAGER_PUSH_LIMIT_PER_DAY': 'จำนวนแจ้งเตือนผู้จัดการสูงสุดต่อวัน',
    'MANAGER_CATCHUP_DAYS': 'ตามเก็บย้อนหลัง (วัน 1–7)',
    'AI_MAX_CALLS_PER_DAY': 'จำนวนเรียก AI สูงสุดต่อวัน',
    'AI_MAX_CALLS_PER_STOCK_PER_DAY': 'จำนวนเรียก AI ต่อหุ้นต่อวัน',
    'PRICE_DROP_PCT': 'เตือนราคาลง (%)', 'PRICE_RISE_PCT': 'เตือนราคาขึ้น (%)',
    'RSI_LOW': 'เตือน RSI ต่ำกว่า', 'RSI_HIGH': 'เตือน RSI สูงกว่า',
    'COOLDOWN_HOURS': 'เว้นเหตุการณ์ซ้ำ (ชั่วโมง)',
    'STOCK_API_KEY': 'กุญแจ API ราคาหุ้น', 'LINE_CHANNEL_ACCESS_TOKEN': 'LINE access token',
    'LINE_CHANNEL_SECRET': 'LINE channel secret', 'LINE_USER_ID': 'LINE user ID',
    'OPENAI_API_KEY': 'OpenAI API key', 'GEMINI_API_KEY': 'Gemini API key',
    'SEC_CONTACT_EMAIL': 'อีเมลติดต่อสำหรับข้อมูล SEC',
    'CODEX_MODEL': 'โมเดล Codex', 'OPENAI_MODEL': 'โมเดล OpenAI', 'GEMINI_MODEL': 'โมเดล Gemini',
    'GEMINI_SHARE_PORTFOLIO_CONTEXT': 'ยินยอมส่งข้อมูลพอร์ตให้ Gemini',
}
CHOICES = {'MOCK_MODE': ('true', 'false'), 'ANALYST_MODE': ('codex', 'openai', 'gemini', 'template'),
           'NOTIFIER_MODE': ('line', 'console'), 'GEMINI_SHARE_PORTFOLIO_CONTEXT': ('false', 'true')}


def is_secret(key):
    return any(part in key for part in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')) or key == 'LINE_USER_ID'


class Desktop:
    def __init__(self, root):
        self.root = root
        apply_theme(root)
        root.title('Stock Manager · ศูนย์ควบคุม')
        root.geometry('1000x800')
        root.minsize(900, 700)
        self.events = queue.Queue()
        self.busy = False
        self.children = {}
        self.status_events = queue.Queue()
        self.monitor_window = None
        self.live_status = {name: tk.StringVar(value='○ กำลังตรวจสถานะ…') for name in ('line', 'market')}
        self.live_detail = tk.StringVar(value='สถานะนี้อ่านจากเครื่อง ไม่ใช้เครดิต API หรือ AI')
        outer = ttk.Frame(root, padding=18)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Stock Manager', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='พอร์ตของคุณ ข่าว และผู้ช่วย LINE — จัดการจากที่เดียว').pack(anchor='w', pady=(0, 6))
        live = ttk.Frame(outer, padding=(12, 4))
        live.pack(fill='x', pady=(0, 4))
        self.live_labels = []
        for name in ('line', 'market'):
            label = ttk.Label(live, textvariable=self.live_status[name])
            label.pack(side='left', padx=(0, 24))
            self.live_labels.append((name, label))
        ttk.Button(live, text='มอนิเตอร์เล็ก', command=self.small_monitor).pack(side='right')
        ttk.Label(outer, textvariable=self.live_detail, wraplength=940).pack(anchor='w', pady=(0, 4))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill='both', expand=True)
        home = ttk.Frame(tabs, padding=14)
        settings = ttk.Frame(tabs, padding=16)
        tools = ttk.Frame(tabs, padding=20)
        tabs.add(home, text='หน้าหลัก')
        tabs.add(settings, text='ตั้งค่า')
        tabs.add(tools, text='เครื่องมือและตรวจสอบ')
        self.status = tk.StringVar(value='พร้อมใช้งาน · เปิดหน้าต่างนี้ไม่มีการเรียก AI หรือดึงราคาหุ้น')
        ttk.Label(outer, textvariable=self.status, wraplength=900).pack(anchor='w', pady=(12, 0))
        ttk.Label(home, text='พอร์ตและการลงทุน', style='Section.TLabel').pack(anchor='w')
        self.buttons(home, [
            ('เปิดกราฟพอร์ต', lambda: self.job('เปิดกราฟจากข้อมูลที่บันทึก', ['report'], open_report=True)),
            ('จัดการพอร์ต / หุ้น / DCA', lambda: self.window('app.portfolio_manager')),
            ('อัปเดตจำนวนหุ้นและต้นทุน', lambda: self.window('app.portfolio_editor')),
            ('คำอธิบายพอร์ตและหุ้นสำหรับ AI', lambda: self.window('app.notes_editor')),
            ('สุขภาพบริษัทและเหตุผลที่ถือ', lambda: self.window('app.company_health')),
            ('ตั้งค่าแจ้งเตือนรายหุ้น', lambda: self.window('app.alert_editor')),
        ])
        ttk.Label(home, text='กราฟเปิดในเบราว์เซอร์เดิม ส่วนการแก้พอร์ตเปิดเป็นหน้าต่างแบบเดิม', wraplength=820).pack(anchor='w', pady=(0, 8))
        ttk.Label(home, text='ระบบเบื้องหลัง', style='Section.TLabel').pack(anchor='w')
        self.buttons(home, [
            ('เปิดระบบ + เริ่มพร้อม Windows', lambda: self.script('start_stock_manager.ps1')),
            ('หยุดระบบ + ปิดเริ่มอัตโนมัติ', self.stop),
        ])
        ttk.Label(home, text='ปิดหน้าต่างแอปได้ บอตและตัวติดตามที่เปิดไว้จะทำงานต่อ\nปุ่มหยุดระบบจะหยุดทั้ง LINE และการติดตามตลาด พร้อมปิดการเริ่มอัตโนมัติ', wraplength=820).pack(anchor='w', pady=(0, 8))
        self.buttons(home, [('รีเฟรชราคาและกราฟ', lambda: self.confirm_job('อัปเดตราคาผ่าน API และอาจส่งแจ้งเตือนตามกฎที่ตั้งไว้', 'view')),
                            ('ตรวจสถานะระบบ', self.inspect)])
        ttk.Label(tools, text='เรียกเมื่อจำเป็น', style='Section.TLabel').pack(anchor='w')
        actions = [
            ('สำรองข้อมูลทั้งหมด', self.backup),
            ('กู้คืนข้อมูลจากไฟล์สำรอง', self.restore),
            ('ตรวจการตั้งค่า (ไม่ต่ออินเทอร์เน็ต)', lambda: self.job('ตรวจการตั้งค่า', ['doctor'])),
            ('ทดสอบการเชื่อมต่อ API', lambda: self.confirm_job('ติดต่อ API เพื่อตรวจการเชื่อมต่อ อาจใช้โควตา', 'doctor', '--online')),
            ('ทดสอบ AI หนึ่งครั้ง', lambda: self.confirm_job('เรียก Codex จริงหนึ่งครั้ง ใช้โควตา AI', 'test-codex')),
            ('อัปเดตข่าวบริษัท', lambda: self.confirm_job('ดึงข่าวบริษัทจากผู้ให้บริการ อาจใช้เครดิตข้อมูล', 'news-sync')),
            ('สร้างรีวิวพอร์ต', lambda: self.confirm_job('สร้างรีวิวพอร์ตตามระบบเดิม อาจเรียก AI', 'review')),
            ('เติมกราฟย้อนหลังรายวัน', lambda: self.confirm_job('ดึงราคาย้อนหลังเพิ่มเติม ใช้เครดิต API', 'refresh-history')),
            ('เติมกราฟย้อนหลังในวัน', lambda: self.confirm_job('ดึงแท่งราคาในวันเพิ่มเติม ใช้เครดิต API', 'refresh-intraday')),
            ('ดูประวัติแจ้งเตือนราคา', lambda: self.job('ประวัติแจ้งเตือน', ['show-alerts'])),
            ('เปิดโฟลเดอร์บันทึกระบบ', lambda: self.open_path(ROOT / 'logs')),
            ('เปิดคู่มือ', lambda: self.open_path(ROOT / 'README.md')),
        ]
        self.buttons(tools, actions, columns=2)
        self.make_settings(settings)
        root.after(150, self.poll)
        self.refresh_live()
        root.protocol('WM_DELETE_WINDOW', self.close)

    def buttons(self, parent, actions, columns=2):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(4, 8))
        for n, (label, command) in enumerate(actions):
            row.columnconfigure(n % columns, weight=1)
            ttk.Button(row, text=label, command=command).grid(row=n // columns, column=n % columns, sticky='ew', padx=(0, 10), pady=5)

    def open_path(self, path):
        try:
            os.startfile(path)
        except OSError:
            messagebox.showerror('เปิดไม่ได้', 'ยังไม่มีไฟล์ หรือ Windows ไม่มีแอปสำหรับเปิดไฟล์นี้', parent=self.root)

    def window(self, module):
        previous = self.children.get(module)
        if previous and previous.poll() is None:
            self.status.set('หน้าต่างนี้เปิดอยู่แล้ว ดูที่แถบงาน Windows ได้ครับ')
            return
        try:
            self.children[module] = subprocess.Popen([str(PYTHONW), '-m', module], cwd=ROOT, creationflags=HIDDEN)
        except OSError:
            messagebox.showerror('เปิดไม่ได้', 'ตรวจสภาพแวดล้อม Python ของโปรเจกต์', parent=self.root)

    def submit(self, label, work, done=None):
        if self.busy:
            messagebox.showinfo('กำลังทำงาน', 'รอรายการปัจจุบันเสร็จก่อนครับ', parent=self.root)
            return
        self.busy = True
        self.status.set('กำลังทำงาน: ' + label)
        def task():
            try:
                result = work()
                self.events.put((label, result, None, done))
            except Exception as exc:
                from app.database import AlreadyRunning
                error = ('มีงานกำลังใช้ข้อมูลอยู่ กรุณาหยุดระบบและปิดหน้าต่างแก้พอร์ตก่อน'
                         if done and isinstance(exc, AlreadyRunning) else
                         str(exc) if done and isinstance(exc, ValueError) else type(exc).__name__)
                self.events.put((label, None, error, done))
        threading.Thread(target=task, daemon=True).start()

    def poll(self):
        try:
            while True:
                label, result, error, done = self.events.get_nowait()
                self.busy = False
                self.status.set(label + (' · ไม่สำเร็จ' if error or getattr(result, 'returncode', 0) else ' · เสร็จแล้ว'))
                if error:
                    messagebox.showerror('ทำรายการไม่สำเร็จ', f'{label}\n{error} — ตรวจบันทึกระบบเพิ่มเติม', parent=self.root)
                elif done:
                    done(result)
                elif result:
                    self.output(label, (result.stdout or '') + (result.stderr or ''))
        except queue.Empty:
            pass
        try:
            while True:
                self.show_live(self.status_events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def refresh_live(self):
        def read():
            from app.system_status import local_paths, read_summary
            try:
                self.status_events.put(read_summary(local_paths(ROOT)))
            except Exception:
                self.status_events.put(None)
        threading.Thread(target=read, daemon=True).start()
        self.root.after(5000, self.refresh_live)

    def show_live(self, summary):
        from app.system_status import thai_time
        if summary is None:
            self.live_detail.set('ตรวจสถานะไม่ได้ชั่วคราว จะลองใหม่อัตโนมัติ')
            return
        states = {'running': ('● ทำงานอยู่', '#16744a'), 'stopped': ('○ หยุดอยู่', '#607080'),
                  'stale': ('● ต้องตรวจสอบ', '#9b6415'), 'unknown': ('○ ยังไม่มีสถานะ', '#607080')}
        for name, label in self.live_labels:
            worker = summary['workers'][name]
            text, color = states[worker['state']]
            self.live_status[name].set(worker['label'] + ' · ' + text)
            label.configure(foreground=color)
        pending = summary['pending'] + summary['manager_pending']
        failed = summary['failed'] + summary['manager_failed']
        latest = summary['last_run']
        warning = ' · รอบล่าสุดมีข้อผิดพลาด' if latest and latest[3] else ''
        self.live_detail.set(f"ราคาล่าสุด {thai_time(summary['last_quote'])} · รอส่ง {pending} · ส่งไม่สำเร็จสะสม {failed}{warning}")
        lines = ['อัปเดตสถานะทุก 5 วินาที · เวลาไทย', '']
        compact = []
        labels = {'ok': 'ตรวจรอบล่าสุดแล้ว', 'busy': 'กำลังทำงาน', 'error': 'งานล่าสุดไม่สำเร็จ', 'waiting': 'กำลังรอ'}
        for worker in summary['workers'].values():
            lines.append(worker['label'] + ' · ' + states[worker['state']][0])
            compact.append(worker['label'] + ' · ' + states[worker['state']][0])
            for name, job in worker['jobs'].items():
                lines.append(f"  {name}: {labels.get(job['state'], job['state'])} ({thai_time(job['at'])})")
                if job.get('detail'):
                    lines.append('  ' + job['detail'])
                if name in {'ช่องทาง HTTPS ของ LINE', 'ข่าวและงบ SEC', 'ราคาและกราฟ', 'รีวิวตามเวลา'}:
                    compact.append(f"  {name}: {labels.get(job['state'], job['state'])}")
            lines.append('')
            compact.append('')
        research = summary.get('research', {})
        if research:
            lines.append('ผลแหล่งข่าวและงบล่าสุด · ' + thai_time(research.get('at')))
            for source, values in research.get('sources', {}).items():
                bad = sum(v not in {'ok', 'available', 'cached', 'no_recent_news'} for v in values.values())
                lines.append(f'  {source}: รายการที่ยังไม่พร้อม {bad}/{len(values)}')
        lines.extend([self.live_detail.get(), '', 'งานข่าวและ AI ทำเมื่อถึงรอบหรือมีเหตุการณ์ ไม่ได้ทำตลอดเวลา',
                      'สถานะในเครื่องไม่ได้ยืนยันว่า LINE ส่งถึงแล้ว', 'ทดสอบรับส่งจริงได้ด้วยการส่ง “เมนู” หาบอต'])
        self.monitor_text = '\n'.join(lines)
        compact.append('ราคาล่าสุด ' + thai_time(summary['last_quote']))
        compact.append(f'รอส่ง {pending} · ส่งไม่สำเร็จสะสม {failed}')
        self.monitor_compact = '\n'.join(compact)
        if self.monitor_window and self.monitor_window.winfo_exists():
            self.monitor_body.set(self.monitor_compact)

    def small_monitor(self):
        if self.monitor_window and self.monitor_window.winfo_exists():
            self.monitor_window.lift()
            return
        win = tk.Toplevel(self.root)
        self.monitor_window = win
        win.title('Stock Manager · มอนิเตอร์')
        win.geometry('450x390')
        win.attributes('-topmost', True)
        self.monitor_body = tk.StringVar(value=getattr(self, 'monitor_compact', 'กำลังตรวจสถานะ…'))
        ttk.Label(win, textvariable=self.monitor_body, wraplength=415, padding=16, justify='left').pack(fill='both', expand=True)
        buttons = ttk.Frame(win)
        buttons.pack(pady=8)
        ttk.Button(buttons, text='ดูงานทั้งหมด', command=lambda: self.output('รายละเอียดระบบ', getattr(self, 'monitor_text', 'กำลังตรวจสถานะ'))).pack(side='left', padx=8)
        ttk.Button(buttons, text='ปิดหน้ามอนิเตอร์', command=win.destroy).pack(side='left')

    def backup(self):
        from datetime import datetime
        destination = filedialog.asksaveasfilename(parent=self.root, title='เก็บไฟล์สำรองไว้ในที่ส่วนตัว',
            initialfile='stock-manager-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip',
            defaultextension='.zip', filetypes=[('ไฟล์สำรอง', '*.zip')])
        if not destination:
            return
        from app.backup import create_backup
        self.submit('สำรองข้อมูล', lambda: create_backup(ROOT, destination),
            lambda count: messagebox.showinfo('สำรองแล้ว', f'เก็บข้อมูล {count} ไฟล์แล้ว\n{destination}\n\nมีข้อมูลพอร์ตส่วนตัว ห้ามเผยแพร่\nไม่รวม API keys ใน .env เก็บไฟล์ .env แยกในที่ปลอดภัย', parent=self.root))

    def restore(self):
        archive = filedialog.askopenfilename(parent=self.root, title='เลือกไฟล์สำรองของ Stock Manager', filetypes=[('ไฟล์สำรอง', '*.zip')])
        if not archive:
            return
        if not messagebox.askokcancel('กู้คืนข้อมูล', 'หยุดระบบและปิดหน้าต่างแก้พอร์ตก่อน\nข้อมูลที่ตรงกับไฟล์สำรองจะถูกแทนที่ ยอดหุ้นจะย้อนกลับตามไฟล์\nมีสำรองก่อนกู้คืนให้ และยกเลิกคิวเก่าเพื่อไม่ส่ง LINE ซ้ำ\nAPI keys และการเริ่มพร้อม Windows ไม่เปลี่ยน\n\nยืนยันกู้คืน?', parent=self.root):
            return
        from app.backup import restore_backup
        self.submit('กู้คืนข้อมูล', lambda: restore_backup(ROOT, archive),
            lambda safety: messagebox.showinfo('กู้คืนแล้ว', f'ไฟล์สำรองก่อนกู้คืน:\n{safety}\n\nเปิดระบบใหม่เมื่อตรวจยอดพอร์ตเรียบร้อยแล้ว', parent=self.root))

    def output(self, title, text):
        # Suppress configured credentials even if a dependency includes them in errors.
        for key, value in dotenv_values(ROOT / '.env', interpolate=False).items():
            if is_secret(key) and value:
                text = text.replace(value, '[ซ่อนข้อมูล]')
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry('850x480')
        from tkinter.scrolledtext import ScrolledText
        box = ScrolledText(win, wrap='word', font='TkTextFont', padx=16, pady=16)
        box.pack(fill='both', expand=True)
        box.insert('1.0', text or 'ทำรายการเรียบร้อยแล้ว')
        box.configure(state='disabled')
        ttk.Button(win, text='ปิด', command=win.destroy).pack(pady=8)

    def job(self, label, args, open_report=False):
        def work():
            result = run_process([str(PYTHON), '-m', 'app.main', *args], timeout=1800)
            if open_report and result.returncode == 0:
                mode = dotenv_values(ROOT / '.env', interpolate=False).get('MOCK_MODE', 'true')
                os.startfile(ROOT / 'data' / ('mock-portfolio.html' if mode.lower() == 'true' else 'live-portfolio.html'))
            return result
        self.submit(label, work)

    def confirm_job(self, text, *args):
        if messagebox.askokcancel('ทำรายการ', text, parent=self.root):
            self.job(text, list(args))

    def script(self, name):
        self.submit('จัดการระบบเบื้องหลัง', lambda: run_process(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'scripts' / name)], timeout=90))

    def stop(self):
        if messagebox.askokcancel('หยุดระบบทั้งหมด', 'หยุดบอตและตัวติดตาม พร้อมปิดการเริ่มอัตโนมัติเมื่อเข้า Windows?', parent=self.root):
            self.script('stop_stock_manager.ps1')

    def inspect(self):
        def work():
            result = run_process(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                  '-File', str(ROOT / 'scripts/desktop_status.ps1')], timeout=30)
            replacements = {
                'line_webhook running:': 'บอต LINE กำลังทำงาน:',
                'market_daemon running:': 'ตัวติดตามราคากำลังทำงาน:',
                'Start after Windows sign-in:': 'เริ่มอัตโนมัติเมื่อเข้า Windows:',
                'Local LINE webhook responding:': 'ตัวรับข้อความ LINE บนเครื่องตอบสนอง:',
                'True': 'ใช่', 'False': 'ไม่',
                'Process status does not confirm Internet delivery. Use the API check or send a LINE command to verify.':
                    'สถานะนี้ตรวจการทำงานในเครื่อง หากต้องการตรวจการรับส่งจริง ให้ส่งข้อความหาบอตใน LINE',
            }
            for source, translated in replacements.items():
                result.stdout = result.stdout.replace(source, translated)
            return result
        self.submit('สถานะระบบ', work)

    def make_settings(self, parent):
        ttk.Label(parent, text='ค่าที่ใช้งานอยู่ · กุญแจแสดงเป็นจุด · บันทึกแล้วเปิดระบบใหม่เพื่อให้มีผล', wraplength=850).pack(anchor='w')
        defaults = dotenv_values(ROOT / '.env.example', interpolate=False)
        current = dotenv_values(ROOT / '.env', interpolate=False)
        self.original = {**defaults, **current}
        self.env_snapshot = (ROOT / '.env').read_bytes() if (ROOT / '.env').exists() else None
        canvas = tk.Canvas(parent, background='#f5f8fc', highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        footer = ttk.Frame(parent)
        footer.pack(side='bottom', fill='x', pady=(10, 0))
        ttk.Button(footer, text='บันทึกการตั้งค่า', command=self.save).pack(side='left')
        ttk.Label(footer, text='พอร์ต หุ้น และ DCA แก้ได้จากหน้าหลัก').pack(side='left', padx=12)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        form = ttk.Frame(canvas, padding=8)
        slot = canvas.create_window((0, 0), window=form, anchor='nw')
        form.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(slot, width=e.width))
        canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(-int(e.delta / 120), 'units'))
        form.columnconfigure(1, weight=1)
        self.fields = {}
        keys = [k for k in LABELS if k in self.original] + [k for k in self.original if k not in LABELS]
        for n, key in enumerate(keys):
            label = LABELS.get(key, key)
            ttk.Label(form, text=label, wraplength=380).grid(row=n, column=0, sticky='w', pady=4)
            var = tk.StringVar(value=self.original[key] or '')
            self.fields[key] = var
            if key in CHOICES:
                widget = ttk.Combobox(form, textvariable=var, values=CHOICES[key], state='readonly')
            else:
                widget = ttk.Entry(form, textvariable=var, show='●' if is_secret(key) else '')
            widget.grid(row=n, column=1, sticky='ew', padx=10, pady=4)
            widget.bind('<MouseWheel>', lambda e: canvas.yview_scroll(-int(e.delta / 120), 'units'))

    def save(self):
        changes = {k: v.get().strip() for k, v in self.fields.items() if v.get().strip() != (self.original[k] or '')}
        if not changes:
            self.status.set('ไม่มีการตั้งค่าที่เปลี่ยน')
            return
        path = ROOT / '.env'
        try:
            if (path.read_bytes() if path.exists() else None) != self.env_snapshot:
                raise ValueError('มีการแก้การตั้งค่าจากหน้าต่างอื่นแล้ว กรุณาปิดและเปิดแอปใหม่ก่อนบันทึก')
            validate_changes(changes)
            save_environment(path, changes)
            self.original.update(changes)
            self.env_snapshot = path.read_bytes()
            self.status.set('บันทึกแล้ว · หยุดและเปิดระบบใหม่เพื่อใช้การตั้งค่าใหม่')
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            messagebox.showerror('ยังไม่บันทึก', str(exc), parent=self.root)

    def close(self):
        dirty = any(v.get().strip() != (self.original[k] or '') for k, v in self.fields.items())
        if dirty and not messagebox.askokcancel('ยังไม่ได้บันทึก', 'ปิดหน้าต่างโดยไม่บันทึกการตั้งค่าที่แก้ไว้?', parent=self.root):
            return
        if self.busy:
            messagebox.showinfo('กำลังทำงาน', 'รอรายการปัจจุบันเสร็จก่อนปิดหน้าต่างครับ', parent=self.root)
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    Desktop(root)
    root.mainloop()


if __name__ == '__main__':
    main()

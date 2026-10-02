"""Small per-stock alert form. Saves policy, never changes holdings or AI caps."""
import tkinter as tk
from tkinter import messagebox, ttk

from app.alert_policy import FIELDS
from app.config import Settings
from app.desktop_theme import apply_theme
from app.portfolio_catalog import PortfolioCatalog


def main():
    settings = Settings.from_env()
    catalog = PortfolioCatalog(settings)
    root = tk.Tk()
    apply_theme(root)
    root.title('Stock Manager · แจ้งเตือนรายหุ้น')
    root.geometry('820x600')
    root.minsize(760, 580)
    outer = ttk.Frame(root, padding=24)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text='แจ้งเตือนรายหุ้น', style='Title.TLabel').pack(anchor='w')
    ttk.Label(outer, text='กำหนดความไวของหุ้นแต่ละตัว · ไม่เปลี่ยนความถี่ดึงราคาหรือโควตา AI').pack(anchor='w')
    selectors = ttk.Frame(outer)
    selectors.pack(fill='x', pady=12)
    ttk.Label(selectors, text='พอร์ต').pack(side='left')
    port = ttk.Combobox(selectors, state='readonly', width=24)
    port.pack(side='left', padx=(0, 18))
    ttk.Label(selectors, text='หุ้น').pack(side='left')
    ticker = ttk.Combobox(selectors, state='readonly', width=16)
    ticker.pack(side='left')
    form = ttk.Frame(outer)
    form.pack(fill='x', pady=8)
    form.columnconfigure(1, weight=1)
    values = {}
    for row, (key, label) in enumerate(FIELDS.items()):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=5)
        var = tk.StringVar()
        values[key] = var
        ttk.Entry(form, textvariable=var, width=15).grid(row=row, column=1, sticky='ew', padx=12)
        ttk.Label(form, text=f'เว้นว่าง = ค่าเริ่มต้น {getattr(settings, key):g}').grid(row=row, column=2, sticky='w')
    escalation = tk.BooleanVar(value=True)
    ttk.Checkbutton(outer, text='เตือนอีกครั้งเมื่อแรงขึ้นเป็น 2 เท่า / 3 เท่าของเกณฑ์', variable=escalation).pack(anchor='w', pady=8)
    ttk.Label(outer, text='ตัวอย่าง: เกณฑ์ราคาลง 5% → ระดับ 1 ที่ −5%, ระดับ 2 ที่ −10%, ระดับ 3 ที่ −15%\n'
        'ข้ามหลายระดับจะส่งระดับสูงสุดครั้งเดียว · ไม่เตือนระดับเดิมซ้ำในวันตลาดเดียวกัน\n'
        'หุ้นเดียวอยู่หลายพอร์ต: ใช้เกณฑ์ที่ไวที่สุด และดึงราคาครั้งเดียวร่วมกัน\n'
        'การเตือนราคาฟื้นไม่ได้แปลว่าความเสี่ยงบริษัทหมดไป', wraplength=740).pack(anchor='w', pady=12)
    notice = tk.StringVar(value='')
    ttk.Label(outer, textvariable=notice, wraplength=740).pack(anchor='w')
    state = {'original': None}

    def current_values():
        return {**{k: v.get().strip() for k, v in values.items()}, 'alert_escalation': escalation.get()}

    def dirty():
        return state['original'] is not None and current_values() != state['original']

    def load(identity, symbol=None):
        data = catalog.read()
        p = catalog.entry(identity, data)
        port['values'] = [e['name'] for e in data['portfolios']]
        port.current(next(i for i, e in enumerate(data['portfolios']) if e['id'] == identity))
        ticker['values'] = [s['symbol'] for s in p['stocks']]
        symbol = symbol if symbol in ticker['values'] else p['stocks'][0]['symbol'] if p['stocks'] else ''
        ticker.set(symbol)
        stock = next((s for s in p['stocks'] if s['symbol'] == symbol), {})
        policy = stock.get('alert_settings', {})
        for key, var in values.items():
            var.set(f'{policy[key]:g}' if key in policy else '')
        escalation.set(policy.get('alert_escalation', True))
        state.update(identity=identity, symbol=symbol, original=current_values(), data=data, digest=catalog.digest())
        notice.set(p['name'] + (' · ' + symbol if symbol else ' · เพิ่มหุ้นก่อนตั้งเกณฑ์'))

    def switch(_=None):
        if dirty() and not messagebox.askokcancel('ยังไม่ได้บันทึก', 'เปลี่ยนรายการโดยไม่บันทึกค่าที่แก้ไว้?', parent=root):
            port.current(next(i for i, e in enumerate(state['data']['portfolios']) if e['id'] == state['identity']))
            ticker.set(state['symbol'])
            return
        identity = state['data']['portfolios'][port.current()]['id']
        load(identity, ticker.get() if identity == state['identity'] else None)

    def save():
        if not state['symbol']:
            return
        try:
            policy = {k: float(v.get().strip()) for k, v in values.items() if v.get().strip()}
            policy['alert_escalation'] = escalation.get()
            catalog.save_alerts(state['identity'], state['symbol'], policy, expected=state['digest'])
            load(state['identity'], state['symbol'])
            notice.set('บันทึกแล้ว · ใช้กับการตรวจราคาครั้งถัดไป ไม่ต้องเปิดระบบใหม่')
        except (ValueError, OSError) as exc:
            messagebox.showerror('ยังไม่บันทึก', str(exc), parent=root)

    def reset():
        for var in values.values():
            var.set('')
        escalation.set(True)

    buttons = ttk.Frame(outer)
    buttons.pack(fill='x', pady=8)
    ttk.Button(buttons, text='บันทึกเกณฑ์ของหุ้นนี้', command=save).pack(side='left', padx=(0, 12))
    ttk.Button(buttons, text='ใช้ค่าเริ่มต้น (แล้วกดบันทึก)', command=reset).pack(side='left')
    port.bind('<<ComboboxSelected>>', switch)
    ticker.bind('<<ComboboxSelected>>', switch)
    load(catalog.selected()['id'])
    def close():
        if not dirty() or messagebox.askokcancel('ยังไม่ได้บันทึก', 'ปิดโดยไม่บันทึกเกณฑ์ที่แก้ไว้?', parent=root):
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.mainloop()


if __name__ == '__main__':
    main()

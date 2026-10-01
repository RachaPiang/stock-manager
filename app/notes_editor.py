"""Local descriptions for portfolios and stocks, using the existing desktop style."""
import json
import tkinter as tk
from tkinter import messagebox, ttk

from app.config import Settings
from app.desktop_theme import apply_theme
from app.investment_notes import PORTFOLIO_FIELDS, STOCK_FIELDS, TAGS, TEXT_FIELDS
from app.portfolio_catalog import PortfolioCatalog


def open_editor(parent, settings, portfolio_id=None, symbol=None, on_saved=None):
    catalog = PortfolioCatalog(settings)
    win = tk.Toplevel(parent)
    win.title('Stock Manager · คำอธิบายและแผนลงทุน')
    win.geometry(f'1000x{min(780, win.winfo_screenheight()-90)}')
    win.minsize(850, 640)
    outer = ttk.Frame(win, padding=20)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text='ข้อมูลประกอบคำแนะนำของ AI', style='Title.TLabel').pack(anchor='w')
    ttk.Label(outer, text='เลือกพอร์ตหรือหุ้น แล้วบอกแผนของคุณ · เว้นช่องที่ยังไม่แน่ใจได้', wraplength=900).pack(anchor='w', pady=(0, 8))
    selectors = ttk.Frame(outer)
    selectors.pack(fill='x', pady=8)
    ttk.Label(selectors, text='พอร์ต').pack(side='left')
    port = ttk.Combobox(selectors, state='readonly', width=26)
    port.pack(side='left', padx=(4, 20))
    ttk.Label(selectors, text='กำกับข้อมูลของ').pack(side='left')
    target = ttk.Combobox(selectors, state='readonly', width=28)
    target.pack(side='left', padx=4)
    footer = ttk.Frame(outer)
    footer.pack(side='bottom', fill='x', pady=(12, 0))
    notice = tk.StringVar(value='การบันทึกไม่เรียก AI และไม่เปลี่ยนโควตาติดตาม ราคา หรือยอดหุ้น')
    ttk.Label(footer, textvariable=notice, wraplength=740).pack(anchor='w')
    toolbar = ttk.Frame(footer)
    toolbar.pack(fill='x', pady=(8, 0))
    canvas = tk.Canvas(outer, background='#f5f8fc', highlightthickness=0)
    scroll = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
    scroll.pack(side='right', fill='y')
    canvas.pack(fill='both', expand=True)
    canvas.configure(yscrollcommand=scroll.set)
    form = ttk.Frame(canvas, padding=(8, 4))
    slot = canvas.create_window((0, 0), window=form, anchor='nw')
    canvas.bind('<Configure>', lambda e: canvas.itemconfigure(slot, width=e.width))
    form.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
    def wheel(e):
        canvas.yview_scroll(-int(e.delta / 120), 'units')
    canvas.bind('<MouseWheel>', wheel)
    state = {'original': None, 'fields': {}, 'texts': {}, 'tags': {}}

    def values():
        return {**{k: v.get() for k, v in state['fields'].items()},
                **{k: widget.get('1.0', 'end-1c').strip() for k, widget in state['texts'].items()},
                'focus': [tag for tag, v in state['tags'].items() if v.get()]}

    def dirty():
        return state['original'] is not None and values() != state['original']

    def allow_switch():
        return not dirty() or messagebox.askokcancel('ยังไม่ได้บันทึก', 'เปลี่ยนรายการโดยไม่บันทึกข้อความที่แก้ไว้?', parent=win)

    def load(chosen, ticker=None):
        value = catalog.read()
        p = catalog.entry(chosen, value)
        state.update(identity=chosen, symbol=ticker, digest=catalog.digest(), catalog=value)
        port['values'] = [row['name'] for row in value['portfolios']]
        port.current(next(i for i, row in enumerate(value['portfolios']) if row['id'] == chosen))
        target['values'] = ['พอร์ตนี้ (ภาพรวม)'] + [s['symbol'] for s in p['stocks']]
        target.current(next((i+1 for i, s in enumerate(p['stocks']) if s['symbol'] == ticker), 0))
        scope = 'stock' if ticker else 'portfolio'
        item = next(s for s in p['stocks'] if s['symbol'] == ticker) if ticker else p
        notes = item.get('investment_notes', {})
        # Existing holding reasons remain visible and editable, including legacy profiles.
        if ticker and 'rationale' not in notes:
            path = catalog.profile_path(chosen)
            if path.exists():
                h = next((h for h in json.loads(path.read_text(encoding='utf-8')).get('holdings', []) if h['symbol'] == ticker), {})
                notes = {**notes, 'rationale': h.get('thesis', '')}
        for child in form.winfo_children():
            child.destroy()
        state.update(fields={}, texts={}, tags={}, scope=scope)
        ttk.Label(form, text=p['name'] + (' · ' + ticker if ticker else ' · ภาพรวม'), style='Section.TLabel').pack(anchor='w', pady=(0, 8))
        options = ttk.Frame(form)
        options.pack(fill='x')
        fields = STOCK_FIELDS if ticker else PORTFOLIO_FIELDS
        for i, (key, (label, choices)) in enumerate(fields.items()):
            cell = ttk.Frame(options, padding=(0, 0, 18, 8))
            cell.grid(row=i//2, column=i%2, sticky='ew')
            options.columnconfigure(i%2, weight=1)
            ttk.Label(cell, text=label).pack(anchor='w')
            var = tk.StringVar(value=notes.get(key, ''))
            state['fields'][key] = var
            ttk.Combobox(cell, textvariable=var, values=choices, state='readonly').pack(fill='x')
        ttk.Label(form, text='อยากให้ AI ใส่ใจเรื่องไหนบ้าง (เลือกได้หลายข้อ)', style='Section.TLabel').pack(anchor='w', pady=(8, 4))
        tags = ttk.Frame(form)
        tags.pack(fill='x')
        for i, tag in enumerate(TAGS):
            var = tk.BooleanVar(value=tag in notes.get('focus', []))
            state['tags'][tag] = var
            ttk.Checkbutton(tags, text=tag, variable=var).grid(row=i//3, column=i%3, sticky='w', padx=(0, 10), pady=3)
            tags.columnconfigure(i%3, weight=1)
        for key, (label, limit) in TEXT_FIELDS[scope].items():
            ttk.Label(form, text=f'{label} (ไม่เกิน {limit} ตัวอักษร)').pack(anchor='w', pady=(12, 2))
            text = tk.Text(form, height=3, wrap='word', font='TkTextFont', padx=10, pady=8,
                           relief='solid', borderwidth=1, undo=True)
            text.insert('1.0', notes.get(key, ''))
            text.pack(fill='x')
            state['texts'][key] = text
        for child in form.winfo_children():
            if not isinstance(child, tk.Text):
                child.bind('<MouseWheel>', wheel)
        state['original'] = values()
        canvas.yview_moveto(0)
        notice.set('ข้อมูลนี้ใช้ในบทวิเคราะห์ครั้งถัดไป · บันทึกแล้วไม่เรียก AI เพิ่ม ไม่เปลี่ยนแผน DCA')

    def switch_port(event=None):
        if not allow_switch():
            port.current(next(i for i, p in enumerate(state['catalog']['portfolios']) if p['id'] == state['identity']))
            return
        load(state['catalog']['portfolios'][port.current()]['id'])

    def switch_target(event=None):
        if not allow_switch():
            target.set(state['symbol'] or 'พอร์ตนี้ (ภาพรวม)')
            return
        load(state['identity'], None if target.current() == 0 else target.get())

    def save():
        try:
            catalog.save_notes(state['identity'], values(), symbol=state['symbol'], expected=state['digest'])
            load(state['identity'], state['symbol'])
            notice.set('บันทึกแล้ว · AI จะใช้ข้อมูลใหม่เมื่อวิเคราะห์ครั้งถัดไป สรุปเก่ายังคงเป็นฉบับเดิม')
            if on_saved:
                on_saved()
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('ยังไม่บันทึก', str(exc), parent=win)

    def close():
        if allow_switch():
            win.destroy()

    def reload():
        if allow_switch():
            load(state['identity'], state['symbol'])

    ttk.Button(toolbar, text='บันทึกคำอธิบาย', command=save).pack(side='left')
    ttk.Button(toolbar, text='โหลดข้อมูลล่าสุด', command=reload).pack(side='left', padx=10)
    ttk.Button(toolbar, text='ปิด', command=close).pack(side='right')
    port.bind('<<ComboboxSelected>>', switch_port)
    target.bind('<<ComboboxSelected>>', switch_target)
    win.protocol('WM_DELETE_WINDOW', close)
    load(portfolio_id or catalog.selected()['id'], symbol)
    return win


def main():
    root = tk.Tk()
    apply_theme(root)
    root.withdraw()
    settings = Settings.from_env()
    if settings.mock_mode:
        messagebox.showinfo('ข้อมูลจำลอง', 'เปลี่ยนเป็นข้อมูลจริงก่อนกำกับคำอธิบายพอร์ต', parent=root)
        root.destroy()
        return
    win = open_editor(root, settings)
    root.wait_window(win)
    root.destroy()


if __name__ == '__main__':
    main()

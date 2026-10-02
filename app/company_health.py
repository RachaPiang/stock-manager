"""Read-only company health and investment-thesis view in the Windows app."""
import json
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import UTC, datetime
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.company_checks import checks
from app.config import Settings
from app.desktop_theme import apply_theme
from app.portfolio_catalog import PortfolioCatalog
from app.research_context import cached_fundamentals

NAMES = {'revenue': 'รายได้', 'net_income': 'กำไรสุทธิ', 'operating_cash': 'เงินสดดำเนินงาน',
         'free_cash_flow': 'เงินสดอิสระ', 'net_margin_pct': 'อัตรากำไรสุทธิ'}


def number(metric):
    if not metric:
        return 'ไม่มีข้อมูล'
    if metric['currency'] == 'percent':
        return f"{metric['value']:.1f}%"
    return f"{metric['value'] / 1_000_000:,.1f} ล้าน {metric['currency']}"


def main():
    settings = Settings.from_env()
    catalog = PortfolioCatalog(settings)
    root = tk.Tk()
    apply_theme(root)
    root.title('Stock Manager · สุขภาพบริษัทและเหตุผลที่ถือ')
    root.geometry('1040x760')
    root.minsize(900, 650)
    outer = ttk.Frame(root, padding=20)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text='สุขภาพบริษัท', style='Title.TLabel').pack(anchor='w')
    ttk.Label(outer, text='ดูงบพร้อมแผนที่คุณบันทึก · เปิดหน้านี้ไม่เรียก AI หรือดึงข้อมูลใหม่').pack(anchor='w')
    selector = ttk.Frame(outer)
    selector.pack(fill='x', pady=8)
    ttk.Label(selector, text='พอร์ต').pack(side='left')
    port = ttk.Combobox(selector, state='readonly', width=25)
    port.pack(side='left', padx=(0, 16))
    ttk.Label(selector, text='หุ้น').pack(side='left')
    ticker = ttk.Combobox(selector, state='readonly', width=12)
    ticker.pack(side='left')
    notice = tk.StringVar(value='')
    ttk.Label(outer, textvariable=notice, wraplength=970).pack(anchor='w')
    box = ScrolledText(outer, wrap='word', font='TkTextFont', height=10, padx=14, pady=10)
    box.pack(fill='both', expand=True, pady=8)
    ttk.Label(outer, text='ไตรมาสเดียว (ไม่ใช่ยอดสะสม) · ตัวเลขเงินหน่วยล้าน', style='Section.TLabel').pack(anchor='w')
    table = ttk.Frame(outer)
    table.pack(fill='x', pady=8)
    tree = ttk.Treeview(table, columns=('end', 'revenue', 'income', 'margin', 'fcf', 'growth'), show='headings', height=6)
    scroll = ttk.Scrollbar(table, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    for name, label, width in [('end', 'สิ้นสุดงบ', 115), ('revenue', 'รายได้', 165), ('income', 'กำไรสุทธิ', 165),
                                ('margin', 'อัตรากำไร', 100), ('fcf', 'เงินสดอิสระ', 165), ('growth', 'รายได้ YoY', 110)]:
        tree.heading(name, text=label)
        tree.column(name, width=width, anchor='w')
    tree.pack(side='left', fill='x', expand=True)
    actions = ttk.Frame(outer)
    actions.pack(fill='x')
    state = {'company': {}, 'busy': False}
    events = queue.Queue()

    def render(_=None):
        value = catalog.read()
        portfolios = value['portfolios']
        p = portfolios[max(0, port.current())]
        stocks = [s['symbol'] for s in p['stocks']]
        chosen = ticker.get()
        ticker['values'] = stocks
        ticker.set(chosen if chosen in stocks else stocks[0] if stocks else '')
        symbol = ticker.get()
        now = datetime.now(UTC)
        company = cached_fundamentals(settings, {symbol}, now).get(symbol, {})
        state.update(company=company, identity=p['id'])
        stock = next((s for s in p['stocks'] if s['symbol'] == symbol), {})
        notes = dict(stock.get('investment_notes', {}))
        if not notes.get('focus'):
            notes['focus'] = p.get('investment_notes', {}).get('focus', [])
        rationale = notes.get('rationale')
        profile = catalog.profile_path(p['id'])
        if not rationale and profile.exists():
            h = next((h for h in json.loads(profile.read_text(encoding='utf-8')).get('holdings', []) if h['symbol'] == symbol), {})
            rationale = h.get('thesis')
        quarter = company.get('latest_quarter')
        annual = company.get('metrics', {})
        date_text = quarter['end'] if quarter else 'ยังไม่มีข้อมูลไตรมาสเดียว'
        notice.set(f'{p["name"]} · {symbol} · งบไตรมาสล่าสุด {date_text}' +
                   (' · แคชเกิน 2 วัน ควรอัปเดต' if company.get('cache_age_days', 0) > 2 else ''))
        lines = [rationale or 'ยังไม่ได้เขียนเหตุผลที่ถือ เพิ่มข้อมูลให้ผู้ช่วยเข้าใจแผนของคุณได้', '']
        if notes.get('review_conditions'):
            lines.extend(['เงื่อนไขที่คุณอยากทบทวน: ' + notes['review_conditions'], ''])
        for item in checks(company, notes, now):
            lines.append(('จับตา: ' if item['status'] in {'review', 'stale'} else '• ') + item['text'])
        if annual:
            lines.extend(['', 'งบรายปีที่บันทึก (แยกจากงบไตรมาส):'])
            for key, label in NAMES.items():
                if key in annual:
                    lines.append(f'{label}: {number(annual[key])} · สิ้นสุด {annual[key]["end"]}')
        lines.extend(['', 'YoY = เทียบไตรมาสเดียวกันปีก่อน · เงินสดอิสระ = เงินสดดำเนินงาน − เงินลงทุนสินทรัพย์',
                      'ข้อมูลไม่ครบจะแสดงว่าไม่มีข้อมูล ไม่แทนด้วยศูนย์ และไม่เดาราคาที่เหมาะสม',
                      'อ้างอิง SEC EDGAR · บางบริษัทไม่มี US-GAAP หรือเผยเงินสดแบบสะสม จึงอาจไม่มีบางช่อง'])
        box.configure(state='normal')
        box.delete('1.0', 'end')
        box.insert('1.0', '\n'.join(lines))
        box.configure(state='disabled')
        tree.delete(*tree.get_children())
        for q in company.get('quarters', [])[-8:]:
            m = q['metrics']
            growth = q['trends'].get('revenue_yoy_pct')
            tree.insert('', 'end', values=(q['end'], number(m.get('revenue')), number(m.get('net_income')),
                number(m.get('net_margin_pct')), number(m.get('free_cash_flow')),
                f'{growth:+.1f}%' if growth is not None else 'ไม่มีฐานเทียบ'))
        if tree.get_children():
            tree.see(tree.get_children()[-1])

    def select_port(_=None):
        ticker.set('')
        render()

    def refresh():
        if state['busy']:
            return
        if settings.mock_mode:
            messagebox.showinfo('ข้อมูลจำลอง', 'การดึงงบ SEC ใช้ในโหมดราคาจริงเท่านั้น', parent=root)
            return
        if not messagebox.askokcancel('อัปเดตงบ SEC', 'ดึงงบของหุ้นที่ติดตามจาก SEC ตามแคช 24 ชั่วโมง\nไม่เรียก AI และไม่ใช้เครดิตราคาหุ้น', parent=root):
            return
        state['busy'] = True
        notice.set('กำลังตรวจงบที่บันทึกและดึงรายการที่ถึงรอบ…')
        def work():
            try:
                from app.fundamentals import collect
                result = collect(settings, [s.symbol for s in settings.stocks()], datetime.now(UTC))
                events.put(sum(bool(v.get('latest_quarter')) for v in result.values()))
            except Exception:
                events.put(None)
        threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            count = events.get_nowait()
            state['busy'] = False
            render()
            messagebox.showinfo('ตรวจงบแล้ว', f'มีงบไตรมาสเดียว {count} บริษัท\nรายการที่ยังอยู่ในช่วงแคชจะไม่ดึงซ้ำ' if count is not None else 'ตรวจงบไม่สำเร็จ ดูบันทึกหรือตั้งค่าอีเมล SEC', parent=root)
        except queue.Empty:
            pass
        root.after(200, poll)

    def source():
        company = state['company']
        metrics = (company.get('latest_quarter') or {}).get('metrics') or company.get('metrics', {})
        url = next((m['source_url'] for m in metrics.values() if m.get('source_url', '').startswith('https://www.sec.gov/Archives/edgar/data/')), None)
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo('ยังไม่มีแหล่งงบ', 'อัปเดต SEC ก่อน หรือดูเอกสารจากเว็บไซต์บริษัท', parent=root)

    def edit():
        if not ticker.get():
            return
        from app.notes_editor import open_editor
        open_editor(root, settings, state['identity'], ticker.get(), on_saved=render)

    ttk.Button(actions, text='อัปเดตงบ SEC', command=refresh).pack(side='left', padx=(0, 10))
    ttk.Button(actions, text='โหลดข้อมูลที่บันทึก', command=render).pack(side='left', padx=(0, 10))
    ttk.Button(actions, text='เปิดเอกสารอ้างอิง', command=source).pack(side='left', padx=(0, 10))
    ttk.Button(actions, text='แก้เหตุผล / เงื่อนไขทบทวน', command=edit).pack(side='left')
    port['values'] = [p['name'] for p in catalog.read()['portfolios']]
    port.current(next(i for i, p in enumerate(catalog.read()['portfolios']) if p['id'] == catalog.selected()['id']))
    port.bind('<<ComboboxSelected>>', select_port)
    ticker.bind('<<ComboboxSelected>>', render)
    render()
    root.after(200, poll)
    def close():
        if state['busy']:
            messagebox.showinfo('กำลังตรวจงบ', 'รอข้อมูลก่อนปิดหน้าต่างครับ', parent=root)
        else:
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.mainloop()


if __name__ == '__main__':
    main()

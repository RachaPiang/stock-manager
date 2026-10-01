"""Local, explicit user actions for multiple portfolios; never sends trades."""
import json

from app.config import Settings
from app.portfolio_catalog import PortfolioCatalog
from app.budget_planner import debug_usage


def main():
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
    settings = Settings.from_env()
    root = tk.Tk()
    root.title('Stock Manager · จัดการพอร์ตและงบติดตาม')
    root.geometry('1080x820'); root.minsize(900,720)
    style = ttk.Style(root); style.theme_use('clam')
    root.configure(background='#f5f8fc')
    style.configure('TFrame',background='#f5f8fc')
    style.configure('TLabel',background='#f5f8fc')
    style.configure('TNotebook',background='#f5f8fc')
    style.configure('Treeview',background='white',fieldbackground='white')
    style.configure('TLabel',font=('Leelawadee UI',11))
    style.configure('TButton',font=('Leelawadee UI',10),padding=8)
    style.configure('Treeview',font=('Leelawadee UI',10),rowheight=30)
    style.configure('Treeview.Heading',font=('Leelawadee UI',10,'bold'))
    if settings.mock_mode:
        messagebox.showinfo('ข้อมูลจำลอง','หน้าจัดการพอร์ตใช้ข้อมูลจริง เปิด MOCK_MODE=false ก่อนครับ',parent=root)
        root.destroy(); return
    catalog = PortfolioCatalog(settings)
    outer = ttk.Frame(root,padding=20); outer.pack(fill='both',expand=True)
    ttk.Label(outer,text='พอร์ตของคุณ แต่ละพอร์ตแยกยอดกัน',font=('Leelawadee UI',20,'bold')).pack(anchor='w')
    ttk.Label(outer,text='น้ำหนักด้านล่างคือความสำคัญในการติดตาม ไม่ใช่สัดส่วนเงินลงทุน · หุ้นซ้ำใช้ราคาชุดเดียวกัน',wraplength=1000).pack(anchor='w',pady=(5,15))
    tabs = ttk.Notebook(outer); tabs.pack(fill='both',expand=True)
    manage, debug = ttk.Frame(tabs,padding=14), ttk.Frame(tabs,padding=14)
    tabs.add(manage,text='  พอร์ตและหุ้น  '); tabs.add(debug,text='  งบติดตาม / Debug  ')
    cols = ('name','weight','percent','active')
    ports = ttk.Treeview(manage,columns=cols,show='headings',height=4,selectmode='browse')
    for col,label,width in zip(cols,['ชื่อพอร์ต','น้ำหนักที่ตั้ง','ความสำคัญรวม','เลือกสำหรับ LINE'],[360,150,160,170]):
        ports.heading(col,text=label); ports.column(col,width=width,stretch=col=='name')
    ports.pack(fill='x')
    port_tools = ttk.Frame(manage); port_tools.pack(fill='x',pady=10)
    port_weight, stock_weight = tk.StringVar(value='100'),tk.StringVar(value='100')
    ttk.Label(port_tools,text='น้ำหนักพอร์ตที่เลือก').pack(side='left')
    ttk.Entry(port_tools,textvariable=port_weight,width=9).pack(side='left',padx=8)
    stock_cols = ('symbol','name','type','weight','percent')
    table = ttk.Frame(manage)
    stocks = ttk.Treeview(table,columns=stock_cols,show='headings',height=7,selectmode='browse')
    for col,label,width in zip(stock_cols,['หุ้น','ชื่อบริษัท','สถานะ','น้ำหนัก','ภายในพอร์ต'],[90,260,140,130,150]):
        stocks.heading(col,text=label); stocks.column(col,width=width,stretch=col=='name')
    stock_scroll = ttk.Scrollbar(table,orient='vertical',command=stocks.yview)
    stocks.configure(yscrollcommand=stock_scroll.set)
    table.pack(fill='both',expand=True)
    stocks.pack(side='left',fill='both',expand=True); stock_scroll.pack(side='right',fill='y')
    stock_tools = ttk.Frame(manage); stock_tools.pack(fill='x',pady=10)
    ttk.Label(stock_tools,text='น้ำหนักหุ้นที่เลือก').pack(side='left')
    ttk.Entry(stock_tools,textvariable=stock_weight,width=9).pack(side='left',padx=8)
    ttk.Label(manage,text='ตัวอย่าง: สองพอร์ต 70 กับ 30 = แบ่งความสำคัญ 70% / 30%\nถ้าหุ้นในพอร์ตน้ำหนัก 2 กับ 1 หุ้นแรกจะได้ความสำคัญเป็นสองเท่า (ก่อนรวมกับพอร์ตอื่น)',wraplength=950).pack(anchor='w',pady=5)
    footer = ttk.Frame(manage); footer.pack(fill='x',pady=(8,0))
    state = dict(value=None,digest=None)
    status = tk.StringVar()
    ttk.Label(outer,textvariable=status,wraplength=1000).pack(anchor='w',pady=(12,0))

    def identity():
        selected = ports.selection()
        if not selected: raise ValueError('เลือกพอร์ตจากตารางก่อนครับ')
        return selected[0]

    def guarded(action):
        try: action()
        except (ValueError,OSError,RuntimeError,KeyError) as exc:
            messagebox.showerror('ยังไม่บันทึก',str(exc),parent=root)

    debug_summary = tk.StringVar()
    ttk.Label(debug,textvariable=debug_summary,wraplength=970).pack(anchor='w',pady=(0,12))
    usage = ttk.Treeview(debug,columns=('symbol','share','checks','minutes','ai','used'),show='headings',height=13)
    for col,label,width in zip(usage['columns'],['หุ้นไม่ซ้ำ','ความสำคัญรวม','ตรวจราคา/วัน','ห่างเฉลี่ย (นาที)','AI สิทธิ์/วัน','AI ใช้วันนี้'],[105,130,140,160,130,120]):
        usage.heading(col,text=label); usage.column(col,width=width)
    usage.pack(fill='both',expand=True)
    ttk.Label(debug,text='ตัวเลขเป็นแผนสำหรับตลาดเปิดเต็มวัน 6.5 ชั่วโมง; เครื่องปิดจะตรวจได้น้อยลง\nAI เรียกเฉพาะเมื่อมีเหตุการณ์ ไม่ได้เรียกทุกครั้งที่อัปเดตราคา; สิทธิ์ 0 ยังได้รับข้อความแม่แบบ\nสิทธิ์จำนวนเต็มอาจไม่ตรงเปอร์เซ็นต์เป๊ะ ระบบสลับหุ้นที่น้ำหนักเท่ากันตามวัน UTC\nเพดานราคาเดิม 8/min และ 760/day ของโปรแกรมยังบังคับทุกคำขอ; เหลือ 40 credits กันการใช้ภายนอก\nAI ที่สงวนไว้ใช้สำหรับรีวิว ข่าว และคำถาม; ไม่ใช่การแบ่งโทเค็นจริงของบัญชี Codex',wraplength=970).pack(anchor='w',pady=12)

    def refresh_debug():
        p = debug_usage(settings)
        debug_summary.set(f"หุ้นไม่ซ้ำ {len(p['stocks'])} ตัว · แผนราคา {p['price_budget']} + เผื่อประวัติ/ปิดตลาด {p['overhead_reserve']} = {p['planned_credits']}/{p['local_limit']} credits\nAI ใช้ {p['ai_used']}/{p['ai_limit']} ครั้งวันนี้ UTC ({p['utc_day']}) · กันไว้สำหรับรีวิว/ข่าว/ถาม {p['ai_reserve']} ครั้ง · ไม่รวมการใช้งานนอกโปรแกรม")
        usage.delete(*usage.get_children())
        for s in p['stocks']:
            usage.insert('', 'end', values=(s['symbol'],f"{s['weight_pct']:.2f}%",s['price_checks'],s['average_minutes'],s['ai_slots'],s['ai_used']))

    def show_stocks(event=None):
        if not ports.selection(): return
        p = catalog.entry(identity(),state['value'])
        port_weight.set(str(p['priority']))
        stocks.delete(*stocks.get_children())
        path = catalog.profile_path(p['id'])
        holdings = json.loads(path.read_text(encoding='utf-8'))['holdings'] if path.exists() else []
        owned = {h['symbol'] for h in holdings}
        total = sum(s['priority'] for s in p['stocks'])
        for s in p['stocks']:
            stocks.insert('', 'end',iid=s['symbol'],values=(s['symbol'],s.get('name',''),
                'ถืออยู่' if s['symbol'] in owned else 'ติดตามอย่างเดียว',s['priority'],f"{s['priority']/total*100:.1f}%"))

    def reload_data(selected=None):
        state['value'],state['digest'] = catalog.read(),catalog.digest()
        ports.delete(*ports.get_children())
        total = sum(p['priority'] for p in state['value']['portfolios'] if p['stocks'])
        for p in state['value']['portfolios']:
            share = p['priority']/total*100 if p['stocks'] and total else 0
            ports.insert('', 'end',iid=p['id'],values=(p['name'],p['priority'],f'{share:.1f}%',
                '✓ เลือกอยู่' if p['id']==state['value']['active_id'] else ''))
        ports.selection_set(selected or state['value']['active_id']); show_stocks(); refresh_debug()
        status.set('อ่านข้อมูลจากเครื่อง · การเปิดหน้านี้ไม่เรียก API หรือ AI')

    def edit_port_weight():
        from app.config import positive
        p = catalog.entry(identity(),state['value']); p['priority'] = positive(port_weight.get(),'น้ำหนักพอร์ต')
        ports.item(p['id'],values=(p['name'],p['priority'],'รอบันทึก',''))
        status.set('ปรับน้ำหนักในร่างแล้ว กด บันทึกความสำคัญ เพื่อใช้จริง')

    def edit_stock_weight():
        from app.config import positive
        if not stocks.selection(): raise ValueError('เลือกหุ้นก่อนครับ')
        symbol = stocks.selection()[0]
        p = catalog.entry(identity(),state['value'])
        next(s for s in p['stocks'] if s['symbol']==symbol)['priority'] = positive(stock_weight.get(),'น้ำหนักหุ้น')
        show_stocks(); stocks.selection_set(symbol)
        status.set('ปรับน้ำหนักในร่างแล้ว กด บันทึกความสำคัญ เพื่อใช้จริง')

    def update_report():
        from app.report import write_report
        write_report(settings)

    def save_weights():
        value = state['value']
        catalog.save_priorities({p['id']:p['priority'] for p in value['portfolios']},
            {p['id']:{s['symbol']:s['priority'] for s in p['stocks']} for p in value['portfolios']},expected=state['digest'])
        chosen = identity(); reload_data(chosen); update_report()
        status.set('บันทึกน้ำหนักแล้ว ระบบใช้ในรอบตรวจถัดไป ไม่มีการเปลี่ยนเงินลงทุนหรือจำนวนหุ้น')

    def create():
        name = simpledialog.askstring('เพิ่มพอร์ต','ชื่อพอร์ตใหม่',parent=root)
        if name:
            chosen = catalog.create(name); reload_data(chosen); update_report()

    def select():
        chosen = identity(); catalog.select(chosen); reload_data(chosen); update_report()
        status.set('เลือกพอร์ตสำหรับ LINE และหน้าเปิดเริ่มต้นแล้ว ทุกพอร์ตยังถูกติดตามพร้อมกัน')

    def add():
        chosen = identity()
        dialog = tk.Toplevel(root); dialog.title('เพิ่มหุ้น · '+catalog.entry(chosen,state['value'])['name'])
        dialog.transient(root); dialog.grab_set(); body = ttk.Frame(dialog,padding=24); body.pack(fill='both',expand=True)
        fields = {}
        for i,(key,label) in enumerate([('symbol','Ticker เช่น AAPL'),('name','ชื่อบริษัท (ไม่บังคับ)'),
            ('quantity','จำนวนหุ้นรวม (เว้น = ติดตามอย่างเดียว)'),('cost','ต้นทุนรวม USD จากโบรกเกอร์')]):
            ttk.Label(body,text=label).grid(row=i,column=0,sticky='w',pady=7)
            fields[key] = tk.StringVar(); ttk.Entry(body,textvariable=fields[key],width=28).grid(row=i,column=1,padx=12,pady=7)
        ttk.Label(body,text='หุ้นที่ถือ: ใส่ทั้งจำนวนและต้นทุน · ไม่ต้องกรอกราคาตลาด\nหากเคยติดตามไว้แล้ว กรอก ticker เดิมพร้อมยอดเพื่อเปลี่ยนเป็นหุ้นที่ถือ\nเพิ่มหุ้นไม่เปลี่ยนแผน DCA เดิม และไม่ดึงราคาเพิ่มทันที\nข่าว/งบของหุ้นใหม่อาจยังไม่มี coverage ดูสถานะบนหน้าเว็บ',wraplength=530).grid(row=4,column=0,columnspan=2,pady=15)
        def submit():
            catalog.add_stock(chosen,fields['symbol'].get(),name=fields['name'].get(),
                quantity=fields['quantity'].get().strip() or None,cost_basis_usd=fields['cost'].get().strip() or None)
            dialog.destroy(); reload_data(chosen); update_report()
            status.set('เพิ่มหุ้นแล้ว ราคาจะเข้ารอบตรวจอัตโนมัติถัดไปเมื่อเปิดระบบและตลาดเปิด · ไม่ส่งคำสั่งซื้อขาย')
        ttk.Button(body,text='ยืนยันเพิ่มหุ้น',command=lambda:guarded(submit)).grid(row=5,column=1,sticky='e')

    def edit_totals():
        from app.portfolio_editor import main as editor
        editor(identity()); reload_data(identity())

    def dca():
        chosen = identity()
        amount = simpledialog.askstring('แผน DCA','ยอด DCA ต่อเดือน (บาท)\nแบ่งเท่ากันเฉพาะหุ้นที่ถือในพอร์ตนี้ · ใส่ 0 เพื่อปิดเตือน\nการแก้แผนนี้ไม่เปลี่ยน Auto DCA ใน Dime',parent=root)
        if amount is None: return
        day = simpledialog.askinteger('วันที่ DCA','วันที่ 1–28',initialvalue=28,minvalue=1,maxvalue=28,parent=root)
        if day is None: return
        catalog.save_dca(chosen,amount,day); update_report()
        status.set('บันทึกแผนเตือน DCA ของพอร์ตนี้แล้ว หากต้องการเปลี่ยนรายการซื้อ ให้จัดการในโบรกเกอร์เอง')

    ttk.Button(port_tools,text='ใช้กับร่าง',command=lambda:guarded(edit_port_weight)).pack(side='left')
    ttk.Button(port_tools,text='เพิ่มพอร์ต',command=lambda:guarded(create)).pack(side='right')
    ttk.Button(port_tools,text='เลือกสำหรับ LINE',command=lambda:guarded(select)).pack(side='right',padx=6)
    ttk.Button(stock_tools,text='ใช้กับร่าง',command=lambda:guarded(edit_stock_weight)).pack(side='left')
    ttk.Button(stock_tools,text='เพิ่มหุ้น',command=lambda:guarded(add)).pack(side='right')
    ttk.Button(stock_tools,text='แก้ยอดหุ้น / ต้นทุน',command=lambda:guarded(edit_totals)).pack(side='right',padx=6)
    ttk.Button(footer,text='แผน DCA พอร์ตนี้',command=lambda:guarded(dca)).pack(side='left')
    ttk.Button(footer,text='โหลดใหม่ / ทิ้งร่าง',command=lambda:guarded(lambda:reload_data(identity()))).pack(side='left',padx=6)
    ttk.Button(footer,text='บันทึกความสำคัญ',command=lambda:guarded(save_weights)).pack(side='right')
    ttk.Button(debug,text='รีเฟรชตัวนับ (ไม่เรียก API)',command=lambda:guarded(refresh_debug)).pack(anchor='e')
    ports.bind('<<TreeviewSelect>>',show_stocks)
    def on_stock(event=None):
        if stocks.selection():
            s = next(s for s in catalog.entry(identity(),state['value'])['stocks'] if s['symbol']==stocks.selection()[0])
            stock_weight.set(str(s['priority']))
    stocks.bind('<<TreeviewSelect>>',on_stock)
    guarded(reload_data)
    root.mainloop()


if __name__ == '__main__':
    main()

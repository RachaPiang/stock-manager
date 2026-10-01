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
    from app.desktop_theme import apply_theme
    apply_theme(root)
    root.title('Stock Manager · จัดการพอร์ต')
    root.geometry(f"1120x{min(820,root.winfo_screenheight()-100)}"); root.minsize(980,680)
    if settings.mock_mode:
        messagebox.showinfo('ข้อมูลจำลอง','หน้าจัดการพอร์ตใช้ข้อมูลจริง เปิด MOCK_MODE=false ก่อนครับ',parent=root)
        root.destroy(); return
    catalog = PortfolioCatalog(settings)
    outer = ttk.Frame(root,padding=20); outer.pack(fill='both',expand=True)
    outer.columnconfigure(0,weight=1); outer.rowconfigure(2,weight=1)
    ttk.Label(outer,text='จัดการพอร์ตของคุณ',style='Title.TLabel').grid(row=0,column=0,sticky='w')
    ttk.Label(outer,text='เลือกพอร์ตเพื่อเพิ่มหุ้น อัปเดตยอด และกำหนดว่าระบบควรติดตามอะไรบ่อยขึ้น',wraplength=920).grid(row=1,column=0,sticky='w',pady=(5,12))
    tabs = ttk.Notebook(outer); tabs.grid(row=2,column=0,sticky='nsew')
    manage, debug = ttk.Frame(tabs,padding=14), ttk.Frame(tabs,padding=14)
    tabs.add(manage,text='พอร์ตและหุ้น'); tabs.add(debug,text='การใช้โควตา')
    manage.columnconfigure(0,weight=1); manage.rowconfigure(4,weight=1)
    ttk.Label(manage,text='1. เลือกพอร์ต',style='Section.TLabel').grid(row=0,column=0,sticky='w',pady=(0,5))
    cols = ('name','weight','percent','active')
    port_table = ttk.Frame(manage); port_table.grid(row=1,column=0,sticky='ew')
    ports = ttk.Treeview(port_table,columns=cols,show='headings',height=2,selectmode='browse')
    for col,label,width in zip(cols,['ชื่อพอร์ต','คะแนนติดตาม','ส่วนแบ่งโควตา','พอร์ตหลักใน LINE'],[340,150,170,190]):
        ports.heading(col,text=label); ports.column(col,width=width,stretch=col=='name')
    ports.pack(side='left',fill='x',expand=True)
    port_scroll = ttk.Scrollbar(port_table,orient='vertical',command=ports.yview)
    port_scroll.pack(side='right',fill='y'); ports.configure(yscrollcommand=port_scroll.set)
    port_tools = ttk.Frame(manage); port_tools.grid(row=2,column=0,sticky='ew',pady=8)
    stock_heading = tk.StringVar(value='2. หุ้นในพอร์ต')
    ttk.Label(manage,textvariable=stock_heading,style='Section.TLabel').grid(row=3,column=0,sticky='w',pady=(4,5))
    stock_cols = ('symbol','name','type','weight','percent')
    table = ttk.Frame(manage)
    stocks = ttk.Treeview(table,columns=stock_cols,show='headings',height=4,selectmode='browse')
    for col,label,width in zip(stock_cols,['หุ้น','ชื่อบริษัท','สถานะ','คะแนนติดตาม','ส่วนแบ่งในพอร์ต'],[80,230,190,150,180]):
        stocks.heading(col,text=label); stocks.column(col,width=width,stretch=col=='name')
    stock_scroll = ttk.Scrollbar(table,orient='vertical',command=stocks.yview)
    stocks.configure(yscrollcommand=stock_scroll.set)
    table.grid(row=4,column=0,sticky='nsew')
    stocks.pack(side='left',fill='both',expand=True); stock_scroll.pack(side='right',fill='y')
    stock_tools = ttk.Frame(manage); stock_tools.grid(row=5,column=0,sticky='ew',pady=8)
    ttk.Label(manage,text='คะแนนมาก = ติดตามบ่อยขึ้น เช่น 70 กับ 30 จะแบ่งโควตา 70% และ 30%\nคะแนนนี้ไม่เปลี่ยนจำนวนหุ้น เงินลงทุน หรือแผน DCA ของคุณ',wraplength=910).grid(row=6,column=0,sticky='w',pady=5)
    footer = ttk.Frame(manage); footer.grid(row=7,column=0,sticky='ew',pady=(8,0))
    state = dict(value=None,digest=None)
    status = tk.StringVar()
    ttk.Label(outer,textvariable=status,wraplength=900).grid(row=3,column=0,sticky='w',pady=(12,0))

    def identity():
        selected = ports.selection()
        if not selected: raise ValueError('เลือกพอร์ตจากตารางก่อนครับ')
        return selected[0]

    def guarded(action):
        try: action()
        except (ValueError,OSError,RuntimeError,KeyError) as exc:
            messagebox.showerror('ทำรายการไม่สำเร็จ',str(exc),parent=root)

    debug_summary = tk.StringVar()
    ttk.Label(debug,textvariable=debug_summary,wraplength=970).pack(anchor='w',pady=(0,12))
    usage_table = ttk.Frame(debug); usage_table.pack(fill='both',expand=True)
    usage = ttk.Treeview(usage_table,columns=('symbol','share','checks','minutes','ai','used'),show='headings',height=6)
    for col,label,width in zip(usage['columns'],['หุ้น','ส่วนแบ่งโควตา','ตรวจราคา/วัน','ทุกกี่นาที*','AI ครั้ง/วัน','AI ใช้แล้ว'],[80,170,170,160,150,130]):
        usage.heading(col,text=label); usage.column(col,width=width)
    usage.pack(side='left',fill='both',expand=True)
    usage_scroll = ttk.Scrollbar(usage_table,orient='vertical',command=usage.yview)
    usage_scroll.pack(side='right',fill='y'); usage.configure(yscrollcommand=usage_scroll.set)
    ttk.Label(debug,text='* ความถี่เฉลี่ยเมื่อเปิดเครื่องตลอดช่วงตลาดเปิด 6.5 ชั่วโมง\nAI วิเคราะห์เมื่อมีเหตุการณ์เท่านั้น การอัปเดตราคาไม่ได้เรียก AI ทุกครั้ง\nระบบจำกัดข้อมูลราคาไว้ที่ 8 credits/นาที และ 760 credits/วัน',wraplength=910).pack(anchor='w',pady=12)

    def refresh_debug():
        p = debug_usage(settings)
        debug_summary.set(f"ติดตามหุ้นทั้งหมด {len(p['stocks'])} ตัว (หุ้นซ้ำใช้ราคาชุดเดียวกัน)\nแผนข้อมูลราคา: {p['planned_credits']} / {p['local_limit']} credits ต่อวัน รวมสำรองประวัติ {p['overhead_reserve']}\nAI ใช้แล้ว {p['ai_used']} / {p['ai_limit']} ครั้ง · สำรองรีวิวและข่าว {p['ai_reserve']} ครั้ง\nรอบนับวันที่ {p['utc_day']} ตาม UTC · นับเฉพาะการใช้งานในโปรแกรมนี้")
        usage.delete(*usage.get_children())
        for s in p['stocks']:
            usage.insert('', 'end', values=(s['symbol'],f"{s['weight_pct']:.2f}%",s['price_checks'],s['average_minutes'],s['ai_slots'],s['ai_used']))

    def show_stocks(event=None):
        if not ports.selection(): return
        p = catalog.entry(identity(),state['value'])
        stock_heading.set('2. หุ้นใน '+p['name'])
        stocks.delete(*stocks.get_children())
        path = catalog.profile_path(p['id'])
        holdings = json.loads(path.read_text(encoding='utf-8'))['holdings'] if path.exists() else []
        owned = {h['symbol'] for h in holdings}
        total = sum(s['priority'] for s in p['stocks'])
        from app.report import NAMES
        for s in p['stocks']:
            stocks.insert('', 'end',iid=s['symbol'],values=(s['symbol'],s.get('name') or NAMES.get(s['symbol'],'—'),
                'ถืออยู่' if s['symbol'] in owned else 'ติดตามอย่างเดียว',s['priority'],f"{s['priority']/total*100:.1f}%"))

    def reload_data(selected=None):
        state['value'],state['digest'] = catalog.read(),catalog.digest()
        ports.delete(*ports.get_children())
        total = sum(p['priority'] for p in state['value']['portfolios'] if p['stocks'])
        for p in state['value']['portfolios']:
            share = p['priority']/total*100 if p['stocks'] and total else 0
            ports.insert('', 'end',iid=p['id'],values=(p['name'],p['priority'],f'{share:.1f}%',
                'ใช้อยู่' if p['id']==state['value']['active_id'] else ''))
        ports.selection_set(selected or state['value']['active_id']); show_stocks(); refresh_debug()
        status.set('อ่านข้อมูลจากเครื่อง · การเปิดหน้านี้ไม่เรียก API หรือ AI')

    def edit_weight(stock=False):
        from copy import deepcopy
        chosen = identity()
        value = deepcopy(state['value'])
        target = catalog.entry(chosen,value)
        label = target['name']
        if stock:
            if not stocks.selection(): raise ValueError('เลือกหุ้นในตารางก่อนครับ')
            label = stocks.selection()[0]
            target = next(s for s in target['stocks'] if s['symbol']==label)
        result = simpledialog.askfloat('ความสำคัญในการติดตาม',
            f'{label}\nคะแนนมาก = ได้รับโควตาติดตามมากขึ้น\nคะแนนเท่ากัน = แบ่งเท่ากัน\n\nใส่คะแนนมากกว่า 0 ถึง 10,000 แล้วกด OK เพื่อบันทึก',
            initialvalue=target['priority'],minvalue=0.000001,maxvalue=10000,parent=root)
        if result is None: return
        target['priority'] = result
        catalog.save_priorities({p['id']:p['priority'] for p in value['portfolios']},
            {p['id']:{s['symbol']:s['priority'] for s in p['stocks']} for p in value['portfolios']},expected=state['digest'])
        reload_data(chosen); update_report()
        status.set('บันทึกความสำคัญของ '+label+' แล้ว · เริ่มใช้ในรอบตรวจถัดไป')

    def update_report():
        from app.report import write_report
        try:
            write_report(settings)
        except Exception:
            messagebox.showwarning('บันทึกข้อมูลแล้ว',
                'ข้อมูลถูกบันทึกแล้ว แต่หน้าเว็บยังอัปเดตไม่สำเร็จ กรุณาสร้างรายงานใหม่ภายหลัง',parent=root)

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

    def notes(stock=False):
        from app.notes_editor import open_editor
        ticker = None
        if stock:
            if not stocks.selection():
                raise ValueError('เลือกหุ้นจากตารางก่อนครับ')
            ticker = stocks.selection()[0]
        open_editor(root, settings, identity(), ticker, on_saved=lambda: (reload_data(identity()), update_report()))

    ttk.Button(port_tools,text='ปรับความสำคัญพอร์ต',command=lambda:guarded(edit_weight)).pack(side='left')
    ttk.Button(port_tools,text='เพิ่มพอร์ต',command=lambda:guarded(create)).pack(side='right')
    ttk.Button(port_tools,text='ใช้เป็นพอร์ตหลักใน LINE',command=lambda:guarded(select)).pack(side='right',padx=6)
    ttk.Button(stock_tools,text='ปรับความสำคัญหุ้น',command=lambda:guarded(lambda:edit_weight(stock=True))).pack(side='left')
    ttk.Button(stock_tools,text='คำอธิบายหุ้น',command=lambda:guarded(lambda:notes(stock=True))).pack(side='left',padx=6)
    ttk.Button(stock_tools,text='เพิ่มหุ้น',command=lambda:guarded(add)).pack(side='right')
    ttk.Button(stock_tools,text='แก้ยอดหุ้น / ต้นทุน',command=lambda:guarded(edit_totals)).pack(side='right',padx=6)
    ttk.Button(footer,text='แผน DCA พอร์ตนี้',command=lambda:guarded(dca)).pack(side='left')
    ttk.Button(footer,text='คำอธิบายพอร์ต',command=lambda:guarded(notes)).pack(side='left',padx=6)
    ttk.Button(footer,text='โหลดข้อมูลล่าสุด',command=lambda:guarded(lambda:reload_data(identity()))).pack(side='right')
    def explain_quota():
        messagebox.showinfo('วิธีแบ่งโควตา',
            '1. แบ่งตามคะแนนพอร์ต แล้วแบ่งตามคะแนนหุ้นในพอร์ตนั้น\n'
            '2. หุ้นซ้ำหลายพอร์ตใช้ราคาชุดเดียวกัน ช่วยประหยัดโควตา\n'
            '3. AI ในตารางคือจำนวนครั้งที่เรียกได้ ไม่ใช่จำนวนโทเค็น\n'
            '4. ถ้า AI เหลือ 0 ครั้ง ยังแจ้งเตือนด้วยข้อความพื้นฐานได้\n'
            '5. หุ้นคะแนนเท่ากันจะสลับสิทธิ์ตามวัน UTC\n\n'
            'เพดาน 760 credits/วัน เหลืออีก 40 จากลิมิต 800 เผื่อใช้นอกโปรแกรม',parent=root)
    ttk.Button(debug,text='อธิบายการแบ่งโควตา',command=explain_quota).pack(side='left')
    ttk.Button(debug,text='รีเฟรชตัวนับ (ไม่เรียก API)',command=lambda:guarded(refresh_debug)).pack(anchor='e')
    ports.bind('<<TreeviewSelect>>',show_stocks)
    guarded(reload_data)
    root.mainloop()


if __name__ == '__main__':
    main()

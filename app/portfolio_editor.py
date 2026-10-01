"""Local portfolio form. User enters totals after DCA; never places trades."""
import copy
import json
import tempfile
from datetime import datetime, UTC
from decimal import Decimal, InvalidOperation

from app.portfolio import validate_portfolio
from app.database import run_lock


def save_holdings(path, changes, now=None, expected_digest=None):
    now = now or datetime.now(UTC)
    with run_lock(path.with_suffix('.edit.lock')):
        original = path.read_text(encoding='utf-8')
        if expected_digest is not None:
            import hashlib
            if hashlib.sha256(original.encode()).hexdigest() != expected_digest:
                raise ValueError('ข้อมูลพอร์ตเปลี่ยนแล้ว กรุณาตรวจและสร้างคำขอใหม่')
        updated = json.loads(original)
        from app.portfolio import quantity_effective_at
        updated['quantity_as_of'] = quantity_effective_at(updated, path.parent)
        holdings = {h['symbol']: h for h in updated['holdings']}
        if set(changes) != set(holdings):
            raise ValueError('ข้อมูลหุ้นไม่ตรงกับพอร์ต กรุณาเปิดหน้าต่างใหม่')
        changed = False
        for symbol, fields in changes.items():
            h = holdings[symbol]
            try:
                quantity = Decimal(str(fields['quantity']))
                cost = None if fields.get('cost_basis_usd') in (None, '') else Decimal(str(fields['cost_basis_usd']))
            except (InvalidOperation, ValueError):
                raise ValueError('กรอกจำนวนหุ้นและต้นทุนเป็นตัวเลข') from None
            if not quantity.is_finite() or quantity <= 0 or cost is not None and (not cost.is_finite() or cost <= 0):
                raise ValueError('จำนวนหุ้นและต้นทุนต้องมากกว่า 0')
            quantity_changed = float(quantity) != h.get('quantity')
            if quantity_changed:
                updated['quantity_as_of'] = now.isoformat()
                changed = True
                h['quantity'] = float(quantity)
                if cost is None:
                    # Quantity is enough to preserve the actual DCA checkpoint.
                    # Do not pretend the old cost still belongs to the new share
                    # total: gains wait until the owner confirms the broker total.
                    h['cost_basis_pending'] = True
            if cost is not None and float(cost) != h.get('cost_basis_usd'):
                h['cost_basis_usd'] = float(cost)
                h.pop('cost_basis_pending', None)
                changed = True
            elif cost is not None and h.get('cost_basis_pending'):
                h.pop('cost_basis_pending', None)
                changed = True
        if not changed:
            return None
        updated['holdings_as_of'] = now.isoformat()
        validate_portfolio(copy.deepcopy(updated))
        # Unique, private recovery copy; never replace a previous backup.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='portfolio-profile-backup-', suffix='.json', delete=False) as backup:
            backup.write(original)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             suffix='.profile.tmp', delete=False) as handle:
                from pathlib import Path
                temporary = Path(handle.name)
                json.dump(updated, handle, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.replace(path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        # Keep a compact audit record of the totals the owner confirmed.  It
        # is not a trade ledger and never infers a purchase from a DCA amount.
        from app.portfolio_ledger import append_reconciliation
        append_reconciliation(path, json.loads(original), updated, now)
        return backup.name


def main(portfolio_id=''):
    import tkinter as tk
    from tkinter import ttk, messagebox
    from app.config import Settings
    from app.report import write_report
    root = tk.Tk()
    root.title('Stock Manager · บันทึกยอดพอร์ต')
    root.geometry('760x680')
    root.minsize(650, 600)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TLabel', font=('Leelawadee UI', 11))
    style.configure('TButton', font=('Leelawadee UI', 11), padding=10)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    try:
        settings = Settings.from_env()
        if settings.mock_mode:
            raise ValueError('เปิดโหมดข้อมูลจริงก่อนแก้พอร์ตส่วนตัว')
        from dataclasses import replace
        from app.portfolio_catalog import PortfolioCatalog
        settings = replace(settings,portfolio_id=portfolio_id)
        catalog = PortfolioCatalog(settings)
        path = catalog.profile_path()
        root.title('Stock Manager · '+catalog.selected()['name']+' · บันทึกยอด')
        raw = json.loads(path.read_text(encoding='utf-8'))
        import hashlib
        digest = hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
        validate_portfolio(copy.deepcopy(raw))
    except (ValueError, OSError):
        messagebox.showerror('ยังเปิดพอร์ตไม่ได้', 'ตรวจโหมดข้อมูลจริงและไฟล์ข้อมูลพอร์ต', parent=root)
        root.destroy()
        return
    ttk.Label(frame, text='อัปเดตยอดหลัง DCA', font=('Leelawadee UI', 19, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 12))
    ttk.Label(frame, text='ใส่ยอดรวมที่ถืออยู่ตอนนี้ ไม่ใช่จำนวนที่เพิ่งซื้อเพิ่ม\nต้นทุนคือเงินลงทุนรวมของหุ้นที่ยังถือ (USD) ไม่ใช่ราคาต่อหุ้น\nจะเว้นต้นทุนก็ได้: ระบบเก็บจำนวนหุ้นเป็นจุด DCA จริง แต่หยุดแสดงกำไรที่ยืนยันได้จนกว่าจะใส่ยอดต้นทุนจาก Dime', wraplength=680).grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 20))
    for column, text in enumerate(['หุ้น', 'จำนวนหุ้นรวม', 'ต้นทุนรวม USD จาก Dime']):
        ttk.Label(frame, text=text).grid(row=2, column=column, sticky='w', padx=8, pady=8)
    entries = {}
    for row, holding in enumerate(raw['holdings'], start=3):
        ttk.Label(frame, text=holding['symbol']).grid(row=row, column=0, sticky='w', padx=8, pady=7)
        quantity = tk.StringVar(value=str(holding.get('quantity') or ''))
        cost = tk.StringVar(value=str(holding.get('cost_basis_usd', '')))
        ttk.Entry(frame, textvariable=quantity, width=23).grid(row=row, column=1, padx=8, pady=7, sticky='ew')
        ttk.Entry(frame, textvariable=cost, width=23).grid(row=row, column=2, padx=8, pady=7, sticky='ew')
        entries[holding['symbol']] = (quantity, cost)
    row = len(entries) + 3
    ttk.Label(frame, text='บันทึกแล้วระบบจะสำรองข้อมูลเดิม และเริ่มช่วงติดตามสำหรับยอดใหม่นี้\nหน้าต่างนี้ไม่ดึงราคา ไม่เรียก AI และไม่ส่งคำสั่งซื้อขาย', wraplength=680).grid(row=row, column=0, columnspan=3, sticky='w', pady=18)
    def save():
        try:
            changes = {symbol: dict(quantity=q.get().strip(), cost_basis_usd=c.get().strip()) for symbol, (q, c) in entries.items()}
            backup = save_holdings(path, changes, expected_digest=digest)
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('ยังไม่บันทึก', str(exc), parent=root)
            return
        try:
            write_report(settings)
        except Exception:
            messagebox.showwarning('บันทึกยอดแล้ว', 'บันทึกยอดสำเร็จ แต่สร้างรายงานไม่สำเร็จ กรุณาเปิดพอร์ตใหม่', parent=root)
        else:
            messagebox.showinfo('เรียบร้อย', ('บันทึกยอดและสำรองข้อมูลเดิมแล้ว' if backup else 'ยอดไม่เปลี่ยนแปลง')+'\nโหลดหน้าพอร์ตใหม่เพื่อดูผล', parent=root)
        root.destroy()
    ttk.Button(frame, text='บันทึกยอดและสร้างรายงาน', command=save).grid(row=row+1, column=1, columnspan=2, sticky='e')
    frame.columnconfigure(1, weight=1)
    frame.columnconfigure(2, weight=1)
    root.mainloop()


if __name__ == '__main__':
    main()

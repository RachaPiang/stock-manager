"""Private portfolio catalog. Shared prices, independent holdings and plans.

The original profile is never moved. New profiles live below data/portfolios/.
Reading a legacy installation does not migrate or change any user data.
"""
import copy
import hashlib
import json
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import Stock, load_watchlist, positive
from app.database import run_lock

SYMBOL = re.compile(r'[A-Z][A-Z0-9.-]{0,11}')
IDENTITY = re.compile(r'(?:main|[a-f0-9]{12})')
MAX_SYMBOLS = 100


def atomic_json(path: Path, value, *, backup=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.stem+'-backup-',
                                         suffix='.json', delete=False) as handle:
            handle.write(path.read_bytes())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.json.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


class PortfolioCatalog:
    def __init__(self, settings):
        self.settings = settings
        self.directory = settings.database_path.parent
        self.path = self.directory/'portfolios.json'

    def read(self):
        if self.path.exists():
            result = json.loads(self.path.read_text(encoding='utf-8'))
        else:
            result = dict(version=1, active_id='main', portfolios=[dict(
                id='main', name='พอร์ตหลัก', priority=100,
                stocks=[dict(symbol=s.symbol, priority=100, target_price=s.target_price)
                        for s in load_watchlist(self.settings.watchlist_path)])])
        self.validate(result)
        return result

    @staticmethod
    def validate(value):
        if value.get('version') != 1 or not isinstance(value.get('portfolios'), list) or not value['portfolios']:
            raise ValueError('รูปแบบรายการพอร์ตไม่ถูกต้อง')
        if len(value['portfolios']) > 10:
            raise ValueError('รุ่นนี้รองรับสูงสุด 10 พอร์ต')
        seen, symbols = set(), set()
        for p in value['portfolios']:
            from app.investment_notes import validate_notes
            validate_notes(p.get('investment_notes', {}), 'portfolio')
            if not IDENTITY.fullmatch(p['id']) or p['id'] in seen:
                raise ValueError('รหัสพอร์ตไม่ถูกต้องหรือซ้ำ')
            seen.add(p['id'])
            if not isinstance(p['name'], str) or not 1 <= len(p['name'].strip()) <= 80:
                raise ValueError('ชื่อพอร์ตต้องมี 1–80 ตัวอักษร')
            positive(p['priority'], 'น้ำหนักพอร์ต')
            if p['priority'] > 10000:
                raise ValueError('น้ำหนักต้องไม่เกิน 10,000')
            own = set()
            for s in p['stocks']:
                from app.alert_policy import validate as validate_alerts
                validate_alerts(s.get('alert_settings', {}))
                validate_notes(s.get('investment_notes', {}), 'stock')
                if not SYMBOL.fullmatch(s['symbol']) or s['symbol'] in own:
                    raise ValueError('ชื่อหุ้นไม่ถูกต้องหรือซ้ำในพอร์ต')
                own.add(s['symbol']); symbols.add(s['symbol'])
                positive(s['priority'], 'น้ำหนักหุ้น')
                if s['priority'] > 10000:
                    raise ValueError('น้ำหนักต้องไม่เกิน 10,000')
                if s.get('target_price') is not None:
                    positive(s['target_price'], 'ราคาเป้าหมาย')
                if len(str(s.get('name', ''))) > 120:
                    raise ValueError('ชื่อบริษัทต้องไม่เกิน 120 ตัวอักษร')
        if value['active_id'] not in seen or 'main' not in seen:
            raise ValueError('ไม่พบพอร์ตที่เลือกหรือพอร์ตเดิม')
        if len(symbols) > MAX_SYMBOLS:
            raise ValueError(f'รุ่นนี้รองรับหุ้นไม่ซ้ำสูงสุด {MAX_SYMBOLS} ตัวเพื่อคงการตรวจได้ทุกวัน')

    def selected(self, catalog=None):
        value = catalog or self.read()
        identity = self.settings.portfolio_id or value['active_id']
        return self.entry(identity, value)

    @staticmethod
    def entry(identity, value):
        found = next((p for p in value['portfolios'] if p['id'] == identity), None)
        if found is None:
            raise ValueError('ไม่พบพอร์ตที่เลือก')
        return found

    def profile_path(self, identity=None):
        identity = identity or self.selected()['id']
        if not IDENTITY.fullmatch(identity):
            raise ValueError('รหัสพอร์ตไม่ถูกต้อง')
        if identity == 'main':
            return self.directory/'portfolio-profile.json'
        return self.directory/'portfolios'/identity/'portfolio-profile.json'

    def stocks(self, *, selected=False):
        catalog = self.read()
        portfolios = [self.selected(catalog)] if selected else catalog['portfolios']
        # Keep legacy targets; the global monitor does not duplicate a ticker.
        base = {s.symbol: s for s in load_watchlist(self.settings.watchlist_path)}
        result = {}
        for p in portfolios:
            rows = list(p['stocks'])
            path = self.profile_path(p['id'])
            if path.exists():
                holdings = json.loads(path.read_text(encoding='utf-8')).get('holdings', [])
                rows += [dict(symbol=h['symbol']) for h in holdings if h['symbol'] not in {s['symbol'] for s in rows}]
            for s in rows:
                result.setdefault(s['symbol'], base.get(s['symbol'], Stock(s['symbol'], target_price=s.get('target_price'))))
        return list(result.values())

    def digest(self):
        return hashlib.sha256(json.dumps(self.read(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _change(self, action, expected=None):
        self.directory.mkdir(parents=True, exist_ok=True)
        with run_lock(self.path.with_suffix('.lock')):
            if expected is not None and expected != self.digest():
                raise ValueError('รายการพอร์ตเปลี่ยนแล้ว กรุณาโหลดหน้าตั้งค่าใหม่')
            value = self.read()
            result = action(value)
            self.validate(value)
            atomic_json(self.path, value, backup=True)
            return result

    def create(self, name, priority=100):
        name = name.strip()
        positive(priority, 'น้ำหนักพอร์ต')
        if not 1 <= len(name) <= 80:
            raise ValueError('ชื่อพอร์ตต้องมี 1–80 ตัวอักษร')
        identity = uuid.uuid4().hex[:12]
        def action(value):
            value['portfolios'].append(dict(id=identity, name=name, priority=float(priority), stocks=[]))
            self.validate(value)
            atomic_json(self.profile_path(identity), dict(as_of=datetime.now(UTC).isoformat(), holdings=[],
                policy=dict(no_autonomous_trades=True), dca=dict(day=28, monthly_total=0,
                    per_stock=0, currency='THB', enabled=False)))
            return identity
        return self._change(action)

    def select(self, identity):
        def action(value):
            self.entry(identity, value)
            value['active_id'] = identity
        self._change(action)

    def save_priorities(self, portfolio_weights, stock_weights, *, expected=None):
        def action(value):
            if set(portfolio_weights) != {p['id'] for p in value['portfolios']}:
                raise ValueError('รายการพอร์ตเปลี่ยนแล้ว กรุณาโหลดใหม่')
            for p in value['portfolios']:
                p['priority'] = positive(portfolio_weights[p['id']], 'น้ำหนักพอร์ต')
                if set(stock_weights[p['id']]) != {s['symbol'] for s in p['stocks']}:
                    raise ValueError('รายการหุ้นเปลี่ยนแล้ว กรุณาโหลดใหม่')
                for s in p['stocks']:
                    s['priority'] = positive(stock_weights[p['id']][s['symbol']], 'น้ำหนักหุ้น')
        self._change(action, expected)

    def add_stock(self, identity, symbol, *, name='', quantity=None, cost_basis_usd=None, priority=100):
        symbol = symbol.strip().upper()
        if not SYMBOL.fullmatch(symbol):
            raise ValueError('กรอก ticker หุ้นสหรัฐ เช่น AAPL หรือ BRK.B')
        if (quantity is None) != (cost_basis_usd is None):
            raise ValueError('สำหรับหุ้นที่ถือ กรอกทั้งจำนวนหุ้นรวมและต้นทุนรวม USD หรือเว้นทั้งคู่เพื่อติดตามอย่างเดียว')
        priority = positive(priority, 'น้ำหนักหุ้น')
        if quantity is not None:
            quantity = positive(quantity, 'จำนวนหุ้น')
            cost_basis_usd = positive(cost_basis_usd, 'ต้นทุนรวม USD')
        def action(value):
            p = self.entry(identity, value)
            existing = next((s for s in p['stocks'] if s['symbol']==symbol), None)
            if existing and quantity is None:
                raise ValueError('หุ้นนี้อยู่ในพอร์ตแล้ว ใช้แก้ยอดแทนการเพิ่มซ้ำ')
            if existing is None:
                p['stocks'].append(dict(symbol=symbol, name=name.strip(), priority=priority))
            self.validate(value)
            if quantity is not None:
                path = self.profile_path(identity)
                with run_lock(path.with_suffix('.edit.lock')):
                    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(
                        as_of=datetime.now(UTC).isoformat(), holdings=[], policy={},
                        dca=dict(day=28, monthly_total=0, per_stock=0, currency='THB', enabled=False))
                    if symbol in {h['symbol'] for h in raw['holdings']}:
                        raise ValueError('มีจำนวนหุ้นนี้แล้ว กรุณาโหลดรายการใหม่')
                    before = copy.deepcopy(raw)
                    raw['holdings'].append(dict(symbol=symbol, quantity=quantity, cost_basis_usd=cost_basis_usd,
                        value_usd=None, gain_pct=None, snapshot_unavailable=True, name=name.strip()))
                    raw['holdings_as_of'] = raw['quantity_as_of'] = datetime.now(UTC).isoformat()
                    from app.portfolio import validate_portfolio
                    validate_portfolio(copy.deepcopy(raw))
                    atomic_json(path, raw, backup=True)
                    from app.portfolio_ledger import append_reconciliation
                    append_reconciliation(path, before, raw, datetime.now(UTC))
        self._change(action)

    def save_dca(self, identity, monthly_total, day=28):
        amount = positive(monthly_total, 'ยอด DCA ต่อเดือน', allow_zero=True)
        if isinstance(day, bool) or int(day) != float(day) or not 1 <= int(day) <= 28:
            raise ValueError('วันที่ DCA ต้องเป็น 1–28')
        with run_lock(self.path.with_suffix('.lock')):
            self.entry(identity, self.read())
            path = self.profile_path(identity)
            with run_lock(path.with_suffix('.edit.lock')):
                raw = json.loads(path.read_text(encoding='utf-8'))
                count = len(raw['holdings'])
                if amount and not count:
                    raise ValueError('เพิ่มหุ้นที่ถือก่อนเปิดแผน DCA')
                # This explicit button changes the plan, not a trade instruction.
                raw['dca'] = {**raw.get('dca', {}), 'day':int(day), 'monthly_total':amount,
                              'currency':'THB', 'per_stock':amount/count if count else 0, 'enabled':amount>0,
                              'plan_as_of':datetime.now(UTC).isoformat()}
                atomic_json(path, raw, backup=True)

    def save_alerts(self, identity, symbol, alerts, *, expected=None):
        from app.alert_policy import validate, FIELDS
        validate(alerts)
        effective = {key: alerts.get(key, getattr(self.settings, key)) for key in FIELDS}
        if effective['rsi_low'] >= effective['rsi_high']:
            raise ValueError('RSI ต่ำต้องน้อยกว่า RSI สูง รวมค่าเริ่มต้นที่ใช้ด้วย')
        def action(value):
            p = self.entry(identity, value)
            stock = next((s for s in p['stocks'] if s['symbol'] == symbol), None)
            if stock is None:
                raise ValueError('ไม่พบหุ้นนี้ กรุณาโหลดข้อมูลใหม่')
            stock['alert_settings'] = dict(alerts)
        self._change(action, expected)

    def save_notes(self, identity, notes, *, symbol=None, expected=None):
        from app.investment_notes import validate_notes
        validate_notes(notes, 'stock' if symbol else 'portfolio')
        def action(value):
            target = self.entry(identity, value)
            if symbol:
                target = next((s for s in target['stocks'] if s['symbol'] == symbol), None)
                if target is None:
                    raise ValueError('ไม่พบหุ้นนี้ในพอร์ตที่เลือก กรุณาโหลดข้อมูลใหม่')
            target['investment_notes'] = copy.deepcopy(notes)
        self._change(action, expected)

    def weights(self):
        """Normalize nested priorities; shared tickers accumulate interest once."""
        catalog = self.read()
        active = [p for p in catalog['portfolios'] if p['stocks']]
        total = sum(p['priority'] for p in active)
        symbols, portfolios = {}, {}
        for p in catalog['portfolios']:
            share = p['priority']/total if p in active else 0
            portfolios[p['id']] = share
            stock_total = sum(s['priority'] for s in p['stocks'])
            for s in p['stocks']:
                symbols[s['symbol']] = symbols.get(s['symbol'], 0)+share*s['priority']/stock_total
        # Reconciled legacy holdings can also be monitored even if not catalogued.
        for s in self.stocks():
            symbols.setdefault(s.symbol, min(symbols.values(), default=1))
        normalizer = sum(symbols.values())
        return portfolios, {s:w/normalizer for s,w in symbols.items()} if normalizer else {}

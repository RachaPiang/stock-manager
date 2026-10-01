"""Deterministic weighted budgets, not a prediction of actual AI token usage."""
import hashlib
import math
from datetime import UTC, datetime, time

from app.market import EARLY, NY, is_open
from app.market_state import DAILY_LIMIT
from app.portfolio_catalog import PortfolioCatalog


def allocate(total, weights, caps, *, minimum=0, seed=''):
    """Capped proportional allocation with rotating deterministic tie breaks."""
    result = {key: min(minimum, caps[key]) for key in weights}
    remaining = max(0, min(total, sum(caps.values()))-sum(result.values()))
    ideals = {key: float(value) for key, value in result.items()}
    eligible = {k for k in weights if caps[k] > ideals[k]}
    while remaining and eligible:
        weight_total = sum(weights[k] for k in eligible)
        shares = {k: remaining*weights[k]/weight_total for k in eligible}
        full = {k for k in eligible if shares[k] >= caps[k]-ideals[k]}
        if not full:
            for k in eligible:
                ideals[k] += shares[k]
            break
        for k in full:
            remaining -= caps[k]-ideals[k]
            ideals[k] = caps[k]
        eligible -= full
    result = {k: int(math.floor(v+1e-10)) for k,v in ideals.items()}
    count = min(total, sum(caps.values()))-sum(result.values())
    order = sorted(weights, key=lambda k: (-(ideals[k]-result[k]),
        hashlib.sha256((seed+':'+k).encode()).hexdigest()))
    for k in order:
        if count > 0 and result[k] < caps[k]:
            result[k] += 1
            count -= 1
    return result


def plan(settings, now=None):
    now = now or datetime.now(UTC)
    catalog = PortfolioCatalog(settings)
    value = catalog.read()
    portfolio_weights, weights = catalog.weights()
    count = len(weights)
    # History during the session, another history after the close, and closing
    # intraday recovery per distinct symbol,
    # and an additional reserve. Failed retries still obey the hard 760 cap.
    overhead = 3*count+20
    quote_budget = max(0, min(78*count, DAILY_LIMIT-overhead))
    quotas = allocate(quote_budget, weights, dict.fromkeys(weights, 78), minimum=1)
    day = now.astimezone(UTC).date().isoformat()
    reserve = min(2, settings.ai_max_calls_per_day) if len(value['portfolios']) > 1 else 0
    ai_budget = settings.ai_max_calls_per_day-reserve
    stock_cap = settings.ai_max_calls_per_stock_per_day
    port_caps = {p['id']: len(p['stocks'])*stock_cap for p in value['portfolios']}
    nonempty = {k:w for k,w in portfolio_weights.items() if w > 0}
    ai_port = allocate(ai_budget, nonempty, {k:port_caps[k] for k in nonempty}, seed=day)
    ai_stock = dict.fromkeys(weights, 0)
    for p in value['portfolios']:
        own = {s['symbol']:s['priority'] for s in p['stocks']}
        own_quotas = allocate(ai_port.get(p['id'], 0), own, dict.fromkeys(own, stock_cap), seed=day+p['id'])
        for symbol, quota in own_quotas.items():
            ai_stock[symbol] = min(stock_cap, ai_stock.get(symbol, 0)+quota)
    return dict(portfolios=[dict(id=p['id'], name=p['name'], weight_pct=portfolio_weights[p['id']]*100,
                ai_slots=ai_port.get(p['id'], 0)) for p in value['portfolios']],
        stocks=[dict(symbol=s, weight_pct=w*100, price_checks=quotas[s],
                     average_minutes=round(390/quotas[s],1) if quotas[s] else None,
                     ai_slots=ai_stock[s]) for s,w in sorted(weights.items(), key=lambda kv:(-kv[1],kv[0]))],
        price_budget=quote_budget, overhead_reserve=overhead, planned_credits=quote_budget+overhead,
        local_limit=DAILY_LIMIT, account_limit=800, ai_limit=settings.ai_max_calls_per_day,
        ai_reserve=reserve, utc_day=day, weighted=catalog.path.exists(),
        notes='Estimated full regular session; separate app/API use is not counted. AI slots are maximum attempts, not tokens. No event means no AI call. History/retry overhead can use the reserve; hard caps remain authoritative.')


def due_symbols(settings, now):
    """Spread each stock's daily allotment evenly across regular five-minute slots."""
    if not is_open(now):
        return set()
    local = now.astimezone(NY)
    slots = 42 if local.date().isoformat() in EARLY else 78
    opened = datetime.combine(local.date(), time(9,30), NY)
    slot = int((now-opened).total_seconds())//300
    result = set()
    for row in plan(settings, now)['stocks']:
        # Ceil instead of floor schedules the first check at market open.
        q = min(slots, row['price_checks'])
        if math.ceil((slot+1)*q/slots) > math.ceil(slot*q/slots):
            result.add(row['symbol'])
    return result


def ai_stock_limit(settings, symbol, now):
    if settings.mock_mode or not PortfolioCatalog(settings).path.exists():
        return settings.ai_max_calls_per_stock_per_day
    return next((r['ai_slots'] for r in plan(settings,now)['stocks'] if r['symbol']==symbol), 0)


def debug_usage(settings, now=None):
    import sqlite3
    now = now or datetime.now(UTC)
    result = plan(settings, now)
    path = settings.database_path
    used = {}
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_calls'").fetchone():
                used = dict(db.execute('SELECT symbol,count(*) FROM ai_calls WHERE day=? GROUP BY symbol', (result['utc_day'],)))
    for row in result['stocks']:
        row['ai_used'] = used.get(row['symbol'],0)
    result['ai_used'] = sum(used.values())
    return result

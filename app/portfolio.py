"""User holdings, explicit cost basis and valuations from saved market prices."""
import json
import hashlib
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def load_portfolio(directory: Path) -> dict | None:
    path = directory / "portfolio-profile.json"
    if not path.exists():
        return None
    profile = json.loads(path.read_text(encoding="utf-8"))
    validate_portfolio(profile)
    profile['quantity_as_of'] = quantity_effective_at(profile, directory)
    return profile


def quantity_effective_at(profile, directory):
    """Cost-only corrections do not restart the share ownership period."""
    if profile.get('quantity_as_of'):
        return profile['quantity_as_of']
    cutoff = profile.get('holdings_as_of', profile['as_of'])
    audit = directory / 'portfolio-reconciliation.jsonl'
    if not audit.exists():
        return cutoff
    try:
        records = [json.loads(line) for line in audit.read_text(encoding='utf-8').splitlines() if line.strip()]
        if not records or records[-1]['at'] != cutoff:
            return cutoff
        for record in reversed(records):
            if any(c['before']['quantity'] != c['after']['quantity'] for c in record['changes']):
                return record['at']
        return profile['as_of']
    except (ValueError, KeyError, TypeError, OSError):
        return cutoff


def validate_portfolio(profile: dict) -> dict:
    """Validate user data and attach calculated fields to a report-only copy."""
    total = Decimal("0")
    seen = set()
    for row in profile["holdings"]:
        value, gain = Decimal(str(row["value_usd"])), Decimal(str(row["gain_pct"]))
        quantity = row.get("quantity")
        if (not value.is_finite() or value <= 0 or not gain.is_finite()
                or gain <= -100 or row["symbol"] in seen
                or quantity is not None and (not Decimal(str(quantity)).is_finite() or Decimal(str(quantity)) <= 0)):
            raise ValueError("Invalid portfolio snapshot")
        seen.add(row["symbol"])
        total += value
        pending = row.get('cost_basis_pending', False)
        if not isinstance(pending, bool):
            raise ValueError('Invalid cost confirmation status')
        cost = Decimal(str(row['cost_basis_usd'])) if row.get('cost_basis_usd') is not None else value / (1 + gain / 100)
        if not cost.is_finite() or cost <= 0:
            raise ValueError('Invalid cost basis')
        row['cost_source'] = ('awaiting_broker_total' if pending else
                              'user_reported' if row.get('cost_basis_usd') is not None else
                              'inferred_from_snapshot')
        row['cost_reliable'] = not pending
        row["estimated_cost_usd"] = float(cost.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
        if row['estimated_cost_usd'] <= 0:
            raise ValueError('Cost basis must be at least one cent')
        row["estimated_gain_usd"] = float((value-cost).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
    if not seen:
        raise ValueError("Empty portfolio snapshot")
    profile["total_usd"] = float(total)
    profile["estimated_cost_total_usd"] = float(sum(Decimal(str(row["estimated_cost_usd"])) for row in profile["holdings"]))
    profile["estimated_gain_total_usd"] = float(total - Decimal(str(profile["estimated_cost_total_usd"])))
    profile["estimated_gain_pct"] = float((total / Decimal(str(profile["estimated_cost_total_usd"])) - 1) * 100)
    for row in profile["holdings"]:
        row["weight_pct"] = float(Decimal(str(row["value_usd"])) / total * 100)
    profile["allocations"] = {
        "sectors": _group_allocations(profile["holdings"], "sector", total),
        "exposures": _group_allocations(profile["holdings"], "exposure", total),
    }
    return profile


def holdings_version(profile: dict) -> str:
    basis = sorted((h['symbol'], h.get('quantity'), h['estimated_cost_usd']) for h in profile['holdings'])
    return hashlib.sha256(json.dumps([profile.get('holdings_as_of', profile.get('as_of')), basis], sort_keys=True).encode()).hexdigest()


def _group_allocations(holdings: list[dict], field: str, total: Decimal) -> list[dict]:
    groups: dict[str, Decimal] = {}
    for row in holdings:
        label = row.get(field) or "ยังไม่จัดกลุ่ม"
        groups[label] = groups.get(label, Decimal("0")) + Decimal(str(row["value_usd"]))
    return [{"label": label, "value_usd": float(value),
             "weight_pct": float(value / total * 100)}
            for label, value in sorted(groups.items(), key=lambda item: (-item[1], item[0]))]


def attach_live_valuation(profile: dict, quotes: dict[str, dict]) -> None:
    """Mutates report-only data: shares never change, latest saved quotes can."""
    total = Decimal("0")
    timestamps: list[str] = []
    complete = True
    cost_complete = True
    for holding in profile["holdings"]:
        for key in list(holding):
            if key.startswith('live_'):
                del holding[key]
        quote = quotes.get(holding["symbol"])
        if not quote or quote.get("price") is None or holding.get('quantity') is None or not quote.get('as_of'):
            complete = False
            continue
        price = Decimal(str(quote['price']))
        if not price.is_finite() or price <= 0:
            complete = False
            continue
        value = Decimal(str(holding["quantity"])) * Decimal(str(quote["price"]))
        holding["live_value_usd"] = float(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
        holding['live_price'] = float(price)
        holding['live_stale'] = quote.get('stale', False)
        holding['live_price_kind'] = quote.get('price_kind', 'quote')
        holding["live_as_of"] = quote["as_of"]
        if holding['cost_reliable']:
            cost = Decimal(str(holding['estimated_cost_usd']))
            holding['live_gain_usd'] = float((Decimal(str(holding['live_value_usd'])) - cost).quantize(Decimal('.01')))
            holding['live_gain_pct'] = float((value / cost - 1) * 100)
        else:
            cost_complete = False
        previous = quote.get('previous_close')
        if previous is not None and Decimal(str(previous)).is_finite() and Decimal(str(previous)) > 0:
            holding['live_previous_value_usd'] = float(Decimal(str(holding['quantity'])) * Decimal(str(previous)))
            holding['live_day_change_usd'] = float((value - Decimal(str(holding['live_previous_value_usd']))).quantize(Decimal('.01')))
        timestamps.append(quote["as_of"])
        total += Decimal(str(holding['live_value_usd']))
    if not complete or len(timestamps) != len(profile["holdings"]):
        profile["live"] = {"complete": False, "reason": "จำนวนหุ้นหรือราคาที่บันทึกยังไม่ครบทุกตัว", 'available': len(timestamps), 'required': len(profile['holdings'])}
        return
    live_holdings = [{**holding, "value_usd": holding["live_value_usd"]} for holding in profile["holdings"]]
    profile["live"] = {
        "complete": True,
        "total_usd": float(total.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)),
        "cost_usd": profile["estimated_cost_total_usd"] if cost_complete else None,
        "gain_usd": (float((total - Decimal(str(profile["estimated_cost_total_usd"]))).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
                     if cost_complete else None),
        "gain_pct": (float((total / Decimal(str(profile["estimated_cost_total_usd"])) - 1) * 100)
                     if cost_complete else None),
        "oldest_quote_as_of": min(timestamps),
        "newest_quote_as_of": max(timestamps),
        'stale': any(h.get('live_stale') for h in profile['holdings']),
        'mixed_times': len(set(timestamps)) > 1,
        'cost_complete': cost_complete,
        'cost_estimated': any(h['cost_source'] == 'inferred_from_snapshot' for h in profile['holdings']),
        'basis_version': holdings_version(profile),
        "allocations": {
            "sectors": _group_allocations(live_holdings, "sector", total),
            "exposures": _group_allocations(live_holdings, "exposure", total),
        },
    }
    for holding in profile["holdings"]:
        holding["live_weight_pct"] = float(Decimal(str(holding["live_value_usd"])) / total * 100)
        if holding['cost_reliable']:
            cost = Decimal(str(holding["estimated_cost_usd"]))
            value = Decimal(str(holding["live_value_usd"]))
            holding["live_gain_usd"] = float((value - cost).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
            holding["live_gain_pct"] = float((value / cost - 1) * 100)
    from app.fetcher import NY
    sessions = {datetime.fromisoformat(t).astimezone(NY).date() for t in timestamps}
    if len(sessions) == 1 and all('live_previous_value_usd' in h for h in profile['holdings']):
        previous_total = sum(Decimal(str(h['live_previous_value_usd'])) for h in profile['holdings'])
        profile['live'].update(day_change_usd=float((total-previous_total).quantize(Decimal('.01'))),
                               day_change_pct=float((total/previous_total-1)*100), session_date=str(next(iter(sessions))))


def portfolio_history(profile: dict, stocks: list[dict], intraday: bool = False) -> list[dict]:
    """Hypothetical fixed-current-shares basket, never actual historical holdings."""
    field, key = ('intraday', 'at') if intraday else ('bars', 'day')
    if any(h.get('quantity') is None for h in profile['holdings']):
        return []
    by_symbol = {stock["symbol"]: {bar[key]: bar["close"] for bar in stock.get(field, [])
                                    if bar.get("close") is not None} for stock in stocks}
    symbols = [holding["symbol"] for holding in profile["holdings"]]
    if not symbols or any(not by_symbol.get(symbol) for symbol in symbols):
        return []
    dates = set.intersection(*(set(by_symbol[symbol]) for symbol in symbols))
    quantities = {holding["symbol"]: Decimal(str(holding["quantity"])) for holding in profile["holdings"]}
    return [{"day": day, "value_usd": round(float(sum(quantities[symbol] * Decimal(str(by_symbol[symbol][day]))
                                                        for symbol in symbols)), 2)}
            for day in sorted(dates)]


def analyst_context(profile: dict, symbol: str, quote: dict | None = None) -> dict:
    """Send only the affected holding, plan and constraints; no full personal history."""
    holding = next((r for r in profile["holdings"] if r["symbol"] == symbol), None)
    if quote and holding:
        from copy import deepcopy
        single = deepcopy(profile)
        single['holdings'] = [deepcopy(holding)]
        single['estimated_cost_total_usd'] = holding['estimated_cost_usd']
        attach_live_valuation(single, {symbol: quote})
        holding = single['holdings'][0]
        holding.pop('live_weight_pct', None)  # A single-stock quote cannot determine the current portfolio weight.
    return {"as_of": profile["as_of"], "status": "user_reported_historical_snapshot_not_live",
            "holding": holding, "policy": profile["policy"], "dca": profile["dca"],
            'valuation_status': 'latest_saved_price' if holding and 'live_value_usd' in holding else 'snapshot_only',
            "limitations": "gain_pct/value_usd are the original snapshot. live_* fields use latest saved prices at live_as_of. Inferred cost is approximate; no broker sync, cash, FX, dividends, fees or transaction history. No verified fundamentals."}

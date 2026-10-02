"""Read-only saved evidence for alerts: never fetch from the price-check loop."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from app.investor_profile import analysis_context


def for_model(value):
    """Keep source names/dates; long URLs belong in buttons, not model tokens."""
    if isinstance(value, dict):
        if 'quarters' in value and 'metrics' in value:
            from app.company_checks import compact_company
            value = compact_company(value)
        return {k:for_model(v) for k,v in value.items() if k not in {'source_url','document_url'}}
    if isinstance(value, list):
        return [for_model(v) for v in value]
    return value


def source_buttons(payload):
    from app.web_news import news_reference
    context=payload.get('decision_context',{})
    items=list(context.get('news',[])[:3])
    for symbol,company in context.get('annual_fundamentals',{}).items():
        quarter = company.get('latest_quarter')
        metrics = quarter['metrics'] if quarter else company.get('metrics', {})
        metric=next((m for m in metrics.values() if m.get('source_url')),None)
        if metric:
            items.append(dict(symbol=symbol,title=('งบไตรมาสสิ้นสุด ' if quarter else 'งบรายปีสิ้นสุด ')+metric['end'],source_name='SEC EDGAR',
                              published_at=metric['filed'],source_url=metric['source_url']))
    return '\n'.join(news_reference(item) for item in items if item.get('source_url'))


def cached_fundamentals(settings, symbols, now):
    from app.fundamentals import current_period_only
    path = settings.database_path.parent/'fundamentals.sqlite3'
    if not path.exists():
        return {}
    result = {}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        for symbol in symbols:
            row = db.execute('SELECT fetched,payload,error FROM facts WHERE symbol=?', (symbol,)).fetchone()
            if not row or not row[1]:
                continue
            value = current_period_only(json.loads(row[1]))
            value['cache_age_days'] = (now.timestamp()-row[0])/86400
            value['refresh_error'] = row[2]
            result[symbol] = value
    return result


def context(settings, report, symbols, now):
    portfolio = report.get('portfolio') or {}
    live = portfolio.get('live', {})
    holdings = []
    for h in portfolio.get('holdings', []):
        if h['symbol'] not in symbols and 'MARKET' not in symbols:
            continue
        holdings.append({k: h.get(k) for k in ('symbol','quantity','live_weight_pct','live_value_usd',
            'live_gain_pct','live_day_change_usd','live_as_of','live_stale','thesis','cost_source','investment_notes')})
    news = []
    for item in report.get('news', []):
        try:
            at = datetime.fromisoformat(item['published_at'].replace('Z','+00:00'))
            if item['symbol'] in symbols and now-timedelta(days=14) <= at <= now:
                news.append({k: item.get(k) for k in ('symbol','title','excerpt','published_at','source_name','source_url')})
        except (ValueError, TypeError):
            continue
    fundamentals = cached_fundamentals(settings, symbols, now)
    from app.company_checks import checks
    stock_notes = {s['symbol']: s.get('investment_notes', {}) for s in report.get('stocks', [])}
    pnotes = portfolio.get('investment_notes', report.get('portfolio_info', {}).get('investment_notes', {}))
    thesis_checks = {symbol: checks(company, {**stock_notes.get(symbol, {}),
        'focus': stock_notes.get(symbol, {}).get('focus') or pnotes.get('focus', [])}, now)
        for symbol, company in fundamentals.items()}
    return dict(investor_profile=analysis_context(portfolio), dca=portfolio.get('dca'), policy=portfolio.get('policy'),
        investment_notes=portfolio.get('investment_notes', report.get('portfolio_info', {}).get('investment_notes', {})),
        monitored_stock_notes=[dict(symbol=s['symbol'], investment_notes=s['investment_notes'])
            for s in report.get('stocks', []) if s.get('investment_notes') and
            (s['symbol'] in symbols or 'MARKET' in symbols) and s['symbol'] not in {h['symbol'] for h in holdings}],
        portfolio_value_usd=live.get('total_usd'), portfolio_prices_complete=live.get('complete',False),
        portfolio_prices_stale=live.get('stale',True), portfolio_allocations=live.get('allocations'), holdings=holdings, news=news[:6],
        annual_fundamentals=fundamentals, thesis_checks=thesis_checks,
        limitations='Saved quotes may have mixed timestamps. News may be headlines only: do not infer causation. Annual and discrete-quarter SEC facts are labelled separately; missing quarter cash flows may be YTD-only. Financial observations do not confirm or reject an owner thesis by themselves. No new money budget confirmed.')

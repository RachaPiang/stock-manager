"""Private audit trail for confirmed holding totals, not brokerage trade execution."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

FILENAME = 'portfolio-reconciliation.jsonl'


def append_reconciliation(profile_path: Path, before: dict, after: dict, now: datetime) -> Path:
    """Append one atomically-written record only after the profile was validated.

    This intentionally records total quantity and total cost reported by the
    owner, not a fabricated fill price, exchange rate, dividend, or trade.
    """
    changes = []
    old = {row['symbol']: row for row in before['holdings']}
    for row in after['holdings']:
        previous = old.get(row['symbol'], {})
        old_values = {'quantity': previous.get('quantity'), 'cost_basis_usd': previous.get('cost_basis_usd'),
                      'cost_basis_pending': previous.get('cost_basis_pending', False)}
        new_values = {'quantity': row.get('quantity'), 'cost_basis_usd': row.get('cost_basis_usd'),
                      'cost_basis_pending': row.get('cost_basis_pending', False)}
        if old_values != new_values:
            changes.append({'symbol': row['symbol'], 'before': old_values, 'after': new_values})
    if not changes:
        raise ValueError('Cannot record an unchanged reconciliation')
    record = {'version': 1, 'type': 'holding_totals_confirmed', 'at': now.isoformat(),
              'changes': changes,
              'limitations': 'Owner-reported holding totals. Not a broker transaction ledger; excludes fills, FX, fees, dividends and cash. A pending cost total means profit is deliberately not calculated as confirmed.'}
    path = profile_path.parent/FILENAME
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    if existing and not existing.endswith('\n'):
        existing += '\n'
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     suffix='.ledger.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(existing)
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path

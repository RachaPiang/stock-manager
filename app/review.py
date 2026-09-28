"""Bounded weekly portfolio review. Only stored prices and saved, source-labelled news are used."""
from __future__ import annotations

import json
from datetime import datetime

from app.codex_client import CodexError, analyze
from app.config import Settings
from app.investor_profile import analysis_context, readiness_note
from app.voice import STYLE, answer_text


def period_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def review_payload(portfolio: dict, news: list[dict]) -> dict:
    live = portfolio.get("live", {})
    # One newest item per topic keeps all holdings represented without sending
    # repeated articles or long Google redirect URLs into the model context.
    selected, seen = [], set()
    for item in news:
        if item['symbol'] not in seen:
            selected.append(item)
            seen.add(item['symbol'])
    return {
        "portfolio_as_of": portfolio["as_of"],
        "holding_totals_confirmed_at": portfolio.get('holdings_as_of'),
        "live_valuation": live,
        "dca": portfolio["dca"],
        "policy": portfolio["policy"],
        "investor_profile": analysis_context(portfolio),
        "holdings": [{"symbol": h["symbol"], "weight_pct": h.get("live_weight_pct", h["weight_pct"]),
                      'snapshot_gain_pct': h['gain_pct'], 'latest_saved_gain_pct': h.get('live_gain_pct'),
                      'latest_saved_gain_usd': h.get('live_gain_usd'), 'price_as_of': h.get('live_as_of'),
                      'price_stale': h.get('live_stale', True), 'cost_source': h.get('cost_source'),
                      "thesis": h["thesis"]} for h in portfolio["holdings"]],
        "important_news": [{"symbol": item["symbol"], "published_at": item["published_at"],
                            "title": item["title"], "reason": item["reason"],
                            "source_name": item["source_name"], "excerpt": item.get('excerpt', '')[:400]}
                           for item in selected[:10]],
        "advice_guardrail": readiness_note(portfolio),
        "limitations": "Prices are latest saved quotes at their timestamps, not guaranteed live. snapshot_gain_pct is historical; latest_saved_gain_* are calculated now from user shares and cost basis. Inferred cost is approximate. Holding-total reconciliation is not a broker transaction ledger and is missing fills, dividends, cash, fees and FX. News consists of saved company releases and web RSS headlines. RSS headlines are not full article evidence. Do not invent facts or performance history.",
    }


def local_review(payload: dict) -> str:
    live = payload.get("live_valuation", {})
    value = f"${live['total_usd']:,.2f} USD" if live.get("complete") else "ยังประเมินไม่ได้ครบทุกหุ้น"
    news_count = len(payload["important_news"])
    return "\n".join([
        "รีวิวพอร์ตประจำสัปดาห์ · แม่แบบในเครื่อง (ไม่ได้เรียก AI)",
        f"มูลค่าประมาณจากราคาที่บันทึกล่าสุด: {value}",
        f"ข่าว/ประกาศสำคัญที่คัดไว้: {news_count} รายการ",
        "สิ่งที่ควรตรวจ: เหตุผลที่ถือยังสอดคล้องกับผลประกอบการและประกาศบริษัทหรือไม่",
        "ความเสี่ยง: น้ำหนักพอร์ตและข่าวที่คัดไว้เป็นภาพข้อมูล ณ เวลาบันทึก ไม่ใช่คำสั่งซื้อขาย",
        "ทางเลือก: ทำ DCA ตามแผนเดิมได้หากเงินสำรองและเหตุผลลงทุนไม่เปลี่ยน; หากข้อมูลใหม่ขัดกับเหตุผลเดิม ให้ทบทวนก่อนเพิ่มเงิน",
    ])


def create_review(settings: Settings, payload: dict) -> tuple[str, bool]:
    if settings.analyst_mode != "codex":
        return local_review(payload), False
    prompt = STYLE + '\nรีวิวพอร์ตระยะยาวจากข้อมูลต่อไปนี้:\n' + json.dumps(payload, ensure_ascii=False, allow_nan=False)
    try:
        answer = analyze(settings, prompt)
    except CodexError:
        return local_review(payload) + "\nหมายเหตุ: Codex ใช้งานไม่ได้ จึงใช้แม่แบบสำรอง", False
    return 'ผมทบทวนพอร์ตให้แล้วครับ\n\n'+answer_text(answer), True

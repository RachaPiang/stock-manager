"""Thai summaries: evidence is formatted by code, optional interpretation by AI."""

import json
from abc import ABC, abstractmethod

from app.config import Settings
from app.voice import STYLE, answer_text, thai_time


class AnalysisError(Exception):
    pass


def evidence_text(payload: dict) -> str:
    label = "[MOCK — ข้อมูลสมมุติ]" if payload["simulated"] else "[ติดตามพอร์ต]"
    values = payload["indicators"]
    def fmt(value) -> str:
        return "ข้อมูลไม่พอ" if value is None else f"{value:,.2f}"
    lines = [f"{label} {payload['symbol']}",
             f"ราคา ณ {thai_time(payload['quote_as_of'])} | {payload['source']}",
             '',
             "เหตุการณ์ที่พบ: " + "; ".join(e["title"] for e in payload["events"]),
             f"ตัวเลข/หลักฐาน: ราคา {fmt(payload['price'])} USD; ปิดก่อนหน้า {fmt(payload['previous_close'])} USD",
             f"เปลี่ยนแปลงรายวัน {values['daily_change_pct']:+.2f}%",
             f"SMA20 {fmt(values['sma20'])} | SMA50 {fmt(values['sma50'])} | RSI14 {fmt(values['rsi14'])}",
             f"ตัวชี้วัดจากแท่งรายวันที่ปิดแล้วถึง {payload['indicators_as_of']}"]
    if values["target_gap_pct"] is not None:
        lines.append(f"เทียบราคาเป้าหมาย {values['target_gap_pct']:+.2f}% (ราคา/เป้าหมาย - 1)")
    context = payload.get('decision_context', {})
    holding = next((h for h in context.get('holdings', []) if h['symbol']==payload['symbol']),None)
    if holding and holding.get('live_day_change_usd') is not None:
        lines.append(f"ผลต่อมูลค่าหุ้นที่เราถือวันนี้ ${holding['live_day_change_usd']:+,.2f}")
    if holding and holding.get('live_weight_pct') is not None and context.get('portfolio_prices_complete'):
        lines.append(f"น้ำหนักในพอร์ตตามราคาที่บันทึก {holding['live_weight_pct']:.2f}%")
    return "\n".join(lines)


class Analyst(ABC):
    uses_ai = False
    @abstractmethod
    def summarize(self, payload: dict) -> str:
        pass


class TemplateAnalyst(Analyst):
    """Deterministic local explanation, explicitly labelled as not an AI call."""
    def summarize(self, payload: dict) -> str:
        rules = {e["rule"] for e in payload["events"]}
        if rules & {"price_drop", "rsi_low", "below_target", "sma_cross_down"}:
            option = ("พิจารณาศึกษาจังหวะทยอยสะสมได้เมื่อพื้นฐานยังสอดคล้องกับเหตุผลที่ถือ "
                      "และมีงบ/น้ำหนักพอร์ตรองรับ; หากเหตุผลการถือเปลี่ยนควรทบทวนก่อนเพิ่มเงิน")
        else:
            option = ("พิจารณาตรวจน้ำหนักหุ้นเทียบแผนก่อนเพิ่มเงินหรือลดบางส่วน "
                      "หากน้ำหนักเกินเพดานจึงศึกษาทางเลือกปรับสมดุล")
        return evidence_text(payload) + "\n\n" + "\n\n".join([
            "อ่านจากตัวเลขเบื้องต้นครับ (แม่แบบในเครื่อง)",
            option,
            "สิ่งที่ควรดูคู่กันคือข่าวบริษัทและงบล่าสุดครับ รอบนี้มีข้อมูลราคา จึงยังสรุปไม่ได้ว่าถูกหรือแพง",
        ])


# Keep data checks internal; only material gaps need to appear in the reply.
INSTRUCTIONS = STYLE + ('\nใช้เหตุการณ์และตัวเลขที่คำนวณไว้แล้ว ไม่คำนวณซ้ำ ถ้าไม่มีข่าวแนบ อย่าเดาสาเหตุราคา '
    'ใช้ decision_context เชื่อมผลต่อพอร์ต เหตุผลถือ ข่าว และงบรายปีที่มี พร้อมชื่อแหล่งและวันที่ '
    'RSS อาจมีแค่หัวข่าว ห้ามเดาสาเหตุการขึ้นลง งบรายปีไม่ใช่มูลค่าเหมาะสมปัจจุบัน '
    'ราคาและน้ำหนักอาจต่างเวลากัน ห้ามเสนอวงเงินลงทุนเพิ่มเมื่อไม่ได้ยืนยันงบ แนะนำเงื่อนไขเพิ่ม รอ หรือทบทวนได้')


class OpenAIAnalyst(Analyst):
    uses_ai = True
    def __init__(self, settings: Settings):
        from openai import OpenAI
        self.client = OpenAI(api_key=settings.openai_api_key, timeout=settings.http_timeout, max_retries=0)
        self.model = settings.openai_model

    def summarize(self, payload: dict) -> str:
        try:
            from app.research_context import for_model
            response = self.client.responses.create(model=self.model, instructions=INSTRUCTIONS,
                input=json.dumps(for_model(payload), ensure_ascii=False, allow_nan=False), store=False, max_output_tokens=1400)
            output = response.output_text.strip()
            if response.status != "completed" or not output or len(output.encode("utf-16-le")) // 2 > 2400:
                raise AnalysisError("AI output incomplete or too long")
        except AnalysisError:
            raise
        except Exception:
            # API errors may contain sensitive request details. Do not persist raw exceptions.
            raise AnalysisError("OpenAI unavailable; use the labelled local template") from None
        return evidence_text(payload) + "\n\nบทวิเคราะห์ AI (ควรตรวจทาน):\n" + output


class GeminiAnalyst(Analyst):
    """Optional Google Gemini provider. Private portfolio context is opt-in."""
    uses_ai = True

    def __init__(self, settings: Settings):
        self.api_key = settings.gemini_api_key
        self.timeout = settings.http_timeout
        self.model = settings.gemini_model
        self.share_portfolio_context = settings.gemini_share_portfolio_context

    def summarize(self, payload: dict) -> str:
        try:
            from app.research_context import for_model
            data = for_model(payload)
            if not self.share_portfolio_context:
                # Keep holdings, investment thesis, cost basis, DCA and personal
                # profile local unless the owner explicitly opts in.
                data.pop("decision_context", None)
                data.pop("portfolio_context", None)
                data.pop("investor_profile", None)
            prompt = (INSTRUCTIONS + "\nสรุปเป็นภาษาไทย ใช้เฉพาะข้อมูลที่ให้มา ไม่สั่งซื้อขาย และแยกเหตุการณ์ หลักฐาน "
                      "สิ่งที่ควรตรวจเพิ่ม ความเสี่ยง และทางเลือกแบบมีเงื่อนไข:\n"
                      + json.dumps(data, ensure_ascii=False, allow_nan=False))
            import requests
            response = requests.post("https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": self.api_key},
                json={"model": self.model, "input": prompt, "store": False}, timeout=self.timeout)
            response.raise_for_status()
            result = response.json()
            output = str(result.get("output_text") or "").strip()
            if result.get("status") != "completed" or not output or len(output.encode("utf-16-le")) // 2 > 2400:
                raise AnalysisError("AI output incomplete or too long")
        except AnalysisError:
            raise
        except Exception:
            raise AnalysisError("Google Gemini unavailable; use the labelled local template") from None
        return evidence_text(payload) + "\n\nบทวิเคราะห์ AI (Google Gemini; ควรตรวจทาน):\n" + output


class CodexAnalyst(Analyst):
    uses_ai = True
    def __init__(self, settings: Settings):
        self.settings = settings

    def summarize(self, payload: dict) -> str:
        from app.codex_client import CodexError, analyze
        from app.research_context import for_model
        prompt = (INSTRUCTIONS + "\nตอบเป็น JSON ตาม schema เท่านั้น ใช้ check_more, risks, options "
                  "ห้ามเรียก tools อ่านไฟล์ ค้นเว็บ หรือเปลี่ยนแปลงสิ่งใด ใช้ข้อมูลต่อไปนี้เท่านั้น:\n"
                  + json.dumps(for_model(payload), ensure_ascii=False, allow_nan=False))
        try:
            answer = analyze(self.settings, prompt)
        except CodexError as exc:
            raise AnalysisError(str(exc)) from None
        return evidence_text(payload) + "\n\n" + answer_text(answer)

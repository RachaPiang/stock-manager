"""Local onboarding; masked secret entry and explicit, read-only API probes."""

import getpass
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import requests
from dotenv import dotenv_values, set_key

from app.config import ROOT, Settings, load_watchlist
from app.fetcher import TwelveDataProvider

FIELDS = {
    'line-webhook': [('LINE_CHANNEL_SECRET', 'LINE Channel secret (Basic settings, not access token)', True)],
    "stock": [("STOCK_API_KEY", "Twelve Data API key", True)],
    "openai": [("OPENAI_API_KEY", "OpenAI API key", True), ("OPENAI_MODEL", "Model name from your OpenAI account", False)],
    "gemini": [("GEMINI_API_KEY", "Google Gemini API key (AI Studio)", True)],
    "line": [("LINE_CHANNEL_ACCESS_TOKEN", "LINE channel access token", True), ("LINE_USER_ID", "Your LINE user ID (U...)", True)],
}


def save_environment(path: Path, changes: dict[str, str]) -> None:
    """Atomic update, preserving unrelated settings/comments; no secret backups."""
    if any("\n" in v or "\r" in v or "\x00" in v for v in changes.values()):
        raise ValueError("Settings must be single-line text")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".env.tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(path.read_text(encoding="utf-8") if path.exists() else "")
        if os.name != "nt":
            temporary.chmod(0o600)
        for name, value in changes.items():
            set_key(str(temporary), name, value, quote_mode="always")
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def setup_connections(service: str | None = None) -> int:
    if not sys.stdin.isatty():
        print("เปิด Setup Connections.cmd ในเครื่องเพื่อใส่ key แบบซ่อนข้อความ")
        return 2
    print("\nStock Manager · ตั้งค่าการเชื่อมต่อบนเครื่อง")
    print("กุญแจจะถูกเก็บใน .env ไม่มีการส่งข้อความหรือเรียก API ในขั้นตอนนี้")
    print("1. ราคาหุ้น (Twelve Data)   2. Codex ในเครื่อง   3. LINE   4. OpenAI API   5. Google Gemini (ทางเลือก)   0. ออก")
    if service is None:
        service = {"1": "stock", "2": "codex", "3": "line", "4": "openai", "5": "gemini"}.get(input("Choose 1/2/3/4/5 (0 = exit): ").strip())
    if service is None:
        return 0
    if service == "codex":
        from app.codex_client import CodexError, check_login
        try:
            check_login(Settings.from_env())
        except CodexError as exc:
            print(str(exc))
            return 1
        save_environment(ROOT / ".env", {"ANALYST_MODE": "codex"})
        print("เลือก Codex แล้ว · ใช้ ChatGPT login เดิม ไม่ต้องใส่ OpenAI API key")
        print("MOCK_MODE=true ยังใช้แม่แบบ · ใช้คำสั่ง test-codex เพื่อทดลอง AI จริงหนึ่งครั้ง")
        return 0
    if service == "gemini":
        path = ROOT / ".env"
        current = dotenv_values(path, interpolate=False)
        existing = os.getenv("GEMINI_API_KEY") or current.get("GEMINI_API_KEY")
        value = getpass.getpass("Google Gemini API key (Enter keeps configured value): " if existing else "Google Gemini API key: ").strip()
        if not value and existing:
            value = existing
        if not value or "\n" in value or "\r" in value:
            print("ยังไม่ได้กรอก key ที่ถูกต้อง จึงยังไม่บันทึก")
            return 2
        save_environment(path, {"GEMINI_API_KEY": value})
        print("บันทึก key แล้ว · ยังไม่สลับจาก Codex จนกว่าจะเปลี่ยน ANALYST_MODE เอง")
        print("หมายเหตุ: Free Tier อาจนำข้อมูลที่ส่งไปใช้ปรับปรุงผลิตภัณฑ์; อย่าเปิดส่งบริบทพอร์ตส่วนตัวจนกว่าจะยอมรับเงื่อนไข")
        if input("ใช้ Gemini แทน Codex ในการวิเคราะห์เหตุการณ์อัตโนมัติหรือไม่? [y/N]: ").strip().lower() == "y":
            save_environment(path, {"ANALYST_MODE": "gemini"})
            print("เลือก Gemini แล้ว · บริบทพอร์ตส่วนตัวถูกปิดไว้เป็นค่าเริ่มต้น")
        return 0
    path = ROOT / ".env"
    current = dotenv_values(path, interpolate=False)
    changes = {}
    for key, label, secret in FIELDS[service]:
        existing = os.getenv(key) or current.get(key)
        if os.getenv(key):
            print(f"{key}: system environment is set; it takes priority over .env")
        prompt = label + (" [Enter = keep configured value]: " if existing else ": ")
        value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        if key == 'LINE_CHANNEL_SECRET' and not re.fullmatch(r'[0-9a-fA-F]{32}', value or existing or ''):
            print('Channel secret ต้องเป็นอักษร/ตัวเลขฐาน 16 จำนวน 32 ตัว จาก Basic settings ของช่องเดียวกัน ไม่ใช่ access token จึงยังไม่บันทึก')
            return 2
        if key == 'LINE_CHANNEL_ACCESS_TOKEN' and re.fullmatch(r'[0-9a-fA-F]{32}', value or existing or ''):
            print('ค่านี้มีรูปแบบเหมือน Channel secret ไม่ใช่ access token; คัดลอกจาก Messaging API > Channel access token จึงยังไม่บันทึก')
            return 2
        if not value and existing:
            continue
        if not value:
            print("ยังไม่ได้กรอกข้อมูลครบ จึงยังไม่บันทึก")
            return 2
        if key == "LINE_USER_ID" and not re.fullmatch(r"U[0-9a-fA-F]{32}", value):
            print("LINE_USER_ID ต้องเป็น user ID จาก Developers Console ไม่ใช่ชื่อบัญชี LINE")
            return 2
        changes[key] = value
    if service == "stock":
        changes["STOCK_PROVIDER"] = "twelvedata"
        print("เปิดราคาจริงแล้ว การกดตรวจหุ้นครั้งต่อไปจะใช้โควตาของบัญชีคุณ")
        if input("Enable live prices on the next check? [y/N]: ").strip().lower() == "y":
            changes["MOCK_MODE"] = "false"
    elif service != 'line-webhook':
        label = "AI summaries (may use paid API credits)" if service == "openai" else "LINE alerts to your configured recipient"
        if input(f"Enable {label} on future checks? [y/N]: ").strip().lower() == "y":
            changes["ANALYST_MODE" if service == "openai" else "NOTIFIER_MODE"] = "openai" if service == "openai" else "line"
        print("หาก MOCK_MODE=true ระบบจะยังไม่เรียก AI หรือส่ง LINE")
    save_environment(path, changes)
    print("บันทึกแล้ว · เปิด Open Portfolio.cmd เพื่อใช้งาน หรือเลือกตรวจการเชื่อมต่อด้วย doctor --online")
    return 0


def doctor(settings: Settings, online: bool = False, service: str | None = None) -> int:
    """Without --online, inspect presence only. Probes never send LINE messages."""
    if service == 'line-webhook':
        ready = bool(re.fullmatch(r'[0-9a-fA-F]{32}', settings.line_channel_secret) and settings.line_token and settings.line_user_id)
        print('LINE webhook: ' + ('ตั้งค่าครบ; ต้องทดสอบ Verify และข้อความจริงผ่าน tunnel อีกครั้ง' if ready else 'ยังขาด token, user ID หรือ Channel secret; ใช้ Setup LINE Bot.cmd'))
        return 0 if ready else 1
    configured = {
        "stock": bool(settings.stock_api_key),
        "openai": bool(settings.openai_api_key and settings.openai_model),
        "gemini": bool(settings.gemini_api_key),
        "line": bool(settings.line_token and settings.line_user_id),
    }
    selected = [service] if service else ["stock", settings.configured_analyst_mode if settings.configured_analyst_mode in {"codex", "openai", "gemini"} else "openai", "line"]
    failures = 0
    print("โหมด:", "จำลอง" if settings.mock_mode else "ข้อมูลจริง")
    for name in selected:
        if name == "codex":
            from app.codex_client import CodexError, check_login
            try:
                check_login(settings)
                print("codex: พบโปรแกรมและล็อกอินด้วย ChatGPT แล้ว (ยังไม่ได้ทดสอบสร้างคำตอบ)")
            except CodexError as exc:
                failures += 1
                print(str(exc))
            continue
        if not configured[name]:
            print(f"{name}: ยังไม่มีข้อมูลเชื่อมต่อครบ")
            failures += 1
            continue
        if not online:
            print(f"{name}: ตั้งค่าแล้ว (ยังไม่ได้ตรวจ API)")
            continue
        try:
            if name == "stock":
                symbol = settings.stocks()[0].symbol
                snapshot = TwelveDataProvider(settings).fetch(symbol, datetime.now(UTC))
                print(f"stock: ดึง quote/history ของ {symbol} ได้ | วันที่ราคา {snapshot.as_of.isoformat()}")
            elif name == "openai":
                from openai import OpenAI
                with OpenAI(api_key=settings.openai_api_key, timeout=settings.http_timeout, max_retries=0) as client:
                    client.models.retrieve(settings.openai_model)
                print("openai: ตรวจ key และการมองเห็น model ผ่าน (ยังไม่ทดสอบสร้างคำตอบหรือยืนยัน billing)")
            elif name == "gemini":
                response = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}",
                    headers={"x-goog-api-key": settings.gemini_api_key}, timeout=settings.http_timeout)
                if response.status_code != 200:
                    raise ValueError("Gemini model probe failed")
                print("gemini: ตรวจ key และการมองเห็น model ผ่าน (ยังไม่ทดสอบสร้างคำตอบ; ตรวจโควตาใน AI Studio)")
            else:
                headers = {"Authorization": f"Bearer {settings.line_token}"}
                if not re.fullmatch(r"U[0-9a-fA-F]{32}", settings.line_user_id):
                    raise ValueError("Invalid user ID")
                for endpoint in ("info", f"profile/{settings.line_user_id}"):
                    response = requests.get(f"https://api.line.me/v2/bot/{endpoint}", headers=headers, timeout=settings.http_timeout)
                    if response.status_code != 200:
                        raise ValueError("LINE probe failed")
                print("line: ตรวจ token และโปรไฟล์ผู้รับผ่าน (ยังไม่ได้ส่งข้อความ)")
        except Exception:
            failures += 1
            print(f"{name}: ตรวจไม่ผ่าน · ตรวจ key, สิทธิ์, โควตา และเครือข่าย (ไม่แสดงข้อมูลลับ)")
    return 1 if failures else 0

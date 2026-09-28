"""Invoke the installed Codex CLI using its own ChatGPT login, never copied tokens."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import ROOT, Settings


class CodexError(Exception):
    """Redacted failure suitable for local logs."""


def find_codex(configured_path: str = "") -> str | None:
    if configured_path:
        path = Path(configured_path).expanduser()
        if path.is_file() and (os.name != "nt" or path.suffix.lower() == ".exe"):
            return str(path.resolve())
        return None
    candidate = shutil.which("codex")
    if candidate and (os.name != "nt" or Path(candidate).suffix.lower() == ".exe"):
        return candidate
    # The desktop app's bundled executable is not always on Explorer's PATH.
    local_appdata = os.getenv("LOCALAPPDATA")
    if os.name == "nt" and local_appdata:
        folder = Path(local_appdata) / "OpenAI/Codex/bin"
        candidates = list(folder.glob("*/codex.exe"))
        if candidates:
            return str(max(candidates, key=lambda p: p.stat().st_mtime))
    return None


def child_environment() -> dict[str, str]:
    # Preserve OS/login configuration, but never pass stock/LINE/Platform keys.
    excluded = {"STOCK_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_USER_ID", "LINE_CHANNEL_SECRET"}
    return {key: value for key, value in os.environ.items() if key.upper() not in excluded}


def _run(args: list[str], *, timeout: float, cwd: Path, prompt: str | None = None):
    try:
        return subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=cwd, env=child_environment(),
                              shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise CodexError("Codex ไม่พร้อมใช้งานหรือเกินเวลาที่กำหนด") from None


def check_login(settings: Settings) -> None:
    executable = find_codex(settings.codex_cli_path)
    if not executable:
        raise CodexError("ไม่พบ Codex CLI · ตรวจการติดตั้งหรือ CODEX_CLI_PATH")
    result = _run([executable, "login", "status"], timeout=20, cwd=ROOT)
    status = (result.stdout + result.stderr).lower()
    if result.returncode != 0 or "logged in using chatgpt" not in status:
        raise CodexError("Codex ยังไม่ยืนยันการล็อกอินแบบ ChatGPT · รัน codex login ด้วยบัญชี Windows ที่ใช้โปรแกรม")


SCHEMA = {
    "type": "object", "properties": {name: {"type": "string"} for name in ("check_more", "risks", "options")},
    "required": ["check_more", "risks", "options"], "additionalProperties": False,
}


def analyze(settings: Settings, prompt: str) -> dict[str, str]:
    check_login(settings)
    executable = find_codex(settings.codex_cli_path)
    (ROOT / "data").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-analysis-", dir=ROOT / "data") as folder:
        work = Path(folder)
        schema = work / "schema.json"
        output = work / "answer.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        args = [executable, "--ask-for-approval", "never", "exec", "--ignore-user-config",
                "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "--color", "never",
                "--config", "project_doc_max_bytes=0", "--config", 'web_search="disabled"',
                "--config", 'model_reasoning_effort="medium"',
                "--output-schema", str(schema), "--output-last-message", str(output)]
        # Only a text response is needed. Do not load personal plugins, tools or memories.
        for feature in ("shell_tool", "apps", "plugins", "hooks", "memories", "multi_agent",
                        "browser_use", "computer_use", "code_mode_host", "image_generation",
                        "view_image", "skill_search", "sleep_tool"):
            args.extend(["--disable", feature])
        args.extend(["--enable", "skip_host_skill_discovery"])
        if settings.codex_model:
            args.extend(["--model", settings.codex_model])
        args.append("-")  # Pass portfolio data via stdin, not command-line arguments.
        result = _run(args, timeout=settings.codex_timeout, cwd=work, prompt=prompt)
        if result.returncode != 0 or not output.is_file():
            raise CodexError("Codex วิเคราะห์ไม่สำเร็จ · ตรวจการล็อกอิน โควตา หรือการเชื่อมต่อ")
        try:
            answer = json.loads(output.read_text(encoding="utf-8"))
            if not isinstance(answer, dict) or set(answer) != set(SCHEMA["required"]):
                raise ValueError("Invalid schema")
            if any(not isinstance(value, str) or not value.strip() for value in answer.values()):
                raise ValueError("Empty answer")
            if sum(len(value.encode("utf-16-le")) // 2 for value in answer.values()) > 2400:
                raise ValueError("Answer too long")
        except (ValueError, OSError, UnicodeError):
            raise CodexError("รูปแบบคำตอบ Codex ไม่ครบหรือยาวเกินกำหนด") from None
        return answer

"""Deterministic intent rules (Thai + English).

Cheap, offline, and auditable. The Reasoning Engine tries these before spending a model
call, which means the §89 demo path costs nothing and works with the network down. Anything
these rules do not recognise with confidence falls through to the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from thursday_shared.enums import IntentKind
from thursday_shared.models import Intent

#: Application aliases the user is likely to speak, mapped to a canonical name.
APP_ALIASES: dict[str, str] = {
    "chrome": "chrome",
    "โครม": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "firefox": "firefox",
    "notepad": "notepad",
    "โน้ตแพด": "notepad",
    "excel": "excel",
    "เอ็กเซล": "excel",
    "word": "winword",
    "เวิร์ด": "winword",
    "calculator": "calc",
    "เครื่องคิดเลข": "calc",
    "calc": "calc",
    "terminal": "terminal",
    "เทอร์มินอล": "terminal",
    "explorer": "explorer",
    "file explorer": "explorer",
    "obsidian": "obsidian",
    "vscode": "code",
    "vs code": "code",
    "code": "code",
    "spotify": "spotify",
    "line": "line",
    "discord": "discord",
}

_STOP = re.compile(r"(?i)^\s*(thursday[,\s]*)?(stop|หยุด|ยกเลิก|cancel|abort|เงียบ)\b")
# Thai is written without spaces, so the verb may sit flush against its object
# ("เปิดโครม"); English always needs the separator. Hence the two-branch prefixes.
# "run" is an app verb and a shell verb both. The lookahead keeps "run shell command whoami"
# from becoming an application named "shell command whoami" — §96's rule that a broad
# instruction must not be narrowed into the wrong action. It fires only on a *compound*
# object, so "open terminal" and "run bash" still name applications, which they are.
# Unrecognised here, the sentence falls through to the model, which is the honest outcome.
_SHELL_OBJECT = r"(?!(?:shell|command|cmd|powershell|bash)\b\s+\S)"
_OPEN_APP = re.compile(
    r"(?i)(?:\b(?:open|launch|start|run)\s+|(?:เปิด|เรียก|สั่งเปิด)\s*)"
    r"(?:app\s+|โปรแกรม\s*|แอป\s*)?" + _SHELL_OBJECT + r"(?P<target>[\w฀-๿ .+-]{1,40}?)"
    r"(?:\s*(?:\bon\b|บน|ที่|ใน)\s*(?P<device>[\w฀-๿-]{2,30}))?\s*$"
)
_CLOSE_APP = re.compile(
    r"(?i)(?:\b(?:close|quit|kill)\s+|\bปิด\s*)"
    r"(?:app\s+|โปรแกรม\s*)?"
    r"(?P<target>[\w฀-๿ .+-]{1,40}?)"
    r"(?:\s*(?:\bon\b|บน|ที่)\s*(?P<device>[\w฀-๿-]{2,30}))?\s*$"
)
_OPEN_FILE = re.compile(r"(?i)(?:\bopen\s+(?:file|ไฟล์)|เปิด\s*(?:file|ไฟล์))\s*(?P<target>\S+)")
# Two word orders: English puts the verb first, Thai trails the question particle.
_DEVICE_STATUS_EN = re.compile(
    r"(?i)\bis\s+(?:the\s+)?(?P<device>[\w -]{2,30}?)\s+(?:still\s+)?"
    r"(?:on|online|up|running|awake)\b"
)
_DEVICE_STATUS_TH = re.compile(
    r"(?P<device>[\w฀-๿-]{2,30}?)\s*(?:ยัง|เปิด)\s*(?:[\w฀-๿]{0,8}?)"
    r"(?:เปิดอยู่|ออนไลน์|อยู่ไหม|ไหม|หรือเปล่า|มั้ย|รึเปล่า)"
)
# An instruction to remember, as opposed to a question about what was remembered. The
# imperative anchor at the start is what keeps "do you remember the file?" out of this rule
# and in _RECALL below, where it belongs.
_REMEMBER = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:remember|note|keep in mind|don'?t forget)"
    r"(?:\s+(?:that|this)\b)?\s*[:,]?\s*(?P<fact>.+)$"
)
#: "ไว้" is required, not optional: without it, "จำได้ไหม…" ("do you remember…") reads as an
#: instruction to store the rest of the question.
_REMEMBER_TH = re.compile(
    r"^\s*(?:ช่วย)?จำ(?:เอา)?ไว้(?:นะ|ด้วย|หน่อย|ครับ|ค่ะ)*\s*(?:ว่า)?\s*[:,]?\s*(?P<fact>.+)$"
)
#: Wrapped form: "จำ <fact> ไว้".
_REMEMBER_TH_WRAPPED = re.compile(r"^\s*(?:ช่วย)?จำ\s*(?P<fact>.+?)\s*(?:เอา)?ไว้(?:นะ|ด้วย)?\s*$")
#: A statement about the owner rather than about the world goes to the preference layer,
#: where it outranks anything an agent later infers (PART 76).
#: Thai routinely drops the subject, so a bare "ชอบ…" is the speaker's own preference; in
#: English the first person has to be explicit, or "the dean prefers PDF" — a fact about
#: someone else — would be filed as something the owner wants.
_PREFERENCE_MARKER = re.compile(
    r"(?i)(ชอบ|ไม่ชอบ|ปกติ(?:ผม|ฉัน|เรา)?|เสมอ|ทุกครั้ง|"
    r"\bi (?:like|prefer|hate|always|never|usually|want|need)\b|"
    r"\bmy (?:preferred|usual|default)\b|\bi'?m (?:always|never)\b|"
    r"\b(?:don'?t|do not) (?:like|want)\b)"
)

_RECALL = re.compile(
    r"(?i)(what did (?:we|i)|เมื่อ(?:อาทิตย์|สัปดาห์|เดือน|วาน)|จำได้ไหม|do you remember|"
    r"เคยทำ|ที่แล้วเรา|last time|ครั้งที่แล้ว|อยู่ไหน|where (?:is|did i put))"
)
_ANALYZE = re.compile(
    r"(?i)(analy[sz]e|วิเคราะห์|summari[sz]e|สรุป|report on|ทำรายงาน|เปรียบเทียบ|compare)"
)
_SEARCH = re.compile(r"(?i)(search|ค้นหา|หาข้อมูล|look up|research|ค้นคว้า|google)")
_STATUS = re.compile(r"(?i)(status|สถานะ|ไปถึงไหน|progress|คืบหน้า|what.*(working on|กำลังทำ)|pending)")
_APPROVE = re.compile(r"(?i)^\s*(approve|yes,? do it|อนุมัติ|ตกลง|ทำเลย|ยืนยัน|confirm)\b")
_SCREENSHOT = re.compile(r"(?i)(screenshot|จับภาพหน้าจอ|แคปหน้าจอ|capture the screen)")
_SYSINFO = re.compile(
    r"(?i)(system info|ข้อมูลเครื่อง|สเปคเครื่อง|สถานะเครื่อง|เครื่อง(?:นี้)?เป็นยังไง|"
    r"how much (ram|memory|disk)|disk space|พื้นที่ดิสก์)"
)
_LIST_DIR = re.compile(r"(?i)\b(?:list|ls|ดู(?:ไฟล์)?ใน|show files in)\s+(?P<target>[^\s]+)")

_THIS_DEVICE = re.compile(r"(?i)(this (?:machine|device|pc|computer)|เครื่องนี้|ที่นี่|here)")


@dataclass(frozen=True)
class RuleMatch:
    intent: Intent

    @property
    def confident(self) -> bool:
        return self.intent.confidence >= 0.75


def _normalise_app(raw: str) -> str:
    cleaned = raw.strip().strip("\"'.,!? ").removesuffix("ครับ").removesuffix("ค่ะ").strip().lower()
    return APP_ALIASES.get(cleaned, cleaned)


def _strip_wake_word(text: str, wake_word: str = "thursday") -> str:
    pattern = re.compile(rf"(?i)^\s*{re.escape(wake_word)}\s*[,:]?\s*")
    return pattern.sub("", text).strip()


def parse(text: str, *, wake_word: str = "thursday") -> RuleMatch | None:
    """Return a confident intent, or None to let the model decide."""
    body = _strip_wake_word(text, wake_word)
    if not body:
        return None

    if _STOP.match(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.STOP,
                objective="stop current work",
                confidence=0.97,
                rationale="explicit stop command",
            )
        )
    if _APPROVE.match(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.APPROVE,
                objective="approve the pending request",
                confidence=0.9,
                rationale="explicit approval",
            )
        )

    status_match = _DEVICE_STATUS_EN.search(body) or _DEVICE_STATUS_TH.search(body)
    if status_match:
        device = status_match.group("device").strip()
        return RuleMatch(
            Intent(
                kind=IntentKind.STATUS,
                objective=f"report the status of {device}",
                entities={"subject": "device", "device_name": device},
                target_device=device,
                confidence=0.85,
                rationale="device status question",
            )
        )
    if _STATUS.search(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.STATUS,
                objective="report current work status",
                confidence=0.8,
                rationale="status question",
            )
        )

    if _SCREENSHOT.search(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.COMPUTER_ACTION,
                objective="capture the screen",
                entities={"action": "screen.capture"},
                target_device=_device_hint(body),
                confidence=0.88,
                rationale="screenshot request",
            )
        )
    if _SYSINFO.search(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.COMPUTER_ACTION,
                objective="read system information",
                entities={"action": "system.info"},
                target_device=_device_hint(body),
                confidence=0.85,
                rationale="system info request",
            )
        )
    if match := _LIST_DIR.search(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.FILE_ACTION,
                objective=f"list {match.group('target')}",
                entities={"action": "file.list", "path": match.group("target")},
                target_device=_device_hint(body),
                confidence=0.82,
                rationale="directory listing",
            )
        )
    if match := _OPEN_FILE.search(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.FILE_ACTION,
                objective=f"open {match.group('target')}",
                entities={"action": "file.open", "path": match.group("target")},
                target_device=_device_hint(body),
                confidence=0.84,
                rationale="file open request",
            )
        )
    if match := _CLOSE_APP.search(body):
        app = _normalise_app(match.group("target"))
        if app and not _looks_like_sentence(app):
            return RuleMatch(
                Intent(
                    kind=IntentKind.COMPUTER_ACTION,
                    objective=f"close {app}",
                    entities={"action": "app.close", "app": app},
                    target_device=match.group("device") or _device_hint(body),
                    confidence=0.86,
                    rationale="application close request",
                )
            )
    if match := _OPEN_APP.search(body):
        app = _normalise_app(match.group("target"))
        if app and not _looks_like_sentence(app):
            return RuleMatch(
                Intent(
                    kind=IntentKind.COMPUTER_ACTION,
                    objective=f"open {app}",
                    entities={"action": "app.open", "app": app},
                    target_device=match.group("device") or _device_hint(body),
                    confidence=0.9,
                    rationale="application open request",
                )
            )

    if (remember := _match_remember(body)) is not None:
        fact, layer = remember
        return RuleMatch(
            Intent(
                kind=IntentKind.MEMORY_WRITE,
                objective=f"remember: {fact}",
                entities={"fact": fact, "layer": layer},
                confidence=0.92,
                rationale="an explicit instruction to remember something",
            )
        )
    if _RECALL.search(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.MEMORY_RECALL,
                objective=body,
                confidence=0.78,
                rationale="memory recall question",
            )
        )
    if _ANALYZE.search(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.DATA_ANALYSIS,
                objective=body,
                needs_plan=True,
                confidence=0.78,
                rationale="analysis request",
            )
        )
    if _SEARCH.search(body):
        return RuleMatch(
            Intent(
                kind=IntentKind.SEARCH,
                objective=body,
                needs_plan=True,
                confidence=0.76,
                rationale="research request",
            )
        )
    return None


def _match_remember(body: str) -> tuple[str, str] | None:
    """Pull the fact out of "remember that …" / "จำ … ไว้", and pick its layer.

    Returns ``None`` rather than guessing when the sentence is only the verb — "remember?"
    is a question, and answering it by storing the word "remember" would be worse than
    admitting the rules did not understand.
    """
    for pattern in (_REMEMBER_TH_WRAPPED, _REMEMBER_TH, _REMEMBER):
        match = pattern.match(body)
        if match is None:
            continue
        fact = match.group("fact").strip(" \t:,.!?")
        if len(fact) < 3:
            return None
        return fact, ("preference" if _PREFERENCE_MARKER.search(fact) else "semantic")
    return None


def _device_hint(text: str) -> str | None:
    if _THIS_DEVICE.search(text):
        return "this"
    match = re.search(r"(?i)\b(?:on|บน|ที่|ไปที่|ส่งไป)\s+(?P<device>[\w฀-๿-]{2,30})", text)
    return match.group("device") if match else None


def _looks_like_sentence(candidate: str) -> bool:
    """Guard against 'open the report and email it to the dean' becoming an app name."""
    return len(candidate.split()) > 3 or len(candidate) > 32

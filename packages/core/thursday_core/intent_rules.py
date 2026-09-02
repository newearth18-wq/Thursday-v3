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
#: A statement about *how work should be done*, as opposed to a fact about the world.
#: "reports start with a summary table" is not trivia to recall later; it is an instruction
#: for next time, and filing it as semantic means it is never applied to anything.
_PROCEDURAL_MARKER = re.compile(
    r"(?i)("
    r"ให้(?:สรุป|ทำ|เขียน|ใส่|จัด|แนบ|เริ่ม|ตรวจ)|"  # "ให้สรุปเป็นตาราง" — do it this way
    r"แบบนี้|ทุกครั้งที่|เวลาทำ|ก่อนเสมอ|ต้อง(?:สรุป|มี|ใส่|แนบ)|"
    r"\balways (?:start|begin|include|attach|use|put|check|sort|group)\b|"
    r"\bwhen (?:i|we|you) (?:make|write|do|create|prepare|send)\b|"
    r"\bthese (?:reports?|documents?|emails?|files?)\b|"
    r"\bshould (?:start|begin|include|be) \b"
    r")"
)

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
#: File types the owner names by their everyday word rather than an extension. Each maps to
#: every glob that word actually means — "Excel" is .xlsx *and* .xls, and answering with only
#: one of them would quietly miss the file they were looking for.
FILE_TYPE_GLOBS: dict[str, list[str]] = {
    "excel": ["*.xlsx", "*.xls", "*.xlsm"],
    "เอ็กเซล": ["*.xlsx", "*.xls", "*.xlsm"],
    "spreadsheet": ["*.xlsx", "*.xls", "*.csv"],
    "word": ["*.docx", "*.doc"],
    "เวิร์ด": ["*.docx", "*.doc"],
    "pdf": ["*.pdf"],
    "powerpoint": ["*.pptx", "*.ppt"],
    "ppt": ["*.pptx", "*.ppt"],
    "csv": ["*.csv"],
    "image": ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"],
    "รูป": ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"],
    "รูปภาพ": ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"],
    "video": ["*.mp4", "*.mov", "*.mkv"],
    "วิดีโอ": ["*.mp4", "*.mov", "*.mkv"],
    "text": ["*.txt", "*.md"],
    "zip": ["*.zip", "*.7z", "*.rar"],
}

#: Folders the owner names rather than paths. Everything resolves under the home directory,
#: so a search cannot be steered outside the node's allowed roots by naming a folder.
FOLDER_ALIASES: dict[str, str] = {
    "downloads": "~/Downloads",
    "ดาวน์โหลด": "~/Downloads",
    "desktop": "~/Desktop",
    "หน้าจอ": "~/Desktop",
    "เดสก์ท็อป": "~/Desktop",
    "documents": "~/Documents",
    "เอกสาร": "~/Documents",
    "pictures": "~/Pictures",
    "รูปภาพ": "~/Pictures",
    "music": "~/Music",
    "videos": "~/Videos",
    "home": "~",
}

#: "Forget X" — remove what is already stored. Distinct from _SUPPRESS below, which is
#: about the exchange happening right now.
_FORGET = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:forget|delete|remove|erase)\s+"
    r"(?:everything\s+|all\s+|what\s+i\s+said\s+|the\s+memory\s+|that\s+)?"
    r"(?:you\s+know\s+)?(?:about\s+|regarding\s+|to\s+do\s+with\s+)?(?P<subject>.+)$"
)
_FORGET_TH = re.compile(
    r"^\s*(?:ช่วย)?ลืม(?:ข้อมูล|เรื่อง|ที่)?\s*(?:เรื่อง|เกี่ยวกับ|ที่ผมบอก(?:ว่า)?)?\s*(?P<subject>.+?)"
    r"(?:\s*(?:ด้วย|หน่อย|ซะ|นะ|ครับ|ค่ะ))*\s*$"
)

#: "Don't remember this" — about the current exchange, not about stored memories. Nothing
#: to search for and nothing to delete: the instruction is to *not write*.
_SUPPRESS = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:do\s*n[o']?t|don't|never)\s+"
    r"(?:remember|store|save|keep|record|log)\b"
)
_SUPPRESS_TH = re.compile(r"^\s*(?:อย่า|ห้าม|ไม่ต้อง)\s*(?:จำ|เก็บ|บันทึก)")

_SEARCH_FILES = re.compile(
    r"(?i)(?:\b(?:find|search for|look for|show me)\b|ค้นหา|หา|ขอ)\s*"
    r"(?:the\s+|a\s+)?(?P<latest>latest|newest|most recent|ล่าสุด|ใหม่สุด)?\s*"
    r"(?:file|ไฟล์)?\s*(?P<kind>[\w฀-๿]+)?\s*(?:file|files|ไฟล์)?\s*"
    r"(?P<latest2>ล่าสุด|ใหม่สุด)?\s*"
    r"(?:\bin\b|ใน|ที่|จาก)\s*(?:the\s+)?(?:folder\s+)?(?P<folder>[\w฀-๿/\\.~-]+)"
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

    if (search := _match_file_search(body)) is not None:
        globs, folder, newest_first = search
        return RuleMatch(
            Intent(
                kind=IntentKind.FILE_ACTION,
                objective=body,
                entities={
                    "action": "file.search",
                    "root": folder,
                    "pattern": globs,
                    # Reading is all this does. The planner maps it to a READ-level tool,
                    # and nothing downstream can turn a search into a modification.
                    "limit": 1 if newest_first else 20,
                },
                target_device=_device_hint(body),
                confidence=0.86,
                rationale="a request to find files of a named type in a named folder",
            )
        )
    if _SUPPRESS.match(body) or _SUPPRESS_TH.match(body):
        # Nothing to search and nothing to delete — the instruction is to not write. It is
        # its own intent rather than a flag on the turn, because the owner deserves to be
        # told it was heard: silence would be indistinguishable from being ignored.
        return RuleMatch(
            Intent(
                kind=IntentKind.MEMORY_FORGET,
                objective=body,
                entities={"mode": "suppress"},
                confidence=0.94,
                rationale="an instruction not to remember this exchange",
            )
        )
    if (forget := _match_forget(body)) is not None:
        return RuleMatch(
            Intent(
                kind=IntentKind.MEMORY_FORGET,
                objective=body,
                entities={"mode": "forget", "subject": forget},
                confidence=0.92,
                rationale="an instruction to forget something already stored",
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


def _looks_like_path(candidate: str) -> bool:
    """A path, rather than a word the folder table should have known."""
    return (
        candidate.startswith(("~", "/", "./"))
        or (len(candidate) > 2 and candidate[1] == ":")  # C:\Users\...
        or "/" in candidate
        or "\\" in candidate
    )


def _match_file_search(body: str) -> tuple[list[str], str, bool] | None:
    """Turn "หาไฟล์ Excel ล่าสุดใน Downloads" into globs, a folder and an ordering.

    Returns ``None`` unless both halves are recognised. A search whose file type or folder
    the rules could not place is better handed to the model than run against the home
    directory with ``*``, which would walk the whole disk to answer the wrong question.
    """
    match = _SEARCH_FILES.search(body)
    if match is None:
        return None

    kind = (match.group("kind") or "").strip().lower()
    globs = FILE_TYPE_GLOBS.get(kind)
    if globs is None:
        # "หาไฟล์ report.xlsx" — an explicit extension is its own glob.
        if "." in kind and not kind.startswith("."):
            globs = [f"*{kind[kind.rindex('.') :]}"]
        else:
            return None

    raw_folder = match.group("folder").strip()
    folder = FOLDER_ALIASES.get(raw_folder.strip("/\\.").lower())
    if folder is None:
        # An explicit path is fine too — "find pdfs in ~/work". It is not a hole: the node
        # confines every path to its own allowed roots, so naming a directory here can
        # widen the search no further than that node already permits.
        if _looks_like_path(raw_folder):
            folder = raw_folder
        else:
            return None

    newest_first = bool(match.group("latest") or match.group("latest2"))
    return globs, folder, newest_first


def _match_forget(body: str) -> str | None:
    """The subject the owner wants forgotten.

    Returns ``None`` when the sentence is only the verb. "Forget it" is a figure of speech
    far more often than an instruction to erase memory, and deleting on that reading is not
    a mistake that can be undone.
    """
    for pattern in (_FORGET_TH, _FORGET):
        match = pattern.match(body)
        if match is None:
            continue
        subject = match.group("subject").strip(" \t:,.!?")
        if len(subject) < 3 or subject.lower() in _NOT_A_SUBJECT:
            return None
        return subject
    return None


#: Words that follow "forget" without naming anything to forget.
_NOT_A_SUBJECT = frozenset({"it", "that", "this", "them", "มัน", "นี่", "นั่น"})


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
        if _PROCEDURAL_MARKER.search(fact):
            # How to do the work. Filed procedurally so it can be *applied* next time
            # rather than merely recalled — the difference between a second brain and a
            # notebook.
            return fact, "procedural"
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

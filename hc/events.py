"""공통 이벤트 모델. Claude·Codex 파서가 모두 이 형식으로 낸다."""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone
from dataclasses import dataclass, asdict


KINDS = ("user_msg", "tool_call", "tool_error", "permission_denied", "hook_error")
# 파서가 넘기는 원문 상한. 실제 길이 제한(config text_max_chars)은 수집기가 가린 뒤에 건다.
# 여기서 짧게 자르면 경계에 걸친 시크릿·개인정보가 가림 패턴에 안 걸려 앞부분이 남는다.
RAW_MAX = 65536

@dataclass(frozen=True)
class Event:
    surface: str
    session_id: str
    ts: str
    cwd: str
    kind: str
    text: str = ""
    tool: str = ""
    error_sig: str = ""

def dumps(e: Event) -> str:
    return json.dumps(asdict(e), ensure_ascii=False)

def loads(s: str) -> Event:
    return Event(**json.loads(s))

def clip(text: str, limit: int = RAW_MAX) -> str:
    return (text or "")[:limit]

# 종료·상태 코드는 서로 다른 실패이므로 숫자를 지우지 않는다.
# 숫자를 글자(0→A … 9→J)로 바꿔 뒤의 \d+ 치환을 피한다.
_CODE = re.compile(r"(?i)\b(exit(?:\s+(?:code|status))?|code|status|http)[\s:=]*(\d+)")
_DIGIT_TO_LETTER = str.maketrans("0123456789", "ABCDEFGHIJ")

def _keep_code(m: re.Match) -> str:
    return f"{m.group(1)}=<C{m.group(2).translate(_DIGIT_TO_LETTER)}>"

_NORM = [
    (re.compile(r"0x[0-9a-fA-F]+"), "<HEX>"),
    (re.compile(r"(?:~|/)[\w.\-/]+"), "<PATH>"),
    (_CODE, _keep_code),
    (re.compile(r"\d+"), "<N>"),
    (re.compile(r"\s+"), " "),
]

def error_signature(text: str) -> str:
    s = (text or "")[:300]
    for rx, rep in _NORM:
        s = rx.sub(rep, s)
    return hashlib.sha1(s.strip().lower().encode("utf-8")).hexdigest()[:12]

_FRAC = re.compile(r"\.(\d+)")

def parse_ts(ts):
    """ISO 시각을 aware datetime으로. 끝의 Z를 받고, 시간대가 없으면 UTC로 본다.
    Python 3.9의 fromisoformat은 소수점 3·6자리만 받으므로 소수부를 6자리로 맞춘다. 실패 시 None."""
    if not isinstance(ts, str):
        return None
    s = _FRAC.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), ts.replace("Z", "+00:00"), count=1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

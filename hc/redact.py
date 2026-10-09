"""수집 단계 가림. 내장 시크릿·개인정보·홈 경로 + 설정 슬롯(단어 목록·정규식 파일).
슬롯 파일이 지정됐는데 읽을 수 없으면 RedactError로 멈춘다(fail-closed)."""
from __future__ import annotations
import json, re
from .config import expand

class RedactError(Exception):
    pass

# 접두 토큰 앞이 영숫자면 단어 안쪽이다('task-notification' 의 'sk-'). '_'·'-' 는 경계로 본다.
_SECRET = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_\-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}"
    r"|sbp_[A-Za-z0-9]{12,}|vcp_[A-Za-z0-9]{12,}|xox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    r"|AIza[0-9A-Za-z_\-]{30,}|(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{10,}"
    r")"
)
_SECRET_SUBS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S), "[SECRET]"),
    (_SECRET, "[SECRET]"),
    # 키 이름 뒤 따옴표를 허용한다: {"CRON_SECRET": "..."} 같은 JSON·dict 형태
    (re.compile(r"(?<![A-Za-z0-9_])([A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*)([\"']?\s*[=:]\s*[\"']?)[^\s\"']{8,}", re.I),
     r"\1\2[SECRET]"),
    (re.compile(r"(?<![A-Za-z0-9_])Bearer\s+[A-Za-z0-9._\-~+/=]{16,}", re.I), "Bearer [SECRET]"),
)
_CN_ID = re.compile(r"(?<![0-9A-Za-z])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9A-Za-z])")
_KR_RRN = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-8]\d{6}(?!\d)")
_CARD = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")
_PHONE = re.compile(
    r"(?<![\d-])(?:\+?82[-\s]?1[016-9]|01[016-9])[-\s]?\d{3,4}[-\s]?\d{4}(?![\d-])"
    r"|(?<![\d-])(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?![\d-])"
    r"|(?<![\d-])(?:\+?1[-.\s]?)?(?:\([2-9]\d{2}\)\s?|[2-9]\d{2}[-.\s])[2-9]\d{2}[-.\s]\d{4}(?![\d-])"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HOME = re.compile(r"(?:/Users|/home)/(?!Shared(?:/|$))[^/\s\"'<>:]+")
_WIN_HOME = re.compile(r"[A-Za-z]:(?:\\\\|\\)Users(?:\\\\|\\)[^\\/\s\"'<>:]+", re.I)
_CN_W = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_LABEL = re.compile(r"^[A-Z][A-Z0-9_]*$")

def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0

def cn_id_ok(s: str) -> bool:
    if len(s) != 18 or not s[:17].isdigit():
        return False
    check = "10X98765432"[sum(int(c) * w for c, w in zip(s[:17], _CN_W)) % 11]
    return s[17].upper() == check

def _card(m: re.Match) -> str:
    return "[CARD]" if luhn_ok(re.sub(r"[ -]", "", m.group(0))) else m.group(0)

def _cn(m: re.Match) -> str:
    return "[ID]" if cn_id_ok(m.group(0)) else m.group(0)

def _read(path, what):
    p = expand(path)
    try:
        return json.loads(p.read_text(encoding="utf-8")), p
    except (OSError, ValueError) as e:
        raise RedactError(f"{what} 읽기 실패: {p}: {e}") from e

def _load_lexicon(path) -> list:
    d, p = _read(path, "가림 단어 목록")
    if isinstance(d, list):
        items = d
    elif isinstance(d, dict):
        items = [t for v in d.values() if isinstance(v, list) for t in v]
    else:
        raise RedactError(f"가림 단어 목록 형식 이상(목록 또는 객체): {p}")
    if not all(isinstance(t, str) for t in items):
        raise RedactError(f"가림 단어 목록에 문자열이 아닌 값: {p}")
    return [t.strip() for t in items if len(t.strip()) >= 2]

def _load_patterns(path) -> list:
    d, p = _read(path, "가림 정규식 파일")
    pats = d.get("patterns") if isinstance(d, dict) else None
    if not isinstance(pats, list) or not pats:
        raise RedactError(f"가림 정규식 파일에 patterns 목록이 없거나 비었다: {p}")
    out = []
    for e in pats:
        if not (isinstance(e, dict) and isinstance(e.get("label"), str) and _LABEL.match(e["label"])
                and isinstance(e.get("regex"), str) and e["regex"]
                and isinstance(e.get("whole", False), bool) and isinstance(e.get("flags", ""), str)):
            raise RedactError(f"가림 정규식 항목 형식 이상: {p}: {e!r}")
        try:
            rx = re.compile(e["regex"], re.I if "i" in e.get("flags", "") else 0)
        except re.error as err:
            raise RedactError(f"가림 정규식 컴파일 실패: {p}: {err}") from err
        out.append((e["label"], rx, e.get("whole", False)))
    return out

class Redactor:
    def __init__(self, lexicon_files=(), pattern_files=(), lexicon_label: str = "NAME"):
        terms = []
        for f in lexicon_files or ():
            terms += _load_lexicon(f)
        uniq = sorted(set(terms), key=len, reverse=True)
        self.lexicon_terms = len(uniq)
        self.has_lexicon = self.lexicon_terms > 0
        self._label = f"[{lexicon_label}]"
        # 영문만인 단어는 단어 경계, 한글 등이 섞인 단어는 조사가 붙으므로 부분 일치. 둘 다 대소문자 무시.
        self._terms = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", re.I) if t.isascii()
                       else re.compile(re.escape(t), re.I) for t in uniq]
        pats = []
        for f in pattern_files or ():
            pats += _load_patterns(f)
        self._whole = [(l, rx) for l, rx, w in pats if w]
        self._replace = [(l, rx) for l, rx, w in pats if not w]

    def redact(self, text: str) -> str:
        if not text:
            return ""
        for label, rx in self._whole:
            if rx.search(text):
                return f"[{label}]"
        out = text
        for rx, repl in _SECRET_SUBS:
            out = rx.sub(repl, out)
        for rx in self._terms:
            out = rx.sub(self._label, out)
        for label, rx in self._replace:
            out = rx.sub(f"[{label}]", out)
        out = _CN_ID.sub(_cn, out)
        out = _KR_RRN.sub("[ID]", out)
        out = _CARD.sub(_card, out)
        out = _PHONE.sub("[PHONE]", out)
        out = _EMAIL.sub("[EMAIL]", out)
        out = _HOME.sub("~", out)
        return _WIN_HOME.sub("~", out)

def from_config(cfg: dict) -> Redactor:
    r = cfg.get("redact") or {}
    return Redactor(r.get("lexicon_files") or [], r.get("pattern_files") or [], r.get("lexicon_label") or "NAME")

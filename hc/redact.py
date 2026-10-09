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
    r"|sbp_[A-Za-z0-9]{12,}|vcp_[A-Za-z0-9]{12,}|xox[abeprs]-[A-Za-z0-9\-]{10,}|xapp-[A-Za-z0-9\-]{10,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    r"|AIza[0-9A-Za-z_\-]{30,}|(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{10,}"
    r")"
)
# 대입문 머리: 키 이름 + 대입 기호(= : => :=). 키 뒤 따옴표(JSON·dict)와 역슬래시 이스케이프
# (\"key\": \"v\")를 허용한다. 둘째 갈래는 공백으로 값을 받는 CLI 플래그(--password <값>)다.
# 키 이름 앞뒤 길이 상한 64는 긴 영숫자열에서의 이차 역추적을 막는다.
_ASSIGN_HEAD = re.compile(
    r"(?<![A-Za-z0-9_])[A-Z0-9_]{0,64}(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]{0,64}(?![A-Za-z0-9_])"
    r"\\?[\"']?\s*(?:=>|:=|[=:])\s*"
    r"|(?<![A-Za-z0-9_-])--[A-Z0-9_-]{0,64}(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_-]{0,64}(?![A-Za-z0-9_-])"
    r"[ \t]+(?=[^\s-])",
    re.I,
)
_QUERY_NEXT = re.compile(r"&\w+=")
_MIN_VALUE = 8
_QUOTES = ('"', "'")
_QUOTE_PAIRS = {'"': '"', "'": "'", "`": "`", "\u201c": "\u201d", "\u2018": "\u2019"}
_QUOTE_SPAN = 4096   # 닫는 따옴표를 찾는 최대 거리(여러 줄 값 허용, 시간 상한)

def _quote_at(text: str, i: int):
    """i 위치의 여는 따옴표. (여는 표기, 닫는 표기, 역슬래시 이스케이프 여부, 길이) 또는 None.
    삼중 따옴표·백틱·굽은 따옴표를 포함한다. 이스케이프는 \\" \\' 만 본다."""
    for tq in ('"""', "'''"):
        if text.startswith(tq, i):
            return tq, tq, False, 3
    c = text[i:i + 1]
    if c in _QUOTE_PAIRS:
        return c, _QUOTE_PAIRS[c], False, 1
    if c == "\\" and text[i + 1:i + 2] in _QUOTES:
        return text[i + 1], text[i + 1], True, 2
    return None

def _quoted_end(text: str, i: int, close: str, escaped: bool):
    """여는 따옴표 바로 뒤 i부터 같은 종류의 닫는 따옴표를 찾는다. (본문 끝, 닫는 따옴표 끝).
    다른 종류 따옴표와 역슬래시로 이스케이프된 따옴표는 본문이다. _QUOTE_SPAN 안에서
    못 찾으면(잘린 레코드) 줄 끝·텍스트 끝까지를 본문으로 본다."""
    stop = min(len(text), i + _QUOTE_SPAN)
    j = i
    while True:
        k = text.find(close, j, stop)
        if k < 0:
            break
        bs = 0
        while k - bs - 1 >= i and text[k - bs - 1] == "\\":
            bs += 1
        if escaped and bs % 2 == 1:
            return k - 1, k + len(close)
        if not escaped and bs % 2 == 0:
            return k, k + len(close)
        j = k + 1
    nl = text.find("\n", i)
    end = nl if nl >= 0 else len(text)
    return end, end

def _bare_end(text: str, i: int, enclosing) -> int:
    """따옴표 없는 값의 끝. 공백에서 멈추고, & 뒤가 다음 쿼리 파라미터(\\w+=)일 때만 &에서 멈춘다.
    대입문을 감싼 따옴표와 같은 따옴표에서 멈추고, 그 밖의 따옴표 조각은 셸처럼 값에 이어 붙인다."""
    n, j = len(text), i
    while j < n:
        c = text[j]
        if c.isspace() or (c == "&" and _QUERY_NEXT.match(text, j)):
            break
        qa = _quote_at(text, j)
        if qa is None:
            j += 1
            continue
        q, close, esc, ln = qa
        if enclosing == (q, esc):
            break
        j = max(j + 1, _quoted_end(text, j + ln, close, esc)[1])
    return j

def _enclosing(text: str, start: int):
    """대입문 바로 앞 따옴표(대입문이 문자열 안에 있는지)."""
    if start >= 2 and text[start - 2] == "\\" and text[start - 1] in _QUOTES:
        return text[start - 1], True
    if start >= 1 and text[start - 1] in _QUOTES:
        return text[start - 1], False
    return None

def _redact_assign(text: str) -> str:
    """대입문 값 가림. 따옴표로 열린 값은 같은 종류 짝 따옴표 전까지 공백 포함(패스프레이즈),
    따옴표 없는 값은 공백 전까지 넉넉히 가린다(과잉 가림이 누출보다 낫다). 8자 미만 값은 둔다."""
    out, pos = [], 0
    m = _ASSIGN_HEAD.search(text)
    while m:
        v = m.end()
        qa = _quote_at(text, v)
        if qa:
            _, close, esc, ln = qa
            start, end = v + ln, _quoted_end(text, v + ln, close, esc)[0]
        else:
            start, end = v, _bare_end(text, v, _enclosing(text, m.start()))
        if end - start >= _MIN_VALUE:
            out += [text[pos:start], "[SECRET]"]
            pos = end
            m = _ASSIGN_HEAD.search(text, end)
        else:
            m = _ASSIGN_HEAD.search(text, m.end())
    out.append(text[pos:])
    return "".join(out)

_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)
_BEARER = re.compile(r"(?<![A-Za-z0-9_])Bearer\s+[A-Za-z0-9._\-~+/=]{16,}", re.I)
# 순서가 의미를 가진다: PEM 블록 통째 → 접두 토큰 → 대입문(이름 유지) → Bearer
_SECRET_SUBS = (
    lambda s: _PEM.sub("[SECRET]", s),
    lambda s: _SECRET.sub("[SECRET]", s),
    _redact_assign,
    lambda s: _BEARER.sub("Bearer [SECRET]", s),
)
_CN_ID = re.compile(r"(?<![0-9A-Za-z])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9A-Za-z])")
_KR_RRN = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[-\s]?[1-8]\d{6}(?!\d)")
_CARD = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")
_PHONE = re.compile(
    r"(?<![\d-])(?:\+?82[-\s]?1[016-9]|01[016-9])[-\s]?\d{3,4}[-\s]?\d{4}(?![\d-])"
    r"|(?<![\d-])(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?![\d-])"
    r"|(?<![\d-])(?:\+?1[-.\s]?)?(?:\([2-9]\d{2}\)\s?|[2-9]\d{2}[-.\s])[2-9]\d{2}[-.\s]\d{4}(?![\d-])"
)
# 로컬 부분 앞 경계와 길이 상한으로 긴 무공백 입력에서의 이차 시간 역추적을 막는다.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+")
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
        if not all(isinstance(v, list) for v in d.values()):
            raise RedactError(f"가림 단어 목록 객체의 값은 문자열 목록이어야 한다: {p}")
        items = [t for v in d.values() for t in v]
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
        for sub in _SECRET_SUBS:
            out = sub(out)
        for rx in self._terms:
            out = rx.sub(lambda _m: self._label, out)   # 라벨을 치환 템플릿으로 해석하지 않는다
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

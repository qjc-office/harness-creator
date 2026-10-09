"""정정 화행·비사용자 발화·재개 문구 판정. 문구는 언어 팩(hc/lang/*.json)이 정본이다."""
from __future__ import annotations
import functools, re
from .lang import LANGS, load_pack

# 접두를 첫 40자 어디서든 찾는 언어(v1 한국어 규칙). 나머지 언어는 문장 첫머리만 본다.
# 영어·중국어는 "you are wrong"·"你现在是在乱改" 같은 실제 정정이 같은 말로 시작하기 때문이다.
_PREFIX_ANYWHERE = frozenset({"ko"})

_STRIP = re.compile(r"[\s.,!?~…·'\"()\[\]。，！？、]+")

def _norm(t: str) -> str:
    return _STRIP.sub("", (t or "").lower())

@functools.lru_cache(maxsize=None)
def _compiled():
    acts, anywhere, start, sigs, resumes = [], [], [], [], set()
    for code in LANGS:
        p = load_pack(code)
        acts += p["correction"]
        (anywhere if code in _PREFIX_ANYWHERE else start).extend(x.lower() for x in p["non_user_prefixes"])
        sigs += [x.lower() for x in p["non_user_signatures"]]
        resumes |= {_norm(x) for x in p["resume_phrases"]}
    return (re.compile("|".join(f"(?:{a})" for a in acts), re.I), tuple(anywhere), tuple(start),
            tuple(sigs), frozenset(resumes))

def is_non_user(p: str) -> bool:
    if len(p) > 400:
        return True
    if "```" in p or "\n# " in p or p.startswith("# "):
        return True
    low = p.lower()
    if low.lstrip().startswith("<"):
        return True
    _, anywhere, start, sigs, _ = _compiled()
    if any(pre in low[:40] for pre in anywhere) or low.lstrip().startswith(start):
        return True
    if "respond with exactly" in low:
        return True
    if "라고만 답" in low and "라고만 답하지" not in low:
        return True
    return any(s in low for s in sigs)

def is_correction(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 5 and not is_non_user(t) and bool(_compiled()[0].search(t))

def is_resume(text: str) -> bool:
    """사람이 쓴 지시가 아니라 이어 달라는 재개 문구뿐인가(자동 재개기가 보내는 'continue' 류)."""
    n = _norm(text)
    return bool(n) and n in _compiled()[4]

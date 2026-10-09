"""언어 팩(ko·zh·en)과 언어 감지. 팩 JSON이 정정 화행·비사용자 발화·재개 문구·리포트 문구의 정본이다."""
from __future__ import annotations
import functools, json, pathlib, re

LANGS = ("ko", "zh", "en")
_DIR = pathlib.Path(__file__).resolve().parent
_HANGUL = re.compile(r"[가-힣]")
_KANA = re.compile(r"[ぁ-ヿ]")
_HAN = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")

@functools.lru_cache(maxsize=None)
def load_pack(code: str) -> dict:
    if code not in LANGS:
        raise ValueError(f"지원하지 않는 언어: {code!r}")
    return json.loads((_DIR / f"{code}.json").read_text(encoding="utf-8"))

def script_of(text: str) -> str:
    h, c, k = len(_HANGUL.findall(text)), len(_HAN.findall(text)), len(_KANA.findall(text))
    if h and h >= c:
        return "ko"
    if c and not k:
        return "zh"
    return "en" if _LATIN.search(text) else ""

def detect_lang(texts) -> str:
    counts = {c: 0 for c in LANGS}
    for t in texts:
        s = script_of(t or "")
        if s:
            counts[s] += 1
    best = max(LANGS, key=lambda c: (counts[c], c == "en"))
    return best if counts[best] else "en"

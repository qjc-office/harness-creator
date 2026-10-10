"""리포트 렌더. 문구는 언어 팩 report 절이 정본이다. 러너의 성공 판정(cli stats-check)과 첫 줄이 같은 함수를 쓴다."""
from __future__ import annotations
from .config import ConfigError, load_config, load_defaults
from .lang import load_pack
from .redact import Redactor, from_config

CREATABLE = ("hook", "rule", "skill")
_INCOMPLETE = (("errors", "reason_errors"), ("deferred", "reason_deferred"), ("malformed", "reason_malformed"))

def _R(lang: str) -> dict:
    return load_pack(lang if lang in ("ko", "zh", "en") else "en")["report"]

def _count(stats, key):
    n = stats.get(key) if isinstance(stats, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None

def parse_errors(stats):
    return _count(stats, "errors")

def stats_corrupt(stats) -> bool:
    return not isinstance(stats, dict) or any(_count(stats, k) is None for k, _ in _INCOMPLETE)

_CFG_ERRORS = (ConfigError, TypeError, ValueError, AttributeError, KeyError)

def _cfg() -> dict:
    """설정을 못 읽어도 리포트(특히 실패 첫 줄)는 써야 하므로 빈 설정으로 내려간다."""
    try:
        cfg = load_config()
    except _CFG_ERRORS:
        return {}
    return cfg if isinstance(cfg, dict) else {}

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

def _warn_ratio(cfg: dict) -> float:
    v = _num(cfg.get("unknown_type_warn_ratio"))
    return v if v is not None else load_defaults()["unknown_type_warn_ratio"]

def _floor(cfg: dict) -> int:
    """lexicon_min_terms 하한. 공개 기본은 0(경고 없음). 값이 깨졌으면 0."""
    try:
        n = int(cfg["redact"].get("lexicon_min_terms") or 0)
    except _CFG_ERRORS:
        return 0
    return max(n, 0)

def _redactor(cfg: dict) -> Redactor:
    """판정 모델 출력은 가림을 거치지 않았으므로 렌더에서 한 번 더 가린다.
    설정 슬롯으로 못 만들면 기본 가림기(내장 패턴)로라도 가린다(리포트를 막지 않는다)."""
    try:
        return from_config(cfg)
    except Exception:   # 슬롯 파일 손상·형식 오류 등 어떤 이유든 기본 가림기로
        return Redactor()

def _unknown_ratio(stats):
    rec, unk = _count(stats, "records"), _count(stats, "unknown")
    if not rec or unk is None:
        return None
    return unk / rec

def incomplete_reasons(stats, lang: str = "ko") -> list:
    R, out = _R(lang), []
    for key, rk in _INCOMPLETE:
        n = _count(stats, key)
        if n:
            out.append(R[rk].format(n))
    ratio = _unknown_ratio(stats)
    if ratio is not None and ratio > _warn_ratio(_cfg()):
        out.append(R["reason_unknown"].format(f"{ratio:.1%}"))
    return out

def _warnings(stats: dict, R: dict, cfg: dict, use_config: bool = True) -> list:
    """리포트 머리 경고. use_config=False(실패 리포트)면 설정에 기대는 경고(모르는 타입 비율·하한)는 건너뛰고
    통계만으로 계산되는 경고(추가 가림 목록 없음·통계 손상·파싱 실패·깨진 레코드)만 낸다."""
    out = []
    ratio = _unknown_ratio(stats)
    if use_config and ratio is not None and ratio > _warn_ratio(cfg):
        out.append("\n" + R["warn_unknown"].format(ratio=f"{ratio:.1%}"))
    no_lexicon = isinstance(stats, dict) and stats.get("lexicon") is False
    if no_lexicon:
        out.append("\n" + R["no_extra_redaction"])
    terms, floor = (0 if no_lexicon else _count(stats, "lexicon_terms")), (_floor(cfg) if use_config else 0)
    if floor and terms is not None and terms < floor:
        out.append("\n" + R["warn_thin"].format(terms=terms, floor=floor))
    if stats_corrupt(stats):
        out.append("\n" + R["warn_corrupt"])
    pe = parse_errors(stats)
    if pe:
        out.append("\n" + R["warn_parse"].format(n=pe))
    m = _count(stats, "malformed")
    if m:
        out.append("\n" + R["warn_malformed"].format(n=m))
    return out

def _items(candidates) -> list:
    items = candidates.get("candidates") if isinstance(candidates, dict) else None
    return [c for c in items if isinstance(c, dict)] if isinstance(items, list) else []

def _render_one(c: dict, R: dict, red: Redactor) -> list:
    """후보의 문자열은 판정 모델이 쓴 것이라 전부 가린 뒤(가린 뒤 자른다) 찍는다."""
    s = lambda v: red.redact(str(v))
    e = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
    pc = c.get("proposed_change") if isinstance(c.get("proposed_change"), dict) else {}
    quotes = e.get("quotes") if isinstance(e.get("quotes"), list) else []
    overlaps = c.get("overlaps") if isinstance(c.get("overlaps"), list) else []
    out = [f"\n## {s(c.get('id', '?'))} [{s(c.get('form', '?'))}] {s(c.get('title', ''))}\n",
           R["c_evidence"].format(sessions=s(e.get("sessions", "?")), range=s(e.get("date_range", "")))]
    out += [f"  - > {s(q)[:200]}" for q in quotes[:3]]
    out.append(R["c_overlaps"].format(v=", ".join(s(o) for o in overlaps) or R["none"]))
    out.append(R["c_change"].format(action=s(pc.get("action", "?")), path=s(pc.get("path", "")),
                                    summary=s(pc.get("summary", ""))))
    out.append(R["c_effect"].format(v=s(c.get("expected_effect", ""))))
    if c.get("form") not in CREATABLE:
        out.append(R["c_manual"])
    return out

def _scope_lines(stats: dict, signals_count: int, scope, R: dict) -> str:
    if isinstance(scope, dict):
        sev = scope.get("events") if isinstance(scope.get("events"), dict) else {}
        seen = R["scope_seen"].format(days=scope.get("days", "?"), sessions=scope.get("sessions", 0),
                                      claude=sev.get("claude", 0), codex=sev.get("codex", 0),
                                      signals=signals_count, skipped=scope.get("skipped", 0))
    else:
        seen = R["scope_none"]
    ev = stats.get("events") if isinstance(stats, dict) and isinstance(stats.get("events"), dict) else {}
    inc = R["scope_inc"].format(files=stats.get("files", 0) if isinstance(stats, dict) else 0,
                                claude=ev.get("claude", 0), codex=ev.get("codex", 0))
    return f"{seen}\n\n{inc}"

_GATES = ("G1", "G2", "G3", "G4", "G5", "invalid")

def _sections(gates, effects, R: dict, red: Redactor) -> list:
    out = []
    if effects:
        out.append("\n" + R["sec_effect"] + "\n")
        for e in effects:
            key = "effect_recurred" if e.get("recurred") else "effect_clean"
            out.append(R[key].format(id=red.redact(str(e.get("candidate_id", "?"))), last_seen=e.get("last_seen", "")))
    gates = gates if isinstance(gates, dict) else {}
    notes = [c for c in gates.get("codex_notes") or [] if isinstance(c, dict)]
    if notes:
        out.append("\n" + R["sec_codex"] + "\n")
        out += [R["codex_item"].format(id=red.redact(str(c.get("id", "?"))), title=red.redact(str(c.get("title", ""))))
                for c in notes]
    counts = {}
    for f in gates.get("filtered") or []:
        if isinstance(f, dict) and f.get("gate") in _GATES:
            counts[f["gate"]] = counts.get(f["gate"], 0) + 1
    resume = _count(gates, "resume_skipped") or 0
    if counts or resume:
        out.append("\n" + R["sec_filtered"] + "\n")
        for g in _GATES:
            if counts.get(g):
                out.append(R["filtered_item"].format(label=R[f"gate_{g}"], n=counts[g]))
        if resume:
            out.append(R["filtered_item"].format(label=R["gate_resume"], n=resume))
    return out

def render(candidates, stats, signals_count, error, scope=None, *, lang="ko", gates=None, effects=None,
           error_kind="") -> str:
    R = _R(lang)
    stats = stats if isinstance(stats, dict) else {}
    seen = _scope_lines(stats, signals_count, scope, R)
    if candidates is None and not error and not error_kind:
        error_kind = "missing"
    cfg = _cfg()
    red = _redactor(cfg)
    head = []
    if error or error_kind:
        title = R["title_fail"] + (f" ({R['cli_upgrade']})" if error_kind == "cli_upgrade" else "")
        detail = red.redact(str(error or ""))   # 러너 stderr가 모델 출력을 되풀이할 수 있다
        body = R.get(f"err_{error_kind}", R["err_other"]).format(detail=detail).strip() if error_kind else detail
        head.append(f"{title}\n\n{body}\n\n{seen}")
    else:
        n = len(_items(candidates))
        title = R["title_n"].format(n=n) if n else R["title_none"]
        reasons = incomplete_reasons(stats, lang)
        if reasons:
            title += f" ({R['incomplete'].format(reasons=', '.join(reasons))})"
        head.append(title)
        head.append(f"\n{seen}")
    failed = bool(error or error_kind)
    # 실패 리포트는 설정에 기대는 경고만 건너뛴다(설정이 깨져도 첫 줄은 써야 한다). 통계 경고는 그대로 낸다
    head.extend(_warnings(stats, R, cfg, use_config=not failed))
    body = []
    if not failed:
        for c in _items(candidates):
            body.extend(_render_one(c, R, red))
    body.extend(_sections(gates, effects, R, red))
    return "\n".join(head + body) + "\n"

"""리포트 렌더. 문구는 언어 팩 report 절이 정본이다. 러너의 성공 판정(cli stats-check)과 첫 줄이 같은 함수를 쓴다."""
from __future__ import annotations
from .config import load_config
from .lang import load_pack

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
    if ratio is not None and ratio > load_config()["unknown_type_warn_ratio"]:
        out.append(R["reason_unknown"].format(f"{ratio:.1%}"))
    return out

def _warnings(stats: dict, R: dict) -> list:
    out = []
    ratio = _unknown_ratio(stats)
    if ratio is not None and ratio > load_config()["unknown_type_warn_ratio"]:
        out.append("\n" + R["warn_unknown"].format(ratio=f"{ratio:.1%}"))
    if isinstance(stats, dict) and stats.get("lexicon") is False:
        out.append("\n" + R["no_extra_redaction"])
    else:
        terms, floor = _count(stats, "lexicon_terms"), int(load_config()["redact"].get("lexicon_min_terms") or 0)
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

def _render_one(c: dict, R: dict) -> list:
    e = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
    pc = c.get("proposed_change") if isinstance(c.get("proposed_change"), dict) else {}
    quotes = e.get("quotes") if isinstance(e.get("quotes"), list) else []
    overlaps = c.get("overlaps") if isinstance(c.get("overlaps"), list) else []
    out = [f"\n## {c.get('id', '?')} [{c.get('form', '?')}] {c.get('title', '')}\n",
           R["c_evidence"].format(sessions=e.get("sessions", "?"), range=e.get("date_range", ""))]
    out += [f"  - > {str(q)[:200]}" for q in quotes[:3]]
    out.append(R["c_overlaps"].format(v=", ".join(str(o) for o in overlaps) or R["none"]))
    out.append(R["c_change"].format(action=pc.get("action", "?"), path=pc.get("path", ""), summary=pc.get("summary", "")))
    out.append(R["c_effect"].format(v=c.get("expected_effect", "")))
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

def _sections(gates, effects, R: dict) -> list:
    out = []
    if effects:
        out.append("\n" + R["sec_effect"] + "\n")
        for e in effects:
            key = "effect_recurred" if e.get("recurred") else "effect_clean"
            out.append(R[key].format(id=e.get("candidate_id", "?"), last_seen=e.get("last_seen", "")))
    gates = gates if isinstance(gates, dict) else {}
    notes = [c for c in gates.get("codex_notes") or [] if isinstance(c, dict)]
    if notes:
        out.append("\n" + R["sec_codex"] + "\n")
        out += [R["codex_item"].format(id=c.get("id", "?"), title=c.get("title", "")) for c in notes]
    counts = {}
    for f in gates.get("filtered") or []:
        if isinstance(f, dict) and f.get("gate") in ("G1", "G2", "G3", "G4", "G5"):
            counts[f["gate"]] = counts.get(f["gate"], 0) + 1
    resume = gates.get("resume_skipped") or 0
    if counts or resume:
        out.append("\n" + R["sec_filtered"] + "\n")
        for g in ("G1", "G2", "G3", "G4", "G5"):
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
    head = []
    if error or error_kind:
        title = R["title_fail"] + (f" ({R['cli_upgrade']})" if error_kind == "cli_upgrade" else "")
        body = R.get(f"err_{error_kind}", R["err_other"]).format(detail=error or "").strip() if error_kind else str(error)
        head.append(f"{title}\n\n{body}\n\n{seen}")
    else:
        n = len(_items(candidates))
        title = R["title_n"].format(n=n) if n else R["title_none"]
        reasons = incomplete_reasons(stats, lang)
        if reasons:
            title += f" ({R['incomplete'].format(reasons=', '.join(reasons))})"
        head.append(title)
        head.append(f"\n{seen}")
    head.extend(_warnings(stats, R))
    body = []
    if not (error or error_kind):
        for c in _items(candidates):
            body.extend(_render_one(c, R))
    body.extend(_sections(gates, effects, R))
    return "\n".join(head + body) + "\n"

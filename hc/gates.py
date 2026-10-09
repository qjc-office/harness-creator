"""과잉 후보 거르기(spec §6). 신호 단계 G1·G2, 후보 단계 G3·G4·G5. 기준값은 config gates.*"""
from __future__ import annotations
import hashlib
from .events import parse_ts

def session_starts(events) -> dict:
    """(surface, 세션 id) → (첫 이벤트 시각 epoch, 첫 사용자 발화 sha1 앞 12자).
    첫 사용자 발화가 없거나 비었으면 해시는 ""(묶지 않는다). 시각을 못 읽은 이벤트는 빼고 본다."""
    first, first_user = {}, {}
    for e in events:
        dt = parse_ts(e.ts)
        if dt is None:
            continue
        t, k = dt.timestamp(), (e.surface, e.session_id)
        if k not in first or t < first[k]:
            first[k] = t
        if e.kind == "user_msg":
            cur = first_user.get(k)
            if cur is None or t < cur[0]:
                first_user[k] = (t, e.text or "")
    out = {}
    for k, t in first.items():
        text = first_user.get(k, (0, ""))[1]
        out[k] = (t, hashlib.sha1(text.encode("utf-8")).hexdigest()[:12] if text else "")
    return out

def batch_groups(starts: dict, window_s: float) -> dict:
    """세션 키 → 묶음 대표 세션 키. 첫 발화 해시가 같고, 묶음 첫 세션 시각에서 window_s 안에 시작한 세션을 한 묶음으로.
    해시가 빈 세션(첫 사용자 발화 없음)은 자기 자신이 대표다."""
    by_hash = {}
    out = {}
    for sid, (t, h) in starts.items():
        if h:
            by_hash.setdefault(h, []).append((t, sid))
        else:
            out[sid] = sid   # 첫 발화가 없으면 같은 작업인지 알 수 없어 묶지 않는다
    for items in by_hash.values():
        items.sort()
        head_t, head = None, None
        for t, sid in items:
            if head is None or t - head_t > window_s:
                head_t, head = t, sid
            out[sid] = head
    return out

def gate_signals(signals, events, cfg, now: float):
    """G1 해소·G2 일괄 실행 묶기. 통과 신호에는 groups(묶음 대표 id 목록)를 채운다. (통과, 걸러진 기록)."""
    g, th = cfg["gates"], cfg["thresholds"]
    groups = batch_groups(session_starts(events), g["batch_window_s"])
    stale_before = now - g["stale_days"] * 86400
    kept, filtered = [], []
    for s in signals:
        dt = parse_ts(s.last_seen)
        if dt is not None and dt.timestamp() < stale_before:
            filtered.append({"gate": "G1", "signal_id": s.id})
            continue
        reps = set()
        for sid in s.sessions:
            keys = [(sf, sid) for sf in s.surfaces if (sf, sid) in groups]
            reps |= {groups[k] for k in keys} if keys else {(s.surfaces[0] if s.surfaces else "", sid)}
        s.groups = sorted(r[1] for r in reps)
        if len(s.groups) < th.get(s.type, {}).get("min_sessions", 1):
            filtered.append({"gate": "G2", "signal_id": s.id})
            continue
        kept.append(s)
    return kept, filtered

def _has_paths_frontmatter(target) -> bool:
    from .frontmatter import front_file
    try:
        return bool(front_file(target).get("paths", "").strip())
    except OSError:
        return False

def _list(v) -> list:
    return v if isinstance(v, list) else []

def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

def _rule_scoped(pc: dict, claude_home) -> bool:
    """rule이 paths: 조건을 갖는가. 새 규칙은 후보가 붙일 proposed_change.paths로,
    기존 규칙 수정은 대상 파일의 frontmatter로 판정한다(수정안에서 모델이 말한 paths만 믿지 않는다)."""
    if pc.get("action") != "modify":
        return any(isinstance(x, str) and x.strip() for x in _list(pc.get("paths")))
    path = pc.get("path")
    if not isinstance(path, str) or not path or path.startswith(("/", "~")) or ".." in path.split("/"):
        return False
    from .config import claude_home as default_home
    import pathlib
    target = pathlib.Path(claude_home if claude_home is not None else default_home()) / path
    return target.is_file() and _has_paths_frontmatter(target)

def gate_candidates(cands, signals, cfg, claude_home=None):
    """G3 Codex 전용 → 참고 절, G4 상시 로드 rule 근거, G5 주간 한도. (통과, Codex 참고, 걸러진 기록).
    입력 후보·신호는 바꾸지 않는다(상시 로드 표시는 복사본에 단다)."""
    g = cfg["gates"]
    idx = {s["id"]: s for s in _list(signals) if isinstance(s, dict) and isinstance(s.get("id"), str)}
    passed, codex, filtered = [], [], []
    for c in _list(cands):
        if not isinstance(c, dict):
            continue
        sigs = [idx[i] for i in _list(c.get("signal_ids")) if isinstance(i, str) and i in idx]
        surfaces = {x for s in sigs for x in _list(s.get("surfaces"))}
        if surfaces == {"codex"}:
            codex.append(c)
            filtered.append({"gate": "G3", "candidate_id": c.get("id")})
            continue
        groups = {x for s in sigs for x in (_list(s.get("groups")) or _list(s.get("sessions")))}
        days = {x for s in sigs for x in _list(s.get("days"))}
        if c.get("form") == "rule":
            pc = c.get("proposed_change") if isinstance(c.get("proposed_change"), dict) else {}
            if not _rule_scoped(pc, claude_home):
                if len(groups) >= g["global_rule_min_sessions"] and len(days) >= g["global_rule_min_days"]:
                    c = {**c, "global_rule": True}
                else:
                    filtered.append({"gate": "G4", "candidate_id": c.get("id")})
                    continue
        passed.append((sum(_int(s.get("count")) for s in sigs) * len(groups), c))
    passed.sort(key=lambda x: -x[0])   # 안정 정렬: 점수가 같으면 판정 모델이 낸 순서
    keep = [c for _, c in passed[: g["max_candidates"]]]
    filtered += [{"gate": "G5", "candidate_id": c.get("id")} for _, c in passed[g["max_candidates"]:]]
    return keep, codex, filtered

"""과잉 후보 거르기(spec §6). 신호 단계 G1·G2, 후보 단계 G3·G4·G5. 기준값은 config gates.*"""
from __future__ import annotations
import hashlib
from .events import parse_ts

def session_starts(events) -> dict:
    """세션 id → (첫 이벤트 시각 epoch, 첫 사용자 발화 sha1 앞 12자). 시각을 못 읽은 이벤트는 빼고 본다."""
    first, first_user = {}, {}
    for e in events:
        dt = parse_ts(e.ts)
        if dt is None:
            continue
        t = dt.timestamp()
        if e.session_id not in first or t < first[e.session_id]:
            first[e.session_id] = t
        if e.kind == "user_msg":
            cur = first_user.get(e.session_id)
            if cur is None or t < cur[0]:
                first_user[e.session_id] = (t, e.text or "")
    return {sid: (t, hashlib.sha1(first_user.get(sid, (0, ""))[1].encode("utf-8")).hexdigest()[:12])
            for sid, t in first.items()}

def batch_groups(starts: dict, window_s: float) -> dict:
    """세션 id → 묶음 대표 세션 id. 첫 발화 해시가 같고, 묶음 첫 세션 시각에서 window_s 안에 시작한 세션을 한 묶음으로."""
    by_hash = {}
    for sid, (t, h) in starts.items():
        by_hash.setdefault(h, []).append((t, sid))
    out = {}
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
        s.groups = sorted({groups.get(sid, sid) for sid in s.sessions})
        if len(s.groups) < th.get(s.type, {}).get("min_sessions", 1):
            filtered.append({"gate": "G2", "signal_id": s.id})
            continue
        kept.append(s)
    return kept, filtered

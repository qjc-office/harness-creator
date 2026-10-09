"""신호 탐지. 문턱값은 config.json thresholds가 정본이다."""
from __future__ import annotations
import hashlib, json, os, re, time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from .config import data_dir, load_config, now as _now
from .fsutil import write_private
from .correction import is_correction, is_non_user, is_resume
from .events import loads, parse_ts as _parse_ts
from .gates import gate_signals
from .lang import detect_lang

@dataclass
class Signal:
    id: str
    type: str
    key: str
    sessions: list = field(default_factory=list)
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    samples: list = field(default_factory=list)
    surfaces: list = field(default_factory=list)
    context: list = field(default_factory=list)
    days: list = field(default_factory=list)
    groups: list = field(default_factory=list)

LAST_LOAD_SKIPPED = 0  # 직전 load_events에서 해석하지 못해 건너뛴 줄 수
LAST_RESUME_SKIPPED = 0  # 직전 반복 지시 탐지에서 재개 문구라 뺀 발화 수

class EventLimitError(Exception):
    """탐지 창의 고유 이벤트 줄이 상한(config max_events)을 넘었다. 일부만 보고 성공으로 끝내지 않는다."""

def _max_events(cfg=None) -> int:
    """상한. 정본은 config max_events. HC_MAX_EVENTS는 테스트 훅이다(launchd는 설정하지 않는다)."""
    env = os.environ.get("HC_MAX_EVENTS")
    return int(env) if env else int((cfg or load_config())["max_events"])

def load_events(days: int, now: float | None = None, max_events: int | None = None) -> list:
    """최근 이벤트를 읽는다.
    - 파일을 통째로 올리지 않고 줄 단위로 흘려 읽는다. 줄 구분은 \n 하나다.
    - 강제 종료 뒤 수집기가 같은 줄을 다시 적재할 수 있으므로 원문 줄이 완전히 같으면 한 번만 낸다(처음 본 순서 유지).
      중복 판정은 원문 대신 16바이트 blake2b 다이제스트 집합으로 한다.
    - 고유 줄이 max_events를 넘으면 EventLimitError(부분 결과로 성공 처리하지 않는다).
    - 잘린 줄·모르는 키·문자열이 아닌 ts처럼 해석 못 하는 줄은 건너뛰고 수를 LAST_LOAD_SKIPPED에 남긴다.
    - 파일 mtime으로 먼저 거르고, 이벤트 ts가 now - days 이전이거나 now 이후면 버린다(ts 해석 불가면 유지)."""
    global LAST_LOAD_SKIPPED
    now = _now() if now is None else now
    cap = _max_events() if max_events is None else max_events
    cutoff = now - days * 86400
    out, seen, skipped = [], set(), 0
    d = data_dir() / "events"
    if d.is_dir():
        for f in sorted(d.glob("*.jsonl")):
            if f.stat().st_mtime < now - (days + 1) * 86400:
                continue
            with f.open(encoding="utf-8", newline="\n") as fh:
                for raw in fh:
                    l = raw[:-1] if raw.endswith("\n") else raw
                    if not l.strip():
                        continue
                    h = hashlib.blake2b(l.encode("utf-8"), digest_size=16).digest()
                    if h in seen:
                        continue
                    seen.add(h)
                    if len(seen) > cap:
                        raise EventLimitError(f"이벤트 상한 초과: 고유 줄 {cap}개를 넘음({f.name})")
                    try:
                        e = loads(l)
                    except (ValueError, TypeError):
                        skipped += 1
                        continue
                    if not isinstance(e.ts, str):  # 정수 ts가 섞이면 뒤의 ts 정렬이 TypeError로 죽는다
                        skipped += 1
                        continue
                    dt = _parse_ts(e.ts)
                    if dt is not None and (dt.timestamp() < cutoff or dt.timestamp() > now):
                        continue
                    out.append(e)
    LAST_LOAD_SKIPPED = skipped
    return out

def _local_day(ts: str) -> str:
    dt = _parse_ts(ts)
    return dt.astimezone().date().isoformat() if dt else str(ts)[:10]

_PUNCT = re.compile(r"[.,!?~…·'\"()\[\]]")

def _norm_correction(text: str) -> str:
    s = re.sub(r"\d+", "<N>", text.lower())
    s = re.sub(r"(?:~|/)[\w.\-/]+", "<PATH>", s)
    s = _PUNCT.sub("", s)
    return re.sub(r"\s+", "", s)[:80]

def _finish(sig_type, key, group, cfg, context=None) -> Signal:
    group = sorted(group, key=lambda e: e.ts)
    samples, seen = [], set()
    for e in group:
        if e.text and e.text not in seen:
            samples.append(e.text)
            seen.add(e.text)
        if len(samples) >= cfg["samples_per_signal"]:
            break
    return Signal(id=f"{sig_type}:{key}", type=sig_type, key=key,
                  sessions=sorted({e.session_id for e in group}), count=len(group),
                  first_seen=group[0].ts, last_seen=group[-1].ts, samples=samples,
                  surfaces=sorted({e.surface for e in group}), context=(context or [])[:3],
                  days=sorted({_local_day(e.ts) for e in group}))

def detect_corrections(events, cfg) -> list:
    th = cfg["thresholds"]["correction"]
    by_session = defaultdict(list)
    for e in events:
        by_session[e.session_id].append(e)
    groups, ctx = defaultdict(list), defaultdict(list)
    for sid, evs in by_session.items():
        evs.sort(key=lambda e: e.ts)
        for i, e in enumerate(evs):
            if e.kind == "user_msg" and is_correction(e.text):
                key = hashlib.sha1(_norm_correction(e.text).encode()).hexdigest()[:12]
                groups[key].append(e)
                prev = [p for p in evs[max(0, i - 5):i] if p.kind == "tool_call"]
                ctx[key] += [(p.ts, f"{p.tool}: {p.text[:120]}") for p in prev[-2:]]
    out = []
    for key, g in groups.items():
        days = {_local_day(e.ts) for e in g}
        if len({e.session_id for e in g}) >= th["min_sessions"] and len(days) >= th["min_days"]:
            out.append(_finish("correction", key, g, cfg, _latest_unique(ctx[key])))
    return out

def _latest_unique(pairs, limit=3) -> list:
    """(ts, 요약) 목록을 최신순으로 정렬해 중복 요약을 빼고 limit개만."""
    out = []
    for _, s in sorted(pairs, key=lambda x: x[0], reverse=True):
        if s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out

def detect_failure_loops(events, cfg) -> list:
    th = cfg["thresholds"]["failure_loop"]
    per = defaultdict(lambda: defaultdict(list))
    for e in events:
        if e.kind in ("tool_error", "permission_denied", "hook_error") and e.error_sig:
            per[e.error_sig][e.session_id].append(e)
    out = []
    for sig, sessions in per.items():
        looping = {sid: g for sid, g in sessions.items() if len(g) >= th["min_repeats_in_session"]}
        if len(looping) >= th["min_sessions"]:
            out.append(_finish("failure_loop", sig, [e for g in looping.values() for e in g], cfg))
    return out

_PLACEHOLDER = re.compile(r"^\s*(\[(L4|L3|SECRET|[A-Z]+)\]\s*)+$")

def _norm_instruction(text: str) -> str:
    s = text.lower()
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"(?:~|/)[\w.\-/]+", " ", s)
    s = re.sub(r"\[[a-z0-9]+\]", " ", s)
    s = re.sub(r"\d+", " ", s)
    return re.sub(r"\s+", "", s)

def _grams(s: str) -> set:
    return {s[i:i + 3] for i in range(max(0, len(s) - 2))}

def _stable_key(evs) -> str:
    """클러스터에서 가장 자주 나온 정규화 문구(동률이면 사전순 최소)의 sha1 앞 12자.
    창이 밀려 가장 오래된 구성원이 빠져도 id가 유지되어 기각 억제가 풀리지 않는다."""
    freq = defaultdict(int)
    for e in evs:
        freq[_norm_instruction(e.text)] += 1
    top = min(freq, key=lambda n: (-freq[n], n))
    return hashlib.sha1(top.encode()).hexdigest()[:12]

def detect_repeated_instructions(events, cfg) -> list:
    global LAST_RESUME_SKIPPED
    th = cfg["thresholds"]["repeated_instruction"]
    cands, resume = [], 0
    # 같은 이벤트 집합이면 입력 순서와 무관하게 같은 클러스터가 나오도록 ts 오름차순으로 탐욕 군집화
    events = sorted(events, key=lambda e: (e.ts, e.session_id, e.text))
    for e in events:
        if e.kind != "user_msg" or not e.text or _PLACEHOLDER.match(e.text) or is_non_user(e.text):
            continue
        n = _norm_instruction(e.text)
        if len(n) < 6:
            continue
        if is_resume(e.text):   # 길이 필터를 통과해 후보가 됐을 발화만 센다
            resume += 1
            continue
        cands.append((e, _grams(n)))
    clusters: list[list] = []
    for e, g in cands:
        for cl in clusters:
            rep = cl[0][1]
            if len(g & rep) / max(1, len(g | rep)) >= th["jaccard"]:
                cl.append((e, g))
                break
        else:
            clusters.append([(e, g)])
    out = []
    for cl in clusters:
        evs = [e for e, _ in cl]
        if len({e.session_id for e in evs}) >= th["min_sessions"]:
            out.append(_finish("repeated_instruction", _stable_key(evs), evs, cfg))
    LAST_RESUME_SKIPPED = resume
    return out

def _decision_entries(d) -> list:
    """결정 한 줄을 (signal_id, decision, count) 목록으로.
    새 형식은 후보당 한 줄(signal_ids + signal_counts), 옛 형식은 신호당 한 줄(signal_id + count)."""
    if not isinstance(d, dict) or d.get("decision") not in ("approve", "reject"):
        return []
    if "signal_ids" in d or "signal_counts" in d:
        sids, counts = d.get("signal_ids"), d.get("signal_counts")
        if not (isinstance(sids, list) and isinstance(counts, dict) and isinstance(d.get("fingerprint"), str)):
            return []
        return [(sid, d["decision"], counts.get(sid, 0)) for sid in sids if isinstance(sid, str)]
    if isinstance(d.get("signal_id"), str):
        return [(d["signal_id"], d["decision"], d.get("count", 0))]
    return []

def load_decisions() -> dict:
    """decisions.jsonl에서 signal_id별 마지막 결정 {"decision","count"}. 해석 못 하는 줄은 건너뛴다."""
    p = data_dir() / "decisions.jsonl"
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for sid, decision, count in _decision_entries(d):
                out[sid] = {"decision": decision, "count": count if isinstance(count, int) and not isinstance(count, bool) else 0}
    return out

def suppress(signals, decisions, ratio) -> list:
    keep = []
    for s in signals:
        d = decisions.get(s.id)
        if d is None:
            keep.append(s)
        elif d["decision"] == "reject" and s.count >= ratio * max(1, d["count"]):
            keep.append(s)
    return keep

def _scope(events, days: int) -> dict:
    """탐지가 실제로 읽은 창. 리포트 '본 범위'의 정본이다(증분 수집 통계와 다르다)."""
    per = {"claude": 0, "codex": 0}
    for e in events:
        per[e.surface] = per.get(e.surface, 0) + 1
    dated = [(dt, e.ts) for e in events for dt in [_parse_ts(e.ts)] if dt is not None]
    return {"days": days, "events": per, "sessions": len({(e.surface, e.session_id) for e in events}),
            "skipped": LAST_LOAD_SKIPPED,
            "lang": detect_lang(e.text for e in events if e.kind == "user_msg" and e.text and not is_non_user(e.text)),
            "resume_skipped": LAST_RESUME_SKIPPED,
            "earliest": min(dated)[1] if dated else "", "latest": max(dated)[1] if dated else ""}

def run_detect(days: int, now: float | None = None):
    cfg = load_config()
    now = _now() if now is None else now
    events = load_events(days, now, _max_events(cfg))
    raw = detect_corrections(events, cfg) + detect_failure_loops(events, cfg) + detect_repeated_instructions(events, cfg)
    scope = _scope(events, days)
    observed = {s.id: {"last_seen": s.last_seen, "count": s.count} for s in raw}   # 효과 확인용(억제·거르기 전)
    sigs = suppress(raw, load_decisions(), cfg["suppress_rejected_until_ratio"])
    sigs, filtered = gate_signals(sigs, events, cfg, now)
    out_dir = data_dir() / "signals"
    out_dir.mkdir(exist_ok=True)
    lt = time.localtime(now)
    path = out_dir / f"{time.strftime('%Y-%m-%d', lt)}.json"
    write_private(path, json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", lt), "scope": scope,
                                    "signals": [asdict(s) for s in sigs], "filtered": filtered, "observed": observed},
                                   ensure_ascii=False, indent=1))
    return path

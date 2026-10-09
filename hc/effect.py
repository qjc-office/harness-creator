"""적용 원장(applied.jsonl)과 재발 확인. 적용한 후보의 신호가 적용 뒤에 다시 나왔는지 본다."""
from __future__ import annotations
import json
from datetime import datetime
from .config import data_dir
from .events import parse_ts
from .fsutil import append_line

def applied_path():
    return data_dir() / "applied.jsonl"

def _now_iso() -> str:
    """`+09:00` 형식(콜론 있음). 3.9 fromisoformat은 `%z`의 `+0900`을 못 읽는다."""
    return datetime.now().astimezone().isoformat(timespec="seconds")

def record_applied(entry: dict) -> None:
    rec = {**entry, "op": "apply"}
    rec.setdefault("ts", _now_iso())
    append_line(applied_path(), (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))

def record_undo(candidate_id: str) -> None:
    rec = {"op": "undo", "candidate_id": candidate_id, "ts": _now_iso()}
    append_line(applied_path(), (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))

def load_applied() -> list:
    p = applied_path()
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("op") in ("apply", "undo") and isinstance(d.get("candidate_id"), str):
                out.append(d)
    return out

def active_applied() -> list:
    last = {}
    for d in load_applied():
        last[d["candidate_id"]] = d
    return [d for d in last.values() if d["op"] == "apply"]

def effect_report(applied: list, observed: dict) -> list:
    out = []
    for e in applied:
        at = parse_ts(e.get("ts"))
        seen = [observed[s] for s in e.get("signal_ids") or [] if s in observed]
        stamps = [(parse_ts(o.get("last_seen")), o.get("last_seen")) for o in seen]
        stamps = [(dt, raw) for dt, raw in stamps if dt is not None]
        latest = max(stamps)[1] if stamps else ""
        recurred = bool(at and any(dt > at for dt, _ in stamps))
        out.append({"candidate_id": e["candidate_id"], "applied_at": e.get("ts", ""), "recurred": recurred,
                    "last_seen": latest})
    return out

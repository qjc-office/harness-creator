from datetime import datetime, timezone
from hc.config import load_config
from hc.detect import Signal
from hc.events import Event
from hc.gates import session_starts, batch_groups, gate_signals

CFG = load_config()
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp()

def ev(sid, ts, kind="user_msg", text="hi"):
    return Event("claude", sid, ts, "/w", kind, text)

def sig(sid_list, last="2026-10-09T00:00:00Z", typ="repeated_instruction"):
    return Signal(id=f"{typ}:k", type=typ, key="k", sessions=sid_list, count=len(sid_list),
                  first_seen="2026-10-08T00:00:00Z", last_seen=last, samples=["x"], surfaces=["claude"])

def test_g1_stale_signal_filtered():
    kept, filt = gate_signals([sig(["a", "b", "c"], last="2026-10-05T00:00:00Z")], [], CFG, NOW)
    assert kept == [] and filt == [{"gate": "G1", "signal_id": "repeated_instruction:k"}]

def test_g1_keeps_recent():
    evs = [ev(s, f"2026-10-08T0{i}:00:00Z", text=f"first {s}") for i, s in enumerate("abc")]
    kept, filt = gate_signals([sig(["a", "b", "c"], last="2026-10-07T00:00:00Z")], evs, CFG, NOW)
    assert len(kept) == 1 and filt == []

def test_g2_batch_sessions_count_once():
    # 같은 첫 발화로 10초 안에 시작한 세션 3개는 1개로 센다 → 3세션 임계 미달
    evs = [ev(s, f"2026-10-08T00:00:0{i}Z", text="nightly job") for i, s in enumerate("abc")]
    kept, filt = gate_signals([sig(["a", "b", "c"])], evs, CFG, NOW)
    assert kept == [] and filt == [{"gate": "G2", "signal_id": "repeated_instruction:k"}]

def test_g2_different_first_message_not_batched():
    evs = [ev(s, f"2026-10-08T00:00:0{i}Z", text=f"task {s}") for i, s in enumerate("abc")]
    kept, _ = gate_signals([sig(["a", "b", "c"])], evs, CFG, NOW)
    assert len(kept) == 1 and len(kept[0].groups) == 3

def test_batch_window_anchored_on_first_session():
    starts = {"a": (0.0, "h"), "b": (8.0, "h"), "c": (16.0, "h")}
    g = batch_groups(starts, 10)
    assert g["a"] == g["b"] == "a" and g["c"] == "c"

def test_session_starts_uses_earliest_event():
    st = session_starts([ev("a", "2026-10-08T00:00:05Z", text="second"),
                         ev("a", "2026-10-08T00:00:01Z", "tool_call", ""),
                         ev("a", "2026-10-08T00:00:03Z", text="first")])
    t, h = st["a"]
    import hashlib
    assert t == datetime(2026, 10, 8, 0, 0, 1, tzinfo=timezone.utc).timestamp()
    assert h == hashlib.sha1("first".encode()).hexdigest()[:12]

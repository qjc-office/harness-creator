import json
from hc.effect import record_applied, record_undo, load_applied, active_applied, effect_report

def test_apply_undo_cycle(cache):
    record_applied({"op": "apply", "candidate_id": "hc-2026-10-09-01", "signal_ids": ["a"], "ts": "2026-10-09T10:00:00+09:00"})
    record_applied({"op": "apply", "candidate_id": "hc-2026-10-09-02", "signal_ids": ["b"], "ts": "2026-10-09T10:00:00+09:00"})
    record_undo("hc-2026-10-09-01")
    assert [e["candidate_id"] for e in active_applied()] == ["hc-2026-10-09-02"]
    assert len(load_applied()) == 3
    assert oct((cache / "applied.jsonl").stat().st_mode & 0o777) == "0o600"

def test_broken_line_skipped(cache):
    cache.mkdir()
    (cache / "applied.jsonl").write_text('{"op": "apply", "candidate_id": "x", "ts": "t"}\n{"op": "ap')
    assert [e["candidate_id"] for e in load_applied()] == ["x"]

def test_effect_recurred_and_clean():
    applied = [{"candidate_id": "c1", "signal_ids": ["s1"], "ts": "2026-10-05T00:00:00+00:00"},
               {"candidate_id": "c2", "signal_ids": ["s2", "s3"], "ts": "2026-10-05T00:00:00+00:00"},
               {"candidate_id": "c3", "signal_ids": ["s4"], "ts": "2026-10-05T00:00:00+00:00"}]
    observed = {"s1": {"last_seen": "2026-10-07T00:00:00Z", "count": 3},
                "s2": {"last_seen": "2026-10-04T00:00:00Z", "count": 2}}
    r = {e["candidate_id"]: e for e in effect_report(applied, observed)}
    assert r["c1"]["recurred"] is True and r["c1"]["last_seen"] == "2026-10-07T00:00:00Z"
    assert r["c2"]["recurred"] is False and r["c3"]["recurred"] is False

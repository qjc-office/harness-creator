import hashlib, json, random, re as _re
from datetime import datetime, timezone
from hc import detect
from hc.events import Event, dumps
from hc.config import load_config
from hc.detect import detect_repeated_instructions, suppress, Signal, run_detect, load_decisions, _norm_instruction

CFG = load_config()

def ev(sid, ts, kind, text="", tool="", sig="", surface="claude"):
    return Event(surface, sid, ts, "/w", kind, text, tool, sig)

def u(sid, text, ts="2026-10-01T00:00:00Z"):
    return Event("claude", sid, ts, "/w", "user_msg", text)

def test_similar_instructions_across_three_sessions():
    evs = [u("a", "PR 만들고 CI 통과하면 머지해줘 12번"),
           u("b", "PR 만들고 CI 통과하면 머지해줘 98번"),
           u("c", "PR 만들고 CI 통과하면 머지해 줘"),
           u("d", "오늘 날씨 어때")]
    sigs = detect_repeated_instructions(evs, CFG)
    assert len(sigs) == 1 and sorted(sigs[0].sessions) == ["a", "b", "c"]

def test_same_session_repeats_do_not_count():
    evs = [u("a", "PR 만들고 머지해줘")] * 5
    assert detect_repeated_instructions(evs, CFG) == []

def test_non_user_payloads_excluded():
    evs = [u(s, "<task-notification> done </task-notification>") for s in "abc"]
    assert detect_repeated_instructions(evs, CFG) == []

def test_l4_placeholder_events_not_clustered():
    evs = [u(s, "[L4]") for s in "abcd"]
    assert detect_repeated_instructions(evs, CFG) == []

def test_rejected_signal_suppressed_until_doubled():
    s = Signal(id="failure_loop:x", type="failure_loop", key="x", count=7)
    dec = {"failure_loop:x": {"decision": "reject", "count": 4}}
    assert suppress([s], dec, 2.0) == []
    s2 = Signal(id="failure_loop:x", type="failure_loop", key="x", count=8)
    assert suppress([s2], dec, 2.0) == [s2]

def test_approved_signal_also_suppressed():
    s = Signal(id="correction:y", type="correction", key="y", count=50)
    assert suppress([s], {"correction:y": {"decision": "approve", "count": 3}}, 2.0) == []

def test_run_detect_writes_file(cache):
    (cache / "events").mkdir(parents=True)
    (cache / "events" / "2026-10-09.jsonl").write_text("")
    path = run_detect(7)
    data = json.loads(path.read_text())
    assert data["signals"] == [] and "generated_at" in data

def test_load_decisions_last_decision_per_signal_wins(cache):
    (cache / "reports").mkdir(parents=True)
    lines = [
        {"candidate_id": "c1", "signal_id": "correction:y", "decision": "reject", "reason": "r", "count": 3, "ts": "2026-10-01T00:00:00Z"},
        {"candidate_id": "c2", "signal_id": "failure_loop:x", "decision": "approve", "reason": "", "count": 5, "ts": "2026-10-01T00:00:01Z"},
        {"candidate_id": "c3", "signal_id": "correction:y", "decision": "approve", "reason": "", "count": 6, "ts": "2026-10-02T00:00:00Z"},
    ]
    (cache / "decisions.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    assert load_decisions() == {"correction:y": {"decision": "approve", "count": 6},
                                "failure_loop:x": {"decision": "approve", "count": 5}}

def test_load_decisions_missing_file_is_empty(cache):
    assert load_decisions() == {}

def test_load_events_skips_non_string_ts(cache):
    d = cache / "events"
    d.mkdir(parents=True)
    good = [dumps(Event("claude", s, "2026-10-02T00:00:00Z", "/w", "user_msg", "PR 만들고 CI 통과하면 머지해줘")) for s in "ab"]
    bad = json.dumps({"surface": "claude", "session_id": "c", "ts": 123, "cwd": "/w",
                      "kind": "user_msg", "text": "PR 만들고 CI 통과하면 머지해줘", "tool": "", "error_sig": ""})
    (d / "2026-10-02.jsonl").write_text("\n".join(good + [bad]) + "\n", encoding="utf-8")
    now = datetime(2026, 10, 3, tzinfo=timezone.utc).timestamp()
    evs = detect.load_events(7, now=now)
    assert [e.session_id for e in evs] == ["a", "b"]
    assert detect.LAST_LOAD_SKIPPED == 1
    # 남은 이벤트로 탐지가 예외 없이 돈다
    assert detect_repeated_instructions(evs, CFG) == []
    detect.detect_corrections(evs, CFG)
    detect.detect_failure_loops(evs, CFG)

def test_load_decisions_skips_broken_lines(cache):
    (cache / "reports").mkdir(parents=True)
    ok = json.dumps({"candidate_id": "c1", "signal_id": "correction:y", "decision": "reject", "reason": "", "count": 2, "ts": "t"})
    (cache / "decisions.jsonl").write_text(ok + "\n" + ok[: len(ok) // 2], encoding="utf-8")
    assert load_decisions() == {"correction:y": {"decision": "reject", "count": 2}}

# --- 신호 id 안정화 ---
A = "PR 만들고 CI 통과하면 머지해줘"
B = "PR 만들고 CI 통과하면 머지해줘요"

def _key(text):
    return hashlib.sha1(_norm_instruction(text).encode()).hexdigest()[:12]

def test_key_is_most_frequent_normalized_text():
    assert _norm_instruction(A) != _norm_instruction(B)
    evs = [u("a", B, "2026-10-01T00:00:00Z"),
           u("b", A, "2026-10-01T00:00:01Z"),
           u("c", A, "2026-10-01T00:00:02Z")]
    sigs = detect_repeated_instructions(evs, CFG)
    assert len(sigs) == 1 and sigs[0].key == _key(A)
    assert sigs[0].id == f"repeated_instruction:{_key(A)}"

def test_id_stable_when_window_drops_oldest():
    evs = [u("a", B, "2026-10-01T00:00:00Z"),
           u("b", A, "2026-10-02T00:00:00Z"),
           u("c", A, "2026-10-03T00:00:00Z"),
           u("d", A, "2026-10-04T00:00:00Z")]
    full = detect_repeated_instructions(evs, CFG)
    shifted = detect_repeated_instructions(evs[1:], CFG)
    assert len(full) == 1 and len(shifted) == 1
    assert full[0].id == shifted[0].id

def test_shuffled_input_gives_same_signals():
    evs = [u("a", B, "2026-10-01T00:00:00Z"),
           u("b", A, "2026-10-02T00:00:00Z"),
           u("c", A, "2026-10-03T00:00:00Z"),
           u("d", "릴리스 노트 쓰고 태그 달아서 배포해줘", "2026-10-01T01:00:00Z"),
           u("e", "릴리스 노트 쓰고 태그 달아서 배포해줘", "2026-10-02T01:00:00Z"),
           u("f", "릴리스 노트 쓰고 태그 달아서 배포해 줘", "2026-10-03T01:00:00Z"),
           u("g", "오늘 날씨 어때", "2026-10-02T02:00:00Z")]
    base = sorted((s.id, tuple(s.sessions), s.count) for s in detect_repeated_instructions(evs, CFG))
    assert len(base) == 2
    rng = random.Random(7)
    for _ in range(20):
        sh = evs[:]
        rng.shuffle(sh)
        got = sorted((s.id, tuple(s.sessions), s.count) for s in detect_repeated_instructions(sh, CFG))
        assert got == base

def test_run_detect_generated_at_is_iso_local_timestamp(cache):
    (cache / "events").mkdir(parents=True)
    path = run_detect(7)
    data = json.loads(path.read_text())
    assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", data["generated_at"])
    assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", path.name)

# --- 신호 파일에 탐지 범위를 남긴다 ---
def test_run_detect_writes_scope(cache):
    d = cache / "events"
    d.mkdir(parents=True)
    now = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc).timestamp()
    lines = [dumps(Event("claude", "a", "2026-10-03T00:00:00Z", "/w", "user_msg", "x")),
             dumps(Event("claude", "a", "2026-10-04T00:00:00Z", "/w", "user_msg", "y")),
             dumps(Event("claude", "b", "2026-10-05T00:00:00Z", "/w", "user_msg", "z")),
             dumps(Event("codex", "c", "2026-10-08T01:00:00+00:00", "/w", "user_msg", "w")),
             "{broken"]
    f = d / "2026-10-08.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    import os
    os.utime(f, (now - 3600, now - 3600))
    scope = json.loads(run_detect(7, now=now).read_text())["scope"]
    assert scope == {"days": 7, "events": {"claude": 3, "codex": 1}, "sessions": 3, "skipped": 1,
                     "lang": "en", "resume_skipped": 0,
                     "earliest": "2026-10-03T00:00:00Z", "latest": "2026-10-08T01:00:00+00:00"}

def test_run_detect_scope_empty(cache):
    (cache / "events").mkdir(parents=True)
    scope = json.loads(run_detect(7).read_text())["scope"]
    assert scope["events"] == {"claude": 0, "codex": 0} and scope["sessions"] == 0
    assert scope["earliest"] == "" and scope["latest"] == ""

# --- 새 결정 형식(후보당 한 줄)에서도 신호별 억제가 동작하고 옛 형식도 읽는다 ---
def _new(cid, sids, counts, decision):
    return {"candidate_id": cid, "fingerprint": "abcd1234", "signal_ids": sids, "signal_counts": counts,
            "decision": decision, "reason": "", "ts": "2026-10-09T00:00:00"}

def test_load_decisions_reads_new_format(cache):
    (cache / "reports").mkdir(parents=True)
    lines = [_new("c1", ["correction:y", "failure_loop:x"], {"correction:y": 3, "failure_loop:x": 5}, "reject")]
    (cache / "decisions.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    assert load_decisions() == {"correction:y": {"decision": "reject", "count": 3},
                                "failure_loop:x": {"decision": "reject", "count": 5}}

def test_load_decisions_mixed_old_then_new_last_wins(cache):
    (cache / "reports").mkdir(parents=True)
    lines = [
        {"candidate_id": "c0", "signal_id": "correction:y", "decision": "approve", "reason": "", "count": 9, "ts": "t"},
        _new("c1", ["correction:y"], {"correction:y": 4}, "reject"),
        {"candidate_id": "c2", "signal_id": "failure_loop:x", "decision": "reject", "reason": "", "count": 2, "ts": "t"},
    ]
    (cache / "decisions.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    assert load_decisions() == {"correction:y": {"decision": "reject", "count": 4},
                                "failure_loop:x": {"decision": "reject", "count": 2}}

def test_suppression_from_new_format(cache):
    (cache / "reports").mkdir(parents=True)
    (cache / "decisions.jsonl").write_text(
        json.dumps(_new("c1", ["correction:y"], {"correction:y": 3}, "reject")) + "\n")
    dec = load_decisions()
    assert suppress([Signal("correction:y", "correction", "y", count=5)], dec, 2.0) == []
    s6 = Signal("correction:y", "correction", "y", count=6)
    assert suppress([s6], dec, 2.0) == [s6]

def test_load_decisions_ignores_truncated_new_record(cache):
    (cache / "reports").mkdir(parents=True)
    full = json.dumps(_new("c1", ["correction:y"], {"correction:y": 3}, "reject"))
    (cache / "decisions.jsonl").write_text(full[: len(full) - 5], encoding="utf-8")
    assert load_decisions() == {}

def test_resume_messages_not_clustered():
    from hc.detect import detect_repeated_instructions
    import hc.detect as D
    evs = [ev(f"s{i}", f"2026-10-0{i}T00:00:00Z", "user_msg", "continue") for i in range(1, 6)]
    assert detect_repeated_instructions(evs, CFG) == [] and D.LAST_RESUME_SKIPPED == 5

# --- run_detect에 G1·G2·observed가 실제로 배선돼 있는가 ---
STALE = "릴리스 노트 쓰고 태그 달아서 배포해줘"
BATCH = "야간 점검 돌리고 결과 표로 정리해줘"
KEEP = "PR 만들고 CI 통과하면 머지해줘"

def test_run_detect_applies_g1_g2_and_records_observed(cache):
    import os
    d = cache / "events"; d.mkdir(parents=True)
    now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp()
    evs = [Event("claude", f"st{i}", f"2026-10-0{3 + i}T00:00:00Z", "/w", "user_msg", STALE) for i in range(3)]
    evs += [Event("claude", f"bt{i}", f"2026-10-08T00:00:0{i}Z", "/w", "user_msg", BATCH) for i in range(3)]
    evs += [Event("claude", f"kp{i}", f"2026-10-08T0{i + 2}:00:00Z", "/w", "user_msg", KEEP) for i in range(3)]
    f = d / "2026-10-08.jsonl"
    f.write_text("".join(dumps(e) + "\n" for e in evs), encoding="utf-8")
    os.utime(f, (now - 3600, now - 3600))
    data = json.loads(run_detect(7, now=now).read_text())
    sid = lambda t: f"repeated_instruction:{_key(t)}"
    assert [s["id"] for s in data["signals"]] == [sid(KEEP)]
    assert data["signals"][0]["groups"] == ["kp0", "kp1", "kp2"]
    assert sorted(data["filtered"], key=lambda x: x["gate"]) == [{"gate": "G1", "signal_id": sid(STALE)},
                                                                  {"gate": "G2", "signal_id": sid(BATCH)}]
    assert set(data["observed"]) == {sid(STALE), sid(BATCH), sid(KEEP)}
    assert data["observed"][sid(STALE)] == {"last_seen": "2026-10-05T00:00:00Z", "count": 3}

# --- scope.lang은 비사용자 발화를 빼고, resume_skipped는 길이 필터 통과분만 ---
def test_scope_lang_ignores_non_user_messages(cache):
    (cache / "events").mkdir(parents=True)
    evs = [Event("claude", s, "2026-10-02T00:00:00Z", "/w", "user_msg", "<task-notification> done </task-notification>")
           for s in "abc"] + [Event("claude", "d", "2026-10-02T00:00:00Z", "/w", "user_msg", "테스트 돌려줘")]
    assert detect._scope(evs, 7)["lang"] == "ko"

def test_resume_skipped_counts_only_length_passing():
    evs = [u(f"r{i}", "계속", f"2026-10-0{i + 1}T00:00:00Z") for i in range(3)]
    evs += [u(f"c{i}", "continue", f"2026-10-0{i + 1}T01:00:00Z") for i in range(2)]
    assert detect_repeated_instructions(evs, CFG) == [] and detect.LAST_RESUME_SKIPPED == 2

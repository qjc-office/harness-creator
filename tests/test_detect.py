from datetime import datetime, timezone
from hc import detect
from hc.events import Event, dumps
from hc.config import load_config
from hc.detect import detect_corrections, detect_failure_loops, load_events

CFG = load_config()
# 테스트 기준 시각: 2026-10-03T00:00:00Z (이벤트 ts 2026-10-01~02가 7일 창 안에 든다)
NOW = datetime(2026, 10, 3, tzinfo=timezone.utc).timestamp()

def ev(sid, ts, kind, text="", tool="", sig="", surface="claude"):
    return Event(surface, sid, ts, "/w", kind, text, tool, sig)

def test_correction_needs_two_sessions_two_days():
    one_day = [ev("a", "2026-10-01T01:00:00Z", "user_msg", "그렇게 하지 마"),
               ev("b", "2026-10-01T05:00:00Z", "user_msg", "그렇게 하지 마")]
    assert detect_corrections(one_day, CFG) == []
    two_days = one_day[:1] + [ev("b", "2026-10-02T05:00:00Z", "user_msg", "그렇게 하지 마")]
    sigs = detect_corrections(two_days, CFG)
    assert len(sigs) == 1 and sorted(sigs[0].sessions) == ["a", "b"]

def test_correction_context_is_preceding_tool_calls():
    evs = [ev("a", "2026-10-01T00:00:00Z", "tool_call", "rm -rf build", "Bash"),
           ev("a", "2026-10-01T00:00:01Z", "user_msg", "다시는 지우지 마"),
           ev("b", "2026-10-02T00:00:00Z", "tool_call", "rm -rf dist", "Bash"),
           ev("b", "2026-10-02T00:00:01Z", "user_msg", "다시는 지우지 마")]
    s = detect_corrections(evs, CFG)[0]
    assert any("rm -rf" in c for c in s.context)

def test_failure_loop_three_in_session_two_sessions():
    evs = []
    for sid in ("a", "b"):
        for i in range(3):
            evs.append(ev(sid, f"2026-10-0{1 if sid=='a' else 2}T00:00:0{i}Z", "tool_error", "ENOENT", "Bash", "sig1"))
    sigs = detect_failure_loops(evs, CFG)
    assert len(sigs) == 1 and sigs[0].count == 6 and sigs[0].key == "sig1"

def test_failure_loop_below_threshold():
    evs = [ev("a", "t", "tool_error", "E", "Bash", "s"), ev("a", "t", "tool_error", "E", "Bash", "s"),
           ev("b", "t", "tool_error", "E", "Bash", "s")]
    assert detect_failure_loops(evs, CFG) == []

def test_permission_and_hook_errors_count_as_failure():
    evs = []
    for sid in ("a", "b"):
        for i in range(3):
            evs.append(ev(sid, "t", "permission_denied", "permission-rule", "Bash", "p1"))
    assert detect_failure_loops(evs, CFG)[0].type == "failure_loop"

def test_surfaces_recorded():
    evs = []
    for sid, sf in (("a", "claude"), ("b", "codex")):
        for i in range(3):
            evs.append(ev(sid, "t", "tool_error", "E", "exec", "z", sf))
    assert sorted(detect_failure_loops(evs, CFG)[0].surfaces) == ["claude", "codex"]

def test_load_events_dedupes_identical_lines(cache):
    d = cache / "events"
    d.mkdir(parents=True)
    a = dumps(ev("a", "2026-10-01T00:00:00Z", "user_msg", "첫째"))
    b = dumps(ev("a", "2026-10-01T00:00:01Z", "user_msg", "둘째"))
    c = dumps(ev("b", "2026-10-02T00:00:00Z", "user_msg", "셋째"))
    # 강제 종료 뒤 재적재: 같은 파일 안 중복 + 다음 날 파일로의 중복
    (d / "2026-10-01.jsonl").write_text(f"{a}\n{b}\n{a}\n", encoding="utf-8")
    (d / "2026-10-02.jsonl").write_text(f"{b}\n{c}\n", encoding="utf-8")
    got = load_events(7, now=NOW)
    assert [e.text for e in got] == ["첫째", "둘째", "셋째"]

def test_load_events_skips_broken_lines(cache):
    d = cache / "events"
    d.mkdir(parents=True)
    good = dumps(ev("a", "2026-10-02T00:00:00Z", "user_msg", "정상"))
    unknown_key = good[:-1] + ', "extra": 1}'
    truncated = good[: len(good) // 2]  # 강제 종료로 잘린 마지막 줄
    (d / "2026-10-02.jsonl").write_text(f"{good}\n{unknown_key}\n{truncated}", encoding="utf-8")
    got = load_events(7, now=NOW)
    assert [e.text for e in got] == ["정상"]
    assert detect.LAST_LOAD_SKIPPED == 2

def test_load_events_drops_events_older_than_days(cache):
    d = cache / "events"
    d.mkdir(parents=True)
    old = dumps(ev("a", "2026-09-20T00:00:00Z", "user_msg", "오래됨"))
    recent = dumps(ev("a", "2026-10-02T00:00:00Z", "user_msg", "최근"))
    bad_ts = dumps(ev("a", "not-a-time", "user_msg", "시각불명"))
    # 파일 mtime은 지금(테스트 실행 시각)이라 mtime 사전 필터는 통과한다
    (d / "2026-10-02.jsonl").write_text(f"{old}\n{recent}\n{bad_ts}\n", encoding="utf-8")
    got = load_events(7, now=NOW)
    assert [e.text for e in got] == ["최근", "시각불명"]

def test_correction_day_bucket_is_kst(monkeypatch):
    import time as _t
    monkeypatch.setenv("TZ", "Asia/Seoul"); _t.tzset()
    # 둘 다 KST 2026-10-02 → 1일 → min_days 2 미달
    evs = [ev("a", "2026-10-01T16:00:00Z", "user_msg", "그렇게 하지 마"),
           ev("b", "2026-10-01T23:00:00Z", "user_msg", "그렇게 하지 마")]
    assert detect_corrections(evs, CFG) == []
    # UTC로는 같은 날(10-01)이지만 KST로는 10-01·10-02 → 2일 → 신호
    evs2 = [ev("a", "2026-10-01T10:00:00Z", "user_msg", "그렇게 하지 마"),
            ev("b", "2026-10-01T16:00:00Z", "user_msg", "그렇게 하지 마")]
    assert len(detect_corrections(evs2, CFG)) == 1

def test_correction_context_prefers_latest_and_dedupes():
    evs = []
    for sid, day in (("a", "01"), ("b", "02"), ("c", "03")):
        evs += [ev(sid, f"2026-10-{day}T00:00:00Z", "tool_call", f"cmd-{sid}-1", "Bash"),
                ev(sid, f"2026-10-{day}T00:00:01Z", "tool_call", f"cmd-{sid}-2", "Bash"),
                ev(sid, f"2026-10-{day}T00:00:02Z", "user_msg", "다시는 지우지 마")]
    s = detect_corrections(evs, CFG)[0]
    assert s.context == ["Bash: cmd-c-2", "Bash: cmd-c-1", "Bash: cmd-b-2"]
    same = []
    for sid, day in (("a", "01"), ("b", "02")):
        same += [ev(sid, f"2026-10-{day}T00:00:00Z", "tool_call", "rm -rf build", "Bash"),
                 ev(sid, f"2026-10-{day}T00:00:01Z", "user_msg", "다시는 지우지 마")]
    assert detect_corrections(same, CFG)[0].context == ["Bash: rm -rf build"]

def test_correction_punctuation_groups_together():
    evs = [ev("a", "2026-10-01T01:00:00Z", "user_msg", "그렇게 하지 마"),
           ev("b", "2026-10-02T01:00:00Z", "user_msg", "그렇게 하지 마.")]
    sigs = detect_corrections(evs, CFG)
    assert len(sigs) == 1 and sigs[0].count == 2

def test_failure_loop_second_session_below_repeat_threshold():
    evs = [ev("a", "t", "tool_error", "E", "Bash", "s") for _ in range(3)]
    evs.append(ev("b", "t", "tool_error", "E", "Bash", "s"))
    assert detect_failure_loops(evs, CFG) == []

# --- 이벤트는 줄 단위로 흘려 읽고, 상한을 넘으면 성공으로 끝내지 않는다 ---
import json, pathlib, subprocess, sys, time as _time
import pytest

def _evfile(cache, lines, name="2026-10-01.jsonl"):
    d = cache / "events"; d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("".join(l + "\n" for l in lines), encoding="utf-8")

def _three():
    return [dumps(ev(s, f"2026-10-0{i}T00:00:00Z", "user_msg", f"문장{i}")) for i, s in ((1, "a"), (2, "b"), (2, "c"))]

def test_config_has_max_events():
    assert CFG["max_events"] == 2000000

def test_load_events_does_not_read_whole_file(cache, monkeypatch):
    _evfile(cache, _three())
    real = pathlib.Path.read_text
    def guard(self, *a, **k):
        if self.suffix == ".jsonl":
            raise AssertionError("이벤트 파일을 통째로 읽었다")
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", guard)
    assert [e.text for e in load_events(7, now=NOW)] == ["문장1", "문장2", "문장2"]

def test_load_events_dedupe_is_exact(cache):
    a = dumps(ev("a", "2026-10-01T00:00:00Z", "user_msg", "같은듯"))
    b = dumps(ev("a", "2026-10-01T00:00:00Z", "user_msg", "같은듯."))   # 한 글자 차이는 다른 줄
    _evfile(cache, [a, b, a, b])
    assert [e.text for e in load_events(7, now=NOW)] == ["같은듯", "같은듯."]

def test_load_events_line_separator_inside_text_is_one_event(cache):
    # 줄 구분은 \n 만이다. 본문 속 U+2028 같은 유니코드 줄 구분자로 이벤트가 쪼개지지 않는다
    _evfile(cache, [dumps(ev("a", "2026-10-01T00:00:00Z", "user_msg", "앞 뒤"))])
    got = load_events(7, now=NOW)
    assert [e.text for e in got] == ["앞 뒤"] and detect.LAST_LOAD_SKIPPED == 0

def test_load_events_cap_exceeded_raises(cache):
    _evfile(cache, _three())
    with pytest.raises(detect.EventLimitError):
        load_events(7, now=NOW, max_events=2)

def test_load_events_cap_counts_unique_lines(cache):
    lines = _three()
    _evfile(cache, lines[:2] + lines[:2])          # 고유 2줄, 중복 2줄
    assert len(load_events(7, now=NOW, max_events=2)) == 2

def _cli_detect(cache, extra):
    cli = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "cli.py"
    env = {"HC_DATA_DIR": str(cache), "PATH": "/usr/bin:/bin", "HOME": str(cache.parent)}
    env.update(extra)
    return subprocess.run([sys.executable, str(cli), "detect"], capture_output=True, text=True, env=env)

def test_cli_detect_cap_exceeded_rc_nonzero(cache):
    now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    _evfile(cache, [dumps(ev(s, now, "user_msg", s)) for s in "ab"], name="today.jsonl")
    r = _cli_detect(cache, {"HC_MAX_EVENTS": "1"})
    assert r.returncode != 0
    assert "상한" in r.stderr and "Traceback" not in r.stderr
    assert not list((cache / "signals").glob("*.json")) if (cache / "signals").exists() else True
    assert _cli_detect(cache, {"HC_MAX_EVENTS": "2"}).returncode == 0

from hc.events import parse_ts

def test_parse_ts_nanoseconds():
    a = parse_ts("2026-10-01T00:00:00.123456789Z")
    b = parse_ts("2026-10-01T00:00:00.1Z")
    assert a is not None and b is not None and a.microsecond == 123456 and b.microsecond == 100000

def test_load_events_drops_future_events(cache):
    d = cache / "events"; d.mkdir(parents=True)
    past = dumps(ev("a", "2026-10-02T00:00:00Z", "user_msg", "과거"))
    fut = dumps(ev("a", "2026-10-04T00:00:00Z", "user_msg", "미래"))
    (d / "x.jsonl").write_text(past + "\n" + fut + "\n", encoding="utf-8")
    assert [e.text for e in load_events(7, now=NOW)] == ["과거"]

def test_signal_days_listed():
    evs = [ev("a", "2026-10-01T01:00:00Z", "user_msg", "그렇게 하지 마"),
           ev("b", "2026-10-02T05:00:00Z", "user_msg", "그렇게 하지 마")]
    s = detect_corrections(evs, CFG)[0]
    assert len(s.days) == 2 and all(len(x) == 10 for x in s.days)

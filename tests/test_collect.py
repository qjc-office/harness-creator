import json, os, time, pytest
from hc.collect import collect
from hc.redact import RedactError

class FakeRedactor:
    def redact(self, t): return t.replace("SECRET", "[SECRET]")

def _cc(root, name, recs):
    d = root / "proj"; d.mkdir(parents=True, exist_ok=True)
    p = d / name
    with p.open("a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p

def _u(text, sid="s1"):
    return {"type": "user", "sessionId": sid, "timestamp": "t", "cwd": "/w", "message": {"content": text}}

@pytest.fixture
def src(tmp_path):
    cc, cx = tmp_path / "cc", tmp_path / "cx"
    cc.mkdir(); cx.mkdir()
    return {"claude": str(cc), "codex": str(cx)}

def _events(cache):
    out = []
    for f in sorted((cache / "events").glob("*.jsonl")):
        out += [json.loads(l) for l in f.read_text().splitlines()]
    return out

def test_collect_writes_redacted_events(cache, src, tmp_path):
    _cc(tmp_path / "cc", "s1.jsonl", [_u("my SECRET here")])
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    ev = _events(cache)
    assert st.events["claude"] == 1 and ev[0]["text"] == "my [SECRET] here"
    assert oct(os.stat(next((cache / "events").glob("*.jsonl"))).st_mode & 0o777) == "0o600"

def test_incremental_no_duplicates(cache, src, tmp_path):
    p = _cc(tmp_path / "cc", "s1.jsonl", [_u("a")])
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert len(_events(cache)) == 1
    _cc(tmp_path / "cc", "s1.jsonl", [_u("b")])
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert [e["text"] for e in _events(cache)] == ["a", "b"]

def test_truncated_file_rereads_from_zero(cache, src, tmp_path):
    p = _cc(tmp_path / "cc", "s1.jsonl", [_u("aaaa"), _u("bbbb")])
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    p.write_text(json.dumps(_u("c")) + "\n")
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert [e["text"] for e in _events(cache)][-1] == "c"

def test_old_files_skipped(cache, src, tmp_path):
    p = _cc(tmp_path / "cc", "old.jsonl", [_u("old")])
    old = time.time() - 30 * 86400
    os.utime(p, (old, old))
    assert collect(7, include_codex=True, sources=src, redactor=FakeRedactor()).files == 0

def test_subagent_files_included(cache, src, tmp_path):
    d = tmp_path / "cc" / "proj" / "s1" / "subagents"; d.mkdir(parents=True)
    (d / "agent-1.jsonl").write_text(json.dumps({"type": "system", "sessionId": "s1", "timestamp": "t", "cwd": "/w", "hookErrors": ["E"]}) + "\n")
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert _events(cache)[0]["kind"] == "hook_error"

def test_codex_session_meta_kept_across_runs(cache, src, tmp_path):
    d = tmp_path / "cx" / "2026" / "10" / "01"; d.mkdir(parents=True)
    p = d / "rollout-x.jsonl"
    p.write_text(json.dumps({"type": "session_meta", "timestamp": "t", "payload": {"session_id": "cx1", "cwd": "/w"}}) + "\n")
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    with p.open("a") as fh:
        fh.write(json.dumps({"type": "response_item", "timestamp": "t", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}}) + "\n")
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    ev = _events(cache)
    assert ev[0]["session_id"] == "cx1" and ev[0]["surface"] == "codex"

def test_max_files_defers(cache, src, tmp_path, monkeypatch):
    for i in range(3):
        _cc(tmp_path / "cc", f"s{i}.jsonl", [_u(str(i), f"s{i}")])
    import hc.collect as c
    monkeypatch.setattr(c, "load_config", lambda: {**c._BASE_CFG, "max_files_per_run": 2})
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert st.files == 2 and st.deferred == 1

def test_redact_failure_writes_nothing(cache, src, tmp_path, monkeypatch):
    _cc(tmp_path / "cc", "s1.jsonl", [_u("a")])
    import hc.collect as c
    def boom(): raise RedactError("x")
    monkeypatch.setattr(c, "from_config", lambda cfg: boom())
    with pytest.raises(RedactError):
        collect(7, include_codex=True, sources=src)
    assert not (cache / "events").exists() or _events(cache) == []

def test_parse_error_skips_file_and_continues(cache, src, tmp_path, monkeypatch):
    bad = _cc(tmp_path / "cc", "bad.jsonl", [_u("x", "sb")])
    _cc(tmp_path / "cc", "good.jsonl", [_u("ok", "sg")])
    import hc.parse_claude as pc
    real = pc.parse
    def flaky(path, start=0):
        if str(path) == str(bad):
            raise ValueError("malformed")
        return real(path, start)
    monkeypatch.setattr(pc, "parse", flaky)
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert [e["text"] for e in _events(cache)] == ["ok"]
    assert st.errors == 1 and st.files == 1
    cursor = json.loads((cache / "cursor.json").read_text())
    assert str(bad) not in cursor

def test_lexicon_flag_reported(cache, src, tmp_path):
    class NoLex(FakeRedactor):
        has_lexicon = False
    _cc(tmp_path / "cc", "s1.jsonl", [_u("a")])
    assert collect(7, include_codex=True, sources=src, redactor=NoLex()).lexicon is False
    stats = json.loads((cache / "collect-stats.json").read_text())
    assert stats["lexicon"] is False
    assert collect(7, include_codex=True, sources=src, redactor=FakeRedactor()).lexicon is True

class _Crash(Exception):
    pass

def test_events_on_disk_before_cursor_write(cache, src, tmp_path, monkeypatch):
    _cc(tmp_path / "cc", "s1.jsonl", [_u("a", "s1")])
    _cc(tmp_path / "cc", "s2.jsonl", [_u("b", "s2")])
    import hc.collect as c
    monkeypatch.setattr(c, "load_config", lambda: {**c._BASE_CFG, "cursor_flush_every": 1})
    real = c._write_atomic
    seen = {}
    def crashing(path, data):
        real(path, data)
        if path.name == "cursor.json":
            # 커서가 디스크에 닿은 바로 그 순간 이벤트 파일에 무엇이 있는지 = SIGKILL 뒤 살아남는 것
            seen["events"] = "".join(f.read_text() for f in (cache / "events").glob("*.jsonl"))
            seen["cursor"] = json.loads(data)
            raise _Crash()
    monkeypatch.setattr(c, "_write_atomic", crashing)
    with pytest.raises(_Crash):
        collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert len(seen["cursor"]) == 1
    texts = [json.loads(l)["text"] for l in seen["events"].splitlines()]
    assert len(texts) == 1 and texts[0] in ("a", "b")

def test_cursor_pruned_outside_window(cache, src, tmp_path):
    p_old = _cc(tmp_path / "cc", "old.jsonl", [_u("o", "so")])
    _cc(tmp_path / "cc", "new.jsonl", [_u("n", "sn")])
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    old = time.time() - 30 * 86400
    os.utime(p_old, (old, old))
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    cursor = json.loads((cache / "cursor.json").read_text())
    assert str(p_old) not in cursor and len(cursor) == 1

def test_cursor_write_count_bounded(cache, src, tmp_path, monkeypatch):
    import math
    import hc.collect as c
    n = 5
    for i in range(n):
        _cc(tmp_path / "cc", f"s{i}.jsonl", [_u(str(i), f"s{i}")])
    monkeypatch.setattr(c, "load_config", lambda: {**c._BASE_CFG, "cursor_flush_every": 2})
    real = c._write_atomic
    calls = []
    def counting(path, data):
        if path.name == "cursor.json":
            calls.append(1)
        real(path, data)
    monkeypatch.setattr(c, "_write_atomic", counting)
    assert collect(7, include_codex=True, sources=src, redactor=FakeRedactor()).files == n
    assert 1 <= len(calls) <= math.ceil(n / 2) + 1
    assert len(json.loads((cache / "cursor.json").read_text())) == n

def test_redact_error_mid_file_is_fail_closed(cache, src, tmp_path):
    _cc(tmp_path / "cc", "s1.jsonl", [_u("SECRET", "s1")])
    class Broken:
        def redact(self, t): raise RedactError("pattern")
    with pytest.raises(RedactError):
        collect(7, include_codex=True, sources=src, redactor=Broken())
    assert _events(cache) == []

def test_file_vanishing_after_discovery_is_skipped(cache, src, tmp_path, monkeypatch):
    gone = _cc(tmp_path / "cc", "gone.jsonl", [_u("g", "sg")])
    _cc(tmp_path / "cc", "keep.jsonl", [_u("k", "sk")])
    import hc.collect as c
    real = c._discover
    def discover_then_delete(*a, **kw):
        out = real(*a, **kw)
        gone.unlink()
        return out
    monkeypatch.setattr(c, "_discover", discover_then_delete)
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert [e["text"] for e in _events(cache)] == ["k"]
    assert st.files == 1 and st.errors == 0

def test_discover_skips_file_missing_at_stat(tmp_path, monkeypatch):
    import hc.collect as c, pathlib
    root = tmp_path / "cc"
    _cc(root, "a.jsonl", [_u("a")])
    _cc(root, "b.jsonl", [_u("b")])
    real_stat = pathlib.Path.stat
    def stat(self, *a, **kw):
        if self.name == "a.jsonl":
            raise FileNotFoundError(str(self))
        return real_stat(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "stat", stat)
    found = c._discover({"claude": str(root), "codex": str(tmp_path / "none")}, 0)
    assert [p.name for _, p, _ in found] == ["b.jsonl"]


def test_cwd_redacted_with_same_redactor(cache, src, tmp_path, monkeypatch):
    from hc.redact import Redactor
    lexp = tmp_path / "lex.json"
    lexp.write_text(json.dumps({"orgs": ["가나다상사"], "people": ["홍길순"]}), encoding="utf-8")
    rec = _u("hello")
    rec["cwd"] = "/Users/u/가나다상사-proj"
    _cc(tmp_path / "cc", "s1.jsonl", [rec])
    collect(7, include_codex=True, sources=src, redactor=Redactor(lexicon_files=[str(lexp)], lexicon_label="TERM"))
    ev = _events(cache)
    assert ev and ev[0]["cwd"] == "~/[TERM]-proj"

# --- 사전 건수를 수집 통계에 싣는다 ---
def test_lexicon_terms_reported(cache, src, tmp_path):
    class Thin(FakeRedactor):
        has_lexicon = True
        lexicon_terms = 3
    _cc(tmp_path / "cc", "s1.jsonl", [_u("a")])
    assert collect(7, include_codex=True, sources=src, redactor=Thin()).lexicon_terms == 3
    assert json.loads((cache / "collect-stats.json").read_text())["lexicon_terms"] == 3

# --- 오래된 이벤트 파일 정리 ---
def test_old_event_files_pruned(cache, src, tmp_path):
    ev_dir = cache / "events"
    ev_dir.mkdir(parents=True)
    old, keep = ev_dir / "2026-08-01.jsonl", ev_dir / "2026-09-20.jsonl"
    old.write_text("{}\n"); keep.write_text("{}\n")
    now = time.time()
    os.utime(old, (now - 31 * 86400, now - 31 * 86400))
    os.utime(keep, (now - 29 * 86400, now - 29 * 86400))
    other = ev_dir / "note.txt"
    other.write_text("x")
    os.utime(other, (now - 40 * 86400, now - 40 * 86400))
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor(), now=now)
    assert not old.exists() and keep.exists() and other.exists()

# --- 깨진 줄 수를 수집 통계에 남기고 커서는 넘긴다(재시도 불가) ---
def test_malformed_lines_counted_in_stats_and_not_retried(cache, src, tmp_path):
    p = _cc(tmp_path / "cc", "s1.jsonl", [_u("a")])
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"type": "user", "mess\n')
    _cc(tmp_path / "cc", "s1.jsonl", [_u("b")])
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert [e["text"] for e in _events(cache)] == ["a", "b"]
    assert st.malformed == 1 and st.errors == 0
    assert json.loads((cache / "collect-stats.json").read_text())["malformed"] == 1
    # 다음 실행: 같은 줄을 다시 읽지 않는다
    _cc(tmp_path / "cc", "s1.jsonl", [_u("c")])
    st2 = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert st2.malformed == 0 and [e["text"] for e in _events(cache)] == ["a", "b", "c"]

def test_malformed_summed_across_files(cache, src, tmp_path):
    for name in ("s1.jsonl", "s2.jsonl"):
        p = _cc(tmp_path / "cc", name, [_u("x", sid=name)])
        with p.open("a", encoding="utf-8") as fh:
            fh.write("{bad\n[]\n")
    assert collect(7, include_codex=True, sources=src, redactor=FakeRedactor()).malformed == 4

# --- 도구 이름(tool)도 같은 가림기를 거친다 ---
_GHP = "ghp_" + "Zx9" * 8   # 테스트용 가짜 토큰

@pytest.fixture
def real_red(tmp_path, monkeypatch):
    from hc.redact import Redactor
    lexp = tmp_path / "lex-tool.json"
    lexp.write_text(json.dumps({"orgs": ["Acme"], "people": []}), encoding="utf-8")
    return Redactor(lexicon_files=[str(lexp)], lexicon_label="TERM")

def _tool_use(tid, name, sid="s1"):
    return {"type": "assistant", "sessionId": sid, "timestamp": "t", "cwd": "/w",
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": {"command": "x"}}]}}

def _tool_err(tid, sid="s1"):
    return {"type": "user", "sessionId": sid, "timestamp": "t", "cwd": "/w",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": True, "content": "boom"}]}}

def test_claude_tool_names_redacted(cache, src, tmp_path, real_red):
    _cc(tmp_path / "cc", "s1.jsonl", [_tool_use("a", "Acme-Tool"), _tool_err("a"),
                                      _tool_use("b", "mcp__" + _GHP), _tool_use("c", "Bash")])
    collect(7, include_codex=True, sources=src, redactor=real_red)
    tools = [e["tool"] for e in _events(cache)]
    raw = json.dumps(_events(cache), ensure_ascii=False)
    assert "Acme" not in raw and _GHP not in raw
    assert tools[0] == "[TERM]-Tool" and tools[1] == "[TERM]-Tool"        # tool_call과 tool_error 둘 다
    assert "[SECRET]" in tools[2]
    assert tools[3] == "Bash"                                       # 평범한 이름은 그대로

def test_codex_tool_names_redacted(cache, src, tmp_path, real_red):
    d = tmp_path / "cx" / "2026"; d.mkdir(parents=True)
    p = d / "rollout-1.jsonl"
    recs = [{"type": "session_meta", "timestamp": "t0", "payload": {"session_id": "cx1", "cwd": "/w"}},
            {"type": "response_item", "timestamp": "t1", "payload": {"type": "function_call", "name": "acme_lookup", "call_id": "c1", "arguments": "{}"}},
            {"type": "response_item", "timestamp": "t2", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c2", "input": "ls"}}]
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    collect(7, include_codex=True, sources=src, redactor=real_red)
    tools = [e["tool"] for e in _events(cache)]
    assert tools == ["[TERM]_lookup", "exec"]

# --- 소스 루트 밖을 가리키는 세션 파일 심링크는 따라가지 않는다 ---
def _ext(tmp_path, name, text):
    o = tmp_path / "ext"; o.mkdir(exist_ok=True)
    f = o / name
    f.write_text(json.dumps(_u(text)) + "\n", encoding="utf-8")
    return f

def test_claude_external_symlink_skipped(cache, src, tmp_path):
    _cc(tmp_path / "cc", "s1.jsonl", [_u("안쪽")])
    (tmp_path / "cc" / "proj" / "evil.jsonl").symlink_to(_ext(tmp_path, "x.jsonl", "바깥 비밀"))
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert [e["text"] for e in _events(cache)] == ["안쪽"]
    assert st.skipped_outside_source == 1
    assert json.loads((cache / "collect-stats.json").read_text())["skipped_outside_source"] == 1

def test_claude_in_root_symlink_kept(cache, src, tmp_path):
    p = _cc(tmp_path / "cc", "real.jsonl", [_u("원본")])
    other = tmp_path / "cc" / "proj2"; other.mkdir()
    (other / "alias.jsonl").symlink_to(p)
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert sorted(e["text"] for e in _events(cache)) == ["원본", "원본"]
    assert st.skipped_outside_source == 0

def test_broken_symlink_skipped_without_exception(cache, src, tmp_path):
    d = tmp_path / "cc" / "proj"; d.mkdir(parents=True)
    (d / "gone.jsonl").symlink_to(d / "nope.jsonl")                    # 안쪽을 가리키는 끊긴 링크
    (d / "gone2.jsonl").symlink_to(tmp_path / "nowhere.jsonl")         # 바깥을 가리키는 끊긴 링크
    _cc(tmp_path / "cc", "ok.jsonl", [_u("정상")])
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert [e["text"] for e in _events(cache)] == ["정상"]
    assert st.errors == 0 and st.skipped_outside_source == 1

def test_codex_external_symlink_skipped_in_root_kept(cache, src, tmp_path):
    d = tmp_path / "cx" / "2026"; d.mkdir(parents=True)
    meta = {"type": "session_meta", "timestamp": "t0", "payload": {"session_id": "cx1", "cwd": "/w"}}
    def user(t):
        return {"type": "response_item", "timestamp": "t1", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": t}]}}
    real = d / "rollout-a.jsonl"
    real.write_text(json.dumps(meta) + "\n" + json.dumps(user("안쪽"), ensure_ascii=False) + "\n", encoding="utf-8")
    o = tmp_path / "ext"; o.mkdir()
    ext = o / "rollout-x.jsonl"
    ext.write_text(json.dumps(meta) + "\n" + json.dumps(user("바깥"), ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "rollout-b.jsonl").symlink_to(real)
    (d / "rollout-c.jsonl").symlink_to(ext)
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert sorted(e["text"] for e in _events(cache)) == ["안쪽", "안쪽"]
    assert st.skipped_outside_source == 1

def test_symlinked_project_dir_outside_root_skipped(cache, src, tmp_path):
    # 변형: 프로젝트 폴더 자체가 바깥 폴더 심링크
    od = tmp_path / "outdir"; od.mkdir()
    (od / "s.jsonl").write_text(json.dumps(_u("바깥")) + "\n", encoding="utf-8")
    (tmp_path / "cc").mkdir(exist_ok=True)
    (tmp_path / "cc" / "linked").symlink_to(od, target_is_directory=True)
    st = collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert _events(cache) == [] and st.skipped_outside_source == 1

# --- 덧붙이기 대상 이벤트 파일이 이미 0644여도 0600으로 조인다 ---
def _today_events(cache):
    d = cache / "events"; d.mkdir(parents=True, exist_ok=True)
    return d / (time.strftime("%Y-%m-%d") + ".jsonl")

@pytest.mark.parametrize("with_new_events", [True, False])
def test_existing_events_file_tightened(cache, src, tmp_path, with_new_events):
    f = _today_events(cache)
    f.write_text(""); os.chmod(f, 0o644)
    if with_new_events:
        _cc(tmp_path / "cc", "s1.jsonl", [_u("a")])
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"

# --- 공개판: Codex는 동의했을 때만 읽는다 ---
def _codex(tmp_path):
    d = tmp_path / "cx" / "2026" / "10" / "01"; d.mkdir(parents=True, exist_ok=True)
    p = d / "rollout-y.jsonl"
    p.write_text(json.dumps({"type": "session_meta", "timestamp": "t", "payload": {"session_id": "cx9", "cwd": "/w"}}) + "\n"
                 + json.dumps({"type": "response_item", "timestamp": "t", "payload": {"type": "message", "role": "user",
                                "content": [{"type": "input_text", "text": "codex said"}]}}) + "\n")

def test_codex_skipped_without_consent(cache, src, tmp_path):
    _codex(tmp_path)
    st = collect(7, sources=src, redactor=FakeRedactor())          # include_codex 미지정 → 설정 sources.codex(null) → 제외
    assert st.events["codex"] == 0 and st.codex_included is False

def test_codex_included_by_config(cache, src, tmp_path):
    from hc.config import save_user_config
    save_user_config({"sources": {"codex": True}})
    _codex(tmp_path)
    st = collect(7, sources=src, redactor=FakeRedactor())
    assert st.events["codex"] == 1 and st.codex_included is True

def test_estimate_counts_files_and_bytes(cache, src, tmp_path):
    from hc.collect import estimate
    _cc(tmp_path / "cc", "s1.jsonl", [_u("abc")])
    _codex(tmp_path)
    e = estimate(7, sources=src)
    assert e["claude"]["files"] == 1 and e["claude"]["bytes"] > 0 and e["codex"]["files"] == 1

def test_slot_failure_writes_nothing(cache, src, tmp_path):
    from hc.config import save_user_config
    from hc.redact import RedactError
    save_user_config({"redact": {"lexicon_files": [str(tmp_path / "missing.json")]}})
    _cc(tmp_path / "cc", "s1.jsonl", [_u("a")])
    with pytest.raises(RedactError):
        collect(7, include_codex=True, sources=src)
    assert not (cache / "events").exists()

# --- 동의는 정확히 true일 때만, 커서에도 가린 cwd만 ---
@pytest.mark.parametrize("val", ["false", "0", 1, "true"])
def test_codex_consent_requires_exact_true(cache, src, tmp_path, val):
    from hc.config import save_user_config
    save_user_config({"sources": {"codex": val}})
    _codex(tmp_path)
    st = collect(7, sources=src, redactor=FakeRedactor())
    assert st.events["codex"] == 0 and st.codex_included is False
    assert json.loads((cache / "collect-stats.json").read_text())["codex_included"] is False

@pytest.mark.parametrize("val", ["false", 1])
def test_codex_include_arg_requires_exact_true(cache, src, tmp_path, val):
    _codex(tmp_path)
    st = collect(7, include_codex=val, sources=src, redactor=FakeRedactor())
    assert st.events["codex"] == 0 and st.codex_included is False

def test_codex_cursor_stores_redacted_cwd(cache, src, tmp_path):
    from hc.redact import Redactor
    lexp = tmp_path / "lex.json"
    lexp.write_text(json.dumps({"orgs": ["Acme"]}), encoding="utf-8")
    red = Redactor(lexicon_files=[str(lexp)], lexicon_label="TERM")
    d = tmp_path / "cx" / "2026" / "10" / "01"; d.mkdir(parents=True)
    p = d / "rollout-z.jsonl"
    p.write_text(json.dumps({"type": "session_meta", "timestamp": "t",
                             "payload": {"session_id": "cz", "cwd": "/Users/u/Acme-proj"}}) + "\n")
    collect(7, include_codex=True, sources=src, redactor=red)
    raw = (cache / "cursor.json").read_text()
    assert "Acme" not in raw and "/Users/u" not in raw
    assert json.loads(raw)[str(p)]["cwd"] == "~/[TERM]-proj"
    with p.open("a") as fh:
        fh.write(json.dumps({"type": "response_item", "timestamp": "t", "payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": "hi"}]}}) + "\n")
    collect(7, include_codex=True, sources=src, redactor=red)
    ev = _events(cache)
    assert ev and ev[-1]["cwd"] == "~/[TERM]-proj" and ev[-1]["session_id"] == "cz"

def test_events_dir_created_private(cache, src, tmp_path, monkeypatch):
    import hc.collect as c
    _cc(tmp_path / "cc", "s1.jsonl", [_u("a")])
    c.data_dir()                                   # 데이터 폴더는 먼저 만든다
    monkeypatch.setattr(c.os, "chmod", lambda *a, **k: None)   # 사후 chmod 없이도 처음부터 0700인지
    old = os.umask(0o022)
    try:
        collect(7, include_codex=True, sources=src, redactor=FakeRedactor())
    finally:
        os.umask(old)
    assert (cache / "events").stat().st_mode & 0o777 == 0o700

# --- 자르기는 가린 뒤에: 500자 경계에 걸친 시크릿·개인정보의 앞부분이 이벤트에 남지 않는다 ---
from hc.redact import Redactor

_BOUNDARY = [
    ("sk-" + "ABCDEFGHIJKLMNOPQRSTUV", "sk-ABCDEFGH"),
    ("900101" + "-1234567", "900101-1234"),
    ("alice.secret" + "@example.com", "alice.secre"),
    ("4111 1111 " + "1111 1111", "4111 1111 1"),
]

@pytest.mark.parametrize("value,prefix", _BOUNDARY, ids=["secret", "national_id", "email", "card"])
def test_value_straddling_text_limit_not_leaked(cache, src, tmp_path, value, prefix):
    pad = "x" * (500 - len(prefix) - 1) + " "           # 값 앞은 공백(단어 경계)
    assert len(pad) + len(prefix) == 500               # 자르면 정확히 prefix 까지 남는 자리
    _cc(tmp_path / "cc", "s1.jsonl", [_u(pad + value + " tail")])
    collect(7, include_codex=True, sources=src, redactor=Redactor())
    text = _events(cache)[0]["text"]
    assert prefix not in text and len(text) <= 500

def test_user_text_max_chars_is_respected(cache, src, tmp_path):
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "config.json").write_text('{"text_max_chars": 40}')
    _cc(tmp_path / "cc", "s1.jsonl", [_u("y" * 300)])
    collect(7, include_codex=True, sources=src, redactor=Redactor())
    assert _events(cache)[0]["text"] == "y" * 40

def test_old_signals_and_input_files_pruned(cache, src, tmp_path):
    # 신호 파일과 판정 입력 파일도 이벤트와 같은 보존 기간(30일)으로 지운다
    (cache / "signals").mkdir(parents=True); (cache / "reports").mkdir(parents=True)
    now = time.time()
    old_sig, new_sig = cache / "signals" / "2026-08-01.json", cache / "signals" / "2026-09-20.json"
    old_in, report = cache / "reports" / "2026-08-01.input.json", cache / "reports" / "2026-08-01.md"
    for f in (old_sig, new_sig, old_in, report):
        f.write_text("{}")
    for f in (old_sig, old_in, report):
        os.utime(f, (now - 31 * 86400, now - 31 * 86400))
    collect(7, include_codex=True, sources=src, redactor=FakeRedactor(), now=now)
    assert not old_sig.exists() and not old_in.exists()
    assert new_sig.exists() and report.exists()        # 리포트는 보존 대상이 아니다

def test_redaction_window_is_bounded(cache, src, tmp_path):
    # 원문 전체(최대 64KB)를 가리지 않는다. text_max_chars + 여유분만 가린 뒤 자른다(수집 시간 상한)
    import hc.collect as C
    seen = []
    class Rec:
        def redact(self, t):
            seen.append(len(t)); return t
    _cc(tmp_path / "cc", "s1.jsonl", [_u("z" * 60000)])
    collect(7, include_codex=True, sources=src, redactor=Rec())
    assert max(seen) <= 500 + C.REDACT_MARGIN and C.REDACT_MARGIN >= 8192
    assert _events(cache)[0]["text"] == "z" * 500

def test_long_redaction_does_not_pull_cut_value_forward(cache, src, tmp_path):
    # 앞의 긴 토큰이 가림으로 짧아져도, 경계 근처(약 text_max_chars + 8KB)에 걸친 값의 앞부분이 남지 않는다.
    # 일부만 잘라 가리면 잘린 조각은 패턴에 안 걸린 채 앞으로 끌려와 text_max_chars 안에 들어온다.
    from hc.redact import Redactor
    edge = 500 + 8192
    big = "sk-" + "A" * 8600
    cases = [("alice.secret" + "@example.com", "alice.secr"),
             ("sk-" + "ABCDEFGHIJKLMNOP", "sk-ABCDEFGH"),
             ("900101" + "-" + "1234567", "900101-1234"),
             ("4111 1111" + " 1111 1111", "4111 1111 1")]
    for i, (val, prefix) in enumerate(cases):
        head = big + " "
        text = head + "y" * (edge - len(head) - len(prefix) - 1) + " " + val + " tail"
        assert text[:edge].endswith(prefix)
        _cc(tmp_path / "cc", f"n{i}.jsonl", [_u(text, sid=f"n{i}")])
    collect(7, include_codex=True, sources=src, redactor=Redactor())
    ev = _events(cache)
    assert len(ev) == len(cases)
    for e in ev:
        assert len(e["text"]) <= 500
        for _, prefix in cases:
            assert prefix not in e["text"]

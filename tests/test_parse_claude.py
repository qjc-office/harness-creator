import json
from hc.parse_claude import parse
from hc.lines import read_complete_lines

def _w(p, recs, tail=""):
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs) + tail, encoding="utf-8")

BASE = {"sessionId": "s1", "timestamp": "2026-10-01T00:00:00Z", "cwd": "/w", "isSidechain": False}

def test_user_msg_and_tool_call(tmp_path):
    p = tmp_path / "s1.jsonl"
    _w(p, [
        {**BASE, "type": "user", "message": {"role": "user", "content": "테스트 돌려줘"}},
        {**BASE, "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}}]}},
    ])
    r = parse(p)
    kinds = [(e.kind, e.tool, e.text) for e in r.events]
    assert ("user_msg", "", "테스트 돌려줘") in kinds
    assert ("tool_call", "Bash", "pytest -q") in kinds

def test_tool_error_gets_tool_name_and_sig(tmp_path):
    p = tmp_path / "s1.jsonl"
    _w(p, [
        {**BASE, "type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}]}},
        {**BASE, "type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
            "content": [{"type": "text", "text": "Exit code 127: command not found"}]}]}},
    ])
    err = [e for e in parse(p).events if e.kind == "tool_error"][0]
    assert err.tool == "Bash" and err.text.startswith("Exit code 127") and len(err.error_sig) == 12

def test_denial_and_hook_error(tmp_path):
    p = tmp_path / "s1.jsonl"
    _w(p, [
        {**BASE, "type": "user", "toolDenialKind": "permission-rule", "message": {"content": [{"type": "tool_result", "tool_use_id": "zz", "is_error": True, "content": "denied"}]}},
        {**BASE, "type": "system", "hookErrors": ["Failed with non-blocking status code: EAGAIN"]},
    ])
    kinds = [e.kind for e in parse(p).events]
    assert "permission_denied" in kinds and "hook_error" in kinds
    assert kinds.count("tool_error") == 0  # 거부는 tool_error로 중복 계상하지 않는다

def test_meta_and_sidechain_user_skipped(tmp_path):
    p = tmp_path / "s1.jsonl"
    _w(p, [
        {**BASE, "type": "user", "isMeta": True, "message": {"content": "<command-name>/x</command-name>"}},
        {**BASE, "isSidechain": True, "type": "user", "message": {"content": "서브에이전트 지시"}},
    ])
    assert [e for e in parse(p).events if e.kind == "user_msg"] == []

def test_unknown_types_counted(tmp_path):
    p = tmp_path / "s1.jsonl"
    _w(p, [{**BASE, "type": "brand-new-thing"}, {**BASE, "type": "attachment"}])
    r = parse(p)
    assert r.records == 2 and r.unknown == 1

def test_partial_last_line_not_consumed(tmp_path):
    p = tmp_path / "s1.jsonl"
    _w(p, [{**BASE, "type": "user", "message": {"content": "첫 줄"}}], tail='{"type": "user", "mess')
    r = parse(p)
    assert len(r.events) == 1
    full = p.read_bytes()
    assert r.end == full.index(b"\n") + 1

def test_resume_from_offset(tmp_path):
    p = tmp_path / "s1.jsonl"
    _w(p, [{**BASE, "type": "user", "message": {"content": "a"}}])
    end = parse(p).end
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**BASE, "type": "user", "message": {"content": "b"}}) + "\n")
    r = parse(p, start=end)
    assert [e.text for e in r.events] == ["b"]

def test_bad_json_line_skipped(tmp_path):
    p = tmp_path / "s1.jsonl"
    p.write_text("not json\n" + json.dumps({**BASE, "type": "user", "message": {"content": "ok"}}) + "\n")
    assert [e.text for e in parse(p).events] == ["ok"]

def test_read_complete_lines(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"a\nb\nc")
    lines, end = read_complete_lines(p, 0)
    assert lines == ["a", "b"] and end == 4

# --- astra r2 B2: 깨진 줄은 조용히 버리지 않고 센다 ---
def _mixed(p, bad, tail=""):
    good = [{**BASE, "type": "user", "message": {"role": "user", "content": "첫째"}},
            {**BASE, "type": "user", "message": {"role": "user", "content": "둘째"}}]
    p.write_text(json.dumps(good[0], ensure_ascii=False) + "\n" + bad + "\n"
                 + json.dumps(good[1], ensure_ascii=False) + "\n" + tail, encoding="utf-8")

def test_malformed_line_counted(tmp_path):
    p = tmp_path / "s1.jsonl"
    _mixed(p, '{"type": "user", "message": ')
    r = parse(p)
    assert [e.text for e in r.events] == ["첫째", "둘째"]
    assert r.malformed == 1 and r.end == p.stat().st_size

def test_non_object_json_line_counted_as_malformed(tmp_path):
    p = tmp_path / "s1.jsonl"
    _mixed(p, "[1, 2]")
    assert parse(p).malformed == 1

def test_unterminated_last_line_not_malformed_nor_consumed(tmp_path):
    p = tmp_path / "s1.jsonl"
    _mixed(p, "{broken", tail='{"type": "user", "mess')
    r = parse(p)
    assert r.malformed == 1                                  # 가운데 깨진 줄만
    assert r.end == p.stat().st_size - len('{"type": "user", "mess')

def test_clean_file_malformed_zero(tmp_path):
    p = tmp_path / "s1.jsonl"
    _w(p, [{**BASE, "type": "user", "message": {"role": "user", "content": "x"}}])
    assert parse(p).malformed == 0

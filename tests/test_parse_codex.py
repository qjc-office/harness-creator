import json
from hc.parse_codex import parse, read_meta

def _w(p, recs):
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")

META = {"type": "session_meta", "timestamp": "t0", "payload": {"session_id": "cx1", "cwd": "/w"}}

def _user(text):
    return {"type": "response_item", "timestamp": "t1", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}]}}

def test_user_msg_skips_injected_blocks(tmp_path):
    p = tmp_path / "rollout-1.jsonl"
    _w(p, [META, _user("<environment_context> cwd"), _user("# AGENTS.md instructions for /w"), _user("빌드 고쳐줘")])
    r = parse(p, 0, *read_meta(p))
    assert [e.text for e in r.events if e.kind == "user_msg"] == ["빌드 고쳐줘"]
    assert all(e.surface == "codex" and e.session_id == "cx1" for e in r.events)

def test_tool_call_and_failed_output(tmp_path):
    p = tmp_path / "rollout-1.jsonl"
    _w(p, [META,
        {"type": "response_item", "timestamp": "t2", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1", "input": "npm test"}},
        {"type": "response_item", "timestamp": "t3", "payload": {"type": "custom_tool_call_output", "call_id": "c1",
            "output": [{"type": "input_text", "text": "Script failed\nError: Cannot find module 'x'"}]}},
        {"type": "response_item", "timestamp": "t4", "payload": {"type": "custom_tool_call_output", "call_id": "c9",
            "output": [{"type": "input_text", "text": "Script completed\nok"}]}},
    ])
    r = parse(p, 0, *read_meta(p))
    calls = [e for e in r.events if e.kind == "tool_call"]
    errs = [e for e in r.events if e.kind == "tool_error"]
    assert calls[0].tool == "exec" and calls[0].text == "npm test"
    assert len(errs) == 1 and errs[0].tool == "exec" and "Cannot find module" in errs[0].text

def test_function_call_arguments(tmp_path):
    p = tmp_path / "rollout-1.jsonl"
    _w(p, [META, {"type": "response_item", "timestamp": "t", "payload": {"type": "function_call", "name": "shell", "call_id": "f1", "arguments": "{\"cmd\":\"ls\"}"}}])
    assert parse(p, 0, *read_meta(p)).events[0].text == "{\"cmd\":\"ls\"}"

def test_unknown_types(tmp_path):
    p = tmp_path / "rollout-1.jsonl"
    _w(p, [META, {"type": "weird", "payload": {}}])
    r = parse(p, 0, *read_meta(p))
    assert r.records == 2 and r.unknown == 1

def test_read_meta_fallback(tmp_path):
    p = tmp_path / "rollout-abc.jsonl"
    _w(p, [_user("x")])
    assert read_meta(p) == ("rollout-abc", "")

def _exec(p, output):
    _w(p, [META,
        {"type": "response_item", "timestamp": "t2", "payload": {"type": "function_call", "name": "exec_command", "call_id": "e1", "arguments": "{\"cmd\":\"npm test\"}"}},
        {"type": "response_item", "timestamp": "t3", "payload": {"type": "function_call_output", "call_id": "e1", "output": output}},
    ])
    return parse(p, 0, *read_meta(p))

def test_exec_nonzero_exit_is_tool_error(tmp_path):
    out = "Chunk ID: abc123\nWall time: 1.2 seconds\nProcess exited with code 1\nOriginal token count: 12\nOutput:\nError: Cannot find module 'x'\n"
    r = _exec(tmp_path / "rollout-1.jsonl", out)
    errs = [e for e in r.events if e.kind == "tool_error"]
    assert len(errs) == 1 and errs[0].tool == "exec_command"
    assert errs[0].text == "Error: Cannot find module 'x'\n"
    from hc.events import error_signature
    assert errs[0].error_sig == error_signature("Error: Cannot find module 'x'\n")

def test_exec_zero_exit_is_not_error(tmp_path):
    out = "Chunk ID: abc123\nWall time: 0.1 seconds\nProcess exited with code 0\nOutput:\nok\n"
    r = _exec(tmp_path / "rollout-1.jsonl", out)
    assert [e for e in r.events if e.kind in ("tool_error", "permission_denied")] == []

def test_secret_like_value_is_permission_denied(tmp_path):
    out = "Secret-like value detected in command; refusing\nmore detail"
    r = _exec(tmp_path / "rollout-1.jsonl", out)
    den = [e for e in r.events if e.kind == "permission_denied"]
    from hc.events import error_signature
    assert len(den) == 1 and den[0].tool == "exec_command"
    assert den[0].text == "Secret-like value detected in command; refusing"
    assert den[0].error_sig == error_signature("Secret-like value detected in command; refusing" + "exec_command")
    assert [e for e in r.events if e.kind == "tool_error"] == []

def test_strip_header_stops_after_output_line(tmp_path):
    out = "Chunk ID: abc\nWall time: 0.5 seconds\nProcess exited with code 2\nOriginal token count: 9\nOutput:\nOutput dir missing\nsecond line\n"
    r = _exec(tmp_path / "rollout-1.jsonl", out)
    errs = [e for e in r.events if e.kind == "tool_error"]
    assert len(errs) == 1 and errs[0].text.startswith("Output dir missing")

def test_empty_body_failures_get_distinct_sigs(tmp_path):
    hdr = "Chunk ID: abc\nWall time: 0.1 seconds\nProcess exited with code {}\nOutput:\n"
    e1 = [e for e in _exec(tmp_path / "rollout-1.jsonl", hdr.format(1)).events if e.kind == "tool_error"]
    e2 = [e for e in _exec(tmp_path / "rollout-2.jsonl", hdr.format(2)).events if e.kind == "tool_error"]
    assert len(e1) == 1 and len(e2) == 1 and e1[0].error_sig != e2[0].error_sig
    from hc.events import error_signature
    assert e1[0].error_sig == error_signature("exec_command exit 1")
    p = tmp_path / "rollout-3.jsonl"
    _w(p, [META,
        {"type": "response_item", "timestamp": "t2", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1", "input": "x"}},
        {"type": "response_item", "timestamp": "t3", "payload": {"type": "custom_tool_call_output", "call_id": "c1",
            "output": [{"type": "input_text", "text": "Script failed\n"}]}},
    ])
    e3 = [e for e in parse(p, 0, *read_meta(p)).events if e.kind == "tool_error"]
    assert len(e3) == 1 and e3[0].error_sig == error_signature("exec script failed")
    assert len({e1[0].error_sig, e2[0].error_sig, e3[0].error_sig}) == 3

# --- Codex 파서도 깨진 줄을 센다 ---
def test_codex_malformed_line_counted(tmp_path):
    p = tmp_path / "rollout-1.jsonl"
    p.write_text(json.dumps(META) + "\n" + "{not json\n" + "7\n" + json.dumps(_user("빌드 고쳐줘"), ensure_ascii=False) + "\n"
                 + '{"type": "resp', encoding="utf-8")
    r = parse(p, 0, *read_meta(p))
    assert [e.text for e in r.events if e.kind == "user_msg"] == ["빌드 고쳐줘"]
    assert r.malformed == 2

import os, stat
from hc.events import Event, dumps, loads, error_signature, clip, KINDS, RAW_MAX
from hc.config import load_config, data_dir

def test_roundtrip():
    e = Event("claude", "s1", "2026-10-01T00:00:00Z", "/x", "tool_error", "boom", "Bash", "abc")
    assert loads(dumps(e)) == e

def test_kinds_fixed():
    assert KINDS == ("user_msg", "tool_call", "tool_error", "permission_denied", "hook_error")

def test_error_signature_ignores_numbers_paths_hex():
    a = error_signature("Error: file /Users/a/x.py line 12 id 0xdeadbeef")
    b = error_signature("Error: file /tmp/y.py line 99 id 0x1234")
    assert a == b and len(a) == 12

def test_error_signature_differs_on_message():
    assert error_signature("ENOENT missing") != error_signature("EACCES denied")

def test_clip_default_is_raw_cap_not_text_limit():
    # 파서 단계에서는 넉넉한 원문 상한만 건다. text_max_chars 는 수집기가 가린 뒤 적용한다
    assert len(clip("가" * 900)) == 900
    assert len(clip("가" * (RAW_MAX + 5))) == RAW_MAX

def test_config_has_thresholds():
    c = load_config()
    assert c["thresholds"]["failure_loop"]["min_repeats_in_session"] == 3

def test_cache_dir_0700(cache):
    d = data_dir()
    assert d == cache and stat.S_IMODE(os.stat(d).st_mode) == 0o700

def test_error_signature_keeps_exit_code():
    assert error_signature("Exit code 1: failed") != error_signature("Exit code 127: failed")

def test_error_signature_keeps_http_status():
    assert error_signature("HTTP 404 not found") != error_signature("HTTP 500 not found")

def test_error_signature_still_collapses_line_numbers():
    assert error_signature("error at line 12") == error_signature("error at line 99")

def test_clip_explicit_limit():
    assert len(clip("가" * 5000, 500)) == 500

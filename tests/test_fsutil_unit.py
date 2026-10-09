import os, stat
from hc.fsutil import append_line
from hc.events import parse_ts

def test_append_line_adds_newline_after_truncated_tail(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_bytes(b'{"a":1}\n{"b":')            # 앞 쓰기가 중단돼 개행 없이 끝났다
    os.chmod(p, 0o644)
    append_line(p, b'{"c":3}\n')
    assert p.read_bytes() == b'{"a":1}\n{"b":\n{"c":3}\n'
    assert stat.S_IMODE(p.stat().st_mode) == 0o600

def test_append_line_creates_0600(tmp_path):
    p = tmp_path / "n.jsonl"
    append_line(p, b"x\n")
    assert p.read_bytes() == b"x\n" and stat.S_IMODE(p.stat().st_mode) == 0o600

def test_parse_ts_variants():
    assert parse_ts("2026-10-01T00:00:00Z").timestamp() == parse_ts("2026-10-01T09:00:00+09:00").timestamp()
    assert parse_ts("2026-10-01T00:00:00").tzinfo is not None      # 시간대 없으면 UTC
    assert parse_ts("t") is None and parse_ts(5) is None

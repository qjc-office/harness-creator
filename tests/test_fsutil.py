"""astra r4 D2: 캐시 파일은 umask와 무관하게 0600으로 쓴다."""
import json, os, pathlib, stat, subprocess, sys
import pytest

CLI = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "cli.py"

def _mode(p):
    return stat.S_IMODE(pathlib.Path(p).stat().st_mode)

def _cli(cache, *args):
    env = {"HC_DATA_DIR": str(cache), "PATH": "/usr/bin:/bin", "HOME": str(cache.parent)}
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env=env, umask=0o022)

def _seed(cache):
    cache.mkdir(); (cache / "signals").mkdir()
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": []}))
    (cache / "collect-stats.json").write_text(json.dumps({"files": 0, "events": {}, "errors": 0}))
    (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"}}))

def test_propose_input_file_0600_under_umask_022(cache):
    _seed(cache)
    r = _cli(cache, "propose-input")
    assert r.returncode == 0, r.stderr
    assert _mode(r.stdout.strip()) == 0o600

@pytest.mark.parametrize("args", [("--error", "x"), ("--candidates", "/nonexistent")])
def test_render_file_0600_under_umask_022(cache, tmp_path, args):
    _seed(cache)
    r = _cli(cache, "render", *args)
    assert r.returncode == 0, r.stderr
    assert _mode(r.stdout.strip()) == 0o600

def test_render_success_report_0600_and_existing_0644_tightened(cache, tmp_path):
    _seed(cache)
    ok = tmp_path / "c.json"; ok.write_text(json.dumps({"candidates": []}))
    r = _cli(cache, "render", "--candidates", str(ok))
    # stdout이 비면 Path("")가 현재 폴더(".")가 되어 아래 chmod가 작업 폴더 권한을 깨뜨린다. 먼저 끊는다
    assert r.returncode == 0 and r.stdout.strip(), r.stderr
    first = pathlib.Path(r.stdout.strip())
    assert first.is_file() and first.parent == cache / "reports"
    os.chmod(first, 0o644)                                    # 예전 실행이 남긴 느슨한 권한
    second = pathlib.Path(_cli(cache, "render", "--candidates", str(ok)).stdout.strip())
    assert second == first and _mode(second) == 0o600

def test_detect_signals_file_0600_under_umask_022(cache):
    cache.mkdir(); (cache / "events").mkdir()
    r = _cli(cache, "detect")
    assert r.returncode == 0, r.stderr
    assert _mode(r.stdout.strip()) == 0o600

def test_write_private_unit(tmp_path):
    from hc.fsutil import write_private
    d = tmp_path / "w"; d.mkdir()          # conftest가 tmp_path/home 을 만들므로 빈 하위 폴더에서 본다
    p = d / "a.txt"
    p.write_text("old"); os.chmod(p, 0o644)
    old = os.umask(0o022)
    try:
        write_private(p, "새 내용")
    finally:
        os.umask(old)
    assert p.read_text(encoding="utf-8") == "새 내용" and _mode(p) == 0o600
    assert [x.name for x in d.iterdir()] == ["a.txt"]            # 임시 파일이 남지 않는다

def test_write_private_failure_leaves_no_tmp(tmp_path, monkeypatch):
    from hc import fsutil
    d = tmp_path / "w"; d.mkdir()
    p = d / "b.txt"
    def boom(*a, **k): raise OSError("replace 실패")
    monkeypatch.setattr(fsutil.os, "replace", boom)
    with pytest.raises(OSError):
        fsutil.write_private(p, "x")
    assert list(d.iterdir()) == []

def test_cli_has_no_bare_write_text():
    root = CLI.parents[1]
    for mod in ("cli.py", "detect.py", "collect.py", "candidates.py", "apply.py", "effect.py"):
        p = root / "hc" / mod
        if p.exists():
            assert ".write_text(" not in p.read_text(encoding="utf-8"), mod

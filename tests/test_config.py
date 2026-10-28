import json, os, pathlib, stat, time
import pytest
from hc import config as C

ROOT = pathlib.Path(__file__).resolve().parents[1]

def test_data_dir_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("HC_DATA_DIR", raising=False)
    assert C.data_source() == "fallback"
    assert C.data_dir_path() == pathlib.Path(os.environ["HOME"]) / ".claude/harness-creator"
    pd = tmp_path / "plugins/data/harness-creator-local"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(pd))
    assert C.data_source() == "CLAUDE_PLUGIN_DATA" and C.data_dir_path() == pd
    monkeypatch.setenv("HC_DATA_DIR", str(tmp_path / "t"))
    assert C.data_source() == "HC_DATA_DIR" and C.data_dir_path() == tmp_path / "t"

def test_empty_plugin_data_falls_back(monkeypatch):
    monkeypatch.delenv("HC_DATA_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "")
    assert C.data_source() == "fallback"
    assert C.data_dir_path() == pathlib.Path(os.environ["HOME"]) / ".claude/harness-creator"

def test_foreign_plugin_data_ignored(tmp_path, monkeypatch):
    """다른 플러그인 값이 세션 환경에 새어 들어와도(실측: codex) 쓰지 않는다."""
    monkeypatch.delenv("HC_DATA_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugins/data/codex-openai-codex"))
    assert C.data_source() == "fallback"
    assert C.data_dir_path() == pathlib.Path(os.environ["HOME"]) / ".claude/harness-creator"

def test_own_plugin_data_used(tmp_path, monkeypatch):
    monkeypatch.delenv("HC_DATA_DIR", raising=False)
    pd = tmp_path / "plugins/data/harness-creator-local"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(pd) + "/")
    assert C.data_source() == "CLAUDE_PLUGIN_DATA" and C.data_dir_path() == pd

def test_data_dir_0700(cache):
    d = C.data_dir()
    assert d == cache and stat.S_IMODE(d.stat().st_mode) == 0o700

def test_load_config_does_not_create_dir(cache):
    C.load_config()
    assert not cache.exists()

def test_defaults_match_spec():
    g = C.load_defaults()["gates"]
    assert g == {"stale_days": 3, "batch_window_s": 10, "global_rule_min_sessions": 10,
                 "global_rule_min_days": 3, "max_candidates": 3}
    d = C.load_defaults()
    assert d["judge"]["model"] == "sonnet" and d["sources"]["codex"] is None and d["run"]["wrap"] == []

def test_user_config_deep_merge_and_include(cache, tmp_path):
    inc = tmp_path / "inc.json"
    inc.write_text(json.dumps({"judge": {"model": "opus"}, "gates": {"max_candidates": 2}}))
    C.save_user_config({"include": [str(inc)], "gates": {"max_candidates": 1}})
    cfg = C.load_config()
    assert cfg["judge"]["model"] == "opus"           # include 가 기본값을 덮는다
    assert cfg["gates"]["max_candidates"] == 1       # 사용자 값이 include 를 덮는다
    assert cfg["gates"]["stale_days"] == 3           # 나머지는 기본값
    assert stat.S_IMODE((cache / "config.json").stat().st_mode) == 0o600

def test_missing_include_fails_closed(cache, tmp_path):
    C.save_user_config({"include": [str(tmp_path / "nope.json")]})
    with pytest.raises(C.ConfigError):
        C.load_config()

def test_broken_user_config_fails_closed(cache):
    cache.mkdir()
    (cache / "config.json").write_text("{broken")
    with pytest.raises(C.ConfigError):
        C.load_config()

def test_now_override(monkeypatch):
    monkeypatch.setenv("HC_NOW", "2026-10-09T23:59:59+09:00")
    assert C.now() == 1791557999.0
    monkeypatch.setenv("HC_NOW", "1700000000")
    assert C.now() == 1700000000.0
    monkeypatch.delenv("HC_NOW")
    assert abs(C.now() - time.time()) < 5

def test_claude_home_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_CLAUDE_HOME", str(tmp_path / "ch"))
    assert C.claude_home() == tmp_path / "ch"

def test_every_module_has_future_annotations():
    for p in sorted((ROOT / "hc").rglob("*.py")):
        if p.name == "__init__.py" and p.parent.name == "hc":
            continue
        src = p.read_text(encoding="utf-8")
        assert "from __future__ import annotations" in src, p

def test_versions_agree():
    import hc
    pj = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    mj = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert pj["version"] == hc.__version__ == mj["plugins"][0]["version"] == "0.1.2"
    assert pj["name"] == mj["plugins"][0]["name"] == "harness-creator" and pj["license"] == "MIT"

def test_fallback_data_dir_gets_gitignore(tmp_path, monkeypatch):
    # 폴백 폴더는 ~/.claude 아래라 ~/.claude 를 git 으로 관리하는 사용자의 저장소에 섞이지 않게 한다
    from hc.config import data_dir
    monkeypatch.delenv("HC_DATA_DIR", raising=False)
    d = data_dir()
    assert d == tmp_path / "home" / ".claude" / "harness-creator"
    assert (d / ".gitignore").read_text() == "*\n"

def test_explicit_data_dir_has_no_gitignore(cache):
    from hc.config import data_dir
    assert not (data_dir() / ".gitignore").exists()

@pytest.mark.parametrize("bad", [-1, 0, "500", 1.5, True, None, 60001, 65536])
def test_text_max_chars_must_be_bounded_int(cache, bad):
    # 가림은 원문(최대 64KB) 전체에 건 뒤 이 길이로 자른다. 정수·범위 밖이면 수집 전에 멈춘다
    C.save_user_config({"text_max_chars": bad})
    with pytest.raises(C.ConfigError):
        C.load_config()

@pytest.mark.parametrize("ok", [1, 500, 60000])
def test_text_max_chars_valid(cache, ok):
    C.save_user_config({"text_max_chars": ok})
    assert C.load_config()["text_max_chars"] == ok

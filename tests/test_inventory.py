import json, os
import pytest
from hc.inventory import build, fit_input
from hc.redact import Redactor

class _Same:
    def redact(self, t): return t

def _home(tmp_path):
    h = tmp_path / "ch"
    for d in ("rules", "skills/foo", "agents", "commands", "hooks", "projects/p1/memory"):
        (h / d).mkdir(parents=True)
    (h / "rules" / "a.md").write_text("---\nname: a\ndescription: 규칙 A\npaths:\n  - \"src/**\"\n---\n# A\n")
    (h / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: 스킬 foo\n---\n")
    (h / "agents" / "bar.md").write_text("---\nname: bar\ndescription: |\n  여러 줄\n  설명\n---\n")
    (h / "commands" / "c.md").write_text("---\ndescription: 명령 c\n---\n")
    (h / "hooks" / "x-guard.sh").write_text("#!/bin/bash\n")
    (h / "projects" / "p1" / "memory" / "MEMORY.md").write_text("# idx\n- [제목](f.md) 후크\n")
    (h / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "~/.claude/hooks/y-check.py --flag"}]}]}}))
    return h

def test_build_reads_assets(tmp_path):
    inv = build(_home(tmp_path), redactor=_Same())
    assert inv["rules"] == [{"path": "rules/a.md", "name": "a", "description": "규칙 A"}]
    assert inv["skills"][0]["path"] == "skills/foo/SKILL.md"
    assert "여러 줄" in inv["agents"][0]["description"]
    assert inv["commands"][0]["description"] == "명령 c"
    assert inv["hook_children"] == ["x-guard.sh"] and inv["hook_commands"] == ["y-check.py"]
    assert inv["memory"] == ["- [제목](f.md) 후크"]

def test_build_requires_redactor(tmp_path):
    with pytest.raises(TypeError):
        build(tmp_path, None)

def test_build_rejects_none_redactor(tmp_path):
    with pytest.raises(TypeError):
        build(_home(tmp_path), redactor=None)

def test_symlinked_category_dir_followed(tmp_path):
    h = _home(tmp_path)
    ext = tmp_path / "dotfiles" / "rules"; ext.mkdir(parents=True)
    (ext / "z.md").write_text("---\nname: z\ndescription: 바깥 저장소 규칙\n---\n")
    import shutil; shutil.rmtree(h / "rules"); (h / "rules").symlink_to(ext, target_is_directory=True)
    assert [e["path"] for e in build(h, redactor=_Same())["rules"]] == ["rules/z.md"]

def test_broken_symlink_does_not_crash(tmp_path):
    h = _home(tmp_path)
    (h / "rules" / "gone.md").symlink_to(h / "rules" / "nope.md")
    inv = build(h, redactor=_Same())
    assert [e["path"] for e in inv["rules"]] == ["rules/a.md"]

def test_symlink_loops_do_not_hang(tmp_path):
    h = _home(tmp_path)
    (h / "rules" / "self.md").symlink_to(h / "rules" / "self.md")
    (h / "skills" / "cyc").symlink_to(h / "skills", target_is_directory=True)
    (h / "hooks" / "loop.sh").symlink_to(h / "hooks" / "loop.sh")
    inv = build(h, redactor=_Same())
    assert [e["path"] for e in inv["rules"]] == ["rules/a.md"]
    assert [e["path"] for e in inv["skills"]] == ["skills/foo/SKILL.md"]
    assert inv["hook_children"] == ["x-guard.sh"]

@pytest.mark.skipif(os.geteuid() == 0, reason="root는 권한 거부를 재현하지 못한다")
def test_unreadable_file_counted(tmp_path):
    h = _home(tmp_path)
    f = h / "rules" / "locked.md"; f.write_text("---\nname: l\n---\n"); f.chmod(0)
    try:
        inv = build(h, redactor=_Same())
    finally:
        f.chmod(0o600)
    assert [e["path"] for e in inv["rules"]] == ["rules/a.md"] and inv["unreadable"] == 1

def test_plugins_listed_and_self_skipped(tmp_path):
    h = _home(tmp_path)
    p1 = tmp_path / "cache" / "sp"; (p1 / "skills" / "tdd").mkdir(parents=True)
    (p1 / "skills" / "tdd" / "SKILL.md").write_text("---\nname: tdd\ndescription: 테스트 먼저\n---\n")
    me = tmp_path / "cache" / "hc"; (me / "skills" / "harness-creator").mkdir(parents=True)
    (me / "skills" / "harness-creator" / "SKILL.md").write_text("---\nname: harness-creator\ndescription: self\n---\n")
    pf = h / "plugins" / "installed_plugins.json"; pf.parent.mkdir()
    pf.write_text(json.dumps({"version": 2, "plugins": {
        "superpowers@x": [{"installPath": str(p1)}], "harness-creator@harness-creator": [{"installPath": str(me)}]}}))
    inv = build(h, pf, redactor=_Same())
    assert inv["plugins"] == [{"path": "plugin:superpowers/skills/tdd/SKILL.md", "name": "tdd", "description": "테스트 먼저"}]

def test_inventory_text_redacted(tmp_path):
    h = _home(tmp_path)
    lex = tmp_path / "lex.json"; lex.write_text(json.dumps(["Acme"]))
    home_dir = "/Users/" + "alice"
    (h / "rules" / "acme-rule.md").write_text("---\nname: r\ndescription: Acme 전용" + "gh" + "p_" + "Ab1" * 8
                                              + " 경로 " + home_dir + "/proj/x.md\n---\n")
    (h / "projects" / "p1" / "memory" / "MEMORY.md").write_text("- 메모 " + home_dir + "/notes.md\n")
    inv = build(h, redactor=Redactor(lexicon_files=[str(lex)]))
    raw = json.dumps(inv, ensure_ascii=False)
    assert "acme" not in raw.lower() and "Ab1Ab1" not in raw
    assert "alice" not in raw and "~/proj/x.md" in raw and inv["memory"] == ["- 메모 ~/notes.md"]

_TOKEN = "gh" + "p_" + "Q7x9Lm2Kp4R" * 4

def test_redact_before_truncating_description(tmp_path):
    h = _home(tmp_path)
    (h / "rules" / "edge.md").write_text("---\nname: e\ndescription: " + "a" * 290 + " " + _TOKEN + "\n---\n")
    inv = build(h, redactor=Redactor())
    d = [e for e in inv["rules"] if e["name"] == "e"][0]["description"]
    assert len(d) <= 300 and "gh" + "p_" not in d and "Q7x9" not in d

def test_memory_line_redacted_then_truncated(tmp_path):
    h = _home(tmp_path)
    (h / "projects" / "p1" / "memory" / "MEMORY.md").write_text("- " + "b" * 290 + " " + _TOKEN + "\n- " + "c" * 500 + "\n")
    inv = build(h, redactor=Redactor())
    raw = json.dumps(inv["memory"])
    assert "gh" + "p_" not in raw and "Q7x9" not in raw
    assert [len(l) <= 300 for l in inv["memory"]] == [True, True]

def test_hook_commands_skip_interpreters_and_quotes(tmp_path):
    h = _home(tmp_path)
    cmds = ["bash ~/.claude/hooks/a-guard.sh", "\"$HOME/.claude/hooks/b-check.sh\" --x",
            "FOO=bar env -i python3 -u ~/.claude/hooks/c.py", "uv run ~/.claude/hooks/d.py",
            "/usr/bin/env node ~/.claude/hooks/e.js", "echo 'unterminated", "env -i FOO=1 python3 -u a.py",
            "\"$HOME/.claude/hooks/sp ace.sh\""]
    (h / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": c} for c in cmds]}]}}))
    inv = build(h, redactor=_Same())
    assert inv["hook_commands"] == sorted(["a-guard.sh", "b-check.sh", "c.py", "d.py", "e.js", "echo", "a.py"])

_HOOK_SECRET = "Zq8" + "Wv3Nk5" + "Rt7Yp2"

def test_hook_commands_never_emit_url_or_inline_script_tail(tmp_path):
    h = _home(tmp_path)
    cmds = ["bash -c \"curl https://x.example/services/T0/B0/" + _HOOK_SECRET + "\"",
            "curl -s https://hooks.example.com/" + _HOOK_SECRET,
            "node -e \"fetch('https://x/" + _HOOK_SECRET + "')\"",
            "bash -c 'curl https://x.example/" + _HOOK_SECRET,          # 따옴표 미닫힘 폴백
            "https://x.example/a?k=" + _HOOK_SECRET]
    (h / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": c} for c in cmds]}]}}))
    names = build(h, redactor=_Same())["hook_commands"]
    assert _HOOK_SECRET not in json.dumps(names) and "Zq8" not in json.dumps(names)
    assert names == ["bash", "curl", "node"]

def test_fit_input_drops_lowest_signals_first():
    sigs = [{"id": f"s{i}", "count": i, "sessions": ["a"] * i, "samples": ["x" * 200]} for i in range(1, 6)]
    inv = {"rules": [], "memory": ["- m" * 50]}
    out = fit_input(sigs, inv, 800)
    assert out["inventory"]["memory"] == [] and [s["id"] for s in out["signals"]][0] == "s5"
    assert len(json.dumps(out, ensure_ascii=False).encode()) <= 800

def test_fit_input_does_not_mutate_or_add_keys():
    inv = {"rules": [{"path": "rules/a.md", "name": "a", "description": "d" * 500}], "memory": ["- m"]}
    out = fit_input([{"id": "s", "count": 1, "sessions": ["a"], "samples": ["x"]}], inv, 300)
    assert out["inventory"]["rules"][0]["description"] == "" and set(out["inventory"]) == {"rules", "memory"}
    assert inv["rules"][0]["description"] == "d" * 500 and inv["memory"] == ["- m"]

def test_fit_input_over_limit_raises():
    with pytest.raises(ValueError):
        fit_input([{"id": "s", "count": 1, "sessions": ["a"], "samples": ["x" * 5000]}], {}, 100)

def test_propose_prompt_contract():
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "propose-prompt.md"
    t = p.read_text(encoding="utf-8")
    assert t.rstrip().endswith("INPUT JSON:") and "{TODAY}" in t and "{LANG}" in t
    assert '{"candidates": []}' in t and "already does the same job" in t
    assert "hooks/harness-creator/<candidate id>.sh" in t

def test_hook_commands_extensionless_path_not_named(tmp_path):
    h = _home(tmp_path)
    cmds = ["/hooks/" + _HOOK_SECRET, "~/bin/" + _HOOK_SECRET, "bash /hooks/" + _HOOK_SECRET + " --x",
            "node ~/h/w.cjs", "perl ~/h/z.pl", "ruby ~/h/r.rb"]
    (h / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": c} for c in cmds]}]}}))
    names = build(h, redactor=_Same())["hook_commands"]
    assert "Zq8" not in json.dumps(names)
    assert names == sorted(["bash", "w.cjs", "z.pl", "r.rb"])

# --- 마지막 대체 토큰도 파일명 문자 집합([A-Za-z0-9._+-])일 때만 내보낸다 ---
import pytest as _pt

@_pt.mark.parametrize("cmd", ["a.sh&&" + "Zq9" * 6, "${HOOK_" + "Zq9" * 6 + "}", "run;" + "Zq9" * 6 + ".sh"])
def test_script_name_rejects_odd_characters(cmd):
    from hc.inventory import _script_name
    out = _script_name(cmd)
    assert "Zq9" not in out

def test_script_name_keeps_plain_names():
    from hc.inventory import _script_name
    assert _script_name("bash ~/hooks/guard-x.sh") == "guard-x.sh"
    assert _script_name("notify") == "notify"

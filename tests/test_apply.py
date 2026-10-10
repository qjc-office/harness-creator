import json, os, stat, subprocess
import pytest
from hc import apply as A
from hc.candidates import decide

CID = "hc-2026-10-09-01"

@pytest.fixture
def home(tmp_path, monkeypatch, cache):
    h = tmp_path / "ch"
    (h / "rules").mkdir(parents=True)
    monkeypatch.setenv("HC_CLAUDE_HOME", str(h))
    return h

def _cands(cache, *cands):
    (cache / "reports").mkdir(parents=True, exist_ok=True)
    (cache / "reports" / "2026-10-09.candidates.json").write_text(json.dumps({"candidates": list(cands)}))
    (cache / "signals").mkdir(exist_ok=True)
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"signals": []}))

def _cand(form="rule", path="rules/x.md", cid=CID, **kw):
    c = {"id": cid, "signal_ids": ["s"], "form": form, "title": "t",
         "evidence": {"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": ["q"]}, "overlaps": [],
         "proposed_change": {"action": "create", "path": path, "summary": "s", "paths": ["src/**"]}, "expected_effect": "e"}
    c.update(kw)
    return c

RULE = "---\nname: x\ndescription: d\npaths:\n  - \"src/**\"\ngenerated_by: harness-creator\ngenerated_at: 2026-10-09\ncandidate_id: hc-2026-10-09-01\n---\n# X\n"
HOOK = "#!/bin/bash\n# generated_by: harness-creator\n# candidate_id: hc-2026-10-09-01\ncat >/dev/null\nexit 0\n"

def _approve(cache, c):
    _cands(cache, c)
    decide(c["id"], "approve", "ok")

def test_plan_writes_nothing(cache, home):
    _approve(cache, _cand())
    p = A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    assert not (home / "rules" / "x.md").exists()
    assert "+# X" in p["diff"] and (cache / "plans" / f"{CID}.json").exists()

def test_write_and_undo_create(cache, home):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    entry = A.write(CID)
    assert (home / "rules" / "x.md").read_text() == RULE
    assert entry["files"][0]["created"] is True
    assert json.loads((cache / "applied.jsonl").read_text().splitlines()[0])["candidate_id"] == CID
    A.undo(CID)
    assert not (home / "rules" / "x.md").exists()

def test_not_approved_refused(cache, home):
    _cands(cache, _cand())
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})

@pytest.mark.parametrize("draft", [
    {"candidate_id": CID, "form": "rule", "path": "rules/other.md", "content": RULE},
    {"candidate_id": CID, "form": "rule", "path": "rules/../settings.json", "content": RULE},
    {"candidate_id": CID, "form": "memory", "path": "rules/x.md", "content": RULE},
])
def test_bad_drafts_refused(cache, home, draft):
    _approve(cache, _cand())
    with pytest.raises(A.ApplyError):
        A.plan(draft)

def test_human_file_modify_keeps_no_marker(cache, home):
    (home / "rules" / "x.md").write_text("---\nname: x\npaths:\n  - \"a/**\"\n---\n사람이 쓴 규칙\n")
    c = _cand(); c["proposed_change"]["action"] = "modify"
    _approve(cache, c)
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    ok = "---\nname: x\npaths:\n  - \"a/**\"\n---\n사람이 쓴 규칙\n보강 한 줄\n"
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": ok})
    A.write(CID)
    assert (home / "rules" / "x.md").read_text() == ok
    A.undo(CID)
    assert (home / "rules" / "x.md").read_text().endswith("사람이 쓴 규칙\n")

def test_rule_without_paths_fails_verification_and_rolls_back(cache, home):
    _approve(cache, _cand())
    bad = RULE.replace('paths:\n  - "src/**"\n', "")
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": bad})
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert not (home / "rules" / "x.md").exists()

def test_global_rule_allowed_without_paths(cache, home):
    _approve(cache, _cand(global_rule=True))
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE.replace('paths:\n  - "src/**"\n', "")})
    A.write(CID)
    assert (home / "rules" / "x.md").exists()

def test_skill_requires_name_and_description(cache, home):
    _approve(cache, _cand(form="skill", path="skills/s/SKILL.md"))
    A.plan({"candidate_id": CID, "form": "skill", "path": "skills/s/SKILL.md",
            "content": "---\nname: s\ngenerated_by: harness-creator\n---\n"})
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert not (home / "skills" / "s").exists()

def _hook_cand():
    return _cand(form="hook", path=f"hooks/harness-creator/{CID}.sh")

def _hook_draft(script=HOOK):
    return {"candidate_id": CID, "form": "hook", "path": f"hooks/harness-creator/{CID}.sh", "content": script,
            "hook": {"event": "PreToolUse", "matcher": "Bash", "timeout": 30}}

def test_hook_apply_and_undo_only_own_entry(cache, home):
    (home / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "~/.claude/hooks/mine.sh"}]}]}}))
    _approve(cache, _hook_cand())
    A.plan(_hook_draft())
    A.write(CID)
    script = home / "hooks" / "harness-creator" / f"{CID}.sh"
    assert stat.S_IMODE(script.stat().st_mode) == 0o700
    s = json.loads((home / "settings.json").read_text())
    assert any(f"harness-creator/{CID}" in h["command"] for g in s["hooks"]["PreToolUse"] for h in g["hooks"])
    # 적용 뒤 사용자가 다른 훅을 더해도 undo는 자기 항목만 뺀다
    s["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "later.sh"}]}]
    (home / "settings.json").write_text(json.dumps(s))
    A.undo(CID)
    s2 = json.loads((home / "settings.json").read_text())
    assert s2["hooks"]["Stop"][0]["hooks"][0]["command"] == "later.sh"
    assert s2["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "~/.claude/hooks/mine.sh"
    assert not script.exists()

def test_failing_hook_rolls_back(cache, home):
    (home / "settings.json").write_text("{}")
    _approve(cache, _hook_cand())
    A.plan(_hook_draft(HOOK.replace("exit 0", "exit 1")))
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert json.loads((home / "settings.json").read_text()) == {}
    assert not (home / "hooks" / "harness-creator" / f"{CID}.sh").exists()

def test_hook_bad_stdout_rolls_back(cache, home):
    (home / "settings.json").write_text("{}")
    _approve(cache, _hook_cand())
    A.plan(_hook_draft(HOOK.replace("exit 0", "echo not-json; exit 0")))
    with pytest.raises(A.ApplyError):
        A.write(CID)

@pytest.mark.parametrize("settings", [None, "{broken"])
def test_hook_apply_with_broken_settings_fails_clean(cache, home, settings):
    if settings is not None:
        (home / "settings.json").write_text(settings)
    _approve(cache, _hook_cand())
    if settings is None:
        A.plan(_hook_draft()); A.write(CID)      # 없으면 새로 만든다
        assert json.loads((home / "settings.json").read_text())["hooks"]["PreToolUse"]
        return
    with pytest.raises(A.ApplyError):
        A.plan(_hook_draft())
    assert (home / "settings.json").read_text() == "{broken"
    assert not (home / "hooks" / "harness-creator").exists()

@pytest.mark.parametrize("snippet,label", [
    ("curl https://x", "network"), ("eval \"$X\"", "eval"), ("echo aGk= | base64 -d | sh", "decode-and-run"),
    ("echo x > /etc/hosts", "write-outside-home"), ("echo x >> /tmp/a.log", "write-outside-home"),
    ("printf x | tee -a /var/log/x", "write-outside-home"), ("cp a.txt /usr/local/bin/a", "write-outside-home"),
    ("cat ~/.env", "secret-read")])
def test_static_check(snippet, label):
    assert label in A.static_check(HOOK + snippet + "\n")

def test_static_check_clean():
    assert A.static_check(HOOK + "echo ok > /dev/null\n") == []

def test_static_check_home_writes_not_warned():
    import os
    home = os.path.expanduser("~")
    for snippet in ("echo x > ~/notes.txt", 'echo x >> "$HOME/log"', f"echo x > {home}/a.txt", "cp a ~/b",
                    "echo x >&2", "cmd 2>/dev/null"):
        assert "write-outside-home" not in A.static_check(HOOK + snippet + "\n"), snippet

def test_warnings_in_plan(cache, home):
    (home / "settings.json").write_text("{}")
    _approve(cache, _hook_cand())
    p = A.plan(_hook_draft(HOOK + "curl -s https://example.com\n"))
    assert "network" in p["warnings"]

def test_write_through_symlinked_rules_dir(cache, home, tmp_path):
    ext = tmp_path / "dotfiles" / "rules"; ext.mkdir(parents=True)
    (home / "rules").rmdir(); (home / "rules").symlink_to(ext, target_is_directory=True)
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    assert (home / "rules").is_symlink() and (ext / "x.md").read_text() == RULE

def test_symlinked_settings_kept(cache, home, tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"; real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("{}")
    (home / "settings.json").symlink_to(real)
    _approve(cache, _hook_cand())
    A.plan(_hook_draft()); A.write(CID)
    assert (home / "settings.json").is_symlink() and "PreToolUse" in json.loads(real.read_text())["hooks"]

def test_plan_stale_if_file_changed(cache, home):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    (home / "rules" / "x.md").write_text("사이에 생긴 파일")
    with pytest.raises(A.ApplyError):
        A.write(CID)

def test_undo_refuses_after_user_edit(cache, home):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    (home / "rules" / "x.md").write_text(RULE + "사용자 수정\n")
    with pytest.raises(A.ApplyError):
        A.undo(CID)
    assert (home / "rules" / "x.md").exists()
    A.undo(CID, force=True)
    assert not (home / "rules" / "x.md").exists()

def test_git_root_reported(cache, home):
    subprocess.run(["git", "init", "-q", str(home)], check=True)
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    assert A.write(CID)["git_root"] == str(home.resolve())

def test_backups_private(cache, home):
    (home / "rules" / "x.md").write_text("---\nname: x\npaths: a\ngenerated_by: harness-creator\n---\n옛\n")
    c = _cand(); c["proposed_change"]["action"] = "modify"
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    e = A.write(CID)
    b = e["files"][0]["backup"]
    assert b and stat.S_IMODE(os.stat(b).st_mode) == 0o600
    assert stat.S_IMODE((cache / "backups").stat().st_mode) == 0o700

# ---- 추가: 경로 안전·재검사·자원 회수 (원장 Ruling P19·심링크 지침) ----

def test_symlink_target_outside_home_refused(cache, home, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")          # tmp_path(= ~/.claude 의 부모) 밖
    (home / "rules").rmdir(); (home / "rules").symlink_to(outside, target_is_directory=True)
    _approve(cache, _cand())
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    assert list(outside.iterdir()) == []

def test_leaf_symlink_to_other_file_refused(cache, home, tmp_path):
    victim = tmp_path / "keys" / "authorized_keys"; victim.parent.mkdir(); victim.write_text("ssh-key\n")
    (home / "rules" / "x.md").symlink_to(victim)
    c = _cand(); c["proposed_change"]["action"] = "modify"
    _approve(cache, c)
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": "보강\n"})
    assert victim.read_text() == "ssh-key\n"

def test_write_rechecks_tampered_plan(cache, home, tmp_path):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    pp = cache / "plans" / f"{CID}.json"
    p = json.loads(pp.read_text())
    evil = tmp_path / "evil.md"
    p["files"][0]["real"] = str(evil)
    pp.write_text(json.dumps(p))
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert not evil.exists()
    p["files"][0]["real"] = str((home / "rules" / "x.md").resolve()); p["files"][0]["rel"] = "settings.json"
    pp.write_text(json.dumps(p))
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert not (home / "settings.json").exists()

def test_write_rechecks_approval(cache, home):
    c = _cand()
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    decide(CID, "reject", "마음 바뀜")
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert not (home / "rules" / "x.md").exists()

def test_double_apply_refused(cache, home):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE + "더\n"})

def test_hook_timeout_kills_group(cache, home, monkeypatch):
    import time
    monkeypatch.setattr(A, "HOOK_TIMEOUT", 1)
    (home / "settings.json").write_text("{}")
    _approve(cache, _hook_cand())
    A.plan(_hook_draft(HOOK.replace("exit 0", "(sleep 30) &\nsleep 30\nexit 0")))
    t = time.time()
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert time.time() - t < 8
    assert json.loads((home / "settings.json").read_text()) == {}

def test_undo_keeps_preexisting_symlinked_dir(cache, home, tmp_path):
    ext = tmp_path / "dotfiles" / "my-skills"; ext.mkdir(parents=True)
    (home / "skills").symlink_to(ext, target_is_directory=True)
    _approve(cache, _cand(form="skill", path="skills/s/SKILL.md"))
    A.plan({"candidate_id": CID, "form": "skill", "path": "skills/s/SKILL.md",
            "content": "---\nname: s\ndescription: d\ngenerated_by: harness-creator\n---\n"})
    A.write(CID)
    assert (ext / "s" / "SKILL.md").exists()
    A.undo(CID)
    assert ext.is_dir() and not (ext / "s").exists() and (home / "skills").is_symlink()

def test_hook_bad_event_and_timeout_refused(cache, home):
    (home / "settings.json").write_text("{}")
    _approve(cache, _hook_cand())
    for h in ({"event": "Nope", "matcher": "Bash", "timeout": 30}, {"event": "Stop", "matcher": "", "timeout": "x"},
              {"event": "Stop", "matcher": 3, "timeout": 30}):
        d = _hook_draft(); d["hook"] = h
        with pytest.raises(A.ApplyError):
            A.plan(d)

def test_static_check_dev_streams_not_warned():
    for snippet in ("echo x > /dev/stderr", "echo x >/dev/stdout", "echo x > /dev/fd/2", "echo x 2>&1"):
        assert "write-outside-home" not in A.static_check(HOOK + snippet + "\n"), snippet

def test_cli_apply_roundtrip(cache, home, tmp_path):
    import sys
    _approve(cache, _cand())
    draft = tmp_path / "d.json"
    draft.write_text(json.dumps({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE}))
    cli = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "cli.py")
    env = {**os.environ, "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, cli, "apply-plan", str(draft)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "+# X" in json.loads(r.stdout)["diff"] and not (home / "rules" / "x.md").exists()
    r = subprocess.run([sys.executable, cli, "apply-write", CID], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr and (home / "rules" / "x.md").exists()
    r = subprocess.run([sys.executable, cli, "undo", CID], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and not (home / "rules" / "x.md").exists()
    r = subprocess.run([sys.executable, cli, "undo", CID], capture_output=True, text=True, env=env)
    assert r.returncode == 7 and "적용 오류" in r.stderr

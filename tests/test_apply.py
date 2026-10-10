import json, os, re, stat, subprocess, time
import pytest
from hc import apply as A
from hc.candidates import decide
from hc.config import now as hc_now

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

# 판정(구조 전환): rule·skill frontmatter 는 초안 것을 버리고 apply 가 고정 키로 만든다.
# 아래 헬퍼는 그 기대 블록과, Claude Code 로더처럼 자른(첫 `---` 에서 끝) 결과를 엄격하게 읽는 판독기다.
_LOADER = re.compile(r"---\s*\n([\s\S]*?)---\s*\n?")

def _today():
    return time.strftime("%Y-%m-%d", time.localtime(hc_now()))

def _fm(desc="d", paths=("src/**",), marked=True, name=None):
    lines = ["---"] + ([f"name: {json.dumps(name, ensure_ascii=False)}"] if name is not None else [])
    lines.append(f"description: {json.dumps(desc, ensure_ascii=False)}")
    if paths:
        lines += ["paths:"] + [f"  - {json.dumps(g, ensure_ascii=False)}" for g in paths]
    if marked:
        lines += ["generated_by: harness-creator", f'generated_at: "{_today()}"', f'candidate_id: "{CID}"']
    return "\n".join(lines) + "\n---\n"

def _rule_out(body="# X\n", **kw):
    return _fm(**kw) + body

def _loader_fm(text):
    """로더 정규식으로 자른 블록을 정규형(`키: JSON`, `paths:` + `  - JSON`)만 받는 판독기로 읽는다. 다른 줄은 실패."""
    m = _LOADER.match(text[1:] if text.startswith("﻿") else text)
    assert m, text
    out, key = {}, None
    for line in m.group(1).splitlines():
        if line.startswith("  - "):
            assert key == "paths", line
            out["paths"].append(json.loads(line[4:]))
        elif line == "paths:":
            key, out["paths"] = "paths", []
        else:
            k, v = line.split(": ", 1)
            key, out[k] = k, (v if k == "generated_by" else json.loads(v))
    return out

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
    assert (home / "rules" / "x.md").read_text() == _rule_out()      # 판정: name 등 초안 키는 버리고 고정 키로 생성
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
    # 판정: 기존 파일 수정도 paths 는 승인 범위로, frontmatter 는 고정 키로 새로 만들고 표지는 넣지 않는다
    # (초안에 description 이 없어 후보 제목 "t" 로 채운다)
    assert (home / "rules" / "x.md").read_text() == _rule_out("사람이 쓴 규칙\n보강 한 줄\n", desc="t", marked=False)
    A.undo(CID)
    assert (home / "rules" / "x.md").read_text().endswith("사람이 쓴 규칙\n")

def test_rule_without_paths_gets_approved_paths(cache, home):
    # 판정: 초안 frontmatter 를 버리고 승인 paths 로 만들므로, 초안에 paths 가 없어도 상시 로드 rule 이 되지 않는다
    # (브리프 원안 기대값 "쓰기 뒤 검증 거부"는 이 구조에서 생길 수 없어 바꿈)
    _approve(cache, _cand())
    bad = RULE.replace('paths:\n  - "src/**"\n', "")
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": bad})
    A.write(CID)
    assert _loader_fm((home / "rules" / "x.md").read_text())["paths"] == ["src/**"]

def test_global_rule_allowed_without_paths(cache, home):
    _approve(cache, _cand(global_rule=True))
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE.replace('paths:\n  - "src/**"\n', "")})
    A.write(CID)
    assert (home / "rules" / "x.md").exists()

def test_skill_description_falls_back_to_title(cache, home):
    # 판정: name·description 은 초안에서 못 얻으면 후보 제목으로 채운다(브리프 원안 기대값 "거부"에서 바꿈)
    _approve(cache, _cand(form="skill", path="skills/s/SKILL.md"))
    A.plan({"candidate_id": CID, "form": "skill", "path": "skills/s/SKILL.md",
            "content": "---\nname: s\ngenerated_by: harness-creator\n---\n"})
    A.write(CID)
    assert (home / "skills" / "s" / "SKILL.md").read_text() == _fm(desc="t", name="s", paths=())

def test_skill_without_usable_title_refused(cache, home):
    _approve(cache, _cand(form="skill", path="skills/s/SKILL.md", title="a --- b"))
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "skill", "path": "skills/s/SKILL.md", "content": "# 본문\n"})
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
    assert (home / "rules").is_symlink() and (ext / "x.md").read_text() == _rule_out()

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
    assert r.returncode == 0, r.stderr
    assert (home / "rules" / "x.md").exists()
    r = subprocess.run([sys.executable, cli, "undo", CID], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and not (home / "rules" / "x.md").exists()
    r = subprocess.run([sys.executable, cli, "undo", CID], capture_output=True, text=True, env=env)
    assert r.returncode == 7 and "적용 오류" in r.stderr


def _rewrite_cands(cache, c):
    (cache / "reports" / "2026-10-09.candidates.json").write_text(json.dumps({"candidates": [c]}))

@pytest.mark.parametrize("change", ["global_rule", "paths"])
def test_approval_not_carried_when_scope_changes(cache, home, change):
    c = _cand()
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    c2 = json.loads(json.dumps(c))
    if change == "global_rule":
        c2["global_rule"] = True
    else:
        c2["proposed_change"]["paths"] = ["**"]
    _rewrite_cands(cache, c2)
    with pytest.raises(A.ApplyError):
        A.write(CID)
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    assert not (home / "rules" / "x.md").exists()

def _tamper_applied(cache, fn):
    p = cache / "applied.jsonl"
    lines = [json.loads(l) for l in p.read_text().splitlines()]
    fn(lines[-1])
    p.write_text("".join(json.dumps(l) + "\n" for l in lines))

def test_tampered_ledger_cannot_delete_human_file(cache, home):
    human = home / "rules" / "human.md"
    human.write_text("---\nname: h\n---\n사람이 쓴 규칙\n")
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    def drop_sha(e):
        e["files"][0].update(rel="rules/human.md", real=str(human.resolve())); e["files"][0].pop("sha_after", None)
    _tamper_applied(cache, drop_sha)
    with pytest.raises(A.ApplyError):
        A.undo(CID)
    with pytest.raises(A.ApplyError):
        A.undo(CID, force=True)
    assert human.read_text().endswith("사람이 쓴 규칙\n")

def test_tampered_ledger_force_still_needs_marker(cache, home):
    human = home / "rules" / "human.md"
    human.write_text("---\nname: h\n---\n사람이 쓴 규칙\n")
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    _tamper_applied(cache, lambda e: e["files"][0].update(rel="rules/human.md", real=str(human.resolve())))
    with pytest.raises(A.ApplyError):
        A.undo(CID, force=True)
    assert human.exists()

def test_tampered_created_dirs_outside_claude_home_kept(cache, home, tmp_path):
    empty = tmp_path / "keep-me"; empty.mkdir()
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    _tamper_applied(cache, lambda e: e["files"][0].update(created_dirs=[str(empty), str(home / "rules")],
                                                          mode_before=0o4777))
    A.undo(CID)
    assert empty.is_dir() and (home / "rules").is_dir()

def test_tampered_mode_before_masked(cache, home):
    (home / "rules" / "x.md").write_text("---\nname: x\npaths: a\ngenerated_by: harness-creator\n---\n옛\n")
    os.chmod(home / "rules" / "x.md", 0o644)
    c = _cand(); c["proposed_change"]["action"] = "modify"
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    _tamper_applied(cache, lambda e: e["files"][0].update(mode_before=0o6755))
    A.undo(CID)
    assert stat.S_IMODE((home / "rules" / "x.md").stat().st_mode) == 0o755

def test_rollback_failure_does_not_hide_original(cache, home, monkeypatch):
    (home / "settings.json").write_text("{}")
    _approve(cache, _hook_cand())
    A.plan(_hook_draft())
    def ledger_fail(entry):
        raise OSError("원장 쓰기 실패 흉내")
    def boom(*a, **k):
        raise OSError("복원 실패 흉내")
    monkeypatch.setattr(A, "record_applied", ledger_fail)
    monkeypatch.setattr(A, "_restore", boom)          # settings 복원이 실패해도
    with pytest.raises(A.ApplyError) as ei:
        A.write(CID)
    assert "원장 쓰기 실패 흉내" in str(ei.value) and "복원 실패 흉내" in str(ei.value)
    assert not (home / "hooks" / "harness-creator" / f"{CID}.sh").exists()   # 나머지 롤백은 계속된다

def test_hook_runs_before_settings_entry(cache, home):
    sp = home / "settings.json"; sp.write_text("{}")
    _approve(cache, _hook_cand())
    script = HOOK.replace("exit 0", f"if grep -q harness-creator/ '{sp}'; then exit 1; fi\nexit 0")
    A.plan(_hook_draft(script))
    A.write(CID)
    assert "PreToolUse" in json.loads(sp.read_text())["hooks"]

def test_create_action_refuses_existing_file(cache, home):
    (home / "rules" / "x.md").write_text("---\nname: x\npaths: a\ngenerated_by: harness-creator\n---\n옛\n")
    _approve(cache, _cand())
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})

def test_static_check_dev_tcp():
    assert "network" in A.static_check(HOOK + "exec 3<>/dev/tcp/example.com/80\n")
    assert "network" in A.static_check(HOOK + "echo x > /dev/udp/1.2.3.4/53\n")


SKILL_OLD = "---\nname: a\ndescription: 옛 A\ngenerated_by: harness-creator\n---\n옛 A 본문\n"
SKILL_NEW = "---\nname: a\ndescription: 새 A\ngenerated_by: harness-creator\n---\n새 A 본문\n"

def test_same_name_backup_cannot_overwrite_other_skill(cache, home):
    import hashlib
    for slug, text in (("a", SKILL_OLD), ("mine", "---\nname: mine\ndescription: 사람\n---\n사람 스킬\n")):
        (home / "skills" / slug).mkdir(parents=True); (home / "skills" / slug / "SKILL.md").write_text(text)
    c = _cand(form="skill", path="skills/a/SKILL.md"); c["proposed_change"]["action"] = "modify"
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "skill", "path": "skills/a/SKILL.md", "content": SKILL_NEW})
    A.write(CID)
    mine = home / "skills" / "mine" / "SKILL.md"
    sha = hashlib.sha256(mine.read_bytes()).hexdigest()
    _tamper_applied(cache, lambda e: e["files"][0].update(rel="skills/mine/SKILL.md", real=str(mine.resolve()),
                                                          sha_after=sha))
    with pytest.raises(A.ApplyError):
        A.undo(CID)
    assert mine.read_text().endswith("사람 스킬\n")

def test_backup_name_bound_to_rel(cache, home):
    import hashlib
    (home / "rules" / "x.md").write_text("---\nname: x\npaths: a\ngenerated_by: harness-creator\n---\n옛\n")
    c = _cand(); c["proposed_change"]["action"] = "modify"
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    b = A.write(CID)["files"][0]["backup"]
    assert hashlib.sha256(b"rules/x.md").hexdigest()[:16] in os.path.basename(b)

# 판정: 대조가 아니라 정규형 강제. 초안 paths 가 무엇이든 최종 파일은 승인 paths 정규형이다.
@pytest.mark.parametrize("paths_block", [
    'paths: "**"\n', 'paths:\n  - "src/**"\n  - "**"\n', "paths: [lib/**]\n",              # 확장
    'paths: ["src/**"]\n', "paths: src/**\n", "paths:\n- 'src/**'\n",                       # 일치 변주
    'paths:\n  - "src/**"\n\n  - "**"\n',                                                  # P1 빈 줄 뒤 항목
    'paths:\n  - "src/**"\n  # c\n  - "**"\n',                                              # P2 주석 뒤 항목
    'paths:\n  - "src/**"\npaths: "**"\n',                                                  # P3 중복 키
    'paths:\n  - "src/**"\n# top\npaths:\n  - "**"\n'])
def test_rule_paths_forced_to_approved_canonical(cache, home, paths_block):
    _approve(cache, _cand())
    draft = RULE.replace('paths:\n  - "src/**"\n', paths_block)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": draft})
    A.write(CID)
    assert (home / "rules" / "x.md").read_text() == _rule_out()    # 고정 키로 생성(description 은 초안 값)

# ---- 정규형 강제 ----

def test_p4_existing_wide_paths_replaced(cache, home):
    (home / "rules" / "x.md").write_text('---\nname: x\npaths: "**"\n---\n사람\n')
    c = _cand(); c["proposed_change"]["action"] = "modify"
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": '---\nname: x\npaths: "**"\n---\n사람 + 새\n'})
    A.write(CID)
    assert (home / "rules" / "x.md").read_text() == _rule_out("사람 + 새\n", desc="t", marked=False)

@pytest.mark.parametrize("pc_paths", [None, [], [""], "src/**"])
def test_p6_non_global_rule_without_approved_paths_refused(cache, home, pc_paths):
    c = _cand()
    if pc_paths is None:
        del c["proposed_change"]["paths"]
    else:
        c["proposed_change"]["paths"] = pc_paths
    _approve(cache, c)
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE.replace('src/**', '**')})
    assert not (home / "rules" / "x.md").exists()

def test_multiple_globs_canonical_json_escaped(cache, home):
    c = _cand(); c["proposed_change"]["paths"] = ["src/**", 'docs/"q"/**', "src/**"]
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE.replace('src/**', '**')})
    A.write(CID)
    t = (home / "rules" / "x.md").read_text()
    assert 'paths:\n  - "src/**"\n  - "docs/\\"q\\"/**"\ngenerated_by' in t and t.count("paths:") == 1

def test_global_rule_paths_removed(cache, home):
    _approve(cache, _cand(global_rule=True))
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md",
            "content": RULE.replace('paths:\n  - "src/**"\n', 'paths: "**"\npaths:\n  - a\n')})
    A.write(CID)
    t = (home / "rules" / "x.md").read_text()
    assert "paths" not in t and t == _rule_out(paths=())

# 판정: 초안 frontmatter 는 버리므로 YAML 형식이 무엇이든 거부하지 않고, 최종 paths 는 승인 집합이다
# (이전 "단순하지 않은 frontmatter 거부" 기대값을 바꿈)
@pytest.mark.parametrize("bad", [
    'base: &b\n  paths: "**"\n<<: *b\n',          # 병합 키·앵커
    '"paths": "**"\n',                             # 따옴표 키
    "? paths\n: '**'\n",                           # 명시 키
    'name2: x\rpaths: "**"\n',                     # CR 줄바꿈
    "\tpaths: '**'\n",                             # 탭 들여쓰기
    'note: *alias\n',                              # 별칭
])
def test_non_simple_draft_frontmatter_discarded(cache, home, bad):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE.replace("generated_by", bad + "generated_by")})
    A.write(CID)
    assert _loader_fm((home / "rules" / "x.md").read_text())["paths"] == ["src/**"]

@pytest.mark.parametrize("draft", [
    '---\n  name: x\n  paths: "**"\n  generated_by: harness-creator\n---\n# X\n',     # 들여쓴 frontmatter
    RULE.replace("---\n# X", "---x\npaths: \"**\"\n---\n# X"),                         # 정확하지 않은 닫는 줄
])
def test_odd_draft_frontmatter_discarded(cache, home, draft):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": draft})
    A.write(CID)
    t = (home / "rules" / "x.md").read_text()
    assert _loader_fm(t)["paths"] == ["src/**"] and t.endswith("\n---\n# X\n")

# ---- 로더 정규식(첫 `---` 에서 끝, YAML 실패면 {}) 대응 ----

_Y = {
    "Y1": "---\nname: x\ndescription: d ---\npaths: src/**\ngenerated_by: harness-creator\n---\n# X\n",
    "Y2": "---\nname: x\n# ---\npaths: src/**\ngenerated_by: harness-creator\n---\n# X\n",
    "Y3": '---\nname: x\ndescription: d\npaths: src/**\nzz: "abc\ngenerated_by: harness-creator\n---\n# X\n',
    "Y4": '---\nname: x\ndescription: "abc\npaths: "**"\ngenerated_by: harness-creator\n---\n# X\n',
}

@pytest.mark.parametrize("label", sorted(_Y))
def test_loader_cut_paths_exactly_approved(cache, home, label):
    _approve(cache, _cand())
    p = A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": _Y[label]})
    A.write(CID)
    t = (home / "rules" / "x.md").read_text()
    fm = _loader_fm(t)
    assert fm["paths"] == ["src/**"] and fm["generated_by"] == "harness-creator" and fm["candidate_id"] == CID
    assert t.startswith(p["files"][0]["frontmatter"]) and t.endswith("\n---\n# X\n")
    assert "---" not in fm["description"]

def test_y1_description_with_dashes_uses_title(cache, home):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": _Y["Y1"]})
    A.write(CID)
    assert (home / "rules" / "x.md").read_text() == _rule_out(desc="t")

def test_skill_loader_cut_has_name_and_description(cache, home):
    _approve(cache, _cand(form="skill", path="skills/s/SKILL.md"))
    A.plan({"candidate_id": CID, "form": "skill", "path": "skills/s/SKILL.md",
            "content": '---\nname: s ---\ndescription: "설명: #1\nzz: "a\n---\n본문\n'})
    A.write(CID)
    fm = _loader_fm((home / "skills" / "s" / "SKILL.md").read_text())
    assert fm["name"] == "t" and fm["description"] and "paths" not in fm

def test_draft_without_frontmatter_is_all_body(cache, home):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": "# X\n본문\n"})
    A.write(CID)
    assert (home / "rules" / "x.md").read_text() == _rule_out("# X\n본문\n", desc="t")

def test_body_starting_with_dashes_kept_apart(cache, home):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md",
            "content": RULE.replace("---\n# X\n", '---\n---\npaths: "**"\n---\n# X\n')})
    A.write(CID)
    t = (home / "rules" / "x.md").read_text()
    assert t == _fm() + '\n---\npaths: "**"\n---\n# X\n' and _loader_fm(t)["paths"] == ["src/**"]

def test_unclosed_draft_frontmatter_refused(cache, home):
    _approve(cache, _cand())
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": "---\nname: x\n# X\n"})

@pytest.mark.parametrize("paths", [["**"], ["src/**", "**"], ["**/*"], ["./**"], ["*"], ["{src,**}"], ["src/**,**"]])
def test_universal_approved_paths_refused(cache, home, paths):
    c = _cand(); c["proposed_change"]["paths"] = paths
    _approve(cache, c)
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    assert not (home / "rules" / "x.md").exists()

@pytest.mark.parametrize("paths", [["src/---/**"], ["src/**\n"], ["a\nb/**"], ["src/\x85**"], [" src/**"]])
def test_unserializable_approved_paths_refused(cache, home, paths):
    c = _cand(); c["proposed_change"]["paths"] = paths
    _approve(cache, c)
    with pytest.raises(A.ApplyError):
        A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})

def test_generated_file_modify_keeps_markers(cache, home):
    (home / "rules" / "x.md").write_text("---\nname: x\npaths: a\ngenerated_by: harness-creator\n---\n옛\n")
    c = _cand(); c["proposed_change"]["action"] = "modify"
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    assert (home / "rules" / "x.md").read_text() == _rule_out()

def test_verify_rejects_non_canonical_final_file(cache, home, monkeypatch):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    real_write = A._write_atomic
    def sneaky(path, text, mode):
        real_write(path, text.replace('  - "src/**"\n', '  - "src/**"\n  - "**"\n'), mode)
    monkeypatch.setattr(A, "_write_atomic", sneaky)
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert not (home / "rules" / "x.md").exists()

# --- 승인 뒤 undo·쓰기 실패면 승인 억제를 푼다(reopen 기록) ---
from hc.candidates import pending, is_approved
from hc.detect import load_decisions

def _last_decision(cache):
    return json.loads((cache / "decisions.jsonl").read_text().splitlines()[-1])

def test_undo_reopens_signals_and_candidate(cache, home):
    c = _cand()
    _approve(cache, c)
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    assert "s" in load_decisions() and pending() == []
    A.undo(CID)
    rec = _last_decision(cache)
    assert rec["decision"] == "reopen" and rec["candidate_id"] == CID and rec["signal_ids"] == ["s"]
    assert "s" not in load_decisions()                  # 신호 억제가 풀린다
    assert not is_approved(c) and [x["id"] for x in pending()] == [CID]

def test_write_failure_reopens(cache, home):
    (home / "settings.json").write_text("{}")
    c = _hook_cand()
    _approve(cache, c)
    A.plan(_hook_draft(HOOK.replace("exit 0", "exit 1")))
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert _last_decision(cache)["decision"] == "reopen"
    assert not is_approved(c) and "s" not in load_decisions()

def test_write_refused_when_already_applied_does_not_reopen(cache, home):
    _approve(cache, _cand())
    A.plan({"candidate_id": CID, "form": "rule", "path": "rules/x.md", "content": RULE})
    A.write(CID)
    (cache / "plans").mkdir(exist_ok=True)
    (cache / "plans" / f"{CID}.json").write_text("{}")
    with pytest.raises(A.ApplyError):
        A.write(CID)
    assert _last_decision(cache)["decision"] == "approve"   # 적용 중인 후보의 억제는 그대로

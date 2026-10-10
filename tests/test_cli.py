import json, subprocess, sys, pathlib
CLI = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "cli.py"

def run(cache, *args):
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                          env={"HC_DATA_DIR": str(cache), "PATH": "/usr/bin:/bin", "HOME": str(cache.parent)})

def test_detect_then_render_zero(cache):
    cache.mkdir(); (cache / "events").mkdir()
    (cache / "config.json").write_text(json.dumps({"lang": "ko"}))
    (cache / "collect-stats.json").write_text(json.dumps({"files": 0, "events": {"claude": 0, "codex": 0}, "records": 0, "unknown": 0, "deferred": 0}))
    assert run(cache, "detect").returncode == 0
    r = run(cache, "render", "--candidates", "/nonexistent")
    assert r.returncode == 0
    assert "실패" in pathlib.Path(r.stdout.strip()).read_text().splitlines()[0]

def test_validate_rc(cache, tmp_path):
    cache.mkdir(); (cache / "signals").mkdir()
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": []}))
    bad = tmp_path / "c.json"; bad.write_text("{not json")
    assert run(cache, "validate", str(bad)).returncode == 2
    ok = tmp_path / "ok.json"; ok.write_text(json.dumps({"candidates": []}))
    assert run(cache, "validate", str(ok)).returncode == 0

def _cand(quote):
    return {"id": "hc-2026-10-09-01", "signal_ids": ["failure_loop:a"], "form": "hook", "title": "t",
            "evidence": {"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": [quote]},
            "proposed_change": {"action": "create", "path": "hooks/harness-creator/hc-2026-10-09-01.sh", "summary": "s"}, "expected_effect": "e"}

def test_validate_checks_quotes_against_signal_samples(cache, tmp_path):
    cache.mkdir(); (cache / "signals").mkdir()
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": [
        {"id": "failure_loop:a", "samples": ["실제 인용"], "sessions": ["s1", "s2"], "count": 6}]}))
    good = tmp_path / "g.json"; good.write_text(json.dumps({"candidates": [_cand("실제 인용")]}))
    assert run(cache, "validate", str(good)).returncode == 0
    bad = tmp_path / "b.json"; bad.write_text(json.dumps({"candidates": [_cand("지어낸 인용")]}))
    r = run(cache, "validate", str(bad))
    assert r.returncode == 2 and "quotes" in r.stderr

def test_render_uses_detect_scope(cache, tmp_path):
    cache.mkdir(); (cache / "signals").mkdir()
    (cache / "config.json").write_text(json.dumps({"lang": "ko"}))
    (cache / "collect-stats.json").write_text(json.dumps({"files": 1, "events": {"claude": 2, "codex": 0}}))
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "scope": {
        "days": 7, "events": {"claude": 50, "codex": 9}, "sessions": 6, "skipped": 0, "earliest": "", "latest": ""},
        "signals": []}))
    empty = tmp_path / "e.json"; empty.write_text(json.dumps({"candidates": []}))
    r = run(cache, "render", "--candidates", str(empty))
    md = pathlib.Path(r.stdout.strip()).read_text()
    assert "본 범위: 최근 7일, 세션 6개, 이벤트 claude 50 / codex 9, 신호 0개 (건너뛴 줄 0)" in md
    assert "이번 증분 수집: 파일 1개, 이벤트 claude 2 / codex 0" in md

def test_decide_unknown_id_rc2(cache):
    cache.mkdir()
    r = run(cache, "decide", "hc-2026-10-09-99", "reject", "--reason", "x")
    assert r.returncode == 2
    assert "hc-2026-10-09-99" in r.stderr
    assert "Traceback" not in r.stderr

# --- 판정 입력(메모리·설명)도 수집과 같은 가림기를 거친다 ---
import os

_FAKE_SECRET = "ghp_" + "Ab1" * 8   # 테스트용 가짜 토큰(접두 패턴만 맞춘다)

def test_build_requires_redactor(tmp_path):
    # 가림기 없이 부르는 길을 남기지 않는다(호출부가 잊으면 원문이 그대로 나간다)
    from hc.inventory import build
    import pytest
    with pytest.raises(TypeError):
        build(tmp_path, None)

# --- 러너가 쓰는 판정 하위 명령(stats-check). 0=완전, 3=불완전, 4=손상 ---
import pytest

@pytest.mark.parametrize("stats,rc,out", [
    ({"errors": 0, "deferred": 0, "malformed": 0}, 0, ""),
    ({"errors": 0, "deferred": 7, "malformed": 0}, 3, "deferred files 7"),
    ({"errors": 2, "deferred": 7, "malformed": 0}, 3, "parse errors 2, deferred files 7"),
    ({"errors": 0, "malformed": 0}, 4, "corrupt"),
    ({"errors": 0, "deferred": "7", "malformed": 0}, 4, "corrupt"),
    ([1, 2], 4, "corrupt"),
])
def test_stats_check_rc(cache, stats, rc, out):
    cache.mkdir()
    (cache / "collect-stats.json").write_text(json.dumps(stats))
    r = run(cache, "stats-check")
    assert r.returncode == rc, r.stderr
    assert r.stdout.strip() == out

@pytest.mark.parametrize("content", [None, "{broken"])
def test_stats_check_missing_or_unreadable_is_4(cache, content):
    cache.mkdir()
    if content is not None:
        (cache / "collect-stats.json").write_text(content)
    r = run(cache, "stats-check")
    assert r.returncode == 4 and "Traceback" not in r.stderr

# --- 깨진 레코드(malformed)도 stats-check에서 불완전이다 ---
@pytest.mark.parametrize("stats,rc,out", [
    ({"errors": 0, "deferred": 0, "malformed": 1}, 3, "malformed lines 1"),
    ({"errors": 1, "deferred": 0, "malformed": 4}, 3, "parse errors 1, malformed lines 4"),
    ({"errors": 0, "deferred": 0}, 4, "corrupt"),
])
def test_stats_check_malformed(cache, stats, rc, out):
    cache.mkdir()
    (cache / "collect-stats.json").write_text(json.dumps(stats))
    r = run(cache, "stats-check")
    assert (r.returncode, r.stdout.strip()) == (rc, out)

# --- 공개판: 판정 입력·동의·설정 슬롯·forget·gate ---
def _seed_signals(cache):
    cache.mkdir(exist_ok=True); (cache / "signals").mkdir(exist_ok=True)
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": []}))

def test_propose_input_has_inventory_and_redacts(cache):
    _seed_signals(cache)
    home = cache.parent
    (home / ".claude/skills/s").mkdir(parents=True)
    (home / ".claude/skills/s/SKILL.md").write_text("---\nname: s\ndescription: 키 " + _FAKE_SECRET + "\n---\n")
    lex = home / "lex.json"; lex.write_text(json.dumps(["Acme"]))
    mem = home / ".claude/projects/p/memory/MEMORY.md"; mem.parent.mkdir(parents=True)
    mem.write_text("- [고객](a.md) Acme 미팅\n")
    (cache / "config.json").write_text(json.dumps({"redact": {"lexicon_files": [str(lex)]}}))
    r = run(cache, "propose-input")
    assert r.returncode == 0, r.stderr
    raw = pathlib.Path(r.stdout.strip()).read_text(encoding="utf-8")
    assert _FAKE_SECRET not in raw and "Acme" not in raw and str(home) not in raw
    assert json.loads(raw)["inventory"]["skills"][0]["path"] == "skills/s/SKILL.md"

def test_propose_input_slot_failure_writes_nothing(cache):
    _seed_signals(cache)
    (cache / "config.json").write_text(json.dumps({"redact": {"pattern_files": [str(cache / "missing.json")]}}))
    r = run(cache, "propose-input")
    assert r.returncode == 3 and "Traceback" not in r.stderr and r.stdout.strip() == ""
    assert not list((cache / "reports").glob("*.input.json")) if (cache / "reports").exists() else True

def test_propose_input_over_limit_rc3_writes_nothing(cache):
    # 신호 하나가 동의한 최대 크기보다 크면 fit_input이 ValueError → rc 3, 파일 없음
    cache.mkdir(); (cache / "signals").mkdir()
    sig = {"id": "failure_loop:a", "samples": ["x" * 2000], "sessions": ["s1"], "count": 1}
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": [sig]}))
    (cache / "config.json").write_text(json.dumps({"judge": {"max_input_bytes": 500}}))
    r = run(cache, "propose-input")
    assert r.returncode == 3 and "Traceback" not in r.stderr and r.stdout.strip() == ""
    assert "최대 크기" in r.stderr

def test_propose_input_samples_identical_to_signal_file_so_quotes_validate(cache, tmp_path):
    # 판정 입력의 samples는 신호 파일 그대로여야 한다(propose-input은 다시 가리거나 바꾸지 않는다).
    # 그래야 모델이 입력에서 그대로 옮긴 인용이 validate의 quotes 정확 일치를 통과한다.
    # 현재 설정의 가림 단어로 다시 가리는 일은 신호 파일을 쓰는 detect가 맡는다(아래 테스트).
    cache.mkdir(); (cache / "signals").mkdir()
    samples = ["토큰 [SECRET] 다시 실패", "~/work/app 에서 [NAME] 빌드 실패", "  앞뒤 공백 유지  "]
    sig = {"id": "failure_loop:a", "samples": samples, "sessions": ["s1", "s2"], "groups": ["s1", "s2"],
           "surfaces": ["claude"], "days": ["2026-10-08"], "count": 6}
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": [sig]}))
    r = run(cache, "propose-input")
    assert r.returncode == 0, r.stderr
    sent = json.loads(pathlib.Path(r.stdout.strip()).read_text(encoding="utf-8"))["signals"][0]["samples"]
    stored = json.loads((cache / "signals" / "2026-10-09.json").read_text())["signals"][0]["samples"]
    assert sent == stored == samples
    judged = tmp_path / "j.json"; judged.write_text(json.dumps({"candidates": [_cand(sent[0])]}))
    v = run(cache, "validate", str(judged))
    assert v.returncode == 0, v.stderr

# --- Task 9 판정: 수집 뒤에 넣은 가림 단어도 detect가 신호 samples·context에 적용한다 ---
_NOW_ISO = "2026-10-09T12:00:00Z"
_INSTR = "Acme PR 만들고 CI 통과하면 머지해줘"

def _ev(sid, ts, kind, text, tool=""):
    return json.dumps({"surface": "claude", "session_id": sid, "ts": ts, "cwd": "/w", "kind": kind,
                       "text": text, "tool": tool, "error_sig": ""}, ensure_ascii=False)

def _seed_raw_events(cache):
    """가림 단어 목록이 없던 때 수집된 이벤트(원문 Acme가 남아 있다)."""
    d = cache / "events"; d.mkdir(parents=True)
    lines = [_ev(f"kp{i}", f"2026-10-08T0{i + 2}:00:00Z", "user_msg", _INSTR) for i in range(3)]
    lines += [_ev("ca", "2026-10-07T03:00:00Z", "tool_call", "deploy Acme prod", "Bash"),
              _ev("ca", "2026-10-07T03:01:00Z", "user_msg", "그렇게 하지 마"),
              _ev("cb", "2026-10-08T09:00:00Z", "tool_call", "deploy Acme prod", "Bash"),
              _ev("cb", "2026-10-08T09:01:00Z", "user_msg", "그렇게 하지 마")]
    (d / "2026-10-08.jsonl").write_text("".join(l + "\n" for l in lines), encoding="utf-8")

def _run_at(cache, *args):
    env = {"HC_DATA_DIR": str(cache), "PATH": "/usr/bin:/bin", "HOME": str(cache.parent), "HC_NOW": _NOW_ISO}
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env=env)

def test_detect_redacts_samples_with_lexicon_added_after_collect(cache, tmp_path):
    _seed_raw_events(cache)
    lex = tmp_path / "lex.json"; lex.write_text(json.dumps(["Acme"]))
    (cache / "config.json").write_text(json.dumps({"redact": {"lexicon_files": [str(lex)]}}))
    d = _run_at(cache, "detect")
    assert d.returncode == 0, d.stderr
    raw_sig = pathlib.Path(d.stdout.strip()).read_text(encoding="utf-8")
    assert "Acme" not in raw_sig
    sigs = json.loads(raw_sig)["signals"]
    rep = next(s for s in sigs if s["type"] == "repeated_instruction")
    cor = next(s for s in sigs if s["type"] == "correction")
    assert rep["samples"] == ["[NAME] PR 만들고 CI 통과하면 머지해줘"]
    assert cor["context"] and all("[NAME]" in c for c in cor["context"])
    p = _run_at(cache, "propose-input")
    assert p.returncode == 0, p.stderr
    assert "Acme" not in pathlib.Path(p.stdout.strip()).read_text(encoding="utf-8")
    c = {**_cand(rep["samples"][0]), "signal_ids": [rep["id"]]}
    judged = tmp_path / "j.json"; judged.write_text(json.dumps({"candidates": [c]}, ensure_ascii=False))
    v = _run_at(cache, "validate", str(judged))
    assert v.returncode == 0, v.stderr

def test_detect_redactor_failure_writes_no_signal_file(cache, tmp_path):
    _seed_raw_events(cache)
    (cache / "config.json").write_text(json.dumps({"redact": {"pattern_files": [str(tmp_path / "missing.json")]}}))
    d = _run_at(cache, "detect")
    assert d.returncode == 3 and "Traceback" not in d.stderr and d.stdout.strip() == ""
    assert not list((cache / "signals").glob("*.json")) if (cache / "signals").exists() else True

def test_collect_requires_consent(cache):
    cache.mkdir()
    r = run(cache, "collect")
    assert r.returncode == 5 and "Traceback" not in r.stderr

def test_bad_include_is_rc6_without_traceback(cache):
    cache.mkdir()
    (cache / "config.json").write_text(json.dumps({"include": [str(cache / "nope.json")]}))
    for cmd in (("status",), ("detect",), ("render", "--error", "x")):
        r = run(cache, *cmd)
        assert r.returncode == 6 and "Traceback" not in r.stderr, cmd

def test_consent_flow(cache):
    r = run(cache, "consent-text")
    assert r.returncode == 0 and "claude" in r.stdout
    assert run(cache, "consent-check").returncode == 5
    assert run(cache, "consent", "--accept", "--codex", "no").returncode == 0
    assert run(cache, "consent-check").returncode == 0
    cfg = json.loads((cache / "config.json").read_text())
    assert cfg["sources"]["codex"] is False and cfg["consent"]["accepted_at"]

def test_status_needs_analysis_without_report(cache):
    r = run(cache, "status")
    st = json.loads(r.stdout)
    assert st["needs_analysis"] is True and st["latest_report"] is None and st["consent"] is False

def test_wrap_argv_expands_tilde(cache):
    cache.mkdir()
    (cache / "config.json").write_text(json.dumps({"run": {"wrap": ["python3", "~/q.py", "--"]}}))
    r = run(cache, "wrap-argv")
    assert r.stdout.split("\0")[:3] == ["python3", str(cache.parent / "q.py"), "--"]

def test_forget_keeps_config_decisions_applied_backups(cache):
    # 지울 목록 방식: 캐시·커서·이벤트·신호·리포트·수집 통계·로그·적용 계획(plans)·초안(drafts)만 지운다.
    # 킬스위치 disabled 와 run.lock(실행 중 아님)은 남긴다(분석을 끈 사용자가 forget해도 수집이 다시 켜지지 않게)
    cache.mkdir()
    for n in ("config.json", "decisions.jsonl", "applied.jsonl", "cursor.json", "collect-stats.json", "disabled"):
        (cache / n).write_text("{}")
    for d in ("backups", "events", "signals", "reports", "logs", "run.lock", "plans", "drafts"):
        (cache / d).mkdir(); (cache / d / "f").write_text("x")
    assert run(cache, "forget").returncode == 0
    assert sorted(p.name for p in cache.iterdir()) == ["applied.jsonl", "backups", "config.json", "decisions.jsonl",
                                                       "disabled", "run.lock"]

import pytest as _pytest

@_pytest.mark.parametrize("run_cfg", [{"wrap": [1, "--"]}, {"wrap": "ab"}, {"wrap": ["a\u0000b", "--"]},
                                      {"wrap": None}, "x"])
def test_wrap_argv_rejects_bad_shape_rc6(cache, run_cfg):
    cache.mkdir()
    (cache / "config.json").write_text(json.dumps({"run": run_cfg}))
    r = run(cache, "wrap-argv")
    assert r.returncode == 6 and "Traceback" not in r.stderr and r.stdout == "", (run_cfg, r.stderr)
    assert "run.wrap" in r.stderr

@_pytest.mark.parametrize("judge", [{"timeout_s": "abc"}, {"timeout_s": True}, {"timeout_s": 1.5}, {"timeout_s": 0},
                                    {"model": 3}, {"claude_bin": ["x"]}, {"model": "a\nb"}])
def test_judge_argv_rejects_bad_values_rc6(cache, judge):
    cache.mkdir()
    (cache / "config.json").write_text(json.dumps({"judge": judge}))
    r = run(cache, "judge-argv")
    assert r.returncode == 6 and "Traceback" not in r.stderr and r.stdout == "", (judge, r.stderr)
    assert "judge" in r.stderr

def test_gate_writes_candidates_and_gates(cache, tmp_path):
    cache.mkdir(); (cache / "signals").mkdir()
    sig = {"id": "failure_loop:a", "samples": ["q"], "sessions": ["s1", "s2"], "groups": ["s1", "s2"],
           "surfaces": ["codex"], "days": ["2026-10-08"], "count": 6}
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": [sig]}))
    judged = tmp_path / "j.json"; judged.write_text(json.dumps({"candidates": [_cand("q")]}))
    r = run(cache, "gate", str(judged))
    assert r.returncode == 0, r.stderr
    rep = cache / "reports"
    assert json.loads(next(rep.glob("*.candidates.json")).read_text())["candidates"] == []
    assert json.loads(next(rep.glob("*.gates.json")).read_text())["codex_notes"][0]["id"] == "hc-2026-10-09-01"

def test_consent_time_is_range_from_default_coefficient(cache):
    # 기본 계수 1000 MB/분, 2500 MB 상당(희소 파일) → 3분, 표시는 범위 3~6분(과소 안내 방지)
    src = cache.parent / ".claude" / "projects" / "p"; src.mkdir(parents=True)
    with open(src / "s.jsonl", "wb") as fh:
        fh.truncate(2500 * 1048576)
    cache.mkdir(exist_ok=True)
    (cache / "config.json").write_text(json.dumps({"lang": "ko"}))
    r = run(cache, "consent-text")
    assert r.returncode == 0, r.stderr
    assert "수집 약 3~6분" in r.stdout
    for lang, want in (("en", "about 3-6 min"), ("zh", "收集约 3~6 分钟")):
        (cache / "config.json").write_text(json.dumps({"lang": lang}))
        assert want in run(cache, "consent-text").stdout, lang

# --- 실패 리포트는 최신 리포트여도 재분석을 막지 않는다 ---
@_pytest.mark.parametrize("lang", ["en", "ko", "zh"])
def test_status_failed_report_needs_analysis(cache, lang):
    from hc.lang import load_pack
    (cache / "reports").mkdir(parents=True)
    title = load_pack(lang)["report"]["title_fail"]
    (cache / "reports" / "2099-01-01.md").write_text(title + " (x)\n\nreason\n", encoding="utf-8")
    st = json.loads(run(cache, "status").stdout)
    assert st["latest_report"] and st["needs_analysis"] is True

def test_status_fresh_ok_report_does_not_need_analysis(cache):
    (cache / "reports").mkdir(parents=True)
    (cache / "reports" / "2099-01-01.md").write_text("# harness-creator report: no candidates\n", encoding="utf-8")
    st = json.loads(run(cache, "status").stdout)
    assert st["needs_analysis"] is False

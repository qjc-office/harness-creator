"""run-analysis.sh 를 스텁으로 돌린다. 실제 claude·수집은 부르지 않는다(수집 테스트 2개는 빈 임시 HOME을 읽는다).
스텁 계약: stdin=프롬프트(입력 JSON 포함), stdout=모델 응답."""
import json, os, pathlib, stat, subprocess, time

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
RUNNER = SCRIPTS / "run-analysis.sh"

def _runlog(tmp_path):
    p = tmp_path / "cache" / "logs" / "run.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""

def _run(tmp_path, stub_body, extra_env=None, pre=None):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    cache = tmp_path / "cache"
    # 정상 수집 통계를 기본으로 둔다(HC_SKIP_COLLECT라 collect가 쓰지 않는다). pre가 덮어쓰거나 지울 수 있다.
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "collect-stats.json").write_text(
        '{"files": 0, "events": {"claude": 0, "codex": 0}, "records": 0, "unknown": 0, "deferred": 0, "errors": 0, "malformed": 0}')
    (cache / "config.json").write_text('{"lang": "ko", "consent": {"accepted_at": "x"}}')
    if pre:
        pre(cache)
    stub = tmp_path / "stub.sh"
    stub.write_text("#!/bin/bash\n" + (stub_body or "exit 99") + "\n")
    stub.chmod(0o755)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HC_DATA_DIR": str(cache), "HC_CLAUDE_STUB": str(stub), "HC_SKIP_COLLECT": "1"}
    if stub_body is None:
        del env["HC_CLAUDE_STUB"]
    env.update(extra_env or {})
    r = subprocess.run(["/bin/bash", str(RUNNER)], capture_output=True, text=True, env=env, timeout=120)
    reports = cache / "reports"
    today = time.strftime("%Y-%m-%d")
    report = reports / f"{today}.md"
    return r, reports, today, report

def _first_line(report):
    return report.read_text(encoding="utf-8").splitlines()[0]

def test_non_json_answer_renamed_invalid_and_report_fails(tmp_path):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; printf 'not json'")
    assert r.returncode == 2, r.stderr
    assert "실패" in _first_line(report)
    inv = reports / f"{today}.judged.invalid.json"
    assert inv.read_text(encoding="utf-8") == "not json"
    assert not (reports / f"{today}.candidates.json").exists()
    assert not list(reports.glob("*.propose.*"))  # trap이 임시 파일을 지운다

def test_schema_invalid_answer_renamed(tmp_path):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; printf '{\"candidates\": [{\"id\": 1}]}'")
    assert r.returncode == 2
    assert "실패" in _first_line(report)
    assert (reports / f"{today}.judged.invalid.json").exists()
    assert not (reports / f"{today}.candidates.json").exists()

def test_stub_failure_reports_failure(tmp_path):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; exit 7")
    assert r.returncode == 2
    assert "실패" in _first_line(report)
    assert not (reports / f"{today}.candidates.json").exists()

def test_empty_candidates_report_says_none(tmp_path):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; printf '{\"candidates\": []}'")
    assert r.returncode == 0, r.stderr
    assert "후보 없음" in _first_line(report)
    out = reports / f"{today}.candidates.json"
    assert out.exists() and stat.S_IMODE(out.stat().st_mode) == 0o600
    assert not (reports / f"{today}.judged.invalid.json").exists()
    assert not list(reports.glob("*.propose.*"))  # 프롬프트·원문 임시 파일 정리

def test_json_wrapped_in_chatter_is_extracted(tmp_path):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; printf '결과입니다\\n{\"candidates\": []}\\n끝'")
    assert r.returncode == 0, r.stderr
    assert "후보 없음" in _first_line(report)

def test_prompt_on_stdin_contains_input_json_and_date(tmp_path):
    today = time.strftime("%Y-%m-%d")
    body = ('p="$(cat)"; '
            f'printf "%s" "$p" | grep -q "hc-{today}-" || exit 9; '
            'printf "%s" "$p" | grep -q "\\"inventory\\"" || exit 9; '
            'printf "%s" "$p" | grep -q "{INPUT\\|{OUTPUT\\|{TODAY}" && exit 9; '
            "printf '{\"candidates\": []}'")
    r, reports, _, report = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr
    assert "후보 없음" in _first_line(report)

def test_render_failure_is_not_success(tmp_path):
    # 판정·검증은 통과하지만 리포트 경로가 디렉터리라 마지막 render가 쓰지 못한다.
    body = ('cat > /dev/null; mkdir -p "$HC_DATA_DIR/reports/$(date +%F).md"; '
            "printf '{\"candidates\": []}'")
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode != 0
    assert "OK report=" not in _runlog(tmp_path)
    assert "FAIL render" in _runlog(tmp_path)

def test_success_logs_ok(tmp_path):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; printf '{\"candidates\": []}'")
    assert r.returncode == 0
    assert "OK report=" in _runlog(tmp_path)

def test_detect_failure_renders_failure_report(tmp_path):
    def signals_as_file(cache):
        cache.mkdir(exist_ok=True)
        (cache / "signals").write_text("not a dir")
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; exit 0", pre=signals_as_file)
    assert r.returncode == 1
    assert "실패" in _first_line(report)

def test_missing_claude_renders_failure_report(tmp_path):
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "judge": {"claude_bin": str(tmp_path / "no-such-claude")}}))
    r, reports, today, report = _run(tmp_path, None, pre=pre)
    assert r.returncode == 1, r.stderr
    assert "실패" in _first_line(report) and "claude CLI를 찾지 못함" in report.read_text(encoding="utf-8")
    assert not list(reports.glob("*.propose.*"))

def test_propose_gives_model_no_tools():
    src = RUNNER.read_text(encoding="utf-8")
    assert "dangerously-skip-permissions" not in src
    assert "--restricted" in src and "--safe-mode" in src
    assert '--tools ""' in src
    assert "--permission-mode dontAsk" in src
    assert "--strict-mcp-config" in src
    line = next(l for l in src.splitlines() if '--disallowedTools "' in l)
    tools = set(line.split('"')[1].split(","))
    assert {"Read", "Write", "Edit", "Bash", "Agent", "Skill", "Monitor", "SendMessage", "Artifact"} <= tools
    # 가변 인자 플래그(--tools·--disallowedTools)는 --model·--output-format 뒤에 온다
    call = src[src.index("--output-format json --model"):]
    assert call.index('--tools "" ') < call.index('--disallowedTools "')
    assert "--restricted" in call and "--permission-mode dontAsk" in call

def test_propose_input_redactor_failure_renders_failure(tmp_path):
    # 판정 입력 가림기를 못 만들면 모델을 부르지 않고 실패 리포트를 남긴다
    marker = tmp_path / "stub-called"
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "redact": {"pattern_files": [str(tmp_path / "missing.json")]}}))
    r, reports, today, report = _run(tmp_path, f"touch {marker}; cat > /dev/null; printf '{{\"candidates\": []}}'",
                                     pre=pre)
    assert r.returncode == 1, r.stderr
    assert "실패" in _first_line(report)
    assert not marker.exists()
    assert not list(reports.glob("*.input.json"))
    assert "OK report=" not in _runlog(tmp_path)

def test_event_cap_exceeded_renders_failure(tmp_path):
    # 이벤트 상한을 넘으면 탐지 실패로 보고하고 성공 하트비트를 남기지 않는다
    def two_events(cache):
        d = cache / "events"; d.mkdir(parents=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec = '{{"surface": "claude", "session_id": "{s}", "ts": "{t}", "cwd": "/w", "kind": "user_msg", "text": "{s}", "tool": "", "error_sig": ""}}\n'
        (d / "today.jsonl").write_text(rec.format(s="a", t=now) + rec.format(s="b", t=now))
    marker = tmp_path / "stub-called"
    r, reports, today, report = _run(tmp_path, f"touch {marker}; cat > /dev/null; printf '{{\"candidates\": []}}'",
                                     extra_env={"HC_MAX_EVENTS": "1"}, pre=two_events)
    assert r.returncode == 1, r.stderr
    assert "실패" in _first_line(report)
    assert not marker.exists()
    assert "OK report=" not in _runlog(tmp_path)

# --- 파싱 실패 파일이 있으면 리포트는 남기되 성공으로 끝내지 않는다 ---
def _stats_with_errors(n):
    def pre(cache):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "collect-stats.json").write_text(
            '{"files": 3, "events": {"claude": 1, "codex": 0}, "records": 5, "unknown": 0, "deferred": 0, "errors": %d, "malformed": 0}' % n)
    return pre

def test_parse_errors_incomplete_rc3_no_ok(tmp_path):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; printf '{\"candidates\": []}'", pre=_stats_with_errors(2))
    assert r.returncode == 3, r.stderr
    assert "불완전: 파싱 실패 파일 2개" in _first_line(report)
    assert "OK report=" not in _runlog(tmp_path)
    assert "incomplete: parse errors 2" in _runlog(tmp_path)
    assert (reports / f"{today}.candidates.json").exists()   # 이번 주 후보는 그대로 남는다

def test_zero_parse_errors_unchanged(tmp_path):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; printf '{\"candidates\": []}'", pre=_stats_with_errors(0))
    assert r.returncode == 0, r.stderr
    assert _first_line(report) == "# harness-creator 리포트: 후보 없음"
    assert "OK report=" in _runlog(tmp_path)

# --- 수집 통계가 없거나 손상이면 성공으로 끝내지 않는다(rc 4, 하트비트 없음) ---
import pytest

def _stats_raw(text):
    def pre(cache):
        p = cache / "collect-stats.json"
        if text is None:
            p.unlink()
        else:
            p.write_text(text)
    return pre

@pytest.mark.parametrize("text", [
    None,                                               # 파일 없음
    '{"files": 1, "events": {}}',                        # errors 키 없음
    '{"files": 1, "events": {}, "errors": "2"}',
    '{"files": 1, "events": {}, "errors": -1}',
    '{"files": 1, "events": {}, "errors": true}',
])
def test_corrupt_collect_stats_fails_rc4(tmp_path, text):
    r, reports, today, report = _run(tmp_path, "cat > /dev/null; printf '{\"candidates\": []}'", pre=_stats_raw(text))
    assert r.returncode == 4, r.stderr
    assert "OK report=" not in _runlog(tmp_path)
    assert "collect-stats 손상" in _runlog(tmp_path)
    assert "수집 통계 손상" in report.read_text(encoding="utf-8")

# --- 동시 실행 잠금($CACHE/run.lock, mkdir) ---
OK_STUB = "cat > /dev/null; printf '{\"candidates\": []}'"

def _dead_pid():
    p = subprocess.Popen(["/usr/bin/true"]); p.wait()
    return p.pid

def _lock(pid):
    def pre(cache):
        d = cache / "run.lock"; d.mkdir(parents=True)
        if pid is not None:
            (d / "pid").write_text(f"{pid}\n")
    return pre

def test_live_lock_exits_75_without_touching_anything(tmp_path):
    today = time.strftime("%Y-%m-%d")
    live = os.getpid()
    def pre(cache):
        _lock(live)(cache)
        (cache / "reports").mkdir(parents=True, exist_ok=True)
        (cache / "reports" / f"{today}.propose.prompt.txt").write_text("다른 실행의 임시 파일")
    marker = tmp_path / "stub-called"
    r, reports, _, report = _run(tmp_path, f"touch {marker}; {OK_STUB}", pre=pre)
    assert r.returncode == 75, r.stderr
    assert not report.exists() and not marker.exists()
    assert (reports / f"{today}.propose.prompt.txt").exists()       # 남의 임시 파일을 지우지 않는다
    assert (tmp_path / "cache/run.lock/pid").read_text().strip() == str(live)
    assert "OK report=" not in _runlog(tmp_path)
    assert "이미 실행 중" in _runlog(tmp_path)

@pytest.mark.parametrize("pid", ["dead", "garbage"])
def test_stale_lock_reclaimed_and_released(tmp_path, pid):
    val = _dead_pid() if pid == "dead" else pid
    r, reports, today, report = _run(tmp_path, OK_STUB, pre=_lock(val))
    assert r.returncode == 0, r.stderr
    assert "후보 없음" in _first_line(report)
    assert not (tmp_path / "cache/run.lock").exists()

@pytest.mark.parametrize("body,rc", [(OK_STUB, 0), ("cat > /dev/null; exit 7", 2)])
def test_lock_released_on_success_and_failure(tmp_path, body, rc):
    r, *_ = _run(tmp_path, body)
    assert r.returncode == rc, r.stderr
    assert not (tmp_path / "cache/run.lock").exists()

def test_lock_taken_over_is_not_removed(tmp_path):
    # 실행 중 잠금 주인이 바뀌면(다른 실행이 회수) 끝날 때 남의 잠금을 지우지 않는다
    other = os.getpid()
    body = f'echo {other} > "$HC_DATA_DIR/run.lock/pid"; {OK_STUB}'
    r, *_ = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "cache/run.lock/pid").read_text().strip() == str(other)

def test_disabled_run_does_not_delete_temp_files(tmp_path):
    # 킬스위치로 끝나는 실행도 진행 중인 실행의 임시 파일을 지우지 않는다
    today = time.strftime("%Y-%m-%d")
    def pre(cache):
        (cache / "reports").mkdir(parents=True, exist_ok=True)
        (cache / "reports" / f"{today}.propose.raw.txt").write_text("x")
    r, reports, _, _ = _run(tmp_path, OK_STUB, extra_env={"HARNESS_CREATOR_DISABLED": "1"}, pre=pre)
    assert r.returncode == 0
    assert (reports / f"{today}.propose.raw.txt").exists()

# --- 컨트롤러 판정(r2 후속): 잠금 회수 원자화 ---
def _aged(sec):
    def pre(cache):
        d = cache / "run.lock"; d.mkdir(parents=True)
        t = time.time() - sec
        os.utime(d, (t, t))
    return pre

def test_lock_without_pid_fresh_is_being_created_75(tmp_path):
    marker = tmp_path / "stub-called"
    r, reports, today, report = _run(tmp_path, f"touch {marker}; {OK_STUB}", pre=_aged(0))
    assert r.returncode == 75, r.stderr
    assert not report.exists() and not marker.exists()
    assert (tmp_path / "cache/run.lock").is_dir()

def test_pid_written_atomically_via_tmp_rename():
    src = RUNNER.read_text(encoding="utf-8")
    assert '"$LOCK/pid.tmp.$$"' in src and 'mv -f "$LOCK/pid.tmp.$$" "$LOCK/pid"' in src
    assert 'STALE="$LOCK.stale.$$"' in src and 'if ! mv "$LOCK" "$STALE"' in src

def _concurrent(tmp_path, pre=None):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    cache = tmp_path / "cache"; cache.mkdir()
    (cache / "collect-stats.json").write_text('{"files": 0, "events": {}, "errors": 0, "deferred": 0, "malformed": 0}')
    (cache / "config.json").write_text('{"lang": "ko", "consent": {"accepted_at": "x"}}')
    if pre:
        pre(cache)
    stub = tmp_path / "stub.sh"
    # 잠금을 쥔 채 잠시 머문다(먼저 끝나 버려 두 번째가 정상 진행하는 경우를 막는다)
    stub.write_text("#!/bin/bash\nsleep 2; " + OK_STUB + "\n"); stub.chmod(0o755)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HC_DATA_DIR": str(cache),
           "HC_CLAUDE_STUB": str(stub), "HC_SKIP_COLLECT": "1"}
    procs = [subprocess.Popen(["/bin/bash", str(RUNNER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for _ in range(2)]
    rcs = sorted(p.wait(timeout=120) for p in procs)
    reports = list((cache / "reports").glob("*.md"))
    return rcs, reports, cache

def test_two_concurrent_runners_one_wins(tmp_path):
    rcs, reports, cache = _concurrent(tmp_path)
    assert rcs == [0, 75]
    assert len(reports) == 1 and "후보 없음" in _first_line(reports[0])
    assert not (cache / "run.lock").exists()

@pytest.mark.parametrize("i", range(3))
def test_two_concurrent_runners_racing_on_stale_lock(tmp_path, i):
    # 남은 잠금(죽은 pid)을 두 실행이 동시에 회수하려 해도 하나만 진행한다
    rcs, reports, cache = _concurrent(tmp_path, pre=_lock(_dead_pid()))
    assert rcs == [0, 75]
    assert len(reports) == 1
    assert not (cache / "run.lock").exists() and not list(cache.glob("run.lock.stale.*"))

# --- pid 없는 잠금은 절대 회수하지 않는다 ---

def test_pidless_old_lock_never_reclaimed_alerts(tmp_path):
    r, reports, today, report = _run(tmp_path, OK_STUB, pre=_aged(120))
    assert r.returncode == 75, r.stderr
    lock = tmp_path / "cache/run.lock"
    assert lock.is_dir() and list(lock.iterdir()) == []            # 손대지 않았다
    assert time.time() - lock.stat().st_mtime >= 100
    assert not report.exists()
    assert f"잠금에 pid 없음: {lock} 수동 확인 후 삭제" in _runlog(tmp_path)

@pytest.mark.parametrize("age", [600, 86400])
def test_pidless_lock_any_age_not_reclaimed(tmp_path, age):
    r, *_ = _run(tmp_path, OK_STUB, pre=_aged(age))
    assert r.returncode == 75
    assert (tmp_path / "cache/run.lock").is_dir()

def test_pidless_fresh_lock_no_failure_alert(tmp_path):
    # 방금 만들어진(60초 미만) pid 없는 잠금은 pid를 쓰기 직전의 다른 실행이다. 경보 없이 물러난다
    r, *_ = _run(tmp_path, OK_STUB, pre=_aged(0))
    assert r.returncode == 75
    assert "FAIL" not in _runlog(tmp_path)

def test_lock_pid_replaced_with_garbage_not_removed(tmp_path):
    body = f'echo not-a-pid > "$HC_DATA_DIR/run.lock/pid"; {OK_STUB}'
    r, *_ = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "cache/run.lock/pid").read_text().strip() == "not-a-pid"

def test_runner_rereads_pid_after_acquire():
    src = RUNNER.read_text(encoding="utf-8")
    i = src.index('mv -f "$LOCK/pid.tmp.$$" "$LOCK/pid"')
    after = src[i:i + 400]
    assert 'if [ "$(_hc_lock_owner)" != "$$" ]' in after and "exit 75" in after
    assert after.index("exit 75") < src[i:].index("trap _hc_cleanup EXIT")

# --- 시간 제한 실행기를 timeout → gtimeout → python3 순으로 고른다 ---
import shutil, sys as _sys

def _no_timeout_path(tmp_path):
    """timeout·gtimeout 없는 PATH. python3는 테스트 인터프리터를 가리킨다(/usr/bin/python3는 3.9라 쓰지 않는다)."""
    b = tmp_path / "pybin"; b.mkdir()
    (b / "python3").symlink_to(_sys.executable)
    path = f"{b}:/usr/bin:/bin"
    assert shutil.which("timeout", path=path) is None and shutil.which("gtimeout", path=path) is None
    return path

def test_python_fallback_runs_without_timeout_binaries(tmp_path):
    path = _no_timeout_path(tmp_path)
    r, reports, today, report = _run(tmp_path, "sleep 0.2; " + OK_STUB, extra_env={"PATH": path})
    assert r.returncode == 0, r.stderr
    assert "후보 없음" in _first_line(report)
    assert "시간 제한 실행기: python3" in _runlog(tmp_path)

def test_python_fallback_passes_stdin_through(tmp_path):
    path = _no_timeout_path(tmp_path)
    body = ('p="$(cat)"; printf "%s" "$p" | grep -q "\\"inventory\\"" || exit 9; ' + "printf '{\"candidates\": []}'")
    r, *_ = _run(tmp_path, body, extra_env={"PATH": path})
    assert r.returncode == 0, r.stderr

@pytest.mark.parametrize("no_bin", [False, True])
def test_propose_timeout_yields_failure_report(tmp_path, no_bin):
    env = {"HC_PROPOSE_TIMEOUT": "1"}
    if no_bin:
        env["PATH"] = _no_timeout_path(tmp_path)
    t0 = time.time()
    r, reports, today, report = _run(tmp_path, "sleep 10; " + OK_STUB, extra_env=env)
    assert time.time() - t0 < 9                       # 제한 시간에 끊겼다
    assert r.returncode == 2, r.stderr
    assert "실패" in _first_line(report)
    assert "rc=124" in _runlog(tmp_path)
    assert "OK report=" not in _runlog(tmp_path)

def test_real_path_uses_resolved_timeout_runner():
    src = RUNNER.read_text(encoding="utf-8")
    assert "exec timeout 1800" not in src                       # 맨 timeout 의존 제거
    assert '"${TO[@]}" "$CLAUDE" "${ISO[@]}"' in src
    assert '"${TO[@]}" "$STUB" "${ISO[@]}"' in src             # 테스트 스텁도 같은 격리 플래그로 부른다

# --- 실제 경로(시간 제한 실행기 → claude)를 가짜 claude로 태운다 ---
# 실제 claude는 부르지 않는다. 가짜 claude는 --help에 --restricted를 답하고,
# 그 밖의 호출에서는 받은 인자를 기록한 뒤 stdin을 읽고 JSON을 낸다. 경로는 config judge.claude_bin으로 넘긴다.
def _real_path(tmp_path, claude_body, extra=None, no_timeout=False):
    fake = tmp_path / "fake-claude"
    fake.write_text("#!/bin/bash\nif [ \"$1\" = --help ]; then echo '  --restricted  Restricted mode'; echo '  --safe-mode  Safe mode'; exit 0; fi\n"
                    "printf '%s\\n' \"$@\" > \"$(dirname \"$0\")/claude-argv.txt\"\n" + claude_body + "\n")
    fake.chmod(0o755)
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "judge": {"claude_bin": str(fake)}}))
    env = {}
    if no_timeout:
        env["PATH"] = _no_timeout_path(tmp_path)
    env.update(extra or {})
    return _run(tmp_path, None, extra_env=env, pre=pre)

@pytest.mark.parametrize("no_timeout", [False, True])
def test_real_path_runs_fake_claude(tmp_path, no_timeout):
    r, reports, today, report = _real_path(tmp_path, "cat > /dev/null; printf '{\"candidates\": []}'", no_timeout=no_timeout)
    assert r.returncode == 0, r.stderr + _runlog(tmp_path)
    assert "후보 없음" in _first_line(report)
    argv = (tmp_path / "claude-argv.txt").read_text(encoding="utf-8").split("\n")
    assert argv[0] == "-p" and "--restricted" in argv and "--strict-mcp-config" in argv and "--safe-mode" in argv
    assert argv[argv.index("--tools") + 1] == ""                 # 도구 0개
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv[argv.index("--output-format") + 1] == "json"
    assert ("python3" in _runlog(tmp_path)) == no_timeout

def test_real_path_timeout_fails(tmp_path):
    r, reports, today, report = _real_path(tmp_path, "sleep 10; cat > /dev/null; printf '{\"candidates\": []}'",
                                           extra={"HC_PROPOSE_TIMEOUT": "1"}, no_timeout=True)
    assert r.returncode == 2, r.stderr
    assert "실패" in _first_line(report)
    assert "rc=124" in _runlog(tmp_path)

# --- deferred > 0 이면 rc 3, 하트비트 없음 ---
def _counts(errors=0, deferred=0, malformed=0):
    def pre(cache):
        (cache / "collect-stats.json").write_text(json.dumps(
            {"files": 1, "events": {"claude": 0, "codex": 0}, "records": 0, "unknown": 0,
             "errors": errors, "deferred": deferred, "malformed": malformed}))
    return pre

@pytest.mark.parametrize("kw,first,fail", [
    ({"deferred": 4}, "(불완전: 미처리 파일 4개)", "incomplete: deferred files 4"),
    ({"errors": 1, "deferred": 2}, "(불완전: 파싱 실패 파일 1개, 미처리 파일 2개)", "incomplete: parse errors 1, deferred files 2"),
])
def test_deferred_incomplete_rc3_no_ok(tmp_path, kw, first, fail):
    r, reports, today, report = _run(tmp_path, OK_STUB, pre=_counts(**kw))
    assert r.returncode == 3, r.stderr
    assert first in _first_line(report)
    assert "OK report=" not in _runlog(tmp_path)
    assert fail in _runlog(tmp_path)

def test_missing_deferred_key_is_corrupt_rc4(tmp_path):
    def pre(cache):
        (cache / "collect-stats.json").write_text('{"files": 1, "events": {}, "errors": 0, "malformed": 0}')
    r, *_ = _run(tmp_path, OK_STUB, pre=pre)
    assert r.returncode == 4
    assert "OK report=" not in _runlog(tmp_path)

# --- malformed > 0 이면 rc 3, 하트비트 없음. 셋 다 0이면 rc 0 + 하트비트 ---
@pytest.mark.parametrize("kw,first,fail", [
    ({"malformed": 2}, "(불완전: 깨진 레코드 2줄)", "incomplete: malformed lines 2"),
    ({"errors": 1, "deferred": 1, "malformed": 1}, "(불완전: 파싱 실패 파일 1개, 미처리 파일 1개, 깨진 레코드 1줄)",
     "incomplete: parse errors 1, deferred files 1, malformed lines 1"),
])
def test_malformed_incomplete_rc3_no_ok(tmp_path, kw, first, fail):
    r, reports, today, report = _run(tmp_path, OK_STUB, pre=_counts(**kw))
    assert r.returncode == 3, r.stderr
    assert first in _first_line(report)
    assert "OK report=" not in _runlog(tmp_path)
    assert fail in _runlog(tmp_path)

def test_all_counts_zero_success_logs_ok(tmp_path):
    r, reports, today, report = _run(tmp_path, OK_STUB, pre=_counts())
    assert r.returncode == 0, r.stderr
    assert _first_line(report) == "# harness-creator 리포트: 후보 없음"
    assert "OK report=" in _runlog(tmp_path)

def test_missing_malformed_key_is_corrupt_rc4(tmp_path):
    def pre(cache):
        (cache / "collect-stats.json").write_text('{"files": 1, "events": {}, "errors": 0, "deferred": 0}')
    r, *_ = _run(tmp_path, OK_STUB, pre=pre)
    assert r.returncode == 4 and "OK report=" not in _runlog(tmp_path)

# --- 공개판: CLI 업그레이드 사전 검사·봉투 해석·동의·run.wrap 슬롯·거르기 파일 ---
def test_cli_without_restricted_is_cli_upgrade_failure(tmp_path):
    fake = tmp_path / "old-claude"
    fake.write_text("#!/bin/bash\nif [ \"$1\" = --help ]; then echo 'Usage: claude'; exit 0; fi\ntouch \"$0.called\"\n")
    fake.chmod(0o755)
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "judge": {"claude_bin": str(fake)}}))
    r, reports, today, report = _run(tmp_path, None, pre=pre)
    assert r.returncode == 1
    assert _first_line(report) == "# harness-creator 리포트: 실패 (CLI 업그레이드 필요)"
    assert not (tmp_path / "old-claude.called").exists()

def test_envelope_json_output_is_unwrapped(tmp_path):
    body = ("cat > /dev/null; printf '%s' '{\"type\":\"result\",\"is_error\":false,"
            "\"result\":\"{\\\"candidates\\\": []}\"}'")
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr
    assert "후보 없음" in _first_line(report)

def test_envelope_is_error_fails(tmp_path):
    body = "cat > /dev/null; printf '%s' '{\"type\":\"result\",\"is_error\":true,\"result\":\"boom\"}'"
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 2 and "실패" in _first_line(report)

def _array_stub(tmp_path, items):
    """CLI 전역 설정 verbose=true 면 --output-format json 이 메시지 배열을 낸다(2.1.296 실측). 그 형태를 흉내 낸다."""
    out = tmp_path / "answer.json"
    out.write_text(json.dumps(items), encoding="utf-8")
    return f"cat > /dev/null; cat '{out}'"

def test_verbose_array_envelope_is_unwrapped(tmp_path):
    body = _array_stub(tmp_path, [
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "{\"candidates\": [1]}"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "{\"candidates\": []}"}])
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr + _runlog(tmp_path)
    assert "후보 없음" in _first_line(report)

@pytest.mark.parametrize("init", [
    {"type": "system", "subtype": "init", "tools": ["Bash"], "mcp_servers": []},
    {"type": "system", "subtype": "init", "tools": [], "mcp_servers": [{"name": "x", "status": "connected"}]},
    {"type": "system", "subtype": "init", "mcp_servers": []},
])
def test_verbose_array_init_not_isolated_fails(tmp_path, init):
    # 배열에 init 원소가 있으면 판정 격리(도구 0개·MCP 0개)를 한 번 더 확인한다. 아니면 결과를 쓰지 않고 실패
    body = _array_stub(tmp_path, [init, {"type": "result", "is_error": False, "result": "{\"candidates\": []}"}])
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 2 and "실패" in _first_line(report)
    assert "isolation" in report.read_text(encoding="utf-8")
    assert "격리 위반" in _runlog(tmp_path)
    assert not (reports / f"{today}.judged.json").exists()

def test_verbose_array_envelope_is_error_fails(tmp_path):
    # init 은 격리 조건을 만족시켜 is_error 분기(rc 65)를 본다. 격리 위반(rc 66)으로 빠지면 안 된다
    body = _array_stub(tmp_path, [{"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
                                  {"type": "result", "is_error": True, "result": "boom"}])
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 2 and "실패" in _first_line(report)
    text = report.read_text(encoding="utf-8")
    assert "rc=65" in text and "isolation" not in text and "오류 봉투" in _runlog(tmp_path)

def test_verbose_array_without_result_fails(tmp_path):
    body = _array_stub(tmp_path, [{"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
                                  {"type": "assistant", "message": {"content": [{"type": "text", "text": "{\"candidates\": []}"}]}}])
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 2 and "실패" in _first_line(report)
    text = report.read_text(encoding="utf-8")
    assert "rc=65" in text and "isolation" not in text and "result 없음" in _runlog(tmp_path)

def test_no_consent_fails_rc5(tmp_path):
    def pre(cache):
        (cache / "config.json").write_text('{"lang": "ko"}')
    r, reports, today, report = _run(tmp_path, OK_STUB, pre=pre)
    assert r.returncode == 5 and "동의 기록 없음" in report.read_text(encoding="utf-8")

def test_wrap_prefix_used_for_collect(tmp_path):
    rec = tmp_path / "wrap-called"
    wrap = tmp_path / "wrap.py"
    wrap.write_text(f"import os, sys\nopen({str(rec)!r}, 'w').write(' '.join(sys.argv[1:]))\n"
                    "a = sys.argv[1:]; os.execvp(a[a.index('--') + 1], a[a.index('--') + 1:])\n")
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "run": {"wrap": ["python3", str(wrap), "--slot", "heavy", "--"]}}))
    r, reports, today, report = _run(tmp_path, OK_STUB, pre=pre, extra_env={"HC_SKIP_COLLECT": "0"})
    assert r.returncode == 0, r.stderr + _runlog(tmp_path)
    assert "collect" in rec.read_text()

def test_empty_wrap_no_unbound_variable(tmp_path):
    r, reports, today, report = _run(tmp_path, OK_STUB, extra_env={"HC_SKIP_COLLECT": "0"})
    assert r.returncode == 0, r.stderr
    assert "unbound variable" not in r.stderr + _runlog(tmp_path)

def test_gates_file_and_report_sections(tmp_path):
    r, reports, today, report = _run(tmp_path, OK_STUB)
    assert (reports / f"{today}.gates.json").exists() and (reports / f"{today}.candidates.json").exists()

def test_input_over_limit_renders_failure_without_calling_model(tmp_path):
    # fit_input 상한 초과(ValueError)는 러너가 받아 리포트 첫 줄 실패로 끝낸다. 모델은 부르지 않는다
    marker = tmp_path / "stub-called"
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "judge": {"max_input_bytes": 10}}))
    r, reports, today, report = _run(tmp_path, f"touch {marker}; {OK_STUB}", pre=pre)
    assert r.returncode == 1, r.stderr
    assert "실패" in _first_line(report) and "판정 입력 생성 실패" in report.read_text(encoding="utf-8")
    assert not marker.exists() and not list(reports.glob("*.input.json"))
    assert "최대 크기" in _runlog(tmp_path)

def test_report_language_from_signal_scope_when_config_lang_unset(tmp_path):
    # config.lang이 없으면 탐지 scope.lang(빈 탐지는 en)으로 리포트를 쓴다
    def pre(cache):
        (cache / "config.json").write_text('{"consent": {"accepted_at": "x"}}')
    r, reports, today, report = _run(tmp_path, OK_STUB, pre=pre, extra_env={"LANG": "ko_KR.UTF-8"})
    assert r.returncode == 0, r.stderr
    en = json.loads((SCRIPTS.parent / "hc" / "lang" / "en.json").read_text(encoding="utf-8"))
    assert _first_line(report) == en["report"]["title_none"]

# --- 설정 형식 오류는 원인대로 실패 리포트, 스텁도 격리 플래그를 받는다 ---
def _seed_stats_text(cache):
    return (cache / "collect-stats.json").read_text()

@pytest.mark.parametrize("wrap", [[1, "--"], "ab"])
def test_bad_wrap_fails_without_running_collect(tmp_path, wrap):
    marker = tmp_path / "stub-called"
    seen = {}
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "run": {"wrap": wrap}}))
        seen["stats"] = _seed_stats_text(cache)
    r, reports, today, report = _run(tmp_path, f"touch {marker}; {OK_STUB}", pre=pre,
                                     extra_env={"HC_SKIP_COLLECT": "0"})
    assert r.returncode == 6, r.stderr + _runlog(tmp_path)
    assert "실패" in _first_line(report) and "수집 실패" in report.read_text(encoding="utf-8")
    assert "run.wrap" in report.read_text(encoding="utf-8")
    assert _seed_stats_text(tmp_path / "cache") == seen["stats"]       # 수집이 돌지 않았다
    assert not marker.exists() and "OK report=" not in _runlog(tmp_path)
    assert "Traceback" not in _runlog(tmp_path)

def test_bad_judge_timeout_reports_config_error(tmp_path):
    marker = tmp_path / "stub-called"
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "judge": {"timeout_s": "abc"}}))
    r, reports, today, report = _run(tmp_path, f"touch {marker}; {OK_STUB}", pre=pre)
    assert r.returncode == 6, r.stderr + _runlog(tmp_path)
    text = report.read_text(encoding="utf-8")
    assert "실패" in _first_line(report) and "설정 오류" in text and "judge" in text
    assert "claude CLI를 찾지 못함" not in text
    assert not marker.exists() and "Traceback" not in _runlog(tmp_path)

def test_stub_receives_isolation_flags(tmp_path):
    rec = tmp_path / "stub-argv.txt"
    body = f"printf '%s\\n' \"$@\" > {rec}; {OK_STUB}"
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr + _runlog(tmp_path)
    argv = rec.read_text(encoding="utf-8").split("\n")
    assert argv[0] == "-p" and "--restricted" in argv and "--strict-mcp-config" in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv[argv.index("--output-format") + 1] == "json"


# --- 판정 격리: --safe-mode(CLAUDE.md·스킬·플러그인·훅 끔) + 빈 임시 폴더를 cwd로 ---
def test_stub_runs_with_safe_mode_in_empty_temp_cwd(tmp_path):
    rec = tmp_path / "stub-env.txt"
    body = (f'{{ pwd; ls -A | wc -l; printf "%s\\n" "$@"; }} > {rec}; ' + OK_STUB)
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr + _runlog(tmp_path)
    lines = rec.read_text(encoding="utf-8").split("\n")
    cwd, n = lines[0], lines[1].strip()
    assert n == "0"                                                # 판정 cwd 는 비어 있다
    assert cwd != str(SCRIPTS.parent) and not cwd.startswith(str(tmp_path / "cache"))
    assert "--safe-mode" in lines[2:]
    assert not pathlib.Path(cwd).exists()                          # 끝나면 지운다

def test_cli_without_safe_mode_is_cli_upgrade_failure(tmp_path):
    fake = tmp_path / "old-claude"
    fake.write_text("#!/bin/bash\nif [ \"$1\" = --help ]; then echo '  --restricted  Restricted mode'; exit 0; fi\n"
                    "touch \"$0.called\"\n")
    fake.chmod(0o755)
    def pre(cache):
        (cache / "config.json").write_text(json.dumps({"lang": "ko", "consent": {"accepted_at": "x"},
                                                       "judge": {"claude_bin": str(fake)}}))
    r, reports, today, report = _run(tmp_path, None, pre=pre)
    assert r.returncode == 1
    assert _first_line(report) == "# harness-creator 리포트: 실패 (CLI 업그레이드 필요)"
    assert not (tmp_path / "old-claude.called").exists()

_BUILTIN = [{"name": "cc-plugin-x", "path": "builtin", "source": "cc-plugin-x@builtin"}]

@pytest.mark.parametrize("extra", [
    {"plugins": _BUILTIN + [{"name": "p", "path": "/somewhere/p", "source": "p@market"}]},
    {"plugins": _BUILTIN, "memory_paths": ["/somewhere/CLAUDE.md"]},
    {"plugins": _BUILTIN, "memory": "notes"},
])
def test_verbose_array_init_plugins_or_memory_fails(tmp_path, extra):
    init = {"type": "system", "subtype": "init", "tools": [], "mcp_servers": [], **extra}
    body = _array_stub(tmp_path, [init, {"type": "result", "is_error": False, "result": "{\"candidates\": []}"}])
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 2 and "isolation" in report.read_text(encoding="utf-8")
    assert "격리 위반" in _runlog(tmp_path)

def test_verbose_array_init_builtin_plugins_only_passes(tmp_path):
    # 2.1.296 --safe-mode 실측 형태: 내장 플러그인만, memory 필드 없음
    init = {"type": "system", "subtype": "init", "tools": [], "mcp_servers": [], "plugins": _BUILTIN,
            "skills": ["debug"], "agents": ["Explore"]}
    body = _array_stub(tmp_path, [init, {"type": "result", "is_error": False, "result": "{\"candidates\": []}"}])
    r, reports, today, report = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr + _runlog(tmp_path)

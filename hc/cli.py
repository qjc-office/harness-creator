"""harness-creator 하위 명령. 러너(run-analysis.sh)와 SKILL.md가 이 명령만 부른다.
종료 코드: 0 정상, 2 검증·결정 오류, 3 가림 실패·불완전·입력 초과, 4 수집 통계 손상, 5 동의 없음, 6 설정 오류, 7 적용 오류, 75 실행 중."""
from __future__ import annotations
import argparse, json, math, os, pathlib, shutil, sys, time
from datetime import datetime
from hc.config import (ConfigError, claude_home, data_dir, data_source, expand, load_config, now,
                       save_user_config)
from hc.fsutil import write_private
from hc.lang import LANGS, load_pack
from hc.redact import RedactError

def _today() -> str:
    return time.strftime("%Y-%m-%d")

def _latest(sub: str, pattern: str):
    d = data_dir() / sub
    files = sorted(p for p in d.glob(pattern)) if d.is_dir() else []
    return files[-1] if files else None

def _signals_doc() -> dict:
    p = _latest("signals", "*.json")
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p else {}
    except (OSError, ValueError):
        return {}

def _resolve_lang(cfg: dict, scope=None) -> str:
    """리포트 언어: 설정 lang > 탐지 scope.lang > LANG 환경 > en."""
    if cfg.get("lang") in LANGS:
        return cfg["lang"]
    if isinstance(scope, dict) and scope.get("lang") in LANGS:
        return scope["lang"]
    env = (os.environ.get("LANG") or "")[:2].lower()
    return env if env in LANGS else "en"

def _lock_running() -> bool:
    pidf = data_dir() / "run.lock" / "pid"
    try:
        pid = int(pidf.read_text().strip())
        os.kill(pid, 0)
        return pid > 1
    except (OSError, ValueError):
        return False

def _report_failed(rep) -> bool:
    """리포트 첫 줄이 실패 제목(어느 언어든)인가. 실패 리포트는 최신이어도 재분석을 막지 않는다."""
    try:
        with open(rep, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().rstrip("\n")
    except OSError:
        return True
    return any(first.startswith(load_pack(l)["report"]["title_fail"]) for l in LANGS)

def _status(cfg: dict) -> dict:
    rep = _latest("reports", "????-??-??.md")
    age = (now() - rep.stat().st_mtime) / 86400 if rep else None
    needs = rep is None or age > cfg["report_max_age_days"] or _report_failed(rep)
    return {"data_dir": str(data_dir()), "data_source": data_source(), "consent": bool(cfg.get("consent")),
            "latest_report": str(rep) if rep else None, "report_age_days": round(age, 2) if age is not None else None,
            "needs_analysis": needs, "running": _lock_running(),
            "lang": _resolve_lang(cfg, _signals_doc().get("scope"))}

def _minutes_range(total_mb: float, mb_per_min) -> dict:
    """수집 시간 추정 범위. 디스크·부하 차이가 커서 한 값 대신 N~2N분으로 보인다(과소 안내 방지)."""
    lo = max(1, math.ceil(total_mb / max(1, mb_per_min)))
    return {"min_lo": lo, "min_hi": 2 * lo}

def _consent_text(cfg: dict) -> str:
    from hc.collect import estimate
    lang = _resolve_lang(cfg)
    K = load_pack(lang)["consent"]
    e = estimate(cfg["days"])
    mb = lambda b: round(b / 1048576, 1)
    total_mb = (e["claude"]["bytes"] + e["codex"]["bytes"]) / 1048576
    lines = [K["title"], "",
             K["read"].format(days=cfg["days"], claude_files=e["claude"]["files"], claude_mb=mb(e["claude"]["bytes"])),
             K["read_codex"].format(codex_files=e["codex"]["files"], codex_mb=mb(e["codex"]["bytes"])),
             K["send"].format(max_kb=cfg["judge"]["max_input_bytes"] // 1024, model=cfg["judge"]["model"]),
             K["send_inventory"],
             K["time"].format(**_minutes_range(total_mb, cfg["estimate_mb_per_min"])),
             K["store"].format(data_dir=data_dir()), "", K["codex_q"]]
    return "\n".join(lines)

# forget이 지우는 것(지울 목록 방식): 세션에서 나온 캐시·커서·이벤트·신호·리포트·수집 통계·로그와 적용 계획(plans)·초안(drafts).
# 남는 것: 설정·동의(config.json), 기각 억제 근거(decisions.jsonl), 적용 원장(applied.jsonl), 되돌리기 백업(backups),
# 킬스위치(disabled), 잠금(run.lock). 목록에 없는 것은 지우지 않는다.
_FORGET_DELETE = ("cursor.json", "collect-stats.json", "events", "signals", "reports", "logs", "plans", "drafts")

def _wrap_argv(cfg: dict) -> list:
    """run.wrap은 문자열 목록이고 NUL이 없어야 한다(wrap-argv가 NUL로 구분해 내보낸다). 아니면 ConfigError."""
    run = cfg.get("run")
    wrap = run.get("wrap") if isinstance(run, dict) else None
    if not isinstance(wrap, list) or not all(isinstance(x, str) and x and "\0" not in x for x in wrap):
        raise ConfigError(f"run.wrap은 문자열 목록이어야 한다(빈 문자열·NUL 금지): {wrap!r}")
    return [str(expand(x)) if x.startswith("~") else x for x in wrap]

def _judge_argv(cfg: dict) -> list:
    """judge-argv 세 줄(claude 경로, 모델, 시간 제한 초). 줄 구분이라 값에 줄바꿈이 있으면 안 된다."""
    j = cfg.get("judge")
    if not isinstance(j, dict):
        raise ConfigError(f"judge는 객체여야 한다: {j!r}")
    binp, model, to = j.get("claude_bin") or "", j.get("model"), j.get("timeout_s")
    if not isinstance(binp, str) or "\n" in binp or "\0" in binp:
        raise ConfigError(f"judge.claude_bin은 한 줄 문자열이어야 한다: {binp!r}")
    if not isinstance(model, str) or not model or "\n" in model or "\0" in model:
        raise ConfigError(f"judge.model은 비지 않은 한 줄 문자열이어야 한다: {model!r}")
    if not isinstance(to, int) or isinstance(to, bool) or to <= 0:
        raise ConfigError(f"judge.timeout_s는 양의 정수여야 한다: {to!r}")
    return [str(expand(binp)) if binp else (shutil.which("claude") or ""), model, str(to)]

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="harness-creator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("where"); p.add_argument("--path", action="store_true")
    for name in ("status", "lang", "consent-text", "consent-check", "wrap-argv", "judge-argv", "propose-input",
                 "stats-check", "pending", "applied", "forget"):
        sub.add_parser(name)
    p = sub.add_parser("consent"); p.add_argument("--accept", action="store_true", required=True)
    p.add_argument("--codex", choices=["yes", "no"], required=True)
    p = sub.add_parser("include-add"); p.add_argument("path")
    for name in ("collect", "detect"):
        p = sub.add_parser(name); p.add_argument("--days", type=int)
    p = sub.add_parser("validate"); p.add_argument("path")
    p = sub.add_parser("gate"); p.add_argument("path")
    p = sub.add_parser("render"); p.add_argument("--candidates"); p.add_argument("--error", default="")
    p.add_argument("--error-kind", default="")
    p = sub.add_parser("decide"); p.add_argument("id"); p.add_argument("decision", choices=["approve", "reject"])
    p.add_argument("--reason", required=True)
    p = sub.add_parser("apply-plan"); p.add_argument("draft")
    p = sub.add_parser("apply-write"); p.add_argument("id")
    p = sub.add_parser("undo"); p.add_argument("id"); p.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    try:
        return _dispatch(a)
    except ConfigError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 6

def _dispatch(a) -> int:
    cfg = load_config()
    if a.cmd == "where":
        d = data_dir()
        print(str(d) if a.path else json.dumps({"data_dir": str(d), "source": data_source()}, ensure_ascii=False))
        return 0
    if a.cmd == "status":
        print(json.dumps(_status(cfg), ensure_ascii=False)); return 0
    if a.cmd == "lang":
        print(_resolve_lang(cfg, _signals_doc().get("scope"))); return 0
    if a.cmd == "consent-text":
        print(_consent_text(cfg)); return 0
    if a.cmd == "consent-check":
        return 0 if cfg.get("consent") else 5
    if a.cmd == "consent":
        save_user_config({"consent": {"accepted_at": datetime.now().astimezone().isoformat(timespec="seconds"), "version": 1,
                                      "days": cfg["days"], "max_input_bytes": cfg["judge"]["max_input_bytes"]},
                          "sources": {"codex": a.codex == "yes"}})
        return 0
    if a.cmd == "include-add":
        from hc.config import load_user_config
        inc = [x for x in load_user_config().get("include") or [] if x != a.path] + [a.path]
        save_user_config({"include": inc}); return 0
    if a.cmd == "wrap-argv":
        sys.stdout.write("".join(x + "\0" for x in _wrap_argv(cfg)))
        return 0
    if a.cmd == "judge-argv":
        print("\n".join(_judge_argv(cfg))); return 0
    if a.cmd == "collect":
        if not cfg.get("consent"):
            print("동의 기록 없음: 수집하지 않는다", file=sys.stderr); return 5
        from hc.collect import collect
        try:
            st = collect(a.days or cfg["days"])
        except RedactError as e:
            print(f"가림 실패, 수집 중단: {e}", file=sys.stderr); return 3
        print(json.dumps(st.__dict__, ensure_ascii=False)); return 0
    if a.cmd == "detect":
        from hc.detect import EventLimitError, run_detect
        try:
            print(run_detect(a.days or cfg["days"]))
        except EventLimitError as e:
            print(f"탐지 중단: {e}", file=sys.stderr); return 3
        except RedactError as e:
            print(f"가림 실패, 신호 파일을 쓰지 않음: {e}", file=sys.stderr); return 3
        return 0
    if a.cmd == "propose-input":
        # 신호 samples는 detect가 현재 설정의 가림기로 다시 가려 신호 파일에 쓴 값이다. 그대로 옮기고 여기서 바꾸지 않는다
        # (validate가 같은 신호 파일 samples와 quotes를 정확 일치로 대조한다)
        from hc.inventory import build, fit_input
        from hc.redact import from_config
        signals = _signals_doc().get("signals", [])
        try:
            inv = build(claude_home(), claude_home() / "plugins" / "installed_plugins.json", redactor=from_config(cfg))
            payload = fit_input(signals, inv, cfg["judge"]["max_input_bytes"])
        except RedactError as e:
            print(f"가림 실패, 판정 입력 생성 중단: {e}", file=sys.stderr); return 3
        except ValueError as e:
            print(f"판정 입력 생성 중단: {e}", file=sys.stderr); return 3
        out = data_dir() / "reports"; out.mkdir(exist_ok=True)
        path = out / f"{_today()}.input.json"
        write_private(path, json.dumps(payload, ensure_ascii=False))
        print(path); return 0
    if a.cmd == "validate":
        # 무효 후보만 빼서 invalid 목록(리포트의 걸러진 절)으로 옮기고 나머지는 진행한다. 전부 무효면 실패
        from hc.candidates import check_each
        try:
            data = json.loads(open(a.path, encoding="utf-8").read())
        except (OSError, ValueError) as e:
            print(f"읽기 실패: {e}", file=sys.stderr); return 2
        top, each = check_each(data, _signals_doc().get("signals", []))
        for e in top + [e for _, errs in each for e in errs]:
            print(e, file=sys.stderr)
        if top:
            return 2
        valid = [c for c, errs in each if not errs]
        bad = [c for c, errs in each if errs]
        if bad and not valid:
            return 2
        if bad:
            inv = [{"candidate_id": c.get("id") if isinstance(c, dict) and isinstance(c.get("id"), str) else "?"}
                   for c in bad]
            write_private(pathlib.Path(a.path), json.dumps({"candidates": valid, "invalid": inv},
                                                           ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "gate":
        from hc.gates import gate_candidates
        try:
            data = json.loads(open(a.path, encoding="utf-8").read())
        except (OSError, ValueError) as e:
            print(f"읽기 실패: {e}", file=sys.stderr); return 2
        keep, codex, filtered = gate_candidates(data.get("candidates", []), _signals_doc().get("signals", []),
                                                cfg, claude_home())
        filtered = [{"gate": "invalid", **x} for x in data.get("invalid") or [] if isinstance(x, dict)] + filtered
        out = data_dir() / "reports"; out.mkdir(exist_ok=True)
        write_private(out / f"{_today()}.candidates.json", json.dumps({"candidates": keep}, ensure_ascii=False, indent=1))
        write_private(out / f"{_today()}.gates.json",
                      json.dumps({"codex_notes": codex, "filtered": filtered}, ensure_ascii=False, indent=1))
        print(out / f"{_today()}.candidates.json"); return 0
    if a.cmd == "render":
        from hc.effect import active_applied, effect_report
        from hc.report import render
        sp = data_dir() / "collect-stats.json"
        try:
            stats = json.loads(sp.read_text()) if sp.exists() else {}
        except (OSError, ValueError):
            stats = {}
        doc = _signals_doc()
        cands, err, kind = None, a.error, a.error_kind
        if not err and not kind:
            try:
                cands = json.loads(open(a.candidates, encoding="utf-8").read())
            except (OSError, ValueError, TypeError) as e:
                err, kind = str(e), "missing"
        gp = _latest("reports", "*.gates.json")
        gates = {}
        if gp and not (err or kind):
            try:
                gates = json.loads(gp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                gates = {}
        gates = {**gates, "filtered": (doc.get("filtered") or []) + (gates.get("filtered") or []),
                 "resume_skipped": (doc.get("scope") or {}).get("resume_skipped", 0)}
        effects = effect_report(active_applied(), doc.get("observed") or {})
        out = data_dir() / "reports"; out.mkdir(exist_ok=True)
        path = out / f"{_today()}.md"
        md = render(cands, stats, len(doc.get("signals", [])), err, scope=doc.get("scope"),
                    lang=_resolve_lang(cfg, doc.get("scope")), gates=gates, effects=effects, error_kind=kind)
        try:
            write_private(path, md)
        except OSError as e:          # 같은 날짜 .md가 디렉터리인 경우 등. 트레이스백 대신 사유와 rc 1
            print(f"리포트 쓰기 실패: {e}", file=sys.stderr); return 1
        print(path); return 0
    if a.cmd == "stats-check":
        from hc.report import incomplete_reasons, stats_corrupt
        p = data_dir() / "collect-stats.json"
        try:
            stats = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            print("unreadable"); return 4
        if stats_corrupt(stats):
            print("corrupt"); return 4
        reasons = incomplete_reasons(stats, lang="en")
        if reasons:
            print(", ".join(reasons)); return 3
        return 0
    if a.cmd == "pending":
        from hc.candidates import pending
        print(json.dumps(pending(), ensure_ascii=False, indent=1)); return 0
    if a.cmd == "decide":
        from hc.candidates import decide
        try:
            decide(a.id, a.decision, a.reason)
        except (ValueError, OSError) as e:
            print(e, file=sys.stderr); return 2
        return 0
    if a.cmd == "applied":
        from hc.effect import active_applied
        print(json.dumps(active_applied(), ensure_ascii=False, indent=1)); return 0
    if a.cmd == "forget":
        if _lock_running():
            print("분석이 실행 중이라 지우지 않는다", file=sys.stderr); return 75
        root = data_dir()
        for name in _FORGET_DELETE:
            p = root / name
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            elif p.exists() or p.is_symlink():
                p.unlink()
        return 0
    if a.cmd in ("apply-plan", "apply-write", "undo"):
        return _apply_cmd(a)
    return 1

def _apply_cmd(a) -> int:
    from hc import apply as A
    try:
        if a.cmd == "apply-plan":
            with open(a.draft, encoding="utf-8") as fh:
                draft = json.loads(fh.read())
            p = A.plan(draft)
            print(json.dumps({"candidate_id": p["candidate_id"], "warnings": p["warnings"], "diff": p["diff"]},
                             ensure_ascii=False, indent=1))
        elif a.cmd == "apply-write":
            print(json.dumps(A.write(a.id), ensure_ascii=False, indent=1))
        else:
            print(json.dumps(A.undo(a.id, force=a.force), ensure_ascii=False, indent=1))
    except (A.ApplyError, OSError, ValueError) as e:
        print(f"적용 오류: {e}", file=sys.stderr)
        return 7
    return 0

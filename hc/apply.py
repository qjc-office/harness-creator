"""승인한 후보를 ~/.claude 에 쓰고 되돌린다(spec §7). plan 은 아무것도 쓰지 않고(plans/<id>.json 만 저장),
write 는 plan 을 처음부터 다시 계산해 그때 본 diff 와 같은지 확인한 뒤 백업하고 원자적으로 쓰고 검증한다.
검증이 실패하면 백업으로 되돌린다. undo 는 백업을 되돌리고 새 파일·새 폴더를 지우며, settings 는 자기 항목만 뺀다.

경로 안전: 쓰는 곳은 rule·skill·hook 허용 경로와 settings.json 뿐이다. ~/.claude 하위가 심링크면 링크를 일반 파일로
바꾸지 않고 링크 대상에 쓰되, 링크를 끝까지 푼 실제 위치가 홈(~/.claude 의 부모) 밖이거나 파일 링크가 다른 이름의
파일을 가리키면 거부한다. write·undo 도 같은 검사를 다시 한다."""
from __future__ import annotations
import difflib, hashlib, json, os, pathlib, posixpath, re, shlex, shutil, signal, subprocess, tempfile, time, unicodedata
from .candidates import find_candidate, is_approved
from .config import claude_home, data_dir, now
from .effect import active_applied, record_applied, record_undo
from .frontmatter import front
from .fsutil import write_private

class ApplyError(Exception):
    pass

MARK = "generated_by: harness-creator"
HOOK_TIMEOUT = 10
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "SessionStart", "SessionEnd",
               "Notification", "PreCompact")
_PATHS = {
    "rule": re.compile(r"^rules/[A-Za-z0-9][A-Za-z0-9._-]*\.md$"),
    "skill": re.compile(r"^skills/[A-Za-z0-9][A-Za-z0-9._-]*/SKILL\.md$"),
    "hook": re.compile(r"^hooks/harness-creator/hc-\d{4}-\d{2}-\d{2}-\d{2}\.sh$"),
}
_HOOK_WARN = (
    ("network", re.compile(r"(?<![\w-])(curl|wget|nc|ncat|ssh|scp)(?![\w-])|/dev/(tcp|udp)/")),
    ("eval", re.compile(r"(?<![\w-])eval(?![\w-])")),
    ("decode-and-run", re.compile(r"base64\s+(-d|-D|--decode)[^\n|]*\|\s*(ba|z)?sh\b")),
    ("secret-read", re.compile(r"\.env\b|id_rsa|id_ed25519|\.pem\b|\.netrc|\.aws/credentials")),
)
_LABELS = ("network", "eval", "decode-and-run", "write-outside-home", "secret-read")
# 쓰기 대상: 리다이렉트(> >>), tee [-a], cp·mv의 둘째 인자
_WRITE_TARGET = re.compile(
    r"(?:>>?\s*|\btee\s+(?:-a\s+)?|\b(?:cp|mv)\s+(?:-\w+\s+)*[^\s;|&]+\s+)[\"']?([^\s\"';|&)]+)")
_DEV_OK = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")

def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")

def _outside_home(target: str) -> bool:
    """쓰기 대상이 ~ 밖 절대 경로인가(spec §7-3, /tmp도 ~ 밖이다). 변수·상대 경로는 정적으로 알 수 없어 판정하지 않는다.
    단순화: cp·mv는 둘째 인자만 대상으로 본다 → 정확히 하려면 셸 파서로 바꾼다."""
    if target in _DEV_OK or target.startswith("/dev/fd/"):
        return False
    if target in ("~", "$HOME", "${HOME}") or target.startswith(("~/", "$HOME/", "${HOME}/")):
        return False
    if not target.startswith("/"):
        return False
    raw = os.path.expanduser("~")
    t = os.path.normpath(target)
    return not (_under(t, raw) or _under(t, os.path.realpath(raw)))

def static_check(script: str) -> list:
    found = {label for label, rx in _HOOK_WARN if rx.search(script)}
    if any(_outside_home(m.group(1)) for m in _WRITE_TARGET.finditer(script)):
        found.add("write-outside-home")
    return [label for label in _LABELS if label in found]

# rule·skill frontmatter 는 초안 것을 버리고 apply 가 고정 키로 새로 만든다(구조 전환).
# Claude Code 로더는 frontmatter 를 LOADER_FM 으로 자르고(줄 첫머리가 아니어도 처음 나오는 `---` 에서 끝남) YAML 로 읽으며,
# 해석이 실패하면 {} 로 보아 paths 없는 rule 을 상시 로드한다. 초안을 편집하면 그 파서와 어긋날 여지가 남으므로, 값은 한 줄
# JSON 문자열로만 쓰고 `---`·제어 문자를 거부해 "우리가 만든 블록 = 로더가 자르는 블록"이 되게 한다.
LOADER_FM = re.compile(r"---\s*\n([\s\S]*?)---\s*\n?")
_BAD_CATS = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")   # 제어·서식·대리·사설·미할당 문자, 줄·문단 구분자

def _fm_value(v):
    """frontmatter 에 쓸 수 있으면 한 줄 JSON 문자열, 아니면 None(빈 값·앞뒤 공백·`---`·제어 문자)."""
    if not isinstance(v, str) or not v or v != v.strip() or "---" in v:
        return None
    if any(unicodedata.category(ch) in _BAD_CATS for ch in v):
        return None
    return json.dumps(v, ensure_ascii=False)

def _alternatives(glob: str, limit: int = 64) -> list:
    """중괄호·쉼표를 펼친 대안들(로더가 펼친다고 보고 보수적으로 나눈다). 단순화: 중첩은 안쪽부터, 대안 64개 넘으면 거부."""
    todo, out = [glob], []
    while todo:
        g = todo.pop()
        m = re.search(r"\{([^{}]*)\}", g)
        if m:
            todo += [g[:m.start()] + part + g[m.end():] for part in m.group(1).split(",")]
        else:
            out += g.split(",")
        if len(todo) + len(out) > limit:
            raise ApplyError(f"승인 paths 의 중괄호·쉼표 대안이 너무 많다: {glob!r}")
    return out

def _universal(glob: str) -> bool:
    """모든 파일과 맞는 glob(`**`, `*`, `**/*`, `./**` 등)이거나 그런 대안을 품었는가."""
    for alt in _alternatives(glob):
        segs = [s for s in re.sub(r"^(?:\./|/)+", "", alt.strip()).split("/") if s]
        if segs and all(re.fullmatch(r"\*+", s) for s in segs):
            return True
    return False

def _approved_paths(c: dict):
    """비전역 rule 후보의 승인 paths(중복 제거). global_rule 이면 None. 없거나·쓸 수 없거나·전역과 같으면 거부."""
    if c.get("global_rule"):
        return None
    paths = (c.get("proposed_change") or {}).get("paths")
    if not (isinstance(paths, list) and paths and all(isinstance(g, str) and g.strip() for g in paths)):
        raise ApplyError("상시 로드(global_rule)가 아닌 rule 후보인데 승인된 proposed_change.paths 가 없다")
    bad = [g for g in paths if _fm_value(g) is None]
    if bad:
        raise ApplyError(f"승인 paths 에 frontmatter 로 쓸 수 없는 값(`---`·제어 문자·앞뒤 공백): {bad!r}")
    if any(_universal(g) for g in paths):
        raise ApplyError("승인 paths 가 모든 파일(`**` 등)을 덮어 상시 로드와 같다. global_rule 후보로 다시 낸다")
    return list(dict.fromkeys(paths))

def _split_draft(text: str):
    """초안을 (frontmatter 줄 목록 또는 None, 본문)으로 나눈다. 첫 줄이 `---` 면 다음 `---` 줄까지가 frontmatter 다.
    이 결과는 name·description 을 줍고 본문을 떼는 데만 쓴다(초안 frontmatter 자체는 버린다)."""
    text = text[1:] if text.startswith("﻿") else text
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    raise ApplyError("초안 frontmatter 의 닫는 '---' 줄이 없다")

def _make_fm(form: str, c: dict, fm_lines, paths, marked: bool) -> str:
    """고정 키만으로 frontmatter 블록을 만든다. name·description 은 초안 값(단순 판독)이 쓸 수 있으면 그것, 아니면 후보 제목."""
    got = front("---\n" + "\n".join(fm_lines or []) + "\n---")
    title = c.get("title").strip() if isinstance(c.get("title"), str) else None
    out = ["---"]
    for k in (("name", "description") if form == "skill" else ("description",)):
        v = _fm_value(got.get(k)) or _fm_value(title)
        if v is None:
            raise ApplyError(f"frontmatter {k} 로 쓸 값이 없다(초안 값도 후보 제목도 비었거나 `---`·제어 문자 포함)")
        out.append(f"{k}: {v}")
    if paths:
        out += ["paths:"] + [f"  - {_fm_value(g)}" for g in paths]
    if marked:
        cid = _fm_value(c.get("id"))
        if cid is None:
            raise ApplyError("후보 id 를 frontmatter 에 쓸 수 없다")
        out += [MARK, f"generated_at: {json.dumps(time.strftime('%Y-%m-%d', time.localtime(now())))}", f"candidate_id: {cid}"]
    return "\n".join(out) + "\n---\n"

def _render_md(form: str, c: dict, content: str, paths, marked: bool):
    """(최종 내용, 우리가 만든 frontmatter 블록). 본문이 `---` 로 시작하면 빈 줄을 넣어 우리 블록과 붙지 않게 한다."""
    fm_lines, body = _split_draft(content)
    block = _make_fm(form, c, fm_lines, paths, marked)
    return block + ("\n" + body if body.startswith("---") else body), block

def _sha(p: pathlib.Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def _boundary() -> str:
    """쓰기 허용 경계. 기본은 ~(= ~/.claude 의 부모), HC_CLAUDE_HOME 을 바꾸면 그 부모."""
    return os.path.realpath(os.path.abspath(str(claude_home().parent)))

def _resolve(rel: str) -> pathlib.Path:
    """~/.claude 기준 상대 경로의 실제 위치. 심링크 폴더·파일을 따라가 링크 자체를 바꾸지 않는다.
    실제 위치가 홈 밖이거나, 파일 링크가 다른 이름을 가리키거나, 일반 파일이 아니면 거부한다."""
    logical = pathlib.Path(os.path.abspath(str(claude_home() / rel)))
    real = pathlib.Path(os.path.realpath(str(logical)))
    if not _under(str(real), _boundary()):
        raise ApplyError(f"링크를 따라간 실제 위치가 홈 밖이라 쓰지 않는다: {rel} -> {real}")
    if real.name != logical.name:
        raise ApplyError(f"파일 심링크가 다른 이름의 파일을 가리킨다(쓰지 않음): {rel} -> {real}")
    if os.path.lexists(str(real)) and not real.is_file():
        raise ApplyError(f"일반 파일이 아니다: {rel} -> {real}")
    return real

def _target(form, rel) -> pathlib.Path:
    if form not in _PATHS:
        raise ApplyError(f"자동 적용 대상 아님: {form!r} (rule·skill·hook만 쓴다)")
    if not (isinstance(rel, str) and _PATHS[form].fullmatch(rel)):
        raise ApplyError(f"허용 경로 밖: {rel!r}")
    return _resolve(rel)

def _hook_command(rel: str) -> str:
    return shlex.quote(os.path.abspath(str(claude_home() / rel)))

def _plans_dir() -> pathlib.Path:
    d = data_dir() / "plans"
    d.mkdir(mode=0o700, exist_ok=True); os.chmod(d, 0o700)
    return d

def _write_atomic(path: pathlib.Path, text: str, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text); fh.flush(); os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise

def _mkdirs(d: pathlib.Path) -> list:
    """없는 폴더를 위에서부터 만들고, 만든 폴더 목록(만든 순서)을 돌려준다. 되돌릴 때 이것만 지운다."""
    missing = []
    while not d.exists():
        missing.append(d)
        d = d.parent
    made = []
    for m in reversed(missing):
        m.mkdir()
        made.append(str(m))
    return made

def _own_dirs(rel) -> set:
    """지워도 되는 폴더: rel 의 둘째 단계 이하 상위 폴더(skills/<slug>, hooks/harness-creator)를 ~/.claude 안의 논리
    경로로 짚어 푼 실제 위치. 첫 단계(rules·skills·hooks)와 심링크 자체는 넣지 않는다. 원장(applied.jsonl)은 세션이
    고칠 수 있으므로 기록된 created_dirs 는 이 집합과 겹치는 것만 믿는다."""
    if not isinstance(rel, str):
        return set()
    parts = pathlib.PurePosixPath(rel).parts[:-1]
    out = set()
    for i in range(2, len(parts) + 1):
        logical = claude_home().joinpath(*parts[:i])
        if not logical.is_symlink():
            out.add(os.path.realpath(str(logical)))
    return out

def _rmdirs(dirs, rel) -> None:
    own = _own_dirs(rel)
    for d in reversed(dirs if isinstance(dirs, list) else []):
        p = pathlib.Path(str(d))
        if str(d) in own and p.is_dir() and not p.is_symlink() and not any(p.iterdir()):
            p.rmdir()

def _load_settings(p: pathlib.Path) -> dict:
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ApplyError(f"settings.json 을 읽지 못함(손대지 않음): {p}: {e}") from e
    if not isinstance(d, dict) or not isinstance(d.get("hooks", {}), dict):
        raise ApplyError(f"settings.json 최상위·hooks 가 객체가 아님(손대지 않음): {p}")
    return d

def _add_hook(s: dict, st: dict) -> dict:
    hooks = dict(s.get("hooks") or {})
    groups = list(hooks.get(st["event"]) or [])
    groups.append({"matcher": st["matcher"], "hooks": [{"type": "command", "command": st["command"], "timeout": st["timeout"]}]})
    hooks[st["event"]] = groups
    return {**s, "hooks": hooks}

def _remove_hook(s: dict, cid: str) -> dict:
    needle = f"harness-creator/{cid}"
    hooks = {}
    for ev, groups in (s.get("hooks") or {}).items():
        if not isinstance(groups, list):
            hooks[ev] = groups
            continue
        kept = []
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                kept.append(g)
                continue
            hs = [h for h in g["hooks"] if not (isinstance(h, dict) and needle in str(h.get("command", "")))]
            if hs:
                kept.append({**g, "hooks": hs})
        if kept:
            hooks[ev] = kept
    out = {k: v for k, v in s.items() if k != "hooks"}
    if hooks:
        out["hooks"] = hooks
    return out

def _settings_text(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2) + "\n"

def _active(cid: str):
    return next((d for d in active_applied() if d["candidate_id"] == cid), None)

def _hook_spec(draft: dict, rel: str) -> dict:
    h = draft.get("hook") if isinstance(draft.get("hook"), dict) else {}
    if h.get("event") not in HOOK_EVENTS:
        raise ApplyError(f"hook.event 허용값 밖: {h.get('event')!r}")
    matcher = h.get("matcher", "")
    if not isinstance(matcher, str):
        raise ApplyError("hook.matcher 는 문자열이어야 한다")
    timeout = h.get("timeout", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 600:
        raise ApplyError(f"hook.timeout 은 1~600 정수여야 한다: {timeout!r}")
    sp = _resolve("settings.json")
    return {"real": str(sp), "exists": sp.exists(), "sha_before": _sha(sp), "event": h["event"],
            "matcher": matcher, "command": _hook_command(rel), "timeout": timeout}

def _hook_settings_diff(draft: dict, rel: str):
    st = _hook_spec(draft, rel)
    cur = _load_settings(pathlib.Path(st["real"]))
    diff = "".join(difflib.unified_diff(_settings_text(cur).splitlines(True) if st["exists"] else [],
                                        _settings_text(_add_hook(cur, st)).splitlines(True),
                                        fromfile="a/settings.json", tofile="b/settings.json"))
    return st, diff

def _build(draft) -> dict:
    """초안을 검사하고 쓸 내용을 계산한다. 아무것도 쓰지 않는다. plan·write 가 같은 함수로 계산한다."""
    if not isinstance(draft, dict):
        raise ApplyError("초안은 JSON 객체여야 한다")
    cid, form, rel, content = draft.get("candidate_id"), draft.get("form"), draft.get("path"), draft.get("content")
    if form not in _PATHS:
        raise ApplyError(f"자동 적용 대상 아님: {form!r} (rule·skill·hook만 쓴다)")
    if not (isinstance(rel, str) and _PATHS[form].fullmatch(rel)):
        raise ApplyError(f"허용 경로 밖: {rel!r}")
    if not isinstance(content, str) or not content.strip():
        raise ApplyError("content 가 비었다")
    c = find_candidate(cid) if isinstance(cid, str) else None
    if c is None:
        raise ApplyError(f"최신 후보에 없는 id: {cid!r}")
    if c.get("form") != form or (c.get("proposed_change") or {}).get("path") != rel:
        raise ApplyError("초안의 형태·경로가 후보와 다르다")
    if not is_approved(c):
        raise ApplyError(f"승인 기록이 없는 후보: {cid}")
    if form == "hook" and rel != f"hooks/harness-creator/{cid}.sh":
        raise ApplyError("hook 경로는 hooks/harness-creator/<후보 id>.sh 여야 한다")
    if _active(cid):
        raise ApplyError(f"이미 적용된 후보: {cid}. 먼저 undo 한다")
    real = _target(form, rel)
    exists = real.exists()
    old = real.read_text(encoding="utf-8", errors="replace") if exists else ""
    generated_before = MARK in old
    if exists and (c.get("proposed_change") or {}).get("action") == "create":
        raise ApplyError(f"후보는 새 파일 만들기(create)인데 이미 파일이 있다: {rel}")
    if exists and not generated_before and MARK in content:
        raise ApplyError("사람이 쓴 파일에 harness-creator 표지를 넣지 않는다")
    if form == "hook" and not exists and MARK not in content:      # rule·skill 표지는 apply 가 frontmatter 에 넣는다
        raise ApplyError(f"새 파일에는 '{MARK}' 표지가 있어야 한다")
    paths, block = (_approved_paths(c) if form == "rule" else None), None
    if form != "hook":
        content, block = _render_md(form, c, content, paths, not exists or generated_before)
    mode = (real.stat().st_mode & 0o777) if exists else (0o700 if form == "hook" else 0o644)
    files = [{"rel": rel, "real": str(real), "exists": exists, "sha_before": _sha(real), "content": content,
              "mode": mode, "generated": not exists or generated_before, "frontmatter": block}]
    diff = "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    settings, warnings = None, []
    if form == "hook":
        settings, sdiff = _hook_settings_diff(draft, rel)
        diff, warnings = diff + sdiff, static_check(content)
    return {"candidate_id": cid, "form": form, "global_rule": bool(c.get("global_rule")), "paths": paths,
            "signal_ids": c.get("signal_ids", []), "files": files, "settings": settings, "diff": diff,
            "warnings": warnings, "draft": draft}

def plan(draft: dict) -> dict:
    p = _build(draft)
    write_private(_plans_dir() / f"{p['candidate_id']}.json", json.dumps(p, ensure_ascii=False, indent=1))
    return p

def _run_hook(command: str, event: str):
    sample = {"session_id": "harness-creator-verify", "hook_event_name": event, "tool_name": "Bash",
              "tool_input": {"command": "echo harness-creator-verify"}, "prompt": "harness-creator verify"}
    with tempfile.TemporaryDirectory() as td:
        proc = subprocess.Popen(["/bin/sh", "-c", command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=td, start_new_session=True)
        try:
            out, _ = proc.communicate(json.dumps(sample), timeout=HOOK_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)       # 손자 프로세스가 출력 파이프를 쥐고 있어도 끝낸다
            except ProcessLookupError:
                pass
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return None, ""
    return proc.returncode, out

def _verify_fm(text: str, block) -> list:
    """최종 파일의 첫 frontmatter 가 우리가 만든 바이트 그대로이고, 로더 정규식으로 잘라도 같은 블록인지 본다."""
    if not isinstance(block, str) or not text.startswith(block):
        return ["frontmatter 가 apply 가 만든 블록과 다르다"]
    m = LOADER_FM.match(text)
    if not m or m.group(1) != block[len("---\n"):-len("---\n")]:
        return ["로더 정규식으로 자른 frontmatter 가 apply 가 만든 블록과 다르다"]
    return []

def _verify_file(p: dict) -> list:
    """파일 검증. hook 은 settings 에 항목을 넣기 전에 실행해 본다(살아 있는 세션이 검증 전 훅을 집어 가지 않게)."""
    errs = []
    f = p["files"][0]
    text = pathlib.Path(f["real"]).read_text(encoding="utf-8", errors="replace")
    if p["form"] in ("rule", "skill"):
        errs += _verify_fm(text, f.get("frontmatter"))
    elif p["form"] == "hook":
        st = p["settings"]
        rc, out = _run_hook(st["command"], st["event"])
        if rc is None:
            return errs + [f"훅이 {HOOK_TIMEOUT}초 안에 끝나지 않았다"]
        if rc not in (0, 2):
            errs.append(f"훅 종료 코드 {rc}(0 또는 2여야 한다)")
        if out.strip():
            try:
                json.loads(out.strip())
            except ValueError:
                errs.append("훅 stdout 은 비었거나 JSON 이어야 한다")
    return errs

def _verify_settings(st: dict) -> list:
    try:
        s = json.loads(pathlib.Path(st["real"]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"settings.json 재파싱 실패: {e}"]
    if not any(isinstance(h, dict) and st["command"] == h.get("command")
               for g in (s.get("hooks") or {}).get(st["event"], []) for h in (g.get("hooks") or [])):
        return ["settings.json 에 훅 항목이 없다"]
    return []

def _restore(path: pathlib.Path, backup: str, mode: int) -> None:
    tmp = path.with_name(f".{path.name}.restore.{os.getpid()}")
    shutil.copyfile(backup, tmp)
    os.chmod(tmp, mode)
    os.replace(tmp, path)

def _undo_record(r: dict) -> None:
    real = pathlib.Path(r["real"])
    if r["created"]:
        if real.is_file():
            real.unlink()
    else:
        mode = r.get("mode_before")
        _restore(real, r["backup"], (mode if isinstance(mode, int) and not isinstance(mode, bool) else 0o644) & 0o777)
    _rmdirs(r.get("created_dirs"), r.get("rel"))

def _rollback(records: list, srec) -> list:
    """기록마다 따로 되돌리고, 실패는 모아서 돌려준다(하나가 실패해도 나머지는 계속한다)."""
    fails = []
    for r in ([srec] if srec else []) + list(reversed(records)):
        try:
            _undo_record(r)
        except Exception as e:
            fails.append(f"{r.get('rel') or 'settings.json'}: {e}")
    return fails

def _backup_dir(cid: str) -> pathlib.Path:
    root = data_dir() / "backups"
    root.mkdir(mode=0o700, exist_ok=True); os.chmod(root, 0o700)
    d = root / cid
    d.mkdir(mode=0o700, exist_ok=True); os.chmod(d, 0o700)
    return d

def _backup_name(rel: str) -> str:
    """백업 이름은 정규화한 rel 의 해시에 묶는다. SKILL.md 처럼 같은 파일 이름이 여럿이어도 undo 가 rel 로 기대 이름을
    계산해 정확히 비교하므로, 원장의 rel 만 바꿔 다른 파일에 이 백업을 덮어쓰게 할 수 없다."""
    norm = posixpath.normpath(rel)
    return f"{hashlib.sha256(norm.encode('utf-8')).hexdigest()[:16]}-{posixpath.basename(norm)}"

def _backup(real: pathlib.Path, bdir: pathlib.Path, rel: str):
    if not real.exists():
        return None
    b = bdir / _backup_name(rel)          # 같은 후보·같은 rel 은 활성 적용이 하나뿐이라 덮어써도 된다
    if os.path.lexists(str(b)):
        b.unlink()
    shutil.copyfile(real, b); os.chmod(b, 0o600)
    return str(b)

def _start_record(real_s: str, bdir: pathlib.Path, rel: str, extra: dict) -> dict:
    """쓰기 직전 상태(새 파일인지·원래 권한)를 적고 백업한다. 되돌리기·undo 가 이 기록만 본다."""
    real = pathlib.Path(real_s)
    exists = real.exists()
    return {**extra, "real": real_s, "created": not exists, "backup": _backup(real, bdir, rel),
            "mode_before": (real.stat().st_mode & 0o777) if exists else None, "created_dirs": []}

def _git_root(path: pathlib.Path):
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return str(pathlib.Path(r.stdout.strip()).resolve()) if r.returncode == 0 and r.stdout.strip() else None

_SAME = ("candidate_id", "form", "global_rule", "paths", "files", "settings", "diff")

def write(candidate_id: str) -> dict:
    pp = _plans_dir() / f"{candidate_id}.json"
    if not pp.is_file():
        raise ApplyError(f"plan 없음: 먼저 apply-plan 을 실행한다 ({candidate_id})")
    try:
        saved = json.loads(pp.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ApplyError(f"plan 파일 손상: {e}") from e
    if not isinstance(saved, dict) or not isinstance(saved.get("draft"), dict):
        raise ApplyError("plan 파일 형식이 아니다. apply-plan 을 다시 실행한다")
    for f in saved.get("files") or []:          # 읽기만 한다(sha). 쓰기 대상은 아래에서 다시 계산한다
        if isinstance(f, dict) and _sha(pathlib.Path(str(f.get("real")))) != f.get("sha_before"):
            raise ApplyError(f"plan 뒤에 파일이 바뀌었다: {f.get('rel')}. apply-plan 을 다시 실행한다")
    p = _build(saved["draft"])                   # 승인·허용 경로·심링크 해석을 처음부터 다시 검사한다
    if p["candidate_id"] != candidate_id or any(p[k] != saved.get(k) for k in _SAME):
        raise ApplyError("plan 뒤에 대상·경로·settings 가 바뀌었다(또는 plan 이 손상됐다). apply-plan 을 다시 실행한다")
    bdir = _backup_dir(candidate_id)
    st = p["settings"]
    records, srec = [], None
    try:
        for i, f in enumerate(p["files"]):
            rec = _start_record(f["real"], bdir, f["rel"], {"rel": f["rel"]})
            records.append(rec)                  # 폴더를 만들기 전에 넣어 둬야 실패 시 되돌릴 수 있다
            rec["created_dirs"] = _mkdirs(pathlib.Path(f["real"]).parent)
            _write_atomic(pathlib.Path(f["real"]), f["content"], f["mode"])
            rec["sha_after"] = _sha(pathlib.Path(f["real"]))
        errs = _verify_file(p)
        if not errs and st:
            sp = pathlib.Path(st["real"])
            cur = _load_settings(sp)
            srec = _start_record(st["real"], bdir, "settings.json", {})
            srec["created_dirs"] = _mkdirs(sp.parent)
            _write_atomic(sp, _settings_text(_add_hook(cur, st)), srec["mode_before"] or 0o600)
            errs = _verify_settings(st)
        if errs:
            raise ApplyError("검증 실패, 되돌린다: " + "; ".join(errs))
        entry = {"candidate_id": candidate_id, "form": p["form"], "signal_ids": p.get("signal_ids", []),
                 "files": records, "settings": srec}
        record_applied(entry)
    except BaseException as exc:
        fails = _rollback(records, srec)
        if fails:
            raise ApplyError(f"{exc} / 되돌리기 일부 실패(백업 {bdir} 에서 손으로 복구): " + "; ".join(fails)) from exc
        raise
    try:
        pp.unlink()
    except FileNotFoundError:
        pass
    return {**entry, "git_root": _git_root(pathlib.Path(records[0]["real"]).parent)}

def _check_undo_record(form, r, broot: str, force: bool) -> None:
    """원장(applied.jsonl)은 세션이 고칠 수 있어 그대로 믿지 않는다. 하나라도 걸리면 undo 전체를 거부한다."""
    if not isinstance(r, dict):
        raise ApplyError("적용 기록 형식이 아니다(원장 손상). 손으로 확인한다")
    if str(_target(form, r.get("rel"))) != r.get("real"):
        raise ApplyError(f"적용 뒤 실제 위치(심링크)가 바뀌었다: {r.get('rel')}. 손으로 확인한다")
    if not isinstance(r.get("sha_after"), str):
        raise ApplyError(f"적용 기록에 sha_after 가 없다(원장 손상): {r.get('rel')}. 손으로 확인한다")
    real = pathlib.Path(r["real"])
    if not r.get("created"):
        b = r.get("backup")
        if not (isinstance(b, str) and os.path.realpath(b) == os.path.join(broot, _backup_name(r["rel"]))
                and os.path.isfile(b)):
            raise ApplyError(f"백업이 없어 되돌릴 수 없다: {r.get('rel')}")
    if real.exists() and _sha(real) != r["sha_after"] and not force:
        raise ApplyError(f"적용 뒤 사용자가 고친 파일: {r['rel']}. 확인 후 --force 로 되돌린다")
    if r.get("created") and real.exists() and MARK not in real.read_text(encoding="utf-8", errors="replace"):
        raise ApplyError(f"'{MARK}' 표지가 없는 파일은 지우지 않는다(--force 여도): {r.get('rel')}")

def undo(candidate_id: str, force: bool = False) -> dict:
    e = _active(candidate_id)
    if e is None:
        raise ApplyError(f"되돌릴 적용 기록 없음: {candidate_id}")
    files = e.get("files") or []
    broot = os.path.realpath(str(data_dir() / "backups" / candidate_id))
    # 먼저 전부 검사하고, 하나라도 걸리면 아무것도 바꾸지 않는다
    for r in files:
        _check_undo_record(e.get("form"), r, broot, force)
    srec, cur, sp = e.get("settings"), None, None
    if srec:
        sp = _resolve("settings.json")
        if str(sp) != srec.get("real"):
            raise ApplyError("적용 뒤 settings.json 실제 위치(심링크)가 바뀌었다. 손으로 확인한다")
        cur = _load_settings(sp) if sp.exists() else None
    for r in reversed(files):
        _undo_record(r)
    if cur is not None:
        new = _remove_hook(cur, candidate_id)
        if srec.get("created") and not new:
            sp.unlink()
        elif new != cur:
            _write_atomic(sp, _settings_text(new), sp.stat().st_mode & 0o777)
    record_undo(candidate_id)
    return {"candidate_id": candidate_id, "files": [r["rel"] for r in files],
            "git_root": _git_root(pathlib.Path(files[0]["real"]).parent) if files else None}

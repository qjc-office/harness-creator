"""기존 하네스 자산 목록. 판정 단계에서 중복 배제에 쓴다. 대상은 ~/.claude 와 설치된 플러그인.

모델 입력으로 나가므로 이름·설명·경로·메모리 줄은 수집과 같은 가림기를 거친다(redactor 필수).
경로는 ~/.claude 기준 상대 경로 또는 plugin:<이름>/<상대 경로>로만 낸다.

심링크: ~/.claude 하위 폴더가 dotfiles 저장소를 가리키는 경우가 흔해서 따라간다. 패턴이 고정 깊이
(rules/*.md, skills/*/SKILL.md 등)라 재귀 탐색이 없으므로 순환 링크가 있어도 끝난다. 끊긴 링크·
자기 순환 링크는 is_file()이 False라 조용히 빠진다. 파일은 앞 16KB의 frontmatter만 읽는다.
(16KB 경계가 frontmatter 안에 걸리면 닫는 `---`가 없어 아무 값도 싣지 않으므로 경계에서 값이 잘려 나가지 않는다.)

자르기는 항상 가린 뒤에 한다. 자른 뒤 가리면 경계에 걸친 시크릿은 정규식에 안 걸려 앞부분이 샌다."""
from __future__ import annotations
import json, pathlib, re, shlex
from .frontmatter import front_file

SELF = "harness-creator"
_MEMORY_MAX = 200
_TEXT_MAX = 300
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_RUNNER = re.compile(r"^(?:env|bash|sh|zsh|dash|node|python[0-9.]*)$")

def _clip(red, text: str) -> str:
    """가린 다음 자른다(순서 바꾸지 말 것)."""
    return red.redact(text)[:_TEXT_MAX]

class _Count:
    def __init__(self):
        self.unreadable = 0

def _entries(paths, base: pathlib.Path, red, cnt, prefix: str = "") -> list:
    out = []
    for p in sorted(paths):
        try:
            if not p.is_file():
                continue
            fm = front_file(p)
        except OSError:
            cnt.unreadable += 1
            continue
        rel = p.relative_to(base).as_posix()
        out.append({"path": red.redact(prefix + rel), "name": _clip(red, fm.get("name", p.parent.name if p.name == "SKILL.md" else p.stem)),
                    "description": _clip(red, fm.get("description", ""))})
    return out

_SCRIPT_EXT = (".sh", ".py", ".js", ".ts", ".mjs", ".rb")
_UNSAFE = re.compile(r"\s|://|[?=]")

def _is_file_token(t: str) -> bool:
    """파일명으로 인정하는 토큰: 공백·`://`·`?`·`=`가 없고, 스크립트 확장자나 경로 구분자가 있는 것."""
    return not _UNSAFE.search(t) and ("/" in t or t.endswith(_SCRIPT_EXT))

def _script_name(cmd: str) -> str:
    """훅 명령에서 스크립트 파일명. env·VAR=값·옵션·인터프리터(bash/sh/zsh/node/python*)·`uv run`을 건너뛴다.
    파일명으로 인정되지 않는 토큰(-c 인자, URL, 명령 문자열)을 만나면 파일명을 내지 않고 실행기 이름
    (예: bash, curl)만 낸다. 판정 모델로 나가는 값이라 가림 패턴이 못 잡는 비밀(URL 꼬리 등)을 담지 않는다.
    셸 문법으로 못 쪼개면(따옴표 미닫힘 등) 공백으로 쪼개 같은 규칙을 적용한다."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    runner, i = "", 0
    while i < len(toks):
        t, base = toks[i], pathlib.PurePosixPath(toks[i]).name
        if base == "uv" and i + 1 < len(toks) and toks[i + 1] == "run":
            runner, i = "uv", i + 2
        elif _ASSIGN.match(t) or t.startswith("-"):
            i += 1
        elif _RUNNER.match(base) and not _UNSAFE.search(t):
            runner, i = base, i + 1
        elif _is_file_token(t):
            return base
        else:
            return runner or ("" if _UNSAFE.search(t) else base)
    return runner

def _hook_commands(settings: pathlib.Path) -> list:
    try:
        d = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = set()
    for groups in (d.get("hooks") or {}).values() if isinstance(d, dict) else []:
        for g in groups if isinstance(groups, list) else []:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                cmd = h.get("command") if isinstance(h, dict) else None
                if isinstance(cmd, str) and cmd.strip():
                    names.add(_script_name(cmd))
    return sorted(n for n in names if n)

def _plugins(plugins_file, red, cnt) -> list:
    if not plugins_file or not pathlib.Path(plugins_file).exists():
        return []
    try:
        d = json.loads(pathlib.Path(plugins_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cnt.unreadable += 1
        return []
    out = []
    plugins = d.get("plugins") if isinstance(d, dict) else None
    for key, entries in sorted((plugins if isinstance(plugins, dict) else {}).items()):
        name = key.split("@")[0]
        if name == SELF or not isinstance(entries, list) or not entries:
            continue
        ip = entries[-1].get("installPath") if isinstance(entries[-1], dict) else None
        if not isinstance(ip, str) or not ip:
            continue
        root = pathlib.Path(ip)
        paths = list(root.glob("skills/*/SKILL.md")) + list(root.glob("agents/*.md")) + list(root.glob("commands/*.md"))
        out += _entries(paths, root, red, cnt, prefix=f"plugin:{name}/")
    return out

def build(claude_home, plugins_file=None, *, redactor) -> dict:
    """redactor는 필수 키워드 인자다. 가림 없이 부르는 길을 두지 않는다."""
    if not callable(getattr(redactor, "redact", None)):
        raise TypeError("redactor는 redact(text) 메서드를 가진 가림기여야 한다")
    red, home, cnt = redactor, pathlib.Path(claude_home), _Count()
    mem = []
    for idx in sorted(home.glob("projects/*/memory/MEMORY.md")):
        try:
            mem += [_clip(red, l) for l in idx.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("- ")]
        except OSError:
            cnt.unreadable += 1
    hooks_dir = home / "hooks"
    out = {
        "rules": _entries(home.glob("rules/*.md"), home, red, cnt),
        "skills": _entries(home.glob("skills/*/SKILL.md"), home, red, cnt),
        "agents": _entries(home.glob("agents/*.md"), home, red, cnt),
        "commands": _entries(home.glob("commands/*.md"), home, red, cnt),
        "hook_children": sorted(red.redact(p.name) for p in hooks_dir.glob("*") if p.suffix in (".sh", ".py") and p.is_file())
                         if hooks_dir.is_dir() else [],
        "hook_commands": [red.redact(n) for n in _hook_commands(home / "settings.json")],
        "memory": mem[:_MEMORY_MAX],
        "plugins": _plugins(plugins_file, red, cnt),
    }
    out["unreadable"] = cnt.unreadable
    return out

def _size(sigs, inv) -> int:
    return len(json.dumps({"signals": sigs, "inventory": inv}, ensure_ascii=False).encode("utf-8"))

def _score(s) -> int:
    return int(s.get("count", 0) or 0) * len(s.get("groups") or s.get("sessions") or [])

def fit_input(signals: list, inventory: dict, max_bytes: int) -> dict:
    """판정 입력을 동의한 최대 크기 안으로 줄인다. 순서: 메모리 줄 → 점수 낮은 신호(1개는 남김) → 자산 설명.
    그래도 넘치면 ValueError(동의한 크기보다 많이 보내지 않는다). 입력은 바꾸지 않는다."""
    sigs = sorted(signals, key=lambda s: -_score(s))
    inv = dict(inventory)
    if _size(sigs, inv) > max_bytes and inv.get("memory"):
        inv["memory"] = []
    while len(sigs) > 1 and _size(sigs, inv) > max_bytes:
        sigs = sigs[:-1]
    for key in ("plugins", "commands", "agents", "skills", "rules"):
        if _size(sigs, inv) <= max_bytes:
            break
        if isinstance(inv.get(key), list):
            inv[key] = [{**e, "description": ""} if isinstance(e, dict) else e for e in inv[key]]
    if _size(sigs, inv) > max_bytes:
        raise ValueError(f"판정 입력이 최대 크기 {max_bytes}바이트를 넘는다")
    return {"signals": sigs, "inventory": inv}

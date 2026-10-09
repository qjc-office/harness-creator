"""설정과 데이터 폴더. 기본값은 defaults.json, 사용자 값은 데이터 폴더 config.json이 정본이다."""
from __future__ import annotations
import copy, json, os, pathlib, time
from datetime import datetime, timezone

_DEFAULTS = pathlib.Path(__file__).resolve().parent / "defaults.json"
FALLBACK = "~/.claude/harness-creator"

class ConfigError(Exception):
    pass

def expand(p) -> pathlib.Path:
    return pathlib.Path(os.path.expanduser(str(p)))

def data_source() -> str:
    if os.environ.get("HC_DATA_DIR"):
        return "HC_DATA_DIR"
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        return "CLAUDE_PLUGIN_DATA"
    return "fallback"

def data_dir_path() -> pathlib.Path:
    src = data_source()
    return expand(FALLBACK) if src == "fallback" else expand(os.environ[src])

def data_dir() -> pathlib.Path:
    d = data_dir_path()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d

def claude_home() -> pathlib.Path:
    return expand(os.environ.get("HC_CLAUDE_HOME") or "~/.claude")

def load_defaults() -> dict:
    return json.loads(_DEFAULTS.read_text(encoding="utf-8"))

def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out

def _read_json(path: pathlib.Path, what: str) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"{what} 읽기 실패: {path}: {e}") from e
    if not isinstance(d, dict):
        raise ConfigError(f"{what} 최상위가 객체가 아님: {path}")
    return d

def user_config_path() -> pathlib.Path:
    return data_dir_path() / "config.json"

def load_user_config() -> dict:
    p = user_config_path()
    return _read_json(p, "사용자 설정") if p.exists() else {}

def load_config() -> dict:
    """기본값 < include 파일들(순서대로) < 사용자 config.json. 읽지 못하면 ConfigError."""
    cfg = load_defaults()
    user = load_user_config()
    for inc in user.get("include") or []:
        if not isinstance(inc, str) or not inc:
            raise ConfigError(f"include 항목이 문자열이 아님: {inc!r}")
        cfg = _merge(cfg, {k: v for k, v in _read_json(expand(inc), "include 설정").items() if k != "include"})
    return _merge(cfg, {k: v for k, v in user.items() if k != "include"})

def save_user_config(patch: dict) -> dict:
    from .fsutil import write_private
    data_dir()
    new = _merge(load_user_config(), patch)
    write_private(user_config_path(), json.dumps(new, ensure_ascii=False, indent=1) + "\n")
    return new

def now() -> float:
    v = os.environ.get("HC_NOW", "").strip()
    if not v:
        return time.time()
    try:
        return float(v)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise ConfigError(f"HC_NOW 형식 오류: {v!r}") from e
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()

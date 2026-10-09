import pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

@pytest.fixture
def cache(tmp_path, monkeypatch):
    """데이터 폴더. v1 테스트 이식을 쉽게 하려고 이름은 cache 그대로 둔다."""
    d = tmp_path / "cache"
    monkeypatch.setenv("HC_DATA_DIR", str(d))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("HC_NOW", raising=False)
    return d

@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """테스트가 실제 ~/.claude 를 읽거나 쓰지 않게 한다."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HC_CLAUDE_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)

@pytest.fixture(autouse=True)
def _restore_tz():
    import os, time
    old = os.environ.get("TZ")
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()

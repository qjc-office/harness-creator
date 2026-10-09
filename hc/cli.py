"""harness-creator 하위 명령."""
from __future__ import annotations
import argparse, json, sys
from hc.config import ConfigError, data_dir, data_source

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="harness-creator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("where"); p.add_argument("--path", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "where":
            d = data_dir()
            print(str(d) if a.path else json.dumps({"data_dir": str(d), "source": data_source()}, ensure_ascii=False))
            return 0
    except ConfigError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 6
    return 1

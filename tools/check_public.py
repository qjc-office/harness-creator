#!/usr/bin/env python3
"""공개 저장소 금지어 검사. 금지어는 sha256 해시로만 둔다(목록 자체가 공개되지 않게).
작업 트리의 텍스트 파일(git 추적 여부 무관, .git 제외)의 영숫자 토큰(대소문자 무시)을 해시해 forbidden.sha256 과 대조한다."""
import hashlib, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HASHES = ROOT / "tools" / "forbidden.sha256"
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")

def digest(word: str) -> str:
    return hashlib.sha256(word.lower().encode("utf-8")).hexdigest()

def load_hashes() -> set:
    return {l.strip() for l in HASHES.read_text().splitlines() if l.strip() and not l.startswith("#")}

def scan_text(text: str, hashes: set) -> list:
    return [i for i, line in enumerate(text.splitlines(), 1) if any(digest(t) in hashes for t in TOKEN.findall(line))]

def files() -> list:
    """추적 파일 + 미추적 파일(스테이징 전 README 포함). .gitignore로 뺀 로컬 전용 폴더(.v1/·.notes/)는 공개되지 않으므로 제외.
    git을 못 쓰면 .git만 빼고 전부 본다(검사 누락보다 오탐이 낫다)."""
    r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0:
        return [ROOT / p for p in r.stdout.split("\0") if p]
    return [p for p in ROOT.rglob("*") if ".git" not in p.relative_to(ROOT).parts]

def main(argv) -> int:
    if argv[:1] == ["--hash"]:
        for w in argv[1:]:
            print(digest(w))
        return 0
    hashes, bad = load_hashes(), 0
    for p in files():
        if p == HASHES or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n in scan_text(text, hashes):
            print(f"{p.relative_to(ROOT)}:{n}")
            bad += 1
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

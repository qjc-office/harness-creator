#!/usr/bin/env python3
"""공개 저장소 금지어 검사. 금지어는 sha256 해시로만 둔다(목록 자체가 공개되지 않게).
공개 대상 파일(git 추적 + 미추적, .gitignore로 뺀 것 제외)의 내용과 저장소 상대 경로를 검사한다.

비교 단위(대소문자 무시, `_`·`.`은 `-`로 맞춘 뒤):
- 토큰 전체와 `-`·숫자 경계로 쪼갠 조각
- 금지어 길이 집합(forbidden.sha256 의 `# lengths:` 줄)에 든 길이의 토큰 내 부분 문자열 전부
  (금지어에 접두·접미·숫자가 붙어 한 토큰이 된 형태를 잡는다)
비UTF-8 바이트는 대체 문자로 읽어 계속 검사한다. 경로 이름에서 걸리면 `<경로>:0`으로 출력한다."""
import hashlib, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HASHES = ROOT / "tools" / "forbidden.sha256"
TOKEN = re.compile(r"[a-z0-9-]+")
PIECE = re.compile(r"[a-z]+|[0-9]+")
_cache = {}

def _norm(text: str) -> str:
    return text.lower().replace("_", "-").replace(".", "-")

def digest(word: str) -> str:
    return hashlib.sha256(_norm(word).encode("utf-8")).hexdigest()

def _hit(word: str, hashes: set) -> bool:
    h = _cache.get(word)
    if h is None:
        h = _cache[word] = hashlib.sha256(word.encode("utf-8")).hexdigest()
    return h in hashes

def load_hashes() -> set:
    return {l.strip() for l in HASHES.read_text().splitlines() if l.strip() and not l.startswith("#")}

def load_lengths() -> set:
    for l in HASHES.read_text().splitlines():
        if l.startswith("# lengths:"):
            return {int(x) for x in l.split(":", 1)[1].replace(",", " ").split()}
    return set()

def _candidates(token: str, lengths) -> set:
    out = {token, token.strip("-")}
    for part in token.split("-"):
        out.add(part)
        out.update(PIECE.findall(part))
    for n in lengths or ():
        out.update(token[i:i + n] for i in range(len(token) - n + 1))
    return out

def _line_hit(line: str, hashes: set, lengths) -> bool:
    return any(_hit(c, hashes) for t in TOKEN.findall(_norm(line)) for c in _candidates(t, lengths) if c)

def scan_text(text: str, hashes: set, lengths=None) -> list:
    return [i for i, line in enumerate(text.splitlines(), 1) if _line_hit(line, hashes, lengths)]

def files() -> list:
    """추적 파일 + 미추적 파일(스테이징 전 README 포함). .gitignore로 뺀 로컬 전용 폴더는 공개되지 않으므로 제외.
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
    if argv[:1] == ["--build"]:          # 해시 파일 전체(머리말·길이·해시)를 출력한다
        print("# forbidden tokens (sha256 of normalized lowercase token). Do not add plain words here.")
        print("# lengths: " + ",".join(str(n) for n in sorted({len(w) for w in argv[1:]})))
        for w in argv[1:]:
            print(digest(w))
        return 0
    hashes, lengths, bad = load_hashes(), load_lengths(), 0
    for p in files():
        rel = p.relative_to(ROOT).as_posix()
        if _line_hit(rel, hashes, lengths):
            print(f"{rel}:0")
            bad += 1
        if p == HASHES or not p.is_file():
            continue
        try:
            text = p.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        for n in scan_text(text, hashes, lengths):
            print(f"{rel}:{n}")
            bad += 1
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

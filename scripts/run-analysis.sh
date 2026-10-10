#!/bin/bash
# harness-creator 분석 러너: collect → detect(G1·G2) → propose(claude -p) → validate → gate(G3·G4·G5) → render
# 킬스위치: <데이터 폴더>/disabled 또는 HARNESS_CREATOR_DISABLED=1
# 테스트 훅:
#   HC_CLAUDE_STUB=<실행파일>  테스트 전용. 판정 단계에서 claude 대신 실행(stdin=프롬프트, stdout=응답).
#                              claude와 같은 격리 플래그 배열(ISO)을 받는다. 스텁은 claude가 아니라 --help 사전검사만 생략
#   HC_SKIP_COLLECT=1          수집 생략(탐지는 그대로)
#   HC_MAX_EVENTS=<N>          탐지 이벤트 상한 덮어쓰기
#   HC_PROPOSE_TIMEOUT=<초>     판정 시간 제한(기본 config judge.timeout_s)
set -u
umask 077
HERE="$(cd "$(dirname "$0")" && pwd)"
CLI=(python3 "$HERE/cli.py")
[ "${HARNESS_CREATOR_DISABLED:-0}" = "1" ] && exit 0
DATA="$("${CLI[@]}" where --path 2>/dev/null)" || { echo "harness-creator: 설정 오류(cli where 실패)" >&2; exit 6; }
[ -f "$DATA/disabled" ] && exit 0
mkdir -p "$DATA/logs" "$DATA/reports"
LOG="$DATA/logs/run.log"
TODAY="$(date +%F)"
JUDGED="$DATA/reports/$TODAY.judged.json"
STUB="${HC_CLAUDE_STUB:-}"
PF="$DATA/reports/$TODAY.propose.prompt.txt"
RAW="$DATA/reports/$TODAY.propose.raw.txt"
LOCK="$DATA/run.lock"
JCWD=""   # 판정 호출용 빈 임시 폴더(cwd). 프로젝트 CLAUDE.md·.claude/ 가 판정에 실리지 않게 한다
_log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }
_fail() { _log "FAIL $*"; printf 'harness-creator: %s\n' "$*" >&2; }
_render_err() { "${CLI[@]}" render --error-kind "$1" --error "${2:-}" >> "$LOG" 2>&1; }

# --- 동시 실행 잠금(v1 run-weekly.sh 와 같은 규칙) ---
_hc_lock_owner() { cat "$LOCK/pid" 2>/dev/null; }
_hc_lock_state() {
  local d="$1" pid
  [ -f "$d/pid" ] || { echo nopid; return; }
  pid="$(cat "$d/pid" 2>/dev/null)"
  case "$pid" in ''|*[!0-9]*) echo dead; return ;; esac
  if [ "$pid" -gt 1 ] && kill -0 "$pid" 2>/dev/null; then echo live; else echo dead; fi
}
_hc_dir_age() {
  # stat 문법은 OS 마다 다르다(GNU stat -f 는 파일시스템 정보). 경과 초를 python 으로 계산한다
  local a
  a="$(python3 -c 'import os, sys, time; print(max(0, int(time.time() - os.stat(sys.argv[1]).st_mtime)))' "$1" 2>/dev/null)"
  case "$a" in ''|*[!0-9]*) echo 0 ;; *) echo "$a" ;; esac
}
if ! mkdir "$LOCK" 2>/dev/null; then
  case "$(_hc_lock_state "$LOCK")" in
    live) _log "이미 실행 중 pid=$(_hc_lock_owner), 이번 실행은 건너뜀"; exit 75 ;;
    nopid)
      if [ "$(_hc_dir_age "$LOCK")" -ge 60 ]; then _fail "잠금에 pid 없음: $LOCK 수동 확인 후 삭제"
      else _log "다른 실행이 잠금을 만드는 중, 이번 실행은 건너뜀"; fi
      exit 75 ;;
  esac
  STALE="$LOCK.stale.$$"
  if ! mv "$LOCK" "$STALE" 2>/dev/null; then _log "남은 잠금을 다른 실행이 먼저 회수함"; exit 75; fi
  if [ "$(_hc_lock_state "$STALE")" != dead ]; then
    mv "$STALE" "$LOCK" 2>/dev/null || _log "잠금 되돌리기 실패: $STALE"
    _log "잠금 회수 경합, 이번 실행은 건너뜀"; exit 75
  fi
  _log "남은 잠금 회수 (pid=$(cat "$STALE/pid" 2>/dev/null))"
  rm -rf "${STALE:?}"
  if ! mkdir "$LOCK" 2>/dev/null; then _log "잠금 회수 중 다른 실행이 먼저 잡음"; exit 75; fi
fi
printf '%s\n' "$$" > "$LOCK/pid.tmp.$$" && mv -f "$LOCK/pid.tmp.$$" "$LOCK/pid"
if [ "$(_hc_lock_owner)" != "$$" ]; then _log "잠금 주인이 바뀜(pid=$(_hc_lock_owner))"; exit 75; fi
_hc_cleanup() {
  rm -f "$PF" "$RAW"
  [ -n "$JCWD" ] && rm -rf "${JCWD:?}"
  if [ "$(_hc_lock_owner)" = "$$" ]; then rm -rf "${LOCK:?}"; fi
}
trap _hc_cleanup EXIT

if ! "${CLI[@]}" consent-check >> "$LOG" 2>&1; then
  _render_err consent; _fail "동의 없음"; exit 5
fi

# --- 수집: run.wrap 접두로 감싼다(빈 배열은 bash 3.2 set -u 에서 ${A[@]+...} 로만 펼친다) ---
if [ "${HC_SKIP_COLLECT:-0}" != "1" ]; then
  # wrap-argv 결과를 파일(umask 077 → 0600, 잠금 폴더 안이라 끝나면 같이 지워짐)로 받아 rc를 본다.
  # 프로세스 치환은 rc를 잃어 설정 오류 때 래퍼 없이 수집해 버린다.
  WA="$LOCK/wrap-argv"; WE="$LOCK/wrap-argv.err"
  if ! "${CLI[@]}" wrap-argv > "$WA" 2> "$WE"; then
    cat "$WE" >> "$LOG"
    _render_err collect "$(head -n 1 "$WE")"; _fail "wrap-argv 실패(run.wrap 설정 오류)"; exit 6
  fi
  WRAP=()
  while IFS= read -r -d '' a; do WRAP+=("$a"); done < "$WA"
  rm -f "$WA" "$WE"
  ${WRAP[@]+"${WRAP[@]}"} python3 "$HERE/cli.py" collect >> "$LOG" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    _render_err collect "rc=$rc"; _fail "collect rc=$rc"; exit "$rc"
  fi
fi
if ! "${CLI[@]}" detect >> "$LOG" 2>&1; then
  _render_err detect; _fail "detect 실패"; exit 1
fi
INPUT="$("${CLI[@]}" propose-input 2>> "$LOG")"
if [ ! -f "$INPUT" ]; then
  _render_err input; _fail "propose-input 실패"; exit 1
fi
LANG_OUT="$("${CLI[@]}" lang 2>> "$LOG")"
JF="$LOCK/judge-argv"; JE="$LOCK/judge-argv.err"
if ! "${CLI[@]}" judge-argv > "$JF" 2> "$JE"; then
  cat "$JE" >> "$LOG"
  msg="$(head -n 1 "$JE")"
  _render_err config "${msg#설정 오류: }"; _fail "judge-argv 실패(judge 설정 오류)"; exit 6
fi
JA=()
while IFS= read -r line; do JA+=("$line"); done < "$JF"
rm -f "$JF" "$JE"
CLAUDE="${JA[0]:-}"; MODEL="${JA[1]:-sonnet}"
PROPOSE_TIMEOUT="${HC_PROPOSE_TIMEOUT:-${JA[2]:-1800}}"
case "$PROPOSE_TIMEOUT" in ''|*[!0-9]*|0) PROPOSE_TIMEOUT=1800 ;; esac

# 시간 제한 실행기: timeout → gtimeout → python3. python3 판은 시간 초과 124, 시그널 종료 128+N.
_HC_TO_PY='import subprocess, sys
try:
    rc = subprocess.run(sys.argv[2:], timeout=int(sys.argv[1])).returncode
except subprocess.TimeoutExpired:
    sys.exit(124)
sys.exit(128 - rc if rc < 0 else rc)'
TO=()
if t="$(command -v timeout 2>/dev/null)" && [ -n "$t" ]; then TO=("$t" "$PROPOSE_TIMEOUT")
elif t="$(command -v gtimeout 2>/dev/null)" && [ -n "$t" ]; then TO=("$t" "$PROPOSE_TIMEOUT")
elif t="$(command -v python3 2>/dev/null)" && [ -n "$t" ] && "$t" -c 'import subprocess' 2>/dev/null; then
  TO=("$t" -c "$_HC_TO_PY" "$PROPOSE_TIMEOUT")
fi
if [ "${#TO[@]}" -eq 0 ]; then
  _render_err timeout_runner; _fail "시간 제한 실행기 없음"; exit 1
fi
_log "시간 제한 실행기: $(basename "${TO[0]}") ${PROPOSE_TIMEOUT}s"

rm -f "${JUDGED:?}" "${RAW:?}"
{ sed -e "s#{TODAY}#$TODAY#g" -e "s#{LANG}#$LANG_OUT#g" "$HERE/propose-prompt.md"; cat "$INPUT"; } > "$PF"
# 판정 모델에는 도구를 하나도 주지 않는다. 입력이 세션 기록이라 프롬프트 주입이 가능하므로 파일 쓰기는 셸이 한다.
# --restricted 로 사용자 settings 를 무시하고, --safe-mode 로 CLAUDE.md·스킬·설치 플러그인·훅을 끄고,
# --tools "" 로 내장 도구를 끄고, --disallowedTools 를 2차 방어선으로 둔다. cwd 는 빈 임시 폴더다.
# 가변 인자 플래그(--tools·--disallowedTools)는 맨 뒤에 둔다. 플래그를 빼고 돌리는 대체 경로는 두지 않는다.
ISO=(-p --output-format json --model "$MODEL" \
  --restricted --safe-mode --permission-mode dontAsk \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --tools "" \
  --disallowedTools "Bash,Read,Write,Edit,MultiEdit,NotebookEdit,Glob,Grep,WebFetch,WebSearch,Agent,Task,Skill,TodoWrite,Monitor,SendMessage,Artifact,ArtifactData,ArtifactComments,ToolSearch,EnterWorktree,ExitWorktree,CronCreate,RemoteTrigger,TaskStop,PushNotification,ScheduleWakeup")
rc=0
if ! JCWD="$(mktemp -d "${TMPDIR:-/tmp}/hc-judge.XXXXXX")" || [ -z "$JCWD" ]; then
  JCWD=""; _render_err judge "rc=1"; _fail "판정용 임시 폴더를 만들지 못함"; exit 2
fi
case "$CLAUDE" in ''|/*) ;; *) CLAUDE="$PWD/$CLAUDE" ;; esac
case "$STUB" in ''|/*) ;; *) STUB="$PWD/$STUB" ;; esac
if [ -n "$STUB" ]; then
  # 테스트 전용 경로. 실제 claude와 같은 ISO 플래그를 넘겨 격리 인자가 빠지지 않았는지 테스트가 본다
  ( cd "$JCWD" && exec "${TO[@]}" "$STUB" "${ISO[@]}" ) < "$PF" > "$RAW" 2>> "$LOG"
  rc=$?
else
  if [ -z "$CLAUDE" ] || [ ! -x "$CLAUDE" ]; then
    _render_err no_claude "${CLAUDE:-PATH}"; _fail "claude 없음: ${CLAUDE:-PATH}"; exit 1
  fi
  HELP="$("$CLAUDE" --help 2>&1)"
  case "$HELP" in
    *--restricted*) ;;
    *) _render_err cli_upgrade; _fail "claude --help 에 --restricted 없음"; exit 1 ;;
  esac
  case "$HELP" in
    *--safe-mode*) ;;
    *) _render_err cli_upgrade; _fail "claude --help 에 --safe-mode 없음"; exit 1 ;;
  esac
  ( cd "$JCWD" && exec "${TO[@]}" "$CLAUDE" "${ISO[@]}" ) < "$PF" > "$RAW" 2>> "$LOG"
  rc=$?
fi
rm -f "${PF:?}"
[ "$rc" -ne 0 ] && _log "판정 단계 rc=$rc"
# 응답이 --output-format json 봉투면 result 문자열을 꺼낸다. 그 안의 첫 '{'~마지막 '}'를 JSON으로 읽어 JUDGED(0600)에 쓴다.
if [ "$rc" -eq 0 ]; then
  python3 - "$RAW" "$JUDGED" <<'PY' >> "$LOG" 2>&1
import json, os, sys
raw_p, out_p = sys.argv[1], sys.argv[2]
try:
    text = open(raw_p, encoding="utf-8", errors="replace").read()
except OSError:
    text = ""
def _write(s):
    fd = os.open(out_p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(s)
try:
    env = json.loads(text)
except ValueError:
    env = None
# CLI 전역 설정 verbose=true 면 json 출력이 메시지 배열이 된다(--restricted 로도 안 꺼짐). 마지막 result 원소만 쓴다
if isinstance(env, list):
    # 배열에는 init 원소가 실린다. 있으면 판정 격리를 한 번 더 확인한다(이중 방어):
    # 도구 0개·MCP 서버 0개, plugins 필드가 있으면 내장(builtin)만, memory 관련 필드가 있으면 비어 있음
    def _builtin(pl):
        return isinstance(pl, dict) and (pl.get("path") == "builtin" or str(pl.get("source", "")).endswith("@builtin"))
    for m in env:
        if isinstance(m, dict) and m.get("type") == "system" and m.get("subtype") == "init":
            bad = m.get("tools") != [] or m.get("mcp_servers") != []
            if "plugins" in m:
                pls = m.get("plugins")
                bad = bad or not isinstance(pls, list) or not all(_builtin(pl) for pl in pls)
            bad = bad or any(m.get(k) for k in m if "memory" in str(k).lower())
            if bad:
                _write(text)
                print("판정 격리 위반: init 의 tools·mcp_servers·plugins·memory 가 격리 조건 밖", file=sys.stderr)
                sys.exit(3)
    res = [m for m in env if isinstance(m, dict) and m.get("type") == "result"]
    if not res:
        _write(text)
        print("판정 응답 배열에 result 없음", file=sys.stderr)
        sys.exit(1)
    env = res[-1]
if isinstance(env, dict) and env.get("type") == "result":
    if env.get("is_error"):
        _write(text)
        print("판정 응답이 오류 봉투", file=sys.stderr)
        sys.exit(1)
    text = env.get("result") if isinstance(env.get("result"), str) else ""
i, j = text.find("{"), text.rfind("}")
try:
    if i < 0 or j < i:
        raise ValueError("응답에 JSON 객체 없음")
    obj = json.loads(text[i:j + 1])
except ValueError as e:
    _write(text)
    print(f"판정 응답 해석 실패: {e}", file=sys.stderr)
    sys.exit(1)
_write(json.dumps(obj, ensure_ascii=False, indent=1))
PY
  prc=$?
  # 3 = 격리 위반(rc 66, 리포트에 isolation 표기), 그 밖의 실패 = 해석 실패(rc 65)
  case "$prc" in 0) ;; 3) rc=66 ;; *) rc=65 ;; esac
fi
rm -f "${RAW:?}"

if [ "$rc" -ne 0 ] || ! "${CLI[@]}" validate "$JUDGED" >> "$LOG" 2>&1; then
  [ -e "$JUDGED" ] && mv -f "$JUDGED" "${JUDGED%.json}.invalid.json"
  det="rc=$rc"; [ "$rc" -eq 66 ] && det="rc=$rc isolation"
  _render_err judge "$det"; _fail "propose/validate 실패 $det"; exit 2
fi
if ! CANDS="$("${CLI[@]}" gate "$JUDGED" 2>> "$LOG")" || [ ! -f "$CANDS" ]; then
  _render_err gate; _fail "gate 실패"; exit 2
fi
REPORT="$("${CLI[@]}" render --candidates "$CANDS" 2>> "$LOG")"
rrc=$?
if [ "$rrc" -ne 0 ] || [ -z "$REPORT" ]; then _fail "render 실패 rc=$rrc"; exit 4; fi
SC="$("${CLI[@]}" stats-check 2>> "$LOG")"
src=$?
case "$src" in
  0) ;;
  3) _log "불완전 report=$REPORT"; _fail "incomplete: $SC"; exit 3 ;;
  *) _log "수집 통계 손상 report=$REPORT"; _fail "collect-stats 손상 (${SC:-?} rc=$src)"; exit 4 ;;
esac
_log "OK report=$REPORT"

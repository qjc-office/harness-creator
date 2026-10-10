# harness-creator

내 Claude Code 세션(동의하면 Codex 세션도)을 읽어 반복 정정·실패 루프·반복 지시를 찾고, 인용과 세션 수를 근거로 규칙·훅·스킬 후보를 주당 최대 3건 제안하는 Claude Code 플러그인입니다. diff를 승인하기 전에는 아무것도 쓰지 않고, 쓸 때마다 백업을 남기며 `undo`로 되돌립니다.

[English](README.md) · [中文](README.zh.md)

## 설치

    /plugin marketplace add qjc-office/harness-creator
    /plugin install harness-creator@harness-creator

설치 후 `/harness-creator`를 실행합니다. Python 3.9 이상, `claude --help`에 `--restricted`가 있는 Claude Code가 필요합니다. `--restricted`가 없으면 판정을 아예 하지 않고 리포트 첫 줄에 CLI 업그레이드가 필요하다고 적습니다. 이 플래그를 빼고 판정하는 대체 경로는 없습니다.

## 하는 일

1. 첫 실행에서 읽는 범위, 보내는 것과 크기, Codex 포함 여부를 보여 주고 동의를 받습니다. Codex를 포함하면 가린 Codex 세션 발췌도 판정을 위해 내 `claude` CLI, 즉 Anthropic으로 보내집니다.
2. 지난 7일을 수집하면서 시크릿·개인정보를 가리고 신호를 찾습니다. 이미 해소된 신호, 일괄 실행으로 부풀려진 신호, Codex에서만 생긴 문제, 근거가 약한 상시 로드 규칙, 3건을 넘는 후보는 코드가 걸러 냅니다.
3. 가린 발췌를 내 `claude` CLI로 한 번 보냅니다. 도구 0개, MCP 서버 0개, 사용자 설정 무시(`--restricted --tools ""`)로 돌립니다.
4. 후보를 보여 주고 고르게 합니다. 승인한 규칙·스킬·훅만 diff 확인 뒤 `~/.claude`에 씁니다. 메모리·스크립트·에이전트는 초안만 보여 줍니다.
5. 다음 분석에서 적용한 하네스의 신호가 다시 나왔는지 알려 줍니다.

명령: `/harness-creator`, `/harness-creator analyze`, `/harness-creator undo <id>`, `/harness-creator forget`, `/harness-creator where`.

### 규칙·스킬을 쓰는 방식

규칙·스킬의 frontmatter는 정해진 키로만 새로 만듭니다. `name`(스킬), `description`, `paths`(승인한 glob, 상시 로드 규칙이면 없음), 그리고 이 플러그인이 만든 파일이면 `generated_by: harness-creator`, `generated_at`, `candidate_id`입니다. 후보가 내가 직접 쓴 규칙·스킬을 고치는 경우에도 마찬가지입니다. 그 파일에는 표지를 넣지 않지만 frontmatter는 이 키들로 다시 만들어지고, 원래 있던 다른 키는 사라집니다. 승인하는 diff에 어떤 줄이 빠지는지 그대로 보이고, 원본은 `backups/`에 남아 `undo`로 되돌릴 수 있습니다.

glob이 거의 모든 파일에 걸리는 규칙(`**/*.*`, `@(**)` 등)이나 `name`이 폴더 이름과 다른 스킬은 승인 전에 잘 살펴보세요. 스킬이 두 경우 모두 따로 짚어서 묻습니다.

## 데이터

플러그인 데이터 폴더(없으면 `~/.claude/harness-creator/`)에 폴더 0700, 파일 0600으로 둡니다. 이벤트는 30일 뒤 지웁니다. 통계 수집은 없고, 네트워크는 내 `claude` CLI 호출뿐입니다. 폴더 위치는 `/harness-creator where`로 확인합니다.

`/harness-creator forget`은 수집한 것을 지웁니다. 대상은 `cursor.json`(수집 커서), `collect-stats.json`, `events/`, `signals/`, `reports/`, `logs/`, 그리고 적용 계획과 초안을 담는 `plans/`, `drafts/`입니다. `config.json`(동의와 설정), `decisions.jsonl`(기각한 신호는 발생 횟수가 두 배가 될 때까지 다시 제안하지 않기 위한 기록), `applied.jsonl`과 `backups/`(`undo`에 필요), 직접 만든 `disabled` 킬스위치 파일은 남깁니다.

분석을 멈추려면 데이터 폴더에 빈 `disabled` 파일을 만들거나 `HARNESS_CREATOR_DISABLED=1`을 설정합니다.

## 설정

데이터 폴더의 `config.json`. 모두 선택입니다.

| 키 | 뜻 |
|---|---|
| `redact.lexicon_files` | 추가로 가릴 단어 목록 파일(JSON 목록, 또는 목록을 담은 객체) |
| `redact.pattern_files` | 추가 정규식 파일: `{"patterns": [{"label", "regex", "flags", "whole"}]}` |
| `run.wrap` | 무거운 수집 단계를 감쌀 argv 접두(문자열 목록) |
| `judge.claude_bin` | `claude` 경로(기본: PATH) |
| `judge.model` | 판정 모델(기본 `sonnet`) |
| `judge.timeout_s` | 판정 시간 제한(초, 기본 1800). 양의 정수여야 합니다. `null`, `0`, 문자열, 소수는 설정 오류로 멈춥니다 |
| `sources.codex` | Codex 세션 포함 여부(동의에서 정함) |
| `lang` | 리포트 언어 `ko`, `zh`, `en`(기본: 내 메시지의 다수 언어) |
| `include` | 이 파일보다 먼저 합칠 다른 설정 파일 |

지정한 파일을 읽지 못하면 가리지 않은 채 진행하지 않고 수집을 멈춥니다.

## 한계

### 가림이 놓치는 것

가림은 정규식과 사용자 단어 목록으로 합니다. 전부 잡지는 못합니다. 알려진 미탐 형태는 다음과 같습니다.

- 앞에 키 이름이 없는 8자 미만 비밀번호
- URL 안의 계정 정보(`user:pw@host`)
- 점으로 구분한 한국·중국 휴대전화 번호(`010.2222.3333`)
- 키 이름이 64자를 넘는 `KEY=값` 대입문
- 이중 이스케이프된 JSON(`{\\"password\\": ...}`)
- YAML 블록 값(`password: |` 다음 줄에 값)
- 값을 붙여 쓰는 한 글자 플래그(`-p<값>`)
- `-`로 시작하는 플래그 값(`--password -abc...`)
- XML 요소(`<password>...</password>`)
- `Authorization: Basic` 헤더
- 단어 목록에 없는 고유명사(고객·프로젝트·사람 이름)

`redact.lexicon_files`로 단어 목록을, `redact.pattern_files`로 정규식을 직접 보강할 수 있습니다. 둘 다 지정한 파일을 읽지 못하면 수집을 멈춥니다.

### 감지 정확도

중국어·영어 정정 패턴은 합성 표본으로만 확인해서 실제 정확도를 재지 않았습니다. 처음 2주는 후보를 직접 읽고 실제 상황과 맞지 않는 것은 기각해 주세요. 기각한 신호는 발생 횟수가 두 배가 될 때까지 다시 제안하지 않습니다.

## 제거

`/harness-creator undo <id>`로 적용한 하네스를 되돌린 뒤 `/plugin uninstall harness-creator`.

## 라이선스

MIT

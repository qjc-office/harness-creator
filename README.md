# harness-creator

A Claude Code plugin that reads your own recent Claude Code (and, if you agree, Codex) sessions, finds repeated corrections, failure loops and repeated instructions, and proposes at most 3 rules, hooks or skills a week, each backed by quotes and session counts. Nothing is written until you approve a diff, every write is backed up, and `undo` restores it.

[한국어](README.ko.md) · [中文](README.zh.md)

## Install

    /plugin marketplace add qjc-office/harness-creator
    /plugin install harness-creator@harness-creator

Then run `/harness-creator`. Requires Python 3.9+ and a Claude Code version whose `claude --help` lists `--restricted` and `--safe-mode`. Without them the plugin does not judge at all: the report's first line says a CLI upgrade is needed. There is no fallback that runs the judge without them.

## What it does

1. First run asks for consent: which sessions are read, what is sent, how big, a rough collection time range (estimated at 1000 MB per minute, shown as N-2N min), and whether to include Codex. If you include Codex, masked Codex excerpts are also sent for judging to your `claude` CLI, that is, to Anthropic.
2. Collects the last 7 days, masks secrets and personal data at collection time, detects signals, and drops stale signals, batch-run inflation, Codex-only issues, weakly supported always-loaded rules, and anything past 3 candidates.
3. Sends masked excerpts once to your own `claude` CLI with no tools, no MCP servers, your settings ignored and your CLAUDE.md, skills, plugins and hooks off (`--restricted --safe-mode --tools ""`), from an empty temporary folder.
4. Shows candidates; you pick. Approved rules, skills and hooks are written to `~/.claude` after you see the diff. Memory, script and agent ideas are shown as drafts only.
5. The next analysis reports whether an applied harness's signal came back.

Commands: `/harness-creator`, `/harness-creator analyze`, `/harness-creator undo <id>`, `/harness-creator forget`, `/harness-creator where`.

### How rules and skills are written

The frontmatter of a rule or skill is generated from a fixed set of keys: `name` (skill), `description`, `paths` (the globs you approved; none for an always-loaded rule) and, for files this plugin creates, `generated_by: harness-creator`, `generated_at` and `candidate_id`. This also applies when a candidate modifies a rule or skill you wrote yourself: that file gets no marker, but its frontmatter is regenerated with these keys and any other key it had is removed. The diff you approve shows exactly which lines go, and the original is kept in `backups/` so `undo` brings it back.

Look closely before approving a rule whose globs match almost everything (for example `**/*.*` or `@(**)`), or a skill whose `name` differs from its folder name; the skill asks you about both.

## Data

Stored in the plugin data folder (or `~/.claude/harness-creator/`), folder 0700 and files 0600. Events are deleted after 30 days. No telemetry; the only network call is your `claude` CLI. `/harness-creator where` prints the folder.

`/harness-creator forget` deletes what was collected: `cursor.json` (the collection cursor), `collect-stats.json`, `events/`, `signals/`, `reports/`, `logs/`, and the apply plans and drafts in `plans/` and `drafts/`. It keeps `config.json` (your consent and settings), `decisions.jsonl` (a rejected signal stays quiet until it happens twice as often), `applied.jsonl` and `backups/` (so `undo` still works), and the `disabled` kill switch file if you created one.

To stop the plugin from running analysis, create an empty file named `disabled` in the data folder or set `HARNESS_CREATOR_DISABLED=1`.

## Configuration

`config.json` in the data folder. All keys are optional.

| key | meaning |
|---|---|
| `redact.lexicon_files` | extra word lists to mask (JSON list, or object of lists) |
| `redact.pattern_files` | extra regex files: `{"patterns": [{"label", "regex", "flags", "whole"}]}` |
| `run.wrap` | argv prefix for the heavy collection step (list of strings) |
| `judge.claude_bin` | path to `claude` (default: PATH) |
| `judge.model` | judging model (default `sonnet`) |
| `judge.timeout_s` | judging time limit in seconds (default 1800). Must be a positive integer; `null`, `0`, strings and decimals are rejected as a config error |
| `sources.codex` | include Codex sessions (set by consent) |
| `lang` | report language `ko`, `zh` or `en` (default: majority of your messages) |
| `include` | other config files merged before this one |

If a configured file cannot be read, collection stops instead of continuing unmasked.

## Limits

### What masking misses

Masking is a set of regular expressions plus your own word lists. It does not catch everything. Known misses:

- passwords shorter than 8 characters that have no key name in front of them
- credentials inside URLs (`user:pw@host`)
- Korean and Chinese mobile numbers separated by dots (`010.2222.3333`)
- `KEY=value` assignments whose key name is longer than 64 characters
- doubly escaped JSON (`{\\"password\\": ...}`)
- YAML block values (`password: |` followed by the value on the next line)
- one-letter flags with the value attached (`-p<value>`)
- flag values that start with `-` (`--password -abc...`)
- XML elements (`<password>...</password>`)
- `Authorization: Basic` headers
- proper nouns (customer, project or person names) that are not in a word list

Add your own word lists with `redact.lexicon_files` and your own regexes with `redact.pattern_files`. Both are fail-closed: a listed file that cannot be read stops collection.

### Detection accuracy

Chinese and English correction patterns have been checked only against synthetic samples, so their accuracy has not been measured. For the first two weeks, read each candidate yourself and reject the ones that do not match what happened. A rejected signal is not proposed again until it happens twice as often.

## Uninstall

Undo applied harnesses with `/harness-creator undo <id>`, then `/plugin uninstall harness-creator`.

## License

MIT

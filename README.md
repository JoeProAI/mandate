# Mandate

Learned least-privilege for unattended Hermes jobs.

A Hermes cron job that "triages my repo issues" gets the whole terminal and file toolset, with any
argument, every night, with nobody watching. Mandate watches what the job actually does, turns that into
a short, readable policy (tools, path globs, command shapes, hosts, a write budget), and once a human
approves it, blocks every call outside that policy inside the tool loop. Blocked calls land in a drift
inbox you can review and widen. Think AppArmor's "complain, then enforce", per cron job, at argument level.

## Why a plugin and not a prompt

- Enforcement happens in `pre_tool_call`, before the tool runs. A prompt can be ignored or overwritten by
  a prompt injection in an issue body; a hook vetoes the call regardless of what the model wants.
- It needs the identity of the running cron execution (`ctx.current_cron_execution()`), so each job gets
  its own policy and two jobs running at once never share one.
- It keeps state across runs (observations, versions, approvals, drift) in its own SQLite store.
- It parses arguments: shell segments, nested interpreters, resolved paths, parsed hostnames.

## Results

From the last `make prove` run (real Hermes loader, real cron execution identity, real tool dispatch;
details in [proof/PROOF.md](proof/PROOF.md), raw numbers in [proof/proof.json](proof/proof.json)):

<!-- proof:start -->
- Hermes: Hermes Agent v0.23.0 (2026.9.24) · stable · local 2bd378ed1b8d
- Tests: 109 passed, 0 failed
- Escape cases caught: 73/73
- Off-policy calls executed: 4/4 in observe mode, 0/4 with Mandate enforcing
- Legit calls that still succeeded while enforcing: 7/7
- `pre_tool_call` dispatch with Mandate enforcing: p50 1.8702 ms, p95 7.7697 ms

Proposal after 3 observed runs:

```yaml
# Mandate for nightly-triage: generated from 3 observed run(s).
# Review every rule. Nothing is enforced until `hermes mandate approve`.
mandate: 1
job: "nightly-triage"
workdir: "/tmp/mandate-int-35cqxnrv/repo"
observed_runs: 3
tools:
  - "read_file"  # seen 3x
  - "search_files"  # seen 3x
  - "terminal"  # seen 12x
  - "write_file"  # seen 3x
paths:
  read:
    - "src"  # seen 6x
    - "src/**"  # seen 3x
  write:
    - "reports/*.md"  # seen 3x
commands:
  - "git log --oneline -n"  # seen 3x
  - "git status --short"  # seen 3x
  - "grep -n -r"  # seen 3x
  - "ls"  # seen 3x
hosts: []
limits:
  max_writes_per_run: 1
# proposed v1 sha256:ddca914065cd  (12 rules from 3 run(s))
# next: hermes mandate approve nightly-triage
```

Leash Line after the poisoned run:

```
MANDATE ▸ nightly-triage  v1 sha256:ddca914065cd  ENFORCING
  tools    ▕▃▃▃▃██▃▃┃ 4 allowed
  paths    ▕██▅▅▅▅──┃ ▼#2 ▼#3  3 allowed · 2 blocked
  commands ▕████████┃ ▼#1 ▼#4  4 allowed · 2 blocked
  hosts    ▕────────┃ 0 allowed
  tools: terminal 12x, read_file 3x, search_files 3x
  paths: src 6x, reports/*.md 3x, src/** 3x
  commands: git log --oneline -n 3x, git status --short 3x, grep -n -r 3x
  4 run(s) observed · last block #4: terminal: bash -c 'cat src/app.py'
```
<!-- proof:end -->

## 60-second quickstart

```bash
hermes plugins install https://github.com/JoeProAI/mandate
hermes plugins enable mandate
hermes mandate watch nightly-triage      # observe mode: records, never blocks
# ...let the cron job run 3 or more times...
hermes mandate propose nightly-triage    # prints the proposed mandate with evidence counts
hermes mandate approve nightly-triage    # shows the diff, asks y/N, then enforces
hermes mandate status nightly-triage     # the Leash Line
```

## What a mandate looks like

The proposal generated in the last proof run is in the Results section above.

A command rule is the command head plus subcommand words, then the flags that are allowed; a call may
use a subset of them. `=<exact text>` allows one exact segment that Mandate otherwise refuses to reason
about (for example `=bash -c 'make test'`). The same observations always produce byte-identical YAML.

## The Leash Line

`hermes mandate status <job>` draws one track per rule family. The green band is what is allowed (bar
height is evidence), yellow diamonds are rules pending approval, red notches are blocked calls with their
drift id, yellow triangles are blocked calls that were widened. Truecolor on a dark terminal; `NO_COLOR`
and non-terminal output fall back to plain text.

The Leash Line from the last proof run is in the Results section above.

## Desktop page

Hermes Desktop gets a **Mandate** page (sidebar entry and a palette command "Mandate: open jobs"): the jobs
list, the Leash Line for the selected job, the diff to the pending version, and the drift timeline. It is
read-only on purpose; approving and widening stay in the terminal, where a human types them. The desktop
half ships disabled: turn it on in Capabilities, Plugins. Its data comes from `dashboard/plugin_api.py`,
which opens Mandate's SQLite file read-only and serves two GET routes on Hermes's own local API
(`/api/plugins/mandate/jobs` and `/api/plugins/mandate/jobs/<job>`), behind Hermes's normal auth.

## Commands

| Command | What it does |
|---|---|
| `hermes mandate watch <job>` | Start observing a job (by cron job name or id). Never blocks. |
| `hermes mandate propose <job>` | Compile observations into a pending mandate. Warns under 3 runs. |
| `hermes mandate approve <job> [--yes] [--observe]` | Show the diff, ask, store version + sha256, switch to enforce (or stay in observe with `--observe`). Refuses without a TTY unless `--yes`. |
| `hermes mandate undo <job>` | Reactivate the previously approved version (or none). |
| `hermes mandate drift <job>` | List blocked calls: id, time, run, failed rule, redacted call. |
| `hermes mandate widen <job> <drift-id> [--pattern]` | New pending version that allows that call. `--pattern` uses a broader rule and previews which past calls it would newly allow. Still needs `approve`. |
| `hermes mandate status <job>` | Leash Line. |
| `hermes mandate export <job> [--json]` | Print the active mandate. |
| `hermes mandate enforce <job>` / `observe <job>` | Switch mode. |
| `hermes mandate list` | Known jobs, mode, version, run count. |
| `hermes mandate doctor` | Database readable and writable, hashes match, hooks registered, mode. |
| `/mandate [status\|drift] [job]` | Same views in chat. |
| `/mandate scope <job>` / `/mandate unscope` | Bind the current chat session to a job's mandate (interactive scope). |
| tool `mandate_status` | Read-only, for the agent: active mandate and the calls blocked in this run, so it can finish the allowed part of the job. |

Config lives in `config.yaml` under `plugins.entries.mandate.settings`: `mode` (`observe` or `enforce`, default `observe`, used for
jobs without their own mode), `unattended_violation` (`block`, the only option), `interactive_violation`
(`approve` escalates to the Hermes approval prompt, or `block`), `observe_all_cron` (default `true`).

## How enforcement decides

- Shell: every segment of `;`, `&&`, `||`, `|`, `&`, newline chains is checked on its own. Command
  substitution, backticks, `eval`, `bash -c`, `sh -c`, pipes into a shell, `python -c` and other inline
  interpreters, `xargs`, `sudo`, `timeout`, `find -exec`, heredocs, subshells, variable expansion and
  anything unparseable are refused unless that exact segment is allowed. `env X=1 cmd` is unwrapped and
  `X` must be allowed. Redirect targets are writes.
- Paths: resolved with `realpath` against the call's working directory, then compared to the job workdir.
  `../`, absolute paths, `~` and symlinks that leave the workdir never match a relative glob.
- Hosts: the parsed hostname after the last `@`, lowercased, IDNA-encoded, legacy IPv4 forms normalized,
  non-default ports kept. `http://allowed.com@evil.com` is `evil.com`.
- Unknown tools (MCP or other plugins) are refused in enforce mode and recorded.
- Cron runs always get `block`: nobody is there to approve, and Mandate never returns `approve` from a
  cron run, so `approvals.cron_mode` cannot turn a violation into an approval. Interactive scopes may
  escalate to the normal Hermes approval prompt. Mandate never approves anything itself and never
  rewrites arguments.
- Fail closed: if Mandate itself errors while a job is in enforce mode (or it cannot tell the mode), the
  call is blocked. A changed policy whose sha256 no longer matches its approval is blocked too.

## Threat model and known limits

Mandate limits what a compromised or confused agent can do inside a cron job's tool calls. It is not a
sandbox.

- It sees tool arguments, not what a program does once it runs. An allowed `curl` that follows a redirect,
  or an allowed `make test` whose Makefile changed, is outside its view.
- Tools with opaque arguments (`execute_code`, unknown MCP tools) can only be allowed or refused whole.
  Inline code is stored as a sha256, never as text.
- Interactive sessions are only covered after `/mandate scope <job>`.
- Path checks resolve symlinks at decision time; a symlink swapped between check and use is not caught.
- Observed workloads that vary a lot (new file names every run) produce broader globs. Review proposals.
- Rules learned from a run that was already compromised would allow that behaviour. Observe on clean runs
  and read the proposal before approving.

## Disclosures

- No outbound network access. The plugin never opens a socket. The optional Desktop page reads through two
  read-only GET routes that Hermes itself serves under `/api/plugins/mandate/`.
- Reads and writes only its own data dir: `<HERMES_HOME>/plugin-data/mandate/mandate.db`.
- Runs no shell commands and starts no background processes or threads.
- Uses no credentials and sends no telemetry.
- Stores call shapes, not file contents. Raw argument values are redacted (tokens, keys, passwords,
  URL credentials) and truncated.
- In enforce mode it blocks tool calls, including when it fails internally.
- Stdlib only, no runtime dependencies.

## Uninstall and data removal

```bash
hermes plugins disable mandate     # hooks stop immediately; nothing else is left registered
hermes plugins remove mandate
rm -rf ~/.hermes/plugin-data/mandate   # observations, mandates, drift (use your HERMES_HOME)
```

Disabling leaves no residual effect: Mandate changes no Hermes config and wraps nothing.

## Proof and demo

```bash
make prove            # pinned Hermes v0.23.0, validate, tests, real-Hermes scenario, benchmark, proof/
./demo/run_demo.sh    # the 90-second story, deterministic, for screen recording (DEMO_PAUSE=0 to skip pauses)
scripts/live_demo.sh  # live run through Hermes cron with a real model: needs your own configured model key
```

## How this was built

AI-assisted: written by Devin (Cognition) from a brief by Grok Bot for Joe Gaither. All code is new since
Nov 5, 2026, built and tested against Hermes Agent v0.23.0. Milestones landed as PRs #1 to #6 in this repo.
API surprises and design notes are in [NOTES.md](NOTES.md).

## License

MIT

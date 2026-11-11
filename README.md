# Mandate

![Mandate: a leash for unattended Hermes jobs. Demo run 4: 0 of 4 off-mandate calls ran, 7 of 7 legit calls finished.](assets/hero-1600x800.png)

Learned least-privilege for unattended Hermes jobs.

Built on [Hermes](https://github.com/NousResearch/hermes-agent) by [Nous Research](https://nousresearch.com). Thanks to Nous for the Hermes Agent Plugin Hackathon.

Docs: [mandate-db2d83af-26675499.mintlify.site](https://mandate-db2d83af-26675499.mintlify.site)

![Leash Line for nightly-triage, mandate v1, demo data](assets/leash-line@2x.png)

Drawn Leash Line from the poisoned demo run in `demo/run_demo.sh`, labeled as demo data. The policy is enforcing in this frame. The no-color text version is under [Results](#results).

A Hermes cron job that "triages my repo issues" gets the whole terminal and file toolset, with any
argument, every night, with nobody watching. Mandate watches what the job actually does, turns that into
a short, readable policy (tools, path globs, command shapes, hosts, a write budget), and once a human
approves it, blocks every call outside that policy inside the tool loop. Blocked calls land in a drift
inbox you can review and widen. Think AppArmor's "complain, then enforce", per cron job, at argument level.

<!-- runtime:start -->
`make prove` wall clock on the build VM (validate, full pytest, real-Hermes scenario, hook benchmark; from [proof/proof.json](proof/proof.json)):

- Hermes v0.23.0: 33.7 s warm (Hermes already installed); 51.3 s cold (fresh Hermes clone and install)
- Hermes v0.23.1: 36.3 s warm (Hermes already installed); 51.6 s cold (fresh Hermes clone and install)
<!-- runtime:end -->

## Why this exists

Hermes users have asked for a way to restrict unattended cron jobs, and several open bugs show that Hermes's own `cron_mode: deny` can be bypassed or ignored. These titles were verified against the live issues on 10 Nov 2026:

- [#20984](https://github.com/NousResearch/hermes-agent/issues/20984) (open): *feat: add cron_mode: allowlist to restrict cron jobs to command_allowlist only.* A feature request for a cron-only allowlist so a job cannot use the rest of the toolset.
- [#60505](https://github.com/NousResearch/hermes-agent/issues/60505) (open): *Approval gate: fail-closed option for non-interactive contexts + permanent allowlist bypasses cron_mode deny.* No fail-closed path for unattended runs, and a permanent allowlist can bypass `cron_mode: deny`.
- [#144434](https://github.com/NousResearch/hermes-agent/issues/144434) (open): *[Bug]: command_allowlist (Always Allow) also exempts commands in cron runs with cron_mode: deny.* "Always Allow" still applies during cron, even when cron mode is deny.
- [#83626](https://github.com/NousResearch/hermes-agent/issues/83626) (open): *Cron command guards ignore cron_mode when HERMES_EXEC_ASK is inherited.* Inherited ask settings can make the cron guards ignore the configured cron mode.
- [#58673](https://github.com/NousResearch/hermes-agent/issues/58673) (open): *[Bug]: approvals.cron_mode: deny never applies to gateway-hosted cron/kanban sessions — each gated call blocks for approvals.gateway_timeout.* Gateway-hosted cron and kanban sessions do not get deny; they wait out the gateway timeout instead.

Mandate does not patch those Hermes bugs. It addresses the same pain from the plugin side: each job gets its own policy, cron violations are always blocked, and Mandate never returns `approve` from a cron run, so `approvals.cron_mode` cannot turn a violation into an approval.

Rivals such as Hermes Guard and agent-security-guard use hand-written fixed pattern lists. Mandate learns a per-job policy from observed runs, then a human approves it.

## How it compares

Two other catalog submissions guard `pre_tool_call`. Facts below are from their public source at the commit
each catalog PR pins, and from the catalog PRs.

| | Mandate | [Hermes Guard](https://github.com/elianjuan-g/hermes-guard/blob/6b8a32db62a4944f2b1c3757bd64e998b638e0c1/README.md) ([catalog PR](https://github.com/NousResearch/hermes-agent/pull/151899)) | [agent-security-guard](https://github.com/xMannixx/Agent-Security-Guard/blob/b1b923e6733f2a3dd7f0a79318d452ad395d8340/README.md) ([catalog PR](https://github.com/NousResearch/hermes-agent/pull/151810)) |
|---|---|---|---|
| Hooks | `pre_tool_call`, `post_tool_call` | `pre_llm_call`, `pre_tool_call` (catalog entry) | `pre_llm_call`, `pre_tool_call`, `transform_tool_result` (catalog entry) |
| Where the rules come from | Compiled from the job's own observed runs, then approved by a human | Built-in deterministic patterns for known prompt-injection shapes ([README](https://github.com/elianjuan-g/hermes-guard/blob/6b8a32db62a4944f2b1c3757bd64e998b638e0c1/README.md#L20-L26)) | Built-in deterministic transition rules (what untrusted content may become) plus configurable tool tiers ([rule table](https://github.com/xMannixx/Agent-Security-Guard/blob/b1b923e6733f2a3dd7f0a79318d452ad395d8340/README.md#L100-L130)) |
| Scope of a policy | One policy per cron job id | Same patterns for every session; it also tracks a per-session taint state ([hooks.py](https://github.com/elianjuan-g/hermes-guard/blob/6b8a32db62a4944f2b1c3757bd64e998b638e0c1/src/hermes_guard/hooks.py#L251-L257)) | A `guard.yaml` per Hermes profile plus a root-owned one in `/etc`; one audit trail per OS user ([plugin README](https://github.com/xMannixx/Agent-Security-Guard/blob/b1b923e6733f2a3dd7f0a79318d452ad395d8340/plugin/README.md#L100-L102)) |
| What `pre_tool_call` checks | Each argument against the approved policy: parsed shell segments, resolved real paths, parsed hostnames, write count; unknown tools denied | Serialized tool arguments against RCE/subshell, hidden-payload and credential-path patterns ([hooks.py](https://github.com/elianjuan-g/hermes-guard/blob/6b8a32db62a4944f2b1c3757bd64e998b638e0c1/src/hermes_guard/hooks.py#L251-L275)) | The call's kind and the chain before it: after web or search content enters a turn, shell, file write, install, config and external-send calls need confirmation; secrets in requests are denied ([rule table](https://github.com/xMannixx/Agent-Security-Guard/blob/b1b923e6733f2a3dd7f0a79318d452ad395d8340/README.md#L100-L130), [README](https://github.com/xMannixx/Agent-Security-Guard/blob/b1b923e6733f2a3dd7f0a79318d452ad395d8340/README.md#L401)) |
| On a violation | Cron: block. Interactive scope: Hermes approval prompt if configured, else block | Block ([hooks.py](https://github.com/elianjuan-g/hermes-guard/blob/6b8a32db62a4944f2b1c3757bd64e998b638e0c1/src/hermes_guard/hooks.py#L397-L416)) | Hermes approval prompt for confirmations, block otherwise ([plugin/\_\_init\_\_.py](https://github.com/xMannixx/Agent-Security-Guard/blob/b1b923e6733f2a3dd7f0a79318d452ad395d8340/plugin/__init__.py#L522-L541)) |
| Stated limits | [Threat model and known limits](#threat-model-and-known-limits) | "novel obfuscation that matches no pattern will pass" ([README](https://github.com/elianjuan-g/hermes-guard/blob/6b8a32db62a4944f2b1c3757bd64e998b638e0c1/README.md#L215)) | "not a sandbox"; unknown third-party tools stay free after outside content unless declared ([plugin README](https://github.com/xMannixx/Agent-Security-Guard/blob/b1b923e6733f2a3dd7f0a79318d452ad395d8340/plugin/README.md#L95-L99)) |

The three solve different problems and can run together: the other two look for injected content and risky
transitions; Mandate limits a known job to the calls it was approved to make.

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
- Hermes v0.23.0: validate passed; tests 112 passed, 0 failed; escape cases caught 73/73; off-policy calls executed 4/4 in observe mode, 0/4 with Mandate enforcing; legit calls succeeded while enforcing 7/7; `pre_tool_call` with Mandate enforcing p50 1.4507 ms, p95 1.9858 ms; `make prove` 33.7 s warm (Hermes already installed); 51.3 s cold (fresh Hermes clone and install)
- Hermes v0.23.1: validate passed; tests 112 passed, 0 failed; escape cases caught 73/73; off-policy calls executed 4/4 in observe mode, 0/4 with Mandate enforcing; legit calls succeeded while enforcing 7/7; `pre_tool_call` with Mandate enforcing p50 1.6983 ms, p95 2.1539 ms; `make prove` 36.3 s warm (Hermes already installed); 51.6 s cold (fresh Hermes clone and install)

Proposal after 3 observed runs (Hermes v0.23.1; the workdir is the scenario's throwaway repo):

```yaml
# Mandate for nightly-triage: generated from 3 observed run(s).
# Review every rule. Nothing is enforced until `hermes mandate approve`.
mandate: 1
job: "nightly-triage"
workdir: "/tmp/mandate-int-lplnjq5_/repo"
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
# proposed v1 sha256:c42f9e67f970  (12 rules from 3 run(s))
# next: hermes mandate approve nightly-triage
```

Leash Line after the poisoned run, no-color fallback:

```
MANDATE ▸ nightly-triage  v1 sha256:c42f9e67f970  ENFORCING
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
make prove HERMES_TAG=v0.23.1   # same on Hermes v0.23.1
make prove-all        # both releases, one after the other
./demo/run_demo.sh    # the 90-second story, deterministic, for screen recording (DEMO_PAUSE=0 to skip pauses)
scripts/live_demo.sh  # live run through Hermes cron with a real model: needs your own configured model key
```

## How this was built

AI-assisted: written by Devin (Cognition) from a brief by Grok Bot for Joe Gaither. All code is new since
Nov 5, 2026, built and tested against Hermes Agent v0.23.0 and v0.23.1. Milestones landed as PRs #1 to #8
in this repo. API surprises and design notes are in [NOTES.md](NOTES.md). Full docs are at
[mandate-db2d83af-26675499.mintlify.site](https://mandate-db2d83af-26675499.mintlify.site).

## License

MIT

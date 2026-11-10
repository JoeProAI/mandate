# Mandate build notes

Working notes for the Hermes Agent Plugin Hackathon entry. Pinned target: Hermes Agent v0.23.0
(tag `v0.23.0`, commit `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`, Nov 5 2026).

## Plan

- M1 scaffold, CI, this file. Inner package is `mandate_core/` (not `mandate/`): the repo root is itself a Python package (`__init__.py`), and when the checkout directory is named `mandate` pytest imports the root as `mandate`, shadowing an inner `mandate/` package.
- M2 pure-Python normalizer, compiler, matcher, adversarial tests.
- M3 Hermes wiring: hooks, SQLite store, CLI, `/mandate`, `mandate_status`, config_schema.
- M4 real-Hermes integration scenario (`nightly-triage`) and `make prove`.
- M5 README, catalog draft, demo script.
- M6 Desktop page only if M1-M5 are green by Tue Nov 10 6 PM ET.

## Hermes v0.23.0 API facts (read from source at the tag)

- There is no `hermes_cli/plugins_context.py` at v0.23.0. `PluginContext` lives in
  `hermes_cli/plugins.py` (`register_hook`, `register_tool`, `register_cli_command`,
  `register_command`, `current_cron_execution`, `on_unload`, `get_config`, `state`).
- `pre_tool_call` directives (`_get_pre_tool_call_directive_details`):
  `{"action": "block", "message": str}` (message required, becomes the tool result),
  `{"action": "approve", "message": str, "rule_key": str?}` (escalates to
  `tools.approval.request_tool_approval`), `{"action": "modify", "args": {...}}`.
  Precedence: any valid block wins over approve, regardless of plugin order.
- `_resolve_block_from_details`: approve fails closed if the gate errors, denies or times out.
  BUT `request_tool_approval` "honors `approvals.cron_mode`" under cron, so an `approve`
  directive could be auto-approved in cron if a user set that mode. Mandate therefore
  never returns `approve` in a cron run; it returns `block`.
- Hook callback exceptions are logged and skipped (call proceeds). Mandate catches its own
  exceptions in enforce paths and returns a block (fail closed). A `pre_tool_call`
  callback that exceeds `plugins.hook_callback_timeout` is failed closed by Hermes.
- `model_tools._pre_dispatch_guards` calls `hermes_cli.plugins._dispatch_pre_tool_call_hooks`
  once per tool call; a block returns `tool_error(message)` with status `blocked` and
  `error_type="plugin_block"`.
- `post_tool_call` fires after blocked, error and successful calls; kwargs include
  `tool_name, args, result, task_id, session_id, tool_call_id, turn_id, duration_ms,
  status, error_type, error_message`. Callbacks must accept `**kwargs`.
- `ctx.current_cron_execution()` returns `cron.execution_identity.CronExecution`
  (`job_id, job_name, execution_id, source, scheduled_instant, started_at, profile`) or
  `None`. It is a ContextVar bound by the scheduler; delegated subagents read `None`.
  Tests can bind it with `cron.execution_identity.enter_cron_execution(job, execution_id, record)`.
- Plugin data dir: `plugins.plugin_storage.plugin_data_dir(name)` ->
  `<HERMES_HOME>/plugin-data/<name>/` (documented in the plugin guide, "Store durable state").
- In `plugin.yaml`, `capabilities:` means privileged host surfaces (tools.override,
  llm.model_override) needing consent. Mandate declares none. Tools/hooks go in
  `provides_tools` / `provides_hooks`; `hermes plugins validate` fails on undeclared
  registrations. The catalog entry's `capabilities:` block mirrors those lists.
- `config_schema` entries: `{type, default, description, choices, label}`.
- Hermes refuses to build wheels outside Nix (`setup.py` guard). Install for tests with a
  checkout at the tag + `uv sync --frozen --python 3.14` (Hermes requires Python 3.14 deps;
  the lock already carries Hermes's own 3-day exclude-newer quarantine).
- Hermes's own test tree ships `tests/fakes/fake_llm_provider.FakeLLMServer` (scripted
  OpenAI-compatible loopback provider) and `tests/cron/conftest.owner_execution_runner`.
  These are usable from a checkout via PYTHONPATH for a no-key end-to-end cron run.
- Tool argument shapes: `terminal{command, workdir, background, ...}`, `read_file{path}`,
  `write_file{path, content}`, `patch{path, old_string, new_string | mode, patch}`,
  `search_files{...}`, `web_search{query}`, `web_extract{urls: [..]}`.
- `hermes plugins validate` scans every file under the plugin dir (only `.git`, `.venv`,
  caches excluded), so the Hermes checkout used for tests lives outside the repo
  (`~/.cache/mandate-hermes/v0.23.0`, or `$HERMES_SRC`).

## M2 design (policy engine, pure Python)

- `shell.py`: own POSIX-ish lexer (shlex loses quote context, so `echo ';'` and `;` look the same).
  Segments split on `; && || | |& & newline`. Opaque (verbatim-only) when: command or process
  substitution, backticks, ANSI-C quoting, heredoc/herestring, subshell, variable or brace
  expansion, nested shells, inline interpreter code (`python -c`, `node -e`, stdin), awk
  programs, command-running wrappers (`eval`, `xargs`, `sudo`, `timeout`, `nohup`, ...),
  `find -exec`, unparseable input. `env`/leading `X=1` assignments are unwrapped and the
  variable names must be allowed by the rule (`X= git log`).
- Command rule syntax: `[NAME=]... head [subcommand...] [-flags...]`; segment flags must be a
  subset of the rule's flags. `=<verbatim>` allows one exact opaque segment. `-*` and a
  trailing `*` word are only produced by `widen --pattern`.
- Paths: `realpath` against the effective cwd (terminal `workdir`, `cd`), then relative to the
  job workdir. Relative globs never match outside paths; outside paths need an absolute rule.
  Command args are checked as paths for file heads (`cat`, `rm`, ...) and for any arg that looks
  like an escape (`/`, `~`, `..`). Redirect targets are writes.
- Hosts: `urlsplit` hostname after the last `@`; reject backslash, percent, whitespace, control
  chars; lowercase, strip trailing dot, IDNA-encode; IPv4 legacy forms normalized with
  `inet_aton` (`2130706433` -> `127.0.0.1`); non-default port becomes part of the host.
- YAML is a fixed subset we render and parse ourselves (no PyYAML dependency); every scalar is
  JSON-quoted, rules are sorted, evidence lives in `# seen Nx` comments.

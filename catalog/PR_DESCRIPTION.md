# Add mandate to the plugin catalog

Adds `plugin-catalog/mandate.yaml` for https://github.com/JoeProAI/mandate (MIT).

## What it does

Mandate gives each cron job its own least-privilege policy, learned from what the job actually does.

1. Observe (default, never blocks): `pre_tool_call` records the shape of each call made under
   `ctx.current_cron_execution()`: tool, paths resolved against the workdir, shell segments (head and
   flags), hosts, read or write, writes per run. No file contents; raw values are redacted and truncated.
2. `hermes mandate propose <job>` compiles a deterministic, human-readable mandate with evidence counts.
3. `hermes mandate approve <job>` shows the diff and stores the version with its sha256.
4. Enforce: out-of-mandate calls in that job's runs are blocked with the failed rule and how to widen it.
   Cron runs always get `block`. Mandate never returns `approve` from a cron run, so `approvals.cron_mode`
   can never turn a violation into an approval.
5. Drift inbox, `widen`, `undo`, read-only `mandate_status` tool, `/mandate` slash command, Leash Line view.

## Catalog checklist

- `hermes plugins validate . --install-deps` passes on Hermes v0.23.0 (see `proof/PROOF.md` in the repo).
- Declared capabilities match registrations: tool `mandate_status`, hooks `pre_tool_call`, `post_tool_call`.
- No core monkeypatching; uses only `PluginContext` APIs and `plugins.plugin_storage.plugin_data_dir`.
- No runtime dependencies (stdlib only). No binaries or `.pyc`.
- No network, no telemetry, no credentials, no background processes, no shell commands run by the plugin.
- Never auto-approves; anything needing a human fails closed in cron.
- `requires_hermes: ">=0.23.0"` in both plugin.yaml and the entry.
- Risky behaviour disclosed in the README: in enforce mode it blocks tool calls, including on its own
  internal errors (fail closed).

## Proof

`make prove` from a clean clone installs pinned Hermes v0.23.0, validates the plugin, runs the tests and a
real-Hermes scenario (plugin loaded by the real loader, real cron execution identity, real tool dispatch),
and writes `proof/PROOF.md` and `proof/proof.json`.

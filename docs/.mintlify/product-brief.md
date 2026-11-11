# Product brief

## Description
Mandate is a Hermes Agent plugin that learns a least-privilege policy for each unattended cron job from the job's own observed runs. After a human approves that policy, Mandate blocks out-of-policy tool calls inside `pre_tool_call` and records them in a drift inbox.

## Primary audience
Hermes users and hackathon judges who run or review unattended cron jobs, and who need to understand how Mandate observes, proposes, approves, and enforces a per-job mandate. Assumed: the reader already uses or is evaluating Hermes Agent v0.23.0 or v0.23.1.

## Jobs to be done
1. Install the plugin, watch a job, propose a mandate, approve it, and switch to enforce.
2. Read a proposed mandate (tools, paths, commands, hosts, write budget, evidence counts) and decide whether to approve it.
3. Review a blocked call, widen one rule, and approve the new version, or undo to the previous approved version.
4. Rerun the public proof (`make prove`) and understand the threat model and known limits.

## Motivation
Hermes cron jobs inherit the agent's full toolset, and open Hermes issues document both a request for a cron allowlist and bugs where `cron_mode: deny` is bypassed or ignored. Other catalog guards (Hermes Guard, agent-security-guard) use hand-written fixed pattern lists. Mandate learns a different policy for each job from observed runs, then enforces it in the tool loop, where a prompt cannot reach.

Assumptions (not independently confirmed with Joe beyond the 2026-11-10 docs request): judges and operators are the primary readers; a public Mintlify site is the right docs home until Scriptorium visuals land; no Nous logos should appear, only a text credit.

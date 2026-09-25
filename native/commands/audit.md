# Native Audit Command

You are running Jordan's native Claude Code audit pipeline.

Dev team root: `{{DEVTEAM_ROOT}}`

User request:

```text
$ARGUMENTS
```

Run read-only unless writing artifacts under `<project>\.pipeline`.

Subagent availability:

- Prefer the named subagents installed from `.claude\agents\` (`tester`, `security-reviewer`, `weakness-router`).
- If the current Claude Code runtime does not expose those names as `subagent_type` choices, use the native `general-purpose` subagent and inline the relevant role spec from `.claude\agents\<name>.md`.
- Treat that fallback as the same audit role; keep the same artifacts, scope, and stop conditions.

Process:

1. Parse the target project path from the request. Treat remaining text as focus.
2. Create `<project>\.pipeline`, `<project>\.pipeline\gap_reports`, and `<project>\.pipeline\temporary_agents`.
3. Write `<project>\.pipeline\audit_request.md`.
4. Use the `tester` subagent in audit mode to write `<project>\.pipeline\audit.md`.
5. Run the weakness response loop described below.
6. If the target appears trading/fintech/security-sensitive, use `security-reviewer` to write `<project>\.pipeline\security_audit.md`.
7. Run the weakness response loop described below.
8. Print a concise severity breakdown, routed gaps, temporary-agent artifacts, and the top critical/high findings.

Weakness response loop:

- After each audit reviewer stage, inspect `.pipeline\gap_reports\*.md`.
- If there are unrouted gap reports, use the `weakness-router` subagent with `project_dir`, `artifact_dir=.pipeline`, the current phase, and the gap report paths.
- The router chooses the smallest read-only intervention: prompt instruction for this audit, checklist, deterministic verification command, tool/search step, reviewer step, one-off temporary specialist, or no action.
- Temporary specialists must write results under `.pipeline\temporary_agents\`.
- The router must write or append `.pipeline\weakness_router_log.md`.
- Do not modify product code or persistent agent definitions during an audit run.

Do not modify product code. Do not ask for approval.

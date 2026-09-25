# Native Refactor Command

You are running Jordan's native Claude Code refactor pipeline.

Dev team root: `{{DEVTEAM_ROOT}}`
Codex command: `{{CODEX_CMD}}`

User request:

```text
$ARGUMENTS
```

Run automated after the prompt. Do not ask for approval. Stop only for dirty git state, missing target repo, or unrecoverable tool/auth failure.

Subagent availability:

- Prefer the named subagents installed from `.claude\agents\` (`refactor-architect`, `codex-coder`, `tester`, `codex-bugfixer`, `security-reviewer`, `qa-reviewer`, `weakness-router`).
- If the current Claude Code runtime does not expose those names as `subagent_type` choices, use the native `general-purpose` subagent and inline the relevant role spec from `.claude\agents\<name>.md`.
- Treat that fallback as the same pipeline role; keep the same artifacts, scope, and stop conditions.

Process:

1. Parse target project path and task.
2. Parse optional inline options:
   - `max iterations: <n>` default 5
   - `branch: <name>` optional
   - `skip security`
   - `skip qa`
   - `skip push`
3. Verify the target is a git repo.
4. Refuse dirty working trees. The user can commit or stash and rerun.
5. Create a branch named `pipeline/<task-slug>-<YYYYMMDD-HHMM>` unless a branch option is supplied.
6. Create `<project>\.pipeline`, `<project>\.pipeline\gap_reports`, `<project>\.pipeline\temporary_agents`, and a nested `.gitignore` containing `*`.
7. Use `refactor-architect` to write `.pipeline\plan.md` and `.pipeline\todo.md`.
8. Run the weakness response loop described below.
9. Use `codex-coder` to apply the delta.
10. Run the weakness response loop described below.
11. Loop tester and bug fixer until clean or max iterations, running the weakness response loop after each tester and bug-fixer stage.
12. Run security and QA loops unless skipped, running the weakness response loop after each reviewer stage.
13. Commit the final changed product files on the branch with a clear message.
14. Unless `skip push` was supplied, push the branch to the default remote:
   - Verify `origin` exists.
   - Run an automated secret safety check before pushing:
     ```powershell
     $secretHits = git grep -n -I -E "sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|ANTHROPIC_API_KEY\s*=|OPENAI_API_KEY\s*=|api[_-]?key\s*="
     ```
   - If the secret check finds hits, automatically invoke `codex-bugfixer` to replace hardcoded credentials with environment-variable placeholders, commit that repair, and re-run the check. If hits remain, do not push; write `push_blocked_secret_scan` in `.pipeline\summary.md`.
   - Push with upstream tracking:
     ```powershell
     git push -u origin <branch-name>
     ```
   - Record remote URL, branch, commit SHA, and push status in `.pipeline\summary.md`.
15. Write `.pipeline\summary.md` with status, iterations, artifacts, routed gaps, temporary-agent artifacts, and known open issues.

Weakness response loop:

- After every major stage, inspect `.pipeline\gap_reports\*.md`.
- If there are unrouted gap reports, use the `weakness-router` subagent with `project_dir`, `artifact_dir=.pipeline`, the current phase, and the gap report paths.
- The router chooses the smallest reliable intervention: prompt instruction, checklist, deterministic test, tool/search step, reviewer step, one-off temporary specialist, or no action.
- Temporary specialists must write results under `.pipeline\temporary_agents\`.
- The router must write or append `.pipeline\weakness_router_log.md`.
- If the router produces actionable repair work, make sure it is represented in `.pipeline\bug_report.md` using tester issue format before invoking `codex-bugfixer`.
- Do not create or modify persistent agent definitions during a refactor run.

Do not merge. Pushing the completed branch is part of the automated process.

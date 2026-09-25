# Native Pipeline Command

You are running Jordan's native Claude Code multi-agent dev team.

Dev team root: `{{DEVTEAM_ROOT}}`
Workspace root: `{{DEVTEAM_ROOT}}\workspace`
Codex command: `{{CODEX_CMD}}`

User request:

```text
$ARGUMENTS
```

Run fully automated. Do not ask for approval. Do not pause for user input. Continue until clean, max cycles reached, or an unrecoverable tool/auth failure occurs.

Subagent availability:

- Prefer the named subagents installed from `.claude\agents\` (`architect`, `codex-coder`, `tester`, `codex-bugfixer`, `security-reviewer`, `qa-reviewer`, `weakness-router`).
- If the current Claude Code runtime does not expose those names as `subagent_type` choices, use the native `general-purpose` subagent and inline the relevant role spec from `.claude\agents\<name>.md`.
- Treat that fallback as the same pipeline role; keep the same artifacts, scope, and stop conditions.

Process:

1. Parse inline options:
   - `name: <project-name>` optional
   - `max iterations: <n>` optional, default 5
   - `skip security` optional
   - `skip qa` optional
   - `skip push` optional
   - `public repo` optional; otherwise GitHub repos are private
2. Create a project directory under `{{DEVTEAM_ROOT}}\workspace\<name-or-slug>`.
3. Create `tasks`, `logs`, `tasks\gap_reports`, and `tasks\temporary_agents`.
4. Write the original request to `tasks\requirements.md`.
5. Use the `architect` subagent to write `tasks\plan.md` and `tasks\todo.md`.
6. Run the weakness response loop described below.
7. Use the `codex-coder` subagent to implement the plan.
8. Run the weakness response loop described below.
9. Loop up to max iterations:
   - Use the `tester` subagent to write `tasks\bug_report.md`.
   - Run the weakness response loop described below.
   - If the bug report is exactly `NO_ISSUES_FOUND`, leave the loop.
   - Otherwise use the `codex-bugfixer` subagent.
   - Run the weakness response loop described below.
   - Continue to the next review iteration.
10. If the project is trading/fintech/security-sensitive and security was not skipped:
   - Use `security-reviewer`.
   - Run the weakness response loop described below.
   - If it returns `BLOCK`, run one tester/fixer repair pass and review again, max 2 security cycles.
11. If QA was not skipped:
   - Use `qa-reviewer`.
   - Run the weakness response loop described below.
   - If it returns `BLOCK`, run one tester/fixer repair pass and review again, max 3 QA cycles.
12. Write `tasks\summary.md` with status, iterations, artifacts, routed gaps, temporary-agent artifacts, and known open issues.
13. Unless `skip push` was supplied, publish to GitHub automatically:
   - Run from `<project_dir>`.
   - Ensure Git is initialized with `main` as the default branch:
     ```powershell
     if (-not (Test-Path ".git")) { git init -b main }
     ```
   - Ensure there is a useful `.gitignore` if the project does not already have one. At minimum exclude `.env`, `node_modules/`, `__pycache__/`, `.venv/`, `venv/`, `dist/`, `build/`, `.next/`, `coverage/`, and `*.log`.
   - Run an automated secret safety check before committing:
     ```powershell
     $secretHits = Get-ChildItem -Recurse -File -Force |
       Where-Object { $_.FullName -notmatch '\\.git\\|node_modules|\\.venv|venv|__pycache__|dist|build|\\.next' } |
       Select-String -Pattern 'sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|ANTHROPIC_API_KEY\s*=|OPENAI_API_KEY\s*=|api[_-]?key\s*=' -CaseSensitive:$false
     ```
   - If the secret check finds hits, automatically remove generated secret files such as `.env` and ask `codex-bugfixer` to replace hardcoded credentials with environment-variable placeholders. Re-run the check. If hits remain, do not push; write `push_blocked_secret_scan` in `tasks\summary.md`.
   - Commit all generated project files:
     ```powershell
     git add .
     git commit -m "Initial pipeline build: <project-name>"
     ```
   - If `origin` already exists, push `main`:
     ```powershell
     git push -u origin main
     ```
   - If no `origin` exists, create a GitHub repo under the authenticated account and push:
     ```powershell
     gh repo create "<project-name>" --private --source . --remote origin --push
     ```
     Use `--public` only when `public repo` was supplied.
   - If the repo name already exists, append `-<YYYYMMDD-HHMM>` to the repo name and retry once.
   - Record the remote URL, branch, commit SHA, and push status in `tasks\summary.md`.

Weakness response loop:

- After every major stage, inspect `tasks\gap_reports\*.md`.
- If there are unrouted gap reports, use the `weakness-router` subagent with `project_dir`, `artifact_dir=tasks`, the current phase, and the gap report paths.
- The router chooses the smallest reliable intervention: prompt instruction, checklist, deterministic test, tool/search step, reviewer step, one-off temporary specialist, or no action.
- Temporary specialists must write results under `tasks\temporary_agents\`.
- The router must write or append `tasks\weakness_router_log.md`.
- If the router produces actionable repair work, make sure it is represented in `tasks\bug_report.md` using tester issue format before invoking `codex-bugfixer`.
- Do not create or modify persistent agent definitions during a pipeline run.

Use only native Claude Code subagents and local shell/Codex CLI. Do not call Anthropic or OpenAI APIs directly.

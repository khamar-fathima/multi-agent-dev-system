# Native Claude Code Pipeline

This is the **second** way to run the multi-agent pipeline, alongside the original Python orchestrator. It lives entirely inside Claude Code as user-level subagents + slash commands — no separate Python process, no JSON config, no `.bat` launcher.

## What's installed

```
~/.claude/agents/ and <dev-team>/.claude/agents/
├── architect.md            Claude opus — plans greenfield projects
├── refactor-architect.md   Claude opus — plans deltas against existing code
├── codex-coder.md          delegates to Codex CLI (new code OR applying a delta)
├── tester.md               Claude opus — reviews; handles greenfield, refactor, and audit modes
├── codex-bugfixer.md       delegates to Codex CLI (applying fixes)
├── weakness-router.md      Claude sonnet — routes gap reports to checklists, tests,
                            tools, reviewers, or one-off temporary specialists
├── security-reviewer.md    Claude opus — security & compliance audit; runs only on
                            trading/fintech projects (detected by keyword or
                            "## Risk Management" section in plan.md); audits against
                            tasks/trading_rules.md + general security; returns CLEAN/BLOCK
                            with max 2 cycles
└── qa-reviewer.md          Claude opus — final senior-dev sign-off pass, runs once
                            after the tester↔fixer loop converges; returns
                            APPROVE / APPROVE_WITH_NOTES / BLOCK with capped escalation

~/.claude/commands/ and <dev-team>/.claude/commands/
├── pipeline.md             /pipeline — build a new project from requirements
├── audit.md                /audit — read-only review of an existing project
└── refactor.md             /refactor — modify an existing project (with git safety)

C:\Users\User\AppData\Roaming\npm\codex.cmd   Codex CLI 0.133.0

C:\Users\User\Documents\Development\multi-agent-dev-system\
├── CLAUDE.md               shared standards — read by every agent
├── tasks/lessons.md        persistent lessons across runs
└── workspace/<name>/       /pipeline projects land here

<your existing project>/.pipeline/    /audit and /refactor artifacts land here
```

## The three commands

### `/pipeline <requirements>` — build something new

```
/pipeline build a CLI todo app in Python with SQLite supporting add/list/done/delete
```

Orchestrates **architect → codex-coder → loop(tester ↔ codex-bugfixer)** in a fresh folder at `C:\Users\User\Documents\Development\multi-agent-dev-system\workspace\<derived-name>\`. Artifacts in `tasks/`. Nothing of yours is touched.

If any stage writes `tasks/gap_reports/*.md`, the `weakness-router` subagent routes it before the pipeline continues. Temporary specialist outputs land in `tasks/temporary_agents/`.

Inline overrides: `name: todo-cli`, `max iterations: 8`, `skip push`, `public repo`.

When the run completes, `/pipeline` initializes git if needed, commits the generated project, creates a private GitHub repo with `gh repo create`, and pushes `main`. Use `public repo` for a public repository or `skip push` to leave the project local. A lightweight secret scan runs before commit/push; if generated secrets remain after automatic repair, the push is blocked and the status is written to `tasks/summary.md`.

### `/audit <path> [focus]` — review an existing project, no changes

```
/audit path/to/project
/audit path/to/webapp auth flow, checkout handlers
```

Runs the **tester** subagent against the existing project, plus the **security-reviewer** for trading/fintech projects (detected by keyword or trading-related filenames in the codebase). No architect, no coder, no fixes. Findings land in `<project>/.pipeline/audit.md` (and `<project>/.pipeline/security_audit.md` for trading projects). The orchestrator prints a severity breakdown and the top critical/high issues across both reports.

Use for: "what's broken before I touch this?", security pass before shipping, second opinion on a PR.

### `/refactor <path> <task>` — modify an existing project

```
/refactor path/to/api add a /healthz endpoint that returns the build SHA
/refactor . migrate the auth middleware from session cookies to JWT
```

Full loop on an existing repo: **refactor-architect** (delta plan) → **codex-coder** (applies the delta) → loop(**tester** focused on changed files ↔ **codex-bugfixer**).

**Git safety net (required):**
- Refuses to run on a dirty working tree — commit or stash first
- Auto-creates a branch like `pipeline/<task-slug>-<YYYYMMDD-HHMM>` and works there
- Auto-commits whatever Codex produced when the loop ends, so the work is one reviewable commit on a branch
- Pushes the branch to `origin` automatically, but does not merge

Artifacts: `<project>/.pipeline/` (plan.md, todo.md, bug_report.md, summary.md, task.md, gap_reports/, temporary_agents/). That folder gets its own `.gitignore` containing `*` so artifacts never get accidentally committed.

Inline overrides: `max iterations: 8`, `branch: my-branch-name`, `skip push`.

When the refactor completes, `/refactor` commits the final changes and pushes the branch to `origin` with upstream tracking. It does not merge. A lightweight secret scan runs before push; if generated secrets remain after automatic repair, the push is blocked and the status is written to `.pipeline/summary.md`.

## Pipelines at a glance

```
/pipeline:                                          /refactor:
  architect (Claude)                                  git safety + auto-branch
    → codex-coder (Codex)                             refactor-architect (Claude)
    → loop(tester ↔ codex-bugfixer)                     → codex-coder (Codex)
    → [if trading/fintech] security loop                → loop(tester ↔ codex-bugfixer)
        (max 2 cycles):                                 → [if trading/fintech] security loop
        security-reviewer (Claude)                          (max 2 cycles):
        if BLOCK → re-run tester↔fixer pass                 security-reviewer (Claude)
        else (CLEAN) → continue to QA                       if BLOCK → re-run tester↔fixer pass
    → QA loop (max 3 cycles):                               else (CLEAN) → continue to QA
        qa-reviewer (Claude)                            → QA loop (max 3 cycles):
        if BLOCK → re-run tester↔fixer pass                 qa-reviewer (Claude)
        else (APPROVE/APPROVE_WITH_NOTES) → ship            if BLOCK → re-run tester↔fixer pass
                                                            else → auto-commit on branch

/audit:
  tester (Claude) — read-only, no fixes
```

The security loop only runs on trading/fintech projects (orchestrator detects via keyword match in the user's input, a `## Risk Management` section in plan.md, or — for refactor — file-name patterns like `*broker*`, `*order*`, `*trading*` in the existing codebase). Non-trading projects skip straight from tester convergence to QA.

## Temporary weakness routing

Every agent can report a capability gap by writing a structured markdown file under `tasks/gap_reports/` or `.pipeline/gap_reports/`. After each major stage, the pipeline checks for unrouted reports and invokes `weakness-router`.

The router chooses the smallest useful response: a prompt instruction, checklist, deterministic test, tool/search step, reviewer pass, one-off temporary specialist, or no action. Temporary specialists are not installed as persistent agents; their specs and findings are written under `temporary_agents/`, and any actionable repair work is converted into tester-format bug report items for Codex to fix.

## Security reviewer — what it adds (trading/fintech only)

The security-reviewer is a specialist pass that runs between tester convergence and QA on trading/fintech projects. Its checklist IS [tasks/trading_rules.md](tasks/trading_rules.md): no hardcoded secrets, order idempotency + retries + circuit breakers, structured audit logging on every order event, dry-run flag that exercises the real path, Decimal (not float) on money, market-data validation, fail-loud error handling on orders. Plus general security: dependency CVE scan, auth-flow trace, input validation at API boundaries.

Verdicts:
- **CLEAN** — passes the security/compliance bar. Continue to QA.
- **BLOCK** — N issues. The reviewer writes them to `bug_report.md` in tester format; the pipeline runs the tester↔fixer loop again to clear them, then security re-reviews.

**Capped at 2 cycles.** If still BLOCK after 2, the pipeline ships (or commits, for refactor) with status `security_blocked_at_max_cycles` surfaced. QA is skipped in that case — the security verdict is the truth of the run; QA on top would muddy the signal.

What it catches that the tester doesn't:
- "Pricing math uses float" — landmine on the next decimal
- "Order placement without idempotency key" — network retry duplicates trades
- "Audit log is `print()` statements" — not compliant, not routable
- "Dry-run flag short-circuits early instead of exercising the full path"
- "`.env` is in the repo" or "logs include credentials"
- Empty/vague Risk Management section in plan.md
- Dependency CVEs the tester wouldn't run a manifest scan against

What it deliberately doesn't do:
- Re-find general bugs (tester already did)
- Flag style/architecture/polish (QA's territory)
- Add new rules — `trading_rules.md` is the binding checklist

Opt out per-run with `skip security` in your `/pipeline` or `/refactor` message.

## QA reviewer — what it adds

The QA reviewer is the **senior dev / shippability** lens, distinct from the tester's per-bug lens. Runs once after the tester↔fixer loop converges. Verdicts:

- **APPROVE** — meets the bar, ship it
- **APPROVE_WITH_NOTES** — meets the bar; nice-to-haves logged in `qa_review.md` but pipeline ships
- **BLOCK** — has real problems that would bite in production OR misses the original requirements. Pipeline runs another full tester↔fixer pass on the QA blockers, then reviews again.

**Capped at 3 QA cycles.** If still BLOCK after 3, the pipeline ships anyway with status `qa_blocked_at_max_cycles` surfaced in the rundown — better to see a known-blocked result than spin forever.

What the QA reviewer catches that the tester doesn't:
- "Tests pass but doesn't actually meet the user's ask" — checks original requirements
- Architectural coherence across the whole change (tester looks file-by-file)
- "Deferred items in FOLLOWUPS.md that should have been done"
- "Smoke test asserts 200 but never checks the response actually contains data"
- Cross-file consistency, naming drift, dead code in the new files

What it deliberately doesn't do:
- Re-find bugs (the tester already did)
- Flag style preferences (per CLAUDE.md: simplicity over polish)
- Write any code (read-only; findings go in markdown)

Opt out per-run with `skip qa` in your `/pipeline` or `/refactor` message if you want the old behavior.

## How Codex is invoked

The `codex-coder` and `codex-bugfixer` subagents use the Bash tool to shell out to the Codex CLI:

```bash
"<prompt text>" | "C:/Users/User/AppData/Roaming/npm/codex.cmd" exec \
  -m gpt-5.5 \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  -C "<project_dir>" \
  -o "<project_dir>/tasks/codex_last_message.md" \
  -
```

Notes on the flags:
- `-m gpt-5.5` / `--model gpt-5.5` is mandatory for Codex coding and bug-fixing calls
- `-C <dir>` sets Codex's working root — keeps it inside the project workspace
- `--dangerously-bypass-approvals-and-sandbox` is required for unattended runs (Codex would otherwise prompt for command approvals)
- `-o <file>` writes Codex's final message to a file the subagent reads back
- `-` reads the prompt from stdin, which is more reliable than passing multi-line PowerShell prompts as a single argv string
- Auth: Codex CLI uses your existing ChatGPT login (`codex login status` → "Logged in using ChatGPT")

## Why two systems?

| Aspect | Native Claude Code (`/pipeline`) | Python orchestrator (`start.bat`) |
|---|---|---|
| Run inside Claude Code? | yes | no — separate process |
| Architect model | Claude (sonnet/opus configurable per-subagent) | Claude (config.json) |
| Tester model | same | same |
| Coder | Codex CLI with `gpt-5.5` (delegated via subagent) | Codex CLI with `gpt-5.5` |
| Setup | none beyond Codex CLI install | needs `pip install -r requirements.txt`, env vars |
| Headless/CI runs? | no — needs Claude Code | yes |
| Best for | day-to-day "build me X" inside the IDE | batch / unattended / when you want OpenAI-API fallback |

You can keep both or delete one. They write to the same `workspace/<name>/` location and use the same `CLAUDE.md` + `tasks/lessons.md`, so a project started by one is fully readable by the other.

## Editing the agents

Each subagent is a single markdown file with YAML frontmatter. To tune behavior:

- Change a model: edit the `model:` field in the agent's frontmatter (`opus` | `sonnet` | `haiku`)
- Change a tool allowlist: edit `tools:` in the frontmatter
- Tweak instructions: edit the body of the markdown file
- Change the codex flags or path: edit `codex-coder.md` and `codex-bugfixer.md`; do not remove the GPT 5.5 model override from coding or bug-fixing calls

After editing native agent or command source files, run `install-native.ps1` again. The installer writes both user-level Claude Code files and a project-local `.claude/` mirror, which keeps native subagents visible to Claude Code sessions started from this repo.

Some non-interactive Claude Code harnesses expose only the built-in `general-purpose` subagent even when project subagent specs are present. The slash commands explicitly fall back by launching `general-purpose` with the relevant `.claude/agents/<role>.md` spec inlined, preserving the same artifacts and role boundaries.

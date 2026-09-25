# Lessons Learned

This file is a persistent record of corrections, surprises, and rules-of-thumb learned across pipeline runs. Each agent should review this at the start of its turn and append to it after any correction.

## Format

```
## YYYY-MM-DD — <short title>
**Agent**: architect | coder | tester | bug_fixer
**Trigger**: what went wrong or what feedback was given
**Rule**: the rule to follow next time
**Why**: the reason the rule matters
```

---

## 2026-05-22 — Audit the fix site for NEW defects, not just whether the fix matches its claim
**Agent**: tester
**Trigger**: On 2026-05-17 a fix landed for an unbounded-accumulation issue in an account-leaderboard component's dedup HashSets. The fix added `if (Count > MAX) Clear()` immediately after `Add(eventKey)`. Tester + QA passed on iteration 1. On 2026-05-22 a fresh audit caught that the Clear wipes the just-added key, causing the surrounding `if (!set.Add(eventKey)) continue;` dedup pattern to recount the same event on every subsequent recompute pass — counters inflate without bound. The bug was introduced by the fix; the tester didn't see it because the review was framed as "verify the cap is enforced", not "audit the fix site for new defects".
**Rule**: When reviewing a fix, audit the fix site as if reviewing fresh code. Ask "does this fix do anything ELSE?", not just "does this fix do what it claims?" Especially watch: (a) mutations that interact with adjacent dedup/cache state, (b) ordering changes (Clear-after-Add vs Clear-before-Add, Remove-during-iteration), (c) side effects on collections the surrounding code reads back from via boolean return values. Cross-check the fix against the *caller's* loop semantics, not just the local site.
**Why**: A fix that "passes" tester review but introduces a regression is worse than the original issue being open — the team believes the issue is closed and stops looking. In this case the project's 10-day harness would have shipped with progressively inflated leaderboard rollups for 5+ days before the divergence became visible enough to investigate.

---

## 2026-05-28 — Stream CLI prompts through stdin on Windows
**Agent**: coder | bug_fixer
**Trigger**: The futures-bot-v2-update run failed before implementation because Codex was launched through `codex.CMD` with an 11k+ prompt as a positional command-line argument; Windows returned `The command line is too long.` on both coder attempts.
**Rule**: For subscription CLI backends, pass large model prompts through stdin or prompt files, never as argv. Use Codex `exec ... -` with `subprocess.run(input=prompt)` and keep only short flags on the command line. Add regression tests that assert long prompt content is absent from argv.
**Why**: Real project requirements/plans routinely exceed the Windows/npx wrapper command-line limit. Retrying cannot fix it because the process never reaches Codex; every coder/fixer stage with a realistic prompt will fail deterministically until prompt transport is changed.

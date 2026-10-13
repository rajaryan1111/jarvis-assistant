# Jarvis / ULTRON Development Workflow

This is the project's practical adaptation of relevant ideas from [obra/superpowers](https://github.com/obra/superpowers) (MIT). It records practices; it does not install a coding-agent plugin, add runtime dependencies, or copy the upstream skill library.

Read [AGENTS.md](../AGENTS.md) for the short rules that apply to all repository work.

## 1. Choose the right amount of process

- **Tiny, unambiguous task:** do it directly, inspect the diff, and run a focused check.
- **Non-trivial feature or behavior change:** clarify the user outcome, constraints, non-goals, and observable acceptance criteria; inspect existing architecture; write a concise plan before editing.
- **Multi-component or security-sensitive change:** document boundaries, failure modes, rollback, and verification. Split independent work only where interfaces and tests are clear.
- Do not make process itself the deliverable. Apply YAGNI and reuse existing patterns.

## 2. Start from repository reality

Before editing, record the repository and current branch, HEAD/base commit, whether the working tree is clean, relevant docs/source/tests/configuration, and baseline status. Never overwrite existing user changes. If the baseline fails, report the exact failures before deciding whether to proceed. Treat remembered branch names, SHAs, patches, and other agents' summaries as leads to verify, not truth.

## 3. Define the work before implementing

For non-trivial changes, write:
- **Goal:** one sentence describing user-visible behavior.
- **Non-goals:** what must not change.
- **Acceptance criteria:** observable behavior, including failure cases.
- **Files/interfaces:** expected code and test locations.
- **Risk review:** permissions, secrets, external effects, retries, duplicate actions, cancellation, malformed inputs, and privacy.
- **Verification:** exact commands and what result constitutes success.

Prefer a short task plan over an oversized design for a small patch. For broad projects, use one plan per independently testable subsystem.

## 4. Test-driven development

For bug fixes and behavior changes, use the red/green/refactor loop:

1. Write one focused test for the intended behavior.
2. Run it and confirm it fails for the expected reason (not because of a typo, import error, or broken fixture).
3. Implement the smallest production change that satisfies the test.
4. Run the focused test and confirm it passes.
5. Run the full relevant test suite and inspect regressions.
6. Refactor only while tests remain green.

Tests should exercise observable behavior, not merely assert mock call counts. Use mocks for genuinely external boundaries, and retain integration/live checks for behaviors that mocks cannot prove.

**Exception:** generated code, throwaway prototypes, configuration-only edits, or cases where a test harness is unavailable may need a different order. State the reason and provide the strongest reproducible check; do not pretend a skipped test-first cycle happened.

## 5. Systematic debugging

When something fails:
1. Read the full error, stack trace, logs, and relevant output.
2. Reproduce the issue and note exact inputs/environment.
3. Inspect recent changes and trace the data across component boundaries.
4. State one falsifiable hypothesis and the evidence supporting it.
5. Run the smallest experiment that distinguishes that hypothesis.
6. Add a regression test, fix the root cause, and verify the original symptom.

Do not pile multiple speculative fixes together. If several different fixes fail or expose recurring coupling, stop and reconsider the design before adding another workaround.

For provider/runtime/tool failures, trace request → provider response/parsing → validation → tool registry/policy → tool result → recovery → final report. Keep diagnostics content-minimizing and redact secrets.

## 6. Review the change

Before calling a task complete:
- inspect the entire diff and Git status;
- verify only intended files changed and no generated artifacts, keys, recordings, tokens, or personal data were added;
- review error paths, malformed/untrusted inputs, retries, idempotency, cancellation, permissions, and false-success paths;
- check that documentation distinguishes implemented behavior from planned work;
- use an independent reviewer for major features or security-sensitive changes when available;
- verify reviewer findings against the code and tests rather than applying feedback blindly.

For agent delegation, give each worker a narrow task, exact interfaces, acceptance tests, and file ownership. Inspect and independently verify the produced diff; an agent's completion message is not evidence.

## 7. Verification before completion claims

Run fresh checks against the final changed tree. Use the repository's available test, lint, compile/build, and secret-scan commands as appropriate. Report exact commands and actual outcomes, test counts/failures/skips when available, which checks were mocked/deterministic/live/on real macOS hardware, what was not run and why, and remaining issues.

A previous run, passing linter, code inspection, or agent summary is not proof that the final code passes. Never say a bug is fixed unless the original failure is now covered by a passing check or clearly state the limitation.

## 8. Branches, worktrees, and integration safety

- Isolate feature work in a branch/worktree when it protects active work; first confirm the location is safe and ignored by Git.
- Keep existing branches and pull requests intact. Do not switch, rewrite, delete, force-push, merge, or overwrite unrelated work.
- Local commits, remote pushes, PR creation, merges, releases, deployment, and publication are separate actions. Perform only actions the user explicitly authorized.
- Before integration, identify the intended base branch and rerun required checks on the exact tree being integrated.
- If the user has asked only to prepare work for review, leave it unmerged and report the branch and diff.

## 9. Jarvis-specific safety and quality checks

For agent/runtime/provider/tool changes, specifically test:
- invalid/malformed model responses and bounded recovery;
- no repeated execution of a successful side-effecting tool during retries;
- policy/confirmation cannot be bypassed by model text, voice, or retrieved content;
- cancellation and concurrency do not leave inconsistent state;
- errors and logs do not expose API keys or sensitive content;
- completion claims match tool/test evidence;
- budgets, retries, output size, and execution time remain bounded.

For voice, wake-word, speech recognition/synthesis, camera, and macOS automation, distinguish unit-test results from tests on the actual target Mac. Evaluate Hindi, English, Hinglish, Romanized Hindi, and Devanagari separately where relevant.

## 10. Reusable task template

Copy and fill for non-trivial work:

~~~markdown
## Goal
## Non-goals
## Current behavior / evidence
## Acceptance criteria
- [ ] ...
## Files and interfaces
## Risks and failure modes
## Test-first plan
1. Test to add and expected failing result
2. Minimal implementation
3. Focused test
4. Full relevant suite / lint / build / secret scan
## Review checklist
## Git / approval boundary
## Result
- Changed:
- Verified:
- Not verified:
- Remaining risks:
- Next step:
~~~

## 11. Source and attribution

Superpowers is available under the MIT license. This document is an original, project-specific summary of general engineering practices; it does not bundle upstream skill files. If substantial upstream code or text is copied in a future change, preserve the applicable copyright and license notice.

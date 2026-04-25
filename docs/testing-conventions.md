# Team Testing Conventions

## Why business-like test prompts

Multi-agent behavior should be validated under realistic workload semantics.  
If tests use obviously synthetic labels (for example `flood-1`, `flood-2`), agents may optimize for the artifact instead of the intended workflow.

## Recommended approach

1. Create a dedicated sandbox project for stress and protocol tests.
2. Use business-like user goals as test input.
3. Keep the same tools and orchestration flow as production.
4. Record expected and observed behavior at the envelope level.

## Anti-pattern

Do not run protocol stress tests directly in a production-like project thread.  
The historical `opc / main-t6c4z` incident showed that noisy synthetic interaction can trigger wrong coordination decisions (for example unnecessary member dismissal).

## Checklist

### Before test

- Confirm target project is sandbox-only.
- Confirm orchestrator prompt includes latest safety constraints.
- Define pass/fail criteria for each scenario.

### During test

- Keep test prompts business-like and coherent.
- Capture key envelope IDs and tool results.
- Stop and label the run if side effects deviate from test intent.

### After test

- Export concise evidence summary (scenario, expected, observed, verdict).
- Reset sandbox members/tasks if needed.
- Feed protocol gaps back to `docs/team-mvp-plus-plus-todolist.md`.

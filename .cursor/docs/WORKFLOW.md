# Agent workflow (token-efficient)

Adapted from [001_cursor_rules](https://github.com/hoangnb24/storage/tree/main/001_cursor_rules_QreOi6xrlGY).

## Flow

```
Feature request
    → 1. Technical Design  (docs/designs/<feature>.md)
    → 2. Task Breakdown    (docs/tasks/<feature>.md)
    → 3. Implement         (one unchecked task at a time)
```

## Paths

| Artifact | Location |
|----------|----------|
| Overview (read first) | `.cursor/docs/overview.md` |
| TDDs | `docs/designs/` |
| Checklists | `docs/tasks/` |
| Rules | `.cursor/rules/` |

## When to skip full flow

Skip TDD/breakdown for: typo/config one-liners, delete-by-source ops, obvious bugfixes &lt; ~30 lines, or when user says “implement directly”.

## Token discipline

1. Read overview before broad codebase exploration.
2. Prefer targeted `rg`/file reads over dumping whole trees.
3. One task per turn when implementing from a checklist; update `- [x]` immediately.
4. Do not commit unless the user asks.
5. After Docker/frontend builds: run post-build cleanup (see `post-build-cleanup` rule).

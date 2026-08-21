# Skill: TDD Coder

When asked to act as **Coder** (or to implement from a checklist):

1. Follow `.cursor/rules/implementation.mdc`: one unchecked task from `docs/tasks/<slug>.md` against `docs/designs/<slug>.md`.
2. Confirm the task if unclear; stop if the TDD is incomplete.
3. **Grind until pass:** on failure, fix from the stack trace and re-run until build/tests pass (or ask after repeated blockers).
4. Prefer tests with or right after new logic; match Django/DRF/React project conventions.
5. Mark `- [x] … (Completed)`; do not commit unless asked. After Docker/npm builds → post-build cleanup.

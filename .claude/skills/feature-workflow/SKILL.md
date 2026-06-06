---
name: feature-workflow
description: The mandatory dev cycle for godpy features — plan, study prior art, implement in library idiom, test, self-review, and file the issue/PR via gh. Use at the start of any feature.
---

# Feature workflow

1. **Plan.** Study `google/adk-samples` and similar agent repos for the pattern.
   Write the plan before code. (`/plan-feature`)
2. **Discover libraries** with the `lib-researcher` subagent — reuse over build.
3. **Implement** matching the chosen library's own style.
4. **Test:** unit (`tests/unit/`) + system (`tests/system/`). Run via the
   `test-runner` subagent.
5. **Self-review** with the `adk-reviewer` subagent. Write honest feedback.
6. **gh:**
   - Mid-feature need discovered → `gh issue create --label enhancement` with a
     proposed approach.
   - Done → `gh pr create` using the PR template (summary, tests, self-feedback).

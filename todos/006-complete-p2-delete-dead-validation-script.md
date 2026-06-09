---
name: delete-dead-validation-script
status: pending
priority: p2
issue_id: "006"
tags: [code-review, dead-code, cleanup]
dependencies: []
---

## Problem Statement

`scripts/validate_meetings_derivation.py` opens `src/_data/meetings.json` on line 11 — a file deleted as part of the Phase 2 refactor. The script raises `FileNotFoundError` immediately on any execution attempt. It is not referenced from any Makefile, CI workflow, or AGENTS.md task. The module docstring says "Run BEFORE deleting meetings.json", which is now contradicted by reality. A future developer who finds it in `scripts/` has no way to know it is permanently broken without reading both the script and git history.

## Findings

- **File**: `scripts/validate_meetings_derivation.py` (63 lines)
- **Error on run**: `FileNotFoundError: [Errno 2] No such file or directory: 'src/_data/meetings.json'`
- **Not referenced**: AGENTS.md, CI workflows, Makefile — none reference this script
- **Migration it validated**: Complete and irreversible
- **Source**: maintainability-reviewer M01

## Proposed Solution

Delete the file. The migration technique (compare derived JS output vs. old JSON using a validation script) is captured in the commit message for `01b911a` and in the plan document (`docs/plans/2026-06-08-001-refactor-automate-nav-derive-meetings-data-plan.md`).

If any element of the technique is worth preserving for future reference, add a one-paragraph note to AGENTS.md in the "Architecture" section describing what was done and pointing to the relevant commit.

**Effort**: Trivial  
**Risk**: Zero (the script cannot run)

## Acceptance Criteria
- [ ] `scripts/validate_meetings_derivation.py` is deleted from the repository
- [ ] `git ls-files scripts/validate_meetings_derivation.py` returns empty
- [ ] AGENTS.md or commit message captures the intent if deemed valuable

## Work Log
- 2026-06-08: Identified by maintainability-reviewer (M01) in ce:review of nav/data-derivation refactor

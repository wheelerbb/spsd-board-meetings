---
name: glossary-replacement-doubles-title-when-alias-is-suffix-of-correct
status: complete
priority: p1
issue_id: "029"
tags: [data-integrity, pipeline, bugfix, live-incident]
dependencies: ["019", "024"]
---

## Problem Statement

Found live, in production, immediately after the terms-registry-rebuild branch (todos/019) was
merged to `main` and its first real `--bucket` pipeline run executed (2026-11-17, run
[44200882781](https://github.com/wheelerbb/spsd-board-meetings/actions/runs/44200882781)):
`glossary_utils.apply_replacements` (added during todos/024's fix, the word-boundary-safe
replacement that closed the earlier non-word-boundary `content.replace()` exposure) has no guard
against an alias that is itself a literal substring of its own `correct` value.

Concretely: the glossary carries `{"correct": "Dr. Jo Prince", "aliases": ["Jo Prince", ...]}`.
Text that already correctly reads "Dr. Jo Prince" still matches the bare `\bJo Prince\b` pattern
(the boundary between the period/space before "Jo" and the "J" is a valid `\b`), and
`apply_replacements` substituted the matched "Jo Prince" with the full `correct` value "Dr. Jo
Prince" — producing "Dr. Dr. Jo Prince" in place of already-correct text.

## Findings

- **Live exposure, confirmed and already fixed**: 42 occurrences across 14 real, already-deployed
  meeting pages, all from two glossary entries whose alias happens to appear, title-free, as a
  trailing substring of their own `correct` value:
  - `"Jo Prince"` within `"Dr. Jo Prince"` — 32 of the 42 occurrences, 9 meetings.
  - `"Rebecca Stern"` within `"Dr. Rebecca Stern"` — the other 10 occurrences, 5 meetings (one
    meeting, `2026-02-09`, had just the one).
  - Fixed directly (de-duplicated "Dr. Dr. " → "Dr. " via a literal find/replace confirmed
    one-to-one against the 42 counted occurrences, re-verified zero "Dr. Dr." instances remain
    corpus-wide) and the root cause fixed in the same pass (see Proposed Solutions). No other
    title-doubling shape (`Mr. Mr.`, `Ms. Ms.`, `Mrs. Mrs.`, `Superintendent Superintendent`, etc.)
    was found corpus-wide — this incident's actual blast radius was exactly these two entries.
- **Five more glossary entries carry the same latent risk shape** (an alias/misspelling that is a
  literal substring of its own `correct` value), not yet triggered only because their title-free
  form hasn't yet appeared, already-titled, in generated content: `"Harold Blaney"` within `"Mr.
  Harold Blaney"`, `"Michael Nalli"` within `"Mr. Michael Nalli"`, `"Angela Atkinson Duina"`
  within `"Dr. Angela Atkinson Duina"`, `"Diane Lang"` within `"Small School Principal Diane
  Lang"`, `"Sarah Glenn"` within `"SPHS principal Sarah Glenn"`. The root-cause fix (below)
  protects all of these the same way the two confirmed cases are protected — not entry-specific.

## Proposed Solutions

Already implemented. `apply_replacements` (`scripts/glossary_utils.py`) now checks, before
substituting any given match, whether `correct` is already present in the content at the position
implied by where `alias` sits inside `correct` (via `correct.find(alias)`) — covering the alias
appearing as a prefix, infix, or suffix of `correct`, not just the two suffix cases that actually
fired live. If `correct` is already there, the match is left untouched; otherwise the normal
replacement proceeds. Two new regression tests added to `test_glossary_utils.py`
(`test_alias_already_covered_by_correct_is_not_doubled`,
`test_alias_not_yet_titled_is_still_corrected`) — 168 tests total, all passing. The 14 already-
corrupted live meeting pages were hand-fixed (not re-run through the LLM pipeline — this was a
pure mechanical de-duplication of an exact-known string, verified 1:1 against the counted
occurrences) and will build/deploy clean on the next push.

## Acceptance Criteria

- [x] Root cause identified and confirmed against real, live content (not just a theoretical gap)
- [x] Fix applied in `apply_replacements`, generalized to prefix/infix/suffix alias-within-correct
      relationships, not just the two confirmed cases
- [x] Regression tests added; full suite (168 tests) passing
- [x] All 14 already-corrupted live meeting pages corrected
- [x] Corpus-wide re-scan confirms zero remaining title-doubling artifacts of this shape

## Work Log

- 2026-11-17: found immediately after todos/019's branch was merged to `main` and its first real
  `--bucket` pipeline run deployed live. The run itself succeeded (both GitHub Actions jobs green,
  166→168 tests unaffected since this was found after the run, not during it), but a post-deploy
  content check turned up this corruption on 14 meeting pages. Root-caused, fixed in
  `apply_replacements`, regression-tested, and the 14 live pages corrected in the same pass — see
  Findings and Proposed Solutions above. To be committed and pushed as a follow-up fix/redeploy,
  separate from the original rebuild merge commit.

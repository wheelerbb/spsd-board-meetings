---
name: glossary-discovery-false-positive-aliases
status: complete
priority: p1
issue_id: "024"
tags: [glossary, data-quality, pipeline, bugfix]
dependencies: ["019"]
---

## Problem Statement

Found during Stage 3 of the terms-registry-rebuild validation plan (todos/019): the full-corpus
glossary rebuild dry run (99 meetings, Stage 2's 1684-entity candidate registry as match target)
surfaced two real bug classes in `glossary_utils.py`'s discovery logic that hadn't shown up in any
smaller-sample test, because they only manifest at full-corpus scale against a comprehensive
registry. Both are now fixed (see Findings/Work Log) — originally reported without a fix, per the
same stop-and-ask posture used for the native-Google-Doc regex bug in todos/023, then fixed on
explicit user go-ahead.

**Severe, high-volume: common-word fuzzy collisions.** A registry entity whose canonical spelling
happens to be an ordinary short English word (`"State"`, `"Board"`, `"District"`, `"Maine"`,
`"Brown"`, `"Small"` — all real `place`/`organization` entities: a U.S. state, a school board, a
school district, towns/schools named Brown/Small) fuzzy-matches against *other* unrelated common
words via `_tokens_match`'s length-scaled ratio fallback (`glossary_utils.py`), since nothing in
that fallback excludes a short *entity* token from being generic rather than a proper noun. Example
candidate-glossary entries this run produced:
- `"State"` misspellings: `["start", "taste", "started", "statute", "stage", "slate", "Start"]`
- `"Board"` misspellings: `["broad"]`; `"District"` misspellings: `["distinct"]`
- `"Maine"` misspellings: `["machine", "marine"]`; `"UMaine"` misspellings: `["main", "Main", "marine"]`
- `"Brown"` (Brown Elementary) misspellings: `["grown", "Drown", "Drown School", "blown"]`
- `"Small"` (Small Elementary) misspellings: `["smell", "shall"]`

This is dangerous specifically because `_enforce_glossary_pass` applies these as a raw
`content.replace(alias, correct)` — not word-boundary-aware. A live alias of bare `"main"` →
`"UMaine"` would corrupt every occurrence of that substring anywhere in a file, including inside
unrelated words (`domain`, `remain`, `maintain`, `mainly`), not just the standalone word. Confirmed
impact if this candidate glossary were ever enforced: **326 replacement occurrences across 58 of
99 meetings** (measured directly against `rebuild_scratch/stage3_enforcement_preview.json`) — this
is not a rare edge case, it's the majority of the corpus.

**Smaller, lower-volume: unsplit multi-person aliases.** `discover_term_candidates`'s person path
anchors on `child.get('speaker')` via `_speaker_names_in_meeting`, which — unlike the
topic-parenthetical path a few lines below it — never runs the mentioned name through
`_split_speaker_list`. When a timeline child's `speaker` field itself holds a joined co-presenter
string (`"Gretchen McCloy & Bryna Latham"`, `"Henry Risch, Matthew Frey, Ryan Gildart, Gedeao
Buanza-David"`), the whole joined string becomes the alias for whichever one name
`name_matching.surname_match` happened to anchor on — e.g. `correct: "Bryna Latham", alias:
"Gretchen McCloy & Bryna Latham"`. Enforcing this would delete Gretchen McCloy's name entirely
wherever that joined phrase appears. Confirmed impact: **8 occurrences across 7 meetings** (same
preview file) — real, but two orders of magnitude smaller than the common-word collision class
above.

## Findings

- Both bugs are in `discover_term_candidates`/`_tokens_match`/`_speaker_names_in_meeting`
  (`glossary_utils.py`), not in anything touched by this session's Drive-extraction work
  (todos/019's extractor_version/processing/throttle changes) — pre-existing logic, first
  exercised at real full-corpus scale by this Stage 3 dry run.
- Neither bug ever reached the live `glossary.json` or any live `.njk` file — Stage 3 is a dry
  run, and the fix landed before any live cutover. Confirmed via a separate live-exposure check
  (read-only): the live `glossary.json` (11 entries) is 100% `type: person`, so `_tokens_match`'s
  place/org/program-only code path has never had a registry entity to misfire against in
  production yet, and none of its aliases/misspellings contain a joined multi-person string.
  Clean on both counts — see the live-exposure note below.
- **Fixed, root cause**: added `name_matching.is_dictionary_word(token)` (backed by
  `scripts/common_words.txt`, a filtered, checked-in copy of the macOS/BSD `words(5)` list — no
  new pip dependency, portable to CI), with a lightweight suffix-stripping fallback (`-s`, `-ed`,
  `-ing`) since a dictionary headword list doesn't carry every inflection (`"started"` isn't its
  own headword; only `"start"` is — this was caught by the first re-run, which still produced
  `"State"`/`"started"`, and closed by the suffix fallback). `_tokens_match` now rejects a window
  token that's itself a recognized dictionary word, which `is_boundary_variant` (prefix/suffix
  only) didn't cover. Deliberately scoped to `_tokens_match` only — **not** applied to
  `fuzzy_match`/`fuzzy_surname_match` (person-name matching), since a misheard surname commonly
  *does* coincide with an obscure real word (`"Holeman"` for `"Holman"` is itself a dictionary
  entry) without being any less genuine a mishearing; applying the guard there would have broken
  that already-tested case.
- **Fixed, defense-in-depth**: added `glossary_utils.apply_replacements(content, replacements)`,
  a word-boundary-safe (`\b`-anchored) replacement, replacing `_enforce_glossary_pass`'s raw
  `content.replace()`. Means even a legitimate short alias can no longer corrupt a substring
  inside an unrelated longer word, independent of whether discovery ever produces one.
  `run_stage2_rebuild`/Stage 3's own preview script now uses the same helper to count what
  enforcement would actually change, instead of a separate raw-substring count.
- **Fixed, multi-person alias class**: `_split_speaker_list` now also splits on `&` (previously
  comma-only), and `_speaker_names_in_meeting` now runs a timeline child's `speaker` field through
  it too (previously only the topic-parenthetical path did). Side effect, confirmed legitimate on
  inspection: a 5th real person ("Coach Kahill"/misspelling "Coach Cahill") was newly discovered
  from a topic parenthetical that used to be swallowed whole as "Coach Cahill & Board" — the
  split now resolves "Coach Cahill" on its own.
- **Re-verified against real full-corpus data** (re-running Stage 3 end to end with the fix):
  candidate glossary entries dropped from 89 to 73; **all 6 place-type entries and 2 of 5
  organization-type entries (`State`, `Board`) are gone entirely**; the 5 person-entries that
  existed *only* because of the multi-person-conflation bug are gone (their names now resolve
  correctly via the split, producing no false observation); one new, legitimate person entry
  (`Coach Kahill`) appeared. Directly confirmed: **0 of the originally-flagged 334 occurrences
  (326 common-word + 8 multi-person) remain** in the re-run's enforcement preview. Remaining
  place/org/program entries (`SPSDSEA`/`SPSEA`, `HiSET`/`HighSET`, `PreK program`,
  `Read-a-thon`/`readathon`) are all genuine, non-dictionary-word corrections — confirmed each
  survives specifically *because* its alias isn't a real English word.
- One acceptable, minor side effect: `"Donor's Choose"`/`"Donors Choose"` (a unicode-curly-quote
  vs. plain spelling mismatch) no longer gets flagged, since `"Donors"` strips to the dictionary
  word `"donor"`. Judged fine to lose — the registry's own `"correct"` spelling here already
  carries a stray curly apostrophe artifact, so this was "one questionable spelling replacing
  another," not a clean fix, even before this change.
- **Live-exposure check, incidental finding — separate and pre-existing, NOT caused by either bug
  above or by this session's Terms Registry work**: scanning live `.njk` content for the same
  class of corruption surfaced glued-together proper nouns (`SOUTHPORTLANDHIGHSCHOOL`,
  `SouthPortlandMiddleSchool`, `SouthPortlandSchool`, `ElementaryBoundariesandConfigurations
  SteeringCommittee`, `PortlandPrideParade`) embedded directly in generated timeline/summary text
  across 6 meeting files. Traced via `git log -S` to July–August 2026 commits (`6765076`,
  `56e10b8`) — months before the Terms Registry project, the Nov 13 glossary-corruption incident,
  and this session's work. Also directly ruled out the Nov 13 incident itself as a cause: that
  corruption (626 garbage glossary entries, a *different* bug already fixed in `3a88b2c` —
  shared-trailing-word ratio matching, not either bug here) was committed to `glossary.json` but
  never actually enforced against any existing meeting content before being reverted ~2 hours
  later — the only file changed in that window was a brand-new, content-free meeting stub. This
  older glued-word pattern is real but out of scope here; not fixed, not otherwise investigated
  beyond confirming it predates and is unrelated to this todo. See todos/025.

## Proposed Solutions

Implemented (see Findings): dictionary-word guard in `_tokens_match`, word-boundary-safe
enforcement (`apply_replacements`), and `&`-splitting in `_split_speaker_list`.

## Acceptance Criteria

- [x] Both bug classes identified and quantified against real full-corpus data
- [x] Direction decided and a fix applied (dictionary-word guard, word-boundary-safe enforcement,
  `&`-splitting)
- [x] Re-run of Stage 3 confirms the 326+8 occurrences no longer appear in the enforcement preview
- [x] Unit tests added/updated (`test_name_matching.py`, `test_glossary_utils.py`); full suite
  passing
- [x] Live-exposure check: live `glossary.json`/`.njk` content confirmed clean of both bug classes

## Work Log

- 2026-11-17: found during Stage 3 (full-corpus glossary rebuild dry run, todos/019). Reported,
  not fixed — stop-and-ask posture per todos/023 precedent, since a fix changes discovery-logic
  behavior beyond Stage 3's own scope.
- 2026-11-17: fixed on explicit user go-ahead. Root cause (dictionary-word guard, with a suffix-
  stripping fallback for inflected forms found on the first re-run), defense-in-depth
  (word-boundary-safe enforcement), and the multi-person alias class (`&`-splitting) all fixed;
  re-verified against a full Stage 3 re-run (0/334 originally-flagged occurrences remain); 13 new
  unit tests added, full suite (166 tests) passing. Live-exposure check confirmed both bugs are
  clean in production (live glossary.json is 100% person-type; the unrelated Nov 13 incident was
  never enforced). Incidentally found a separate, pre-existing (July–August 2026) glued-word
  content issue unrelated to either bug — spun off to todos/025, not fixed here.

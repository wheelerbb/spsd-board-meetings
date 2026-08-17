# Topic Taxonomy

Reference for the topic tagging system used across the SPSD Board Meeting Archive.

---

## Tag Extraction Criteria

Topics are extracted by Gemini using each meeting's transcript summary bullets plus (when cached) an official agenda excerpt — either `generate_tags()` for one meeting at a time, or `batch_tag_all_meetings()` for the whole corpus at once (see "Single vs. Batch Tagging" below). Both apply the same rules:

- **3–5 tags per meeting** — not a comprehensive list; just the headline threads
- **Reuse existing tags** before coining new ones
- **Time-binding follows the 3-category framework below** — the main lever against topic fragmentation, so read it carefully before adding a new tag by hand
- **Subject, not process stage** — see below

### Time-Binding: 3 Categories

Whether a topic should carry a year/FY suffix — and how granular that suffix should be — depends on what kind of issue it is:

| Category | Rule | Examples |
|---|---|---|
| **A. Cyclical** | Always time-bound, but **one tag per fiscal year, period** — never split a single FY's cycle into phase-specific tags. All of a fiscal year's budget development, approval votes, referendum, and mid-year challenges collapse into one tag. Use a small, fixed vocabulary for the theme name itself — `Budget`, `Audit`, `School Calendar`, `Collective Bargaining` — never a new theme word like `Financial Deficit` for what's really that year's budget cycle. | `FY27 Budget` (not `FY27 Budget Development` / `FY27 Budget Approval` / `FY27 Budget Challenges`), `FY26 Audit`, `Administrators Collective Bargaining FY25-28` |
| **B. Discrete initiatives** | Bind to a year/era only when the same *type* of event could plausibly recur later and future disambiguation will matter. Otherwise leave undated. | `Superintendent Search`, `Kaler Closure`, `Student Cell Phone Policy 2026` |
| **C. Evergreen** | Never time-bound. Standing, systemic domains discussed continuously with no natural end. | `Reconfiguration`, `Special Education`, `Transportation`, `Facilities`, `Equity`, `Board Governance`, `Student Mental Health` |

**The most common failure mode** is treating category A like category B — minting a new tag for every sub-event of the same fiscal year's budget cycle instead of reusing one FY-scoped tag. When tagging, check `src/_data/all_topics.json` for an existing same-FY tag on the same cyclical theme before creating a new one.

**Fiscal year convention:** named for its ending year, runs July 1 – June 30 (`FY27` = July 2026–June 2027). Budget deliberation for a fiscal year typically happens in the spring before it starts (roughly January–June) — a budget-related tag from that window usually belongs to the *upcoming* fiscal year, not the one about to end. Occasionally the district starts planning a later fiscal year's budget unusually early (e.g. discussing next year's budget in December instead of the following spring) — when content clearly indicates that, the tag should reflect the fiscal year actually being discussed, not whatever a rigid calendar cutoff would imply. This is deliberately left to the model's judgment rather than a date formula — a formula was tried and reverted after checking it against real data (see `docs/prompts.md` §2–3).

### Subject, Not Process Stage

A tag names the *subject* being discussed, never *what stage of board deliberation it's at*. Words like `Development`, `Presentation`, `Discussion`, `Update`, `Overview`, `Review`, `Consideration`, `Process`, `Session`, `Report`, `Debate`, `Announcement`, `Revision(s)`, `Projection` describe where something is in the meeting cycle, not what the topic is — a tag should never end with one of these (`"Cell Phone Policy Development"` → `"Cell Phone Policy"`). Words naming a specific, substantive *outcome* — `Closure`, `Adoption`, `Referendum`, `Resignation`, `Appointment` — are fine to keep; they're not process-stage words and often carry real distinguishing meaning (e.g. `Kaler Elementary Closure` is a valid, specific topic — `Closure` isn't a modifier to strip).

This rule is enforced two ways: the tagging prompt asks for it directly, and `_strip_modifiers()` in `post_process.py` deterministically strips a trailing modifier word from every generated tag regardless of whether the model complied — added after a full-corpus batch run violated the rule extensively despite the prompt instruction being present. See `docs/prompts.md` §2–3 for the exact word lists.

### Single vs. Batch Tagging

- **Single-meeting** (`generate_tags()`) — one Gemini call per meeting, used for the daily incremental case (tagging a newly-processed meeting). Grounded by an explicit `allowed_tags` list with each tag's current-status description, plus the previous meeting's tags as a continuity hint.
- **Batch** (`batch_tag_all_meetings()`, run via `--retag`) — one Gemini call covering every meeting at once, so the model can see an entire multi-meeting narrative (e.g. a school closure spanning several meetings) and tag it consistently instead of reconstructing continuity one meeting at a time. Reserved for periodic full recalibration — never run automatically in CI, since full-corpus retagging on every cron run risks topic churn on a public site. See `docs/prompts.md` §3 for the full prompt and a documented case where batch mode fragmented worse than single-meeting tagging on its first attempt, before the rules above were tightened.

### Evidence Linkage

Both tagging paths report which of a meeting's own summary bullets support each tag, alongside the tag itself — stored as a `topic_evidence: {tag: [bullet_index, ...]}` frontmatter field next to `topics:`. `synthesize_topic()` reads this directly instead of fuzzy-matching a tag's name against bullet text after the fact (`_evidence_match()`, still used only as a fallback for meetings tagged before this field existed). See `docs/prompts.md` §2/§3/§5 and `todos/012`.

### What does NOT become a topic tag

| Type | Example | Why |
|------|---------|-----|
| Organization names | SPESPA, SPTA | Use a topic describing what the board *did* with them, e.g. "Service Employees Contract" |
| Routine agenda boilerplate | Personnel, Finance, Contracts, Budget | Blacklisted — too generic to be meaningful navigation |
| One-off procedural items | Adjournment vote, consent agenda | Not substantive enough |

---

## Blacklist

Single source of truth: `src/_data/topic_blacklist.json` — both `src/_data/meetings.js` (filters the meeting-list filter chips) and `scripts/post_process.py` (`_drop_blacklisted()`, filters `all_topics.json`/page generation itself) load from this one file. Don't hardcode the list in either place (todos/005). Exact match only — a more specific tag containing one of these words untouched (e.g. `Board Governance Ethics Policy` is not dropped, only bare `Board Governance` is).

```
Personnel, Contracts, Finance, Budget, Policy, Board Governance
```

`Personnel`, `Contracts`, `Finance`, `Budget`: appear in nearly every meeting and add no navigational value. `Policy`, `Board Governance`: added after both kept recurring as bare, low-value tags in batch-mode output despite being listed as bad examples in the tagging prompt — see `docs/prompts.md` §3's "Known failure mode."

---

## Current Topics

`src/_data/all_topics.json` is the live, authoritative list (100+ entries and growing) — it is not hand-maintained and this doc does not attempt to mirror it. Use the topic index page (`/topics/`) or `cat src/_data/all_topics.json` to see the current set.

---

## How to Add a Topic

1. Tag one or more meetings with the new topic name in their `.njk` frontmatter `topics:` list
2. Run `post_process.py` — the topic is added to `all_topics.json` automatically and a summary is synthesized

## How to Retire a Topic

1. Remove the topic from all meeting `.njk` `topics:` frontmatter entries
2. Delete the topic's entry from `src/_data/topic_summaries.json`
3. Delete the topic's hash from `scripts/topic_hashes.json`
4. Run `post_process.py` — the topic drops from `all_topics.json`

## Merging Topics

Two topics covering the same ground (e.g. "Student Mental Health" and "Mental & Behavioral Health"), or a fiscal year's cycle fragmented into phase-specific tags, are a symptom that `generate_tags()` didn't have enough context to reuse the right existing tag. The fix is at the source, not a recurring cleanup step: `generate_tags()` is fed each existing tag's `current_status` blurb (not just its bare name) plus the topics discussed at the board's most recent prior meeting, so it can recognize a continuing thread instead of coining a duplicate. Use `--dry-run-tag SLUG` to test whether a prompt/context change resolves a specific case before committing to it.

If a duplicate still gets through, either fix it the manual way from "How to Add/Retire a Topic" above (re-tag the affected meetings, delete the loser's summary/hash entries), or — if the pattern is widespread rather than a one-off — run `post_process.py --retag` for a full batch recalibration (see "Single vs. Batch Tagging" above). There is currently no automated consolidation tool separate from re-tagging; fully automated merging (no human review step) may be worth revisiting once there's a track record of confidence in the tagging quality, but that's not built today.

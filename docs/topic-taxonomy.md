# Topic Taxonomy

Reference for the topic tagging system used across the SPSD Board Meeting Archive.

---

## Tag Extraction Criteria

Topics are extracted from each meeting's summary bullets by Gemini (`generate_tags()` in `post_process.py`) using these rules:

- **3–5 tags per meeting** — not a comprehensive list; just the headline threads
- **Reuse existing tags** from `src/_data/topics.json` before coining new ones — Gemini is seeded with the current list at tagging time
- **Time-binding follows the 3-category framework below** — this is the main lever against topic fragmentation, so read it carefully before adding a new tag by hand

### Time-Binding: 3 Categories

Whether a topic should carry a year/FY suffix — and how granular that suffix should be — depends on what kind of issue it is:

| Category | Rule | Examples |
|---|---|---|
| **A. Cyclical** | Always time-bound, but **one tag per fiscal year, period** — never split a single FY's cycle into phase-specific tags. All of a fiscal year's budget development, approval votes, referendum, and mid-year challenges collapse into one tag. | `FY27 Budget` (not `FY27 Budget Development` / `FY27 Budget Approval` / `FY27 Budget Challenges`), `FY26 Audit`, `Administrators Collective Bargaining FY25-28` |
| **B. Discrete initiatives** | Bind to a year/era only when the same *type* of event could plausibly recur later and future disambiguation will matter. Otherwise leave undated. | `Superintendent Search`, `Kaler Closure`, `Student Cell Phone Policy 2026` |
| **C. Evergreen** | Never time-bound. Standing, systemic domains discussed continuously with no natural end. | `Reconfiguration`, `Special Education`, `Transportation`, `Facilities`, `Equity`, `Board Governance`, `Student Mental Health` |

**The most common failure mode** is treating category A like category B — minting a new tag for every sub-event of the same fiscal year's budget cycle instead of reusing one FY-scoped tag. When tagging, check `src/_data/topics.json` for an existing same-FY tag on the same cyclical theme before creating a new one.

### What does NOT become a topic tag

| Type | Example | Why |
|------|---------|-----|
| Organization names | SPESPA, SPTA | Use a topic describing what the board *did* with them, e.g. "Service Employees Contract" |
| Routine agenda boilerplate | Personnel, Finance, Contracts, Budget | Blacklisted — too generic to be meaningful navigation |
| One-off procedural items | Adjournment vote, consent agenda | Not substantive enough |

---

## Blacklist

Defined in `post_process.py → TOPIC_BLACKLIST`. Any tag matching these terms is silently dropped when building `topics.json`:

```
Personnel, Contracts, Finance, Budget
```

Rationale: these appear in nearly every meeting and add no navigational value.

---

## Current Topics

`src/_data/topics.json` is the live, authoritative list (100+ entries and growing) — it is not hand-maintained and this doc does not attempt to mirror it. Use the topic index page (`/topics/`) or `cat src/_data/topics.json` to see the current set.

---

## How to Add a Topic

1. Tag one or more meetings with the new topic name in their `.njk` frontmatter `topics:` list
2. Run `post_process.py` — the topic is added to `topics.json` automatically and a summary is synthesized

## How to Retire a Topic

1. Remove the topic from all meeting `.njk` `topics:` frontmatter entries
2. Delete the topic's entry from `src/_data/topic_summaries.json`
3. Delete the topic's hash from `scripts/topic_hashes.json`
4. Run `post_process.py` — the topic drops from `topics.json`

## Merging Topics

Two topics covering the same ground (e.g. "Student Mental Health" and "Mental & Behavioral Health"), or a fiscal year's cycle fragmented into phase-specific tags, are a symptom that `generate_tags()` didn't have enough context to reuse the right existing tag. The fix is at the source, not a recurring cleanup step: `generate_tags()` is fed each existing tag's `current_status` blurb (not just its bare name) plus the topics discussed at the board's most recent prior meeting, so it can recognize a continuing thread instead of coining a duplicate. Use `--dry-run-tag SLUG` to test whether a prompt/context change resolves a specific case before committing to it.

If a duplicate still gets through, fix it the manual way from "How to Add/Retire a Topic" above (re-tag the affected meetings, delete the loser's summary/hash entries) — there is currently no automated consolidation tool. Fully automated merging (no human review step) may be worth revisiting once there's a track record of confidence in the tagging quality, but that's not built today.

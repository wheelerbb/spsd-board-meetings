# Topic Taxonomy

Reference for the topic tagging system used across the SPSD Board Meeting Archive.

---

## Tag Extraction Criteria

Topics are extracted from meeting transcripts by Gemini (`process_transcripts.py`) using these rules:

- **3–5 tags per meeting** — not a comprehensive list; just the headline threads
- **Specific and time-bound** where possible: `"Student Cell Phone Policy 2026"` rather than `"Cell Phones"`, `"FY26 Transportation Challenges"` rather than `"Transportation"`
- **Standing systemic issues** may use short, undated names when the issue spans years and has no natural end: `"Reconfiguration"`, `"Kaler Closure"`, `"Equity"`
- **Reuse existing tags** from `src/_data/topics.json` before coining new ones — Gemini is seeded with the current list at processing time

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

## Current Topics (29)

Sorted by most recent meeting activity.

| Topic | Notes |
|-------|-------|
| Reconfiguration | Elementary school boundary/structure review; ongoing multi-year initiative |
| FY2026 Budget | FY2025–26 budget cycle |
| Superintendent Search | 2025–26 search leading to Angela Atkinson Duina's hire |
| Board Governance | Governance process, transparency, meeting procedures |
| Student Cell Phone Policy 2026 | Policy JICI; mandated state requirement, approved July 2026 |
| Equity | Educational equity policy and DEI initiatives |
| FY2027 Budget | FY2026–27 budget cycle |
| Transportation | Bus routes, overcrowding, contractor issues |
| Student Recognition | PBIS, awards, student achievements |
| Kaler Closure | Closure of Kaler Elementary; student placement transition |
| Special Education | SpEd services, staffing, compliance |
| Student Enrollment | Enrollment trends, projections, capacity planning |
| Facilities | Buildings, maintenance, heat mitigation, capital projects |
| FY2025 Budget | FY2024–25 budget cycle and $1.6M deficit |
| Athletics | Athletic fields referendum, programs |
| Student Support Systems | Counseling, social-emotional support, intervention |
| Administrators Contract 2025-2028 | SPAA collective bargaining agreement |
| Student Achievement | Academic outcomes, testing, curriculum effectiveness |
| Adult Education | HiSET, adult literacy, continuing education programs |
| Mental & Behavioral Health | District-wide mental health supports and staffing |
| K-4 Math Curriculum | Math curriculum adoption/review for grades K–4 |
| Service Employees Contract | SPESPA collective bargaining (covers all negotiations and ratifications) |
| Snow Day Policy | Elimination of remote snow days; days added to calendar instead |
| Student Data | Data privacy, PowerSchool, student information systems |
| Community School Model | SPMS community school pilot and potential expansion |
| Student Mental Health | Student-specific mental health programs and crisis response |
| District Goals | Strategic plan "Pillars and Priorities," board goal-setting |
| AI in Education | Artificial intelligence policy, tools, and classroom use |
| School Climate | Culture, belonging, PBIS, DEI climate surveys |

---

## How to Add a Topic

1. Tag one or more meetings with the new topic name in their `.njk` frontmatter `topics:` list
2. Run `post_process.py` — the topic is added to `topics.json` automatically and a summary is synthesized
3. Add a row to this table

## How to Retire a Topic

1. Remove the topic from all meeting `.njk` `topics:` frontmatter entries
2. Delete the topic's entry from `src/_data/topic_summaries.json`
3. Delete the topic's hash from `scripts/topic_hashes.json`
4. Run `post_process.py` — the topic drops from `topics.json`
5. Remove the row from this table

## Merging Topics

If two topics cover the same ground (e.g. "Student Mental Health" and "Mental & Behavioral Health"), retire one by re-tagging its meetings with the surviving topic, then follow the retire steps above.

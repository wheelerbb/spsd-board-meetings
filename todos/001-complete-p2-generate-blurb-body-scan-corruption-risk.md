---
name: generate-blurb-body-scan-corruption-risk
status: pending
priority: p2
issue_id: "001"
tags: [code-review, correctness, data-integrity]
dependencies: []
---

## Problem Statement

`generate_blurb()` in `post_process.py` checks for an existing blurb by scanning the entire file string: `if 'blurb:' not in content`. If the word `blurb:` appears anywhere in the Nunjucks body (a meeting description, template comment, or quoted speech), the function takes the wrong branch — `re.sub(r'blurb:.*', ...)` replaces the first occurrence of `blurb:` anywhere in the file, potentially corrupting body content rather than updating the YAML front matter field.

No current `.njk` file has `blurb:` in its body, so this does not fire today. But it is structurally incorrect and a silent data corruption risk as content grows.

## Findings

- **File**: `post_process.py`
- **Branch taken incorrectly**: `if 'blurb:' not in content` — should be `if 'blurb:' not in front_matter_only`
- **Regex risk**: `re.sub(r'blurb:.*', ...)` replaces first match anywhere in file, not just in YAML block
- **Current corpus**: 0 files affected (no meeting body contains the string `blurb:`)
- **Source**: correctness-reviewer finding F4

## Proposed Solutions

### Option A: Scope the check to front matter only (Recommended)
Parse the file with `re.split(r'^---\s*$', content, flags=re.MULTILINE)` to isolate the front matter block, then check and replace within that block only.

**Pros**: Correct fix, matches how `remove_nav_keys.py` approached the same problem  
**Cons**: Slightly more code  
**Effort**: Small  
**Risk**: Low

### Option B: Use gray-matter in Python (via PyYAML)
Load the front matter with `yaml.safe_load`, update the `blurb` key, and reserialize. Same approach as `remove_nav_keys.py`.

**Pros**: Robust, consistent with existing Python YAML pattern  
**Cons**: YAML round-trip may cause cosmetic key-ordering diffs (same caveat as migration script)  
**Effort**: Small  
**Risk**: Low

### Option C: Accept the current risk (not recommended)
Add a comment documenting the assumption and rely on the content constraint holding.

**Effort**: Trivial  
**Risk**: Medium (silent data corruption if invariant breaks)

## Acceptance Criteria
- [ ] `generate_blurb` only checks the YAML front matter block for `blurb:`, not the full file
- [ ] A meeting body containing the substring `blurb:` does not corrupt the file on a `post_process.py` run
- [ ] Existing blurb generation behavior is unchanged for current corpus

## Work Log
- 2026-06-08: Identified by correctness-reviewer in ce:review of the nav/data-derivation refactor

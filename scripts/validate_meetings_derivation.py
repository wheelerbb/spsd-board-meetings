"""Pre-deletion validation: compare meetings.json with the output of meetings.js.
Run BEFORE deleting meetings.json as part of the Phase 2 migration.

Usage:
  node -e "const m=require('./src/_data/meetings.js'); process.stdout.write(JSON.stringify(m()))" > /tmp/derived.json
  python3 scripts/validate_meetings_derivation.py
"""
import json
import sys

with open('src/_data/meetings.json') as f:
    original = {m['slug']: m for m in json.load(f)}

with open('/tmp/derived.json') as f:
    derived = {m['slug']: m for m in json.load(f)}

# Blocking fields: must match exactly
BLOCKING = ['display_date', 'day_of_week', 'school_year',
            'has_video', 'has_transcript', 'stub', 'topics']
# Warning fields: known expected differences (logged but non-blocking)
WARNING = ['type', 'doc_count', 'title']

blocking = 0
warnings = 0

# Known intentional changes: treat as warnings not blocking mismatches
KNOWN_CHANGES = {
    '2026-07-13': {'school_year'},  # Was '2026-2027' (sync_drive bug), now '2025-2026'
}

for slug, orig in original.items():
    drv = derived.get(slug)
    if not drv:
        print(f'MISSING from derived: {slug}')
        blocking += 1
        continue
    known = KNOWN_CHANGES.get(slug, set())
    for field in BLOCKING:
        if orig.get(field) != drv.get(field):
            if field in known:
                print(f'  known {slug} [{field}] (intentional): '
                      f'json={orig.get(field)!r} → derived={drv.get(field)!r}')
                warnings += 1
            else:
                print(f'MISMATCH {slug} [{field}]: '
                      f'json={orig.get(field)!r} vs derived={drv.get(field)!r}')
                blocking += 1
    for field in WARNING:
        if orig.get(field) != drv.get(field):
            print(f'  warn  {slug} [{field}]: '
                  f'json={orig.get(field)!r} vs derived={drv.get(field)!r}')
            warnings += 1

new_count = len(derived) - len(original)
print()
print(f'{new_count:+d} new meetings in derived (previously-invisible stubs)')
print(f'{blocking} blocking mismatches | {warnings} expected differences')
print()
if blocking > 0:
    print('❌  Blocking mismatches found — do not proceed with deletion')
    sys.exit(1)
else:
    print('✅  No blocking mismatches — safe to proceed')

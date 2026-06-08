"""One-time migration: remove prev: and next: keys from all meeting .njk files.
Navigation is now computed at build time by the meetingNav Eleventy filter.
Run: python scripts/remove_nav_keys.py
"""
import os
import re
import yaml

MEETINGS_DIR = 'src/meetings'

modified = 0
for fname in sorted(os.listdir(MEETINGS_DIR)):
    if not fname.endswith('.njk'):
        continue
    path = os.path.join(MEETINGS_DIR, fname)
    with open(path, 'r') as f:
        content = f.read()
    # Split on bare '---' lines → [pre, front_matter, body...]
    parts = re.split(r'^---\s*$', content, flags=re.MULTILINE)
    if len(parts) < 3:
        continue  # No closing delimiter — skip (should not occur)
    fm = yaml.safe_load(parts[1]) or {}
    if 'prev' not in fm and 'next' not in fm:
        continue  # Nothing to remove
    fm.pop('prev', None)
    fm.pop('next', None)
    new_fm = yaml.dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True)
    body = '---'.join(parts[2:])
    with open(path, 'w') as f:
        f.write(f'---\n{new_fm}---\n{body}')
    modified += 1
    print(f'  Removed nav keys: {fname}')

print(f'\nDone — modified {modified} files.')

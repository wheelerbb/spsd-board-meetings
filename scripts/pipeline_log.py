import os
import json
from datetime import datetime

from sourcing.auth import get_credentials

MAX_ENTRIES = 200
LOCAL_DIR = 'src/_data'
LOG_NAMES = ('sourcing_log', 'processing_log')


def current_run_id():
    """A stable id shared by every script invocation within one GitHub Actions run — used
    only to let the processing-log page group the transcripts/post_process rows for a given
    pass; not relied on for any read-modify-write correctness (see append_entry)."""
    return os.environ.get('GITHUB_RUN_ID') or datetime.utcnow().isoformat()


def _bucket(bucket_uri):
    from google.cloud import storage
    credentials, project_id = get_credentials()
    bucket_name = bucket_uri[5:]  # strip gs://
    return storage.Client(credentials=credentials, project=project_id).bucket(bucket_name)


def load_log(bucket_uri, name):
    """Load `{name}.json` (an array of run entries, newest first) from the bucket root.
    Returns [] if no bucket is configured or nothing's been logged yet."""
    if not bucket_uri:
        return []
    try:
        blob = _bucket(bucket_uri).blob(f'{name}.json')
        if not blob.exists(timeout=60):
            return []
        return json.loads(blob.download_as_text(timeout=60))
    except Exception as e:
        print(f"  Warning: could not load {name}.json: {e}")
        return []


def _save_log(bucket_uri, name, log):
    try:
        _bucket(bucket_uri).blob(f'{name}.json').upload_from_string(
            json.dumps(log, indent=2), content_type='application/json', timeout=60)
    except Exception as e:
        print(f"  Warning: could not save {name}.json: {e}")


def append_entry(bucket_uri, name, entry, limit=MAX_ENTRIES):
    """Prepend `entry` to `{name}.json` and save. Each call is a single independent
    load+append+save — callers never look up or merge into an existing entry, so there's
    nothing to race or fail to find across separate script invocations."""
    if not bucket_uri:
        return
    log = load_log(bucket_uri, name)
    log.insert(0, entry)
    _save_log(bucket_uri, name, log[:limit])


def sync_local_copy(bucket_uri):
    """Pull sourcing_log.json and processing_log.json down to src/_data/ for Eleventy.
    Only meant to be called right before a build actually happens (see deploy.yml's gated
    "Sync processing log for build" step) — writing these locally on every run, including
    no-op cron ticks, would make `git diff --staged --quiet` never pass and defeat the
    scheduled-run gate the logs are meant to report on."""
    os.makedirs(LOCAL_DIR, exist_ok=True)
    for name in LOG_NAMES:
        log = load_log(bucket_uri, name)
        with open(f'{LOCAL_DIR}/{name}.json', 'w') as f:
            json.dump(log, f, indent=2)

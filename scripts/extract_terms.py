import os
import sys
import json
import re
import yaml
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from googleapiclient.errors import HttpError
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Global default for non-genai network calls in this file (Drive/GCS) — the genai client itself
# carries its own explicit HttpOptions timeout via model_config.build_client().
socket.setdefaulttimeout(120)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from sourcing import drive as drive_mod
import terms_registry
import pipeline_log
from model_config import call_llm, build_client, BATCH_TIMEOUT


def _make_client():
    """Client factory passed to call_llm() — memoized so repeated in-process calls reuse one
    client, while a subprocess (fresh process memory, no cache) builds its own on first use."""
    global _client
    if _client is None:
        _client = build_client()
    return _client


_client = None
# Eager, not just for fail-fast diagnostics: a freshly-constructed genai.Client used immediately
# (no warm-up gap) has been observed to fail with "client has been closed" — building it here,
# well before the first real call, avoids that race. Looks unused below since call_llm() gets the
# client via _make_client(), not this variable directly — do not remove it.
client = _make_client()

MAX_WORKERS = 4

# Raw agenda/packet/minutes document text is far more token-heavy per item than a condensed
# transcript summary (unlike post_process.py::batch_tag_all_meetings's single all-corpus call,
# an all-at-once call isn't viable here) — chunk cache-miss documents into groups of this size,
# one Gemini call per group. Multiple batches still run concurrently via ThreadPoolExecutor,
# same MAX_WORKERS convention as every other I/O-bound stage in this pipeline.
TERMS_BATCH_SIZE = 5


class ExtractedTerm(BaseModel):
    term: str
    type: str
    context: str = ''


class DocumentTerms(BaseModel):
    file_id: str
    terms: list[ExtractedTerm]


class BatchTermResponse(BaseModel):
    results: list[DocumentTerms]


def _scan_meetings(meeting_dir):
    """Parse every meeting .njk's front matter into {slug, docs, ...} dicts. Read-only, no
    glossary replacement — that pass belongs to post_process.py, not this upstream stage."""
    meetings_data = []
    for filename in sorted(os.listdir(meeting_dir)):
        if not filename.endswith('.njk'):
            continue
        slug = filename.replace('.njk', '')
        with open(os.path.join(meeting_dir, filename), 'r') as f:
            content = f.read()
        m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
        if not m:
            continue
        try:
            data = yaml.safe_load(m.group(1)) or {}
        except Exception as e:
            print(f"  Warning: could not parse {filename}: {e}")
            continue
        data['slug'] = slug
        meetings_data.append(data)
    return meetings_data


def _collect_targets(meetings_data):
    """Every (meeting_slug, doc_type, file_id) for agenda/packet/minutes docs across the corpus
    — same qualifying-doc definition the old post_process.py::_extract_official_terms() used."""
    targets = []
    for m in meetings_data:
        slug = m['slug']
        for d in (m.get('docs') or []):
            doc_type = d.get('type')
            if doc_type not in ('agenda', 'packet', 'minutes'):
                continue
            file_id = drive_mod.extract_file_id(d.get('url', ''))
            if not file_id:
                continue
            targets.append((slug, doc_type, file_id))
    return targets


def _ensure_cache_metadata(catalog, svc, file_id, doc_type):
    """Return this file's current modifiedTime, ensuring catalog[file_id] carries the minimal
    fields other cache logic (text_cache_fresh, get_or_extract_text) depends on. Does NOT hit
    Drive's API when the catalog already has it: source_data.py's own folder scan (run earlier
    in the same job — see sourcing/drive.py::build_meeting_map) already keeps modified_time
    current for every file within its scope, so re-fetching here would just duplicate a call
    source_data.py already made moments ago. Only a file that scan never saw (not in the shared
    folder, or discovered solely via this meeting's .njk front matter) needs a live lookup here.

    Returns None if the file is (or was just confirmed) inaccessible — caller skips it for this
    run, same as before."""
    entry = catalog.get(file_id)
    if entry and entry.get('access') == 'no_access':
        return None  # confirmed gone/inaccessible on a prior run — don't keep re-checking
    if entry and entry.get('modified_time') and entry.get('mime_type'):
        entry.setdefault('doc_type', doc_type)
        return entry['modified_time']
    try:
        meta = svc.files().get(fileId=file_id, fields='modifiedTime,mimeType,createdTime').execute()
    except HttpError as e:
        if e.resp.status == 404:
            catalog.setdefault(file_id, {})['access'] = 'no_access'
            print(f"    Drive file {file_id} is no longer accessible (404) — flagging no_access "
                  f"so future runs don't retry it.")
        else:
            print(f"    Warning: could not get Drive metadata for {file_id}: {e}")
        return None
    except Exception as e:
        print(f"    Warning: could not get Drive metadata for {file_id}: {e}")
        return None
    entry = catalog.setdefault(file_id, {})
    entry.setdefault('doc_type', doc_type)
    entry.setdefault('mime_type', meta.get('mimeType'))
    entry.setdefault('created_time', meta.get('createdTime'))
    return meta.get('modifiedTime')


def _hydrate_cached_terms(bucket_uri, entry):
    """A cache-hit entry may still carry only a legacy terms_blob pointer (no inline `terms`
    yet) — hydrate it once, then drop the pointer. Returns the entry's terms list, or None if
    nothing's cached."""
    terms = entry.get('terms')
    if terms is None and entry.get('terms_blob'):
        cached = drive_mod.read_cached_blob(bucket_uri, entry['terms_blob'])
        if cached is not None:
            try:
                terms = json.loads(cached)
                entry['terms'] = terms
                entry.pop('terms_blob', None)
            except Exception:
                terms = None
    return terms


def _extract_batch(batch):
    """One Gemini call covering every document in `batch` at once. `batch` is a list of
    (slug, doc_type, file_id, modified_time, text); returns {file_id: terms} for whichever
    documents the model actually returned results for."""
    doc_blocks = [
        f"### file_id: {file_id} (type: {doc_type})\n{text}"
        for slug, doc_type, file_id, _modified, text in batch
    ]
    documents_text = '\n\n'.join(doc_blocks)

    prompt = (
        "Extract all proper nouns from each of the following school board meeting documents. "
        "For each document, include:\n"
        "- People with official roles (board members, administrators, staff)\n"
        "- Named places (schools, buildings, streets, districts)\n"
        "- Organizations and associations (unions, parent groups, state agencies)\n"
        "- Named programs, policies, or ongoing initiatives (not one-time events like a single "
        "assembly, fundraiser, or celebration — only things with standing beyond one occurrence)\n\n"
        "State context as a stable fact about the term (e.g. \"Board Member\", \"State agency\") - "
        "never meeting-attendance phrasing (\"present at the meeting\") or hedged/speculative "
        "phrasing (\"likely\", \"probably\", \"appears to be\"). Omit context entirely rather than "
        "guessing if you're not sure.\n"
        "Use the exact spelling from the document. A document with nothing qualifying gets an "
        "empty terms list, not an omitted entry.\n\n"
        "Respond with one entry per document below, keyed by its file_id.\n\n"
        f"{documents_text}"
    )
    try:
        response_text, _model = call_llm(
            _make_client, prompt, response_schema=BatchTermResponse,
            timeout=BATCH_TIMEOUT, label=f"term extraction batch ({len(batch)} docs)",
        )
        parsed = json.loads(response_text)
    except Exception as e:
        print(f"    Warning: batch term extraction failed for {len(batch)} doc(s): {e}", flush=True)
        return {}
    return {r['file_id']: r['terms'] for r in parsed['results']}


def _chunk(items, size):
    """Split `items` into consecutive groups of at most `size`. Split out as its own pure
    function purely so it's unit-testable without any Drive/Gemini mocking."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _apply_batch_results(catalog, batch, by_file_id):
    """Write each document's returned terms onto its catalog entry. `batch` is a list of
    (slug, doc_type, file_id, modified_time, text); `by_file_id` is _extract_batch()'s return
    value. Pure data-distribution step, split out from the network/IO orchestration in
    extract_terms() below so it's testable without mocking Drive/Gemini at all."""
    for slug, doc_type, file_id, modified_time, _text in batch:
        terms = by_file_id.get(file_id)
        if terms is None:
            print(f"    Warning: no terms returned for {slug}/{doc_type} ({file_id})", flush=True)
            continue
        entry = catalog[file_id]
        entry['terms'] = terms
        entry['modified_time'] = modified_time
        print(f"    Extracted and cached {len(terms)} terms for {slug}/{doc_type}.", flush=True)


def extract_terms(meetings_data, bucket_uri, catalog):
    """Batch-extract canonical proper-noun terms from every meeting's official documents
    (agenda/packet/minutes), caching results inline on drive_catalog.json entries. Replaces
    post_process.py's old per-document, unbatched _extract_official_terms() — see todos/026."""
    svc = drive_mod.get_drive_service()
    targets = _collect_targets(meetings_data)
    if not targets:
        return

    cache_miss = []  # (slug, doc_type, file_id, modified_time, text)
    for slug, doc_type, file_id in targets:
        current_modified = _ensure_cache_metadata(catalog, svc, file_id, doc_type)
        if current_modified is None:
            continue
        entry = catalog[file_id]
        # Terms are derived from extracted text, so they're only as fresh as the text that
        # produced them — a modified_time match alone would keep serving terms pulled from
        # pre-fix, glued-word text forever (see sourcing/drive.py::TEXT_EXTRACTOR_VERSION).
        if drive_mod.text_cache_fresh(entry, entry.get('mime_type'), current_modified):
            terms = _hydrate_cached_terms(bucket_uri, entry)
            if terms is not None:
                print(f"    Loaded {len(terms)} terms for {slug}/{doc_type} from cache.", flush=True)
                continue

        print(f"    Need extraction for {slug}/{doc_type} (not in cache)...", flush=True)
        text, updates = drive_mod.get_or_extract_text(bucket_uri, svc, catalog, file_id)
        if updates:
            entry.update(updates)
        if not text:
            continue
        cache_miss.append((slug, doc_type, file_id, current_modified, text))

    if not cache_miss:
        print("  No new official documents to extract terms from.", flush=True)
        return

    batches = _chunk(cache_miss, TERMS_BATCH_SIZE)
    print(f"  Extracting terms for {len(cache_miss)} document(s) in {len(batches)} batch(es) "
          f"of up to {TERMS_BATCH_SIZE}...", flush=True)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_extract_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            _apply_batch_results(catalog, batch, future.result())


def main():
    bucket_uri = None
    if '--bucket' in sys.argv:
        idx = sys.argv.index('--bucket')
        if idx + 1 < len(sys.argv):
            bucket_uri = sys.argv[idx + 1]
    bucket_uri = bucket_uri or os.getenv('GCS_BUCKET_URI') or None

    if not bucket_uri:
        print("No bucket configured — skipping term extraction and registry rebuild.", flush=True)
        return

    force = '--force' in sys.argv
    if not force:
        # Mirrors post_process.py's own any_changes check: if source_data.py found nothing new
        # this run, there are no new/changed official documents for this stage to extract terms
        # from either — skip the full per-document cache-freshness pass entirely rather than
        # paying its cost (Drive lookups for anything not already in the catalog, cache-hit
        # hydration, a line of output per document) for a guaranteed no-op.
        run_id = pipeline_log.current_run_id()
        this_run_sourcing = [
            e for e in pipeline_log.load_log(bucket_uri, 'sourcing_log')
            if e.get('run_id') == run_id
        ]
        if this_run_sourcing and not this_run_sourcing[0].get('any_changes'):
            print("No new source content this run — skipping term extraction and registry "
                  "rebuild.", flush=True)
            return

    meetings_data = _scan_meetings('src/meetings/')
    catalog = drive_mod.load_catalog(bucket_uri)

    print("Extracting canonical names from official meeting documents...", flush=True)
    extract_terms(meetings_data, bucket_uri, catalog)
    drive_mod.save_catalog(bucket_uri, catalog)

    print("Rebuilding Terms Registry if anything qualifying changed...", flush=True)
    current_fp = terms_registry.registry_fingerprint(catalog, meetings_data)
    if current_fp == terms_registry.load_fingerprint(bucket_uri):
        print("  No change in qualifying documents — registry already up to date.", flush=True)
    else:
        registry = terms_registry.build_registry_from_catalog(catalog, meetings_data)
        terms_registry.save_registry(bucket_uri, registry)
        terms_registry.save_fingerprint(bucket_uri, current_fp)
        print(f"  Rebuilt registry: {len(registry)} entities.", flush=True)


if __name__ == "__main__":
    main()

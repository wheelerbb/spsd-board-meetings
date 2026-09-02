import queue as _stdlib_queue
from multiprocessing import Process, Queue as MpQueue
from google import genai
from google.genai import types as genai_types

# Callers (process_transcripts.py, post_process.py) insert their own scripts/ dir onto sys.path
# before importing this module, which is what makes this import resolve.
from sourcing.auth import get_credentials

DEFAULT_MODEL = 'gemini-2.5-pro'
BACKUP_MODEL = 'gemini-3.5-flash'
MODELS_TO_TRY = [DEFAULT_MODEL, BACKUP_MODEL]

# BACKUP_MODEL (gemini-3.5-flash) is only served on Vertex AI's global endpoint, not regional
# ones like us-central1 (see commit c773440) — every client MUST use this location, or a 429
# fallback to BACKUP_MODEL fails outright instead of retrying. Single value, not a per-script
# choice: there is no correct reason for a client in this pipeline to use anything else.
VERTEX_LOCATION = 'global'

# Single temperature for every call — no current call has a documented reason to run at a
# different value from any other.
DEFAULT_TEMPERATURE = 0.1

# Every call gets subprocess+SIGTERM hard-kill protection by default (see call_llm below).
# TRANSCRIPT_TIMEOUT/BATCH_TIMEOUT are the two calls whose payload is genuinely an order of
# magnitude larger than everything else (a full meeting transcript; the entire meeting corpus
# in one call) — not arbitrary per-call tuning, just proportionate to those two inputs.
DEFAULT_TIMEOUT = 120
TRANSCRIPT_TIMEOUT = 300
BATCH_TIMEOUT = 600


def build_client(http_timeout=120.0):
    """Construct a fresh Vertex AI client using the pipeline's single shared location. Called by
    each script's own module-level client-factory function (which must stay a plain top-level
    function, not defined here, so multiprocessing can pickle it by reference — see call_llm)."""
    creds, project_id = get_credentials()
    return genai.Client(
        credentials=creds, project=project_id, location=VERTEX_LOCATION, vertexai=True,
        http_options=genai_types.HttpOptions(client_args={'timeout': http_timeout}),
    )


class AllModelsRateLimited(Exception):
    """Every model in MODELS_TO_TRY was rate-limited (429) for this call."""
    pass


def _llm_subprocess(client_factory, model_name, prompt_text, config, result_queue):
    """Runs one LLM call in a separate process so SIGTERM can hard-kill it on timeout.
    client_factory is called here (not passed a live client) since a client/socket can't cross
    the process boundary — it's rebuilt fresh in the child."""
    try:
        response = client_factory().models.generate_content(
            model=model_name, contents=prompt_text, config=config,
        )
        result_queue.put(('ok', response.text))
    except Exception as e:
        result_queue.put(('err', str(e)))


def call_llm(client_factory, prompt, *, temperature=DEFAULT_TEMPERATURE, response_schema=None,
             json_output=False, timeout=DEFAULT_TIMEOUT, label="", on_attempt=None):
    """Send one prompt to the configured LLM, trying MODELS_TO_TRY (pro, then flash) in order —
    falls through to the next model only on a 429; any other exception propagates immediately.

    client_factory: zero-arg callable returning a client. Called inside a child process to
    isolate each attempt (see `timeout` below) — must therefore be a plain module-level function
    (not a lambda/closure), since multiprocessing ('spawn') pickles it by reference rather than
    by value.

    response_schema (a Pydantic model, or e.g. list[SomeModel]) requests structured JSON output.
    json_output=True requests plain JSON mime type with no schema, for callers that parse the
    shape themselves. Leave both unset for non-JSON output (plain text, HTML).

    temperature defaults to DEFAULT_TEMPERATURE — every call in the pipeline uses the same value;
    override only with a specific, documented reason for that one call to differ.

    timeout defaults to DEFAULT_TIMEOUT: every model attempt runs in a child process,
    SIGTERM-killed if it's still alive after that many seconds, so no call can hang the pipeline
    indefinitely. Pass TRANSCRIPT_TIMEOUT/BATCH_TIMEOUT (or another explicit value) only when a
    call's input/output is genuinely much larger than the default case — not for arbitrary
    per-call tuning. timeout=None calls in-process instead, with no hard-kill protection at all;
    only use this if the hard-kill subprocess overhead is itself provably a problem for that call.

    label is folded into log/error messages only (e.g. a meeting slug) — no behavioral effect.
    on_attempt(model), if given, is called immediately before each model attempt (e.g. for
    per-attempt progress logging).

    Returns (response_text, model_served). Raises AllModelsRateLimited if every model was
    rate-limited, or the underlying exception for any other failure."""
    config = {'temperature': temperature}
    if response_schema is not None:
        config['response_mime_type'] = 'application/json'
        config['response_schema'] = response_schema
    elif json_output:
        config['response_mime_type'] = 'application/json'

    suffix = f" for {label}" if label else ""

    if timeout is not None:
        def _attempt(model):
            if on_attempt:
                on_attempt(model)
            q = MpQueue()
            p = Process(target=_llm_subprocess, args=(client_factory, model, prompt, config, q), daemon=True)
            p.start()
            p.join(timeout=timeout)
            if p.is_alive():
                p.terminate()
                p.join()
                raise TimeoutError(f"LLM call timed out after {timeout}s{suffix}")
            try:
                kind, val = q.get_nowait()
            except _stdlib_queue.Empty:
                raise Exception(f"LLM subprocess exited with no result{suffix}")
            if kind == 'err':
                raise Exception(val)
            return val
    else:
        client = client_factory()

        def _attempt(model):
            if on_attempt:
                on_attempt(model)
            return client.models.generate_content(model=model, contents=prompt, config=config).text

    for model in MODELS_TO_TRY:
        try:
            return _attempt(model), model
        except Exception as model_err:
            if "429" in str(model_err):
                print(f"  Rate-limited on {model}, trying next model...", flush=True)
                continue
            raise
    raise AllModelsRateLimited(f"All models rate-limited{suffix}")

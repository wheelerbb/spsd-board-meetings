import queue as _stdlib_queue
from multiprocessing import Process, Queue as MpQueue

DEFAULT_MODEL = 'gemini-2.5-pro'
BACKUP_MODEL = 'gemini-3.5-flash'
MODELS_TO_TRY = [DEFAULT_MODEL, BACKUP_MODEL]


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


def call_llm(client_factory, prompt, *, temperature=0.1, response_schema=None,
             json_output=False, timeout=None, label="", on_attempt=None):
    """Send one prompt to the configured LLM, trying MODELS_TO_TRY (pro, then flash) in order —
    falls through to the next model only on a 429; any other exception propagates immediately.

    client_factory: zero-arg callable returning a client. Called directly for an in-process call
    (timeout=None); called again inside a child process for an isolated call (timeout set) — must
    therefore be a plain module-level function (not a lambda/closure), since multiprocessing
    ('spawn') pickles it by reference rather than by value.

    response_schema (a Pydantic model, or e.g. list[SomeModel]) requests structured JSON output.
    json_output=True requests plain JSON mime type with no schema, for callers that parse the
    shape themselves. Leave both unset for non-JSON output (plain text, HTML).

    timeout=None calls in-process, relying on the client's own transport timeout. A numeric
    timeout runs each model attempt in a child process, SIGTERM-killed if it's still alive after
    that many seconds — use this for calls over large/variable-length input (transcripts, batch
    prompts) where an in-process hang would block the whole run.

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

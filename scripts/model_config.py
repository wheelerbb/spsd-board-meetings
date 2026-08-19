DEFAULT_MODEL = 'gemini-2.5-pro'
BACKUP_MODEL = 'gemini-3.5-flash'
MODELS_TO_TRY = [DEFAULT_MODEL, BACKUP_MODEL]


def call_with_fallback(make_call):
    """Try each model in MODELS_TO_TRY in order. `make_call(model)` performs one attempt and
    returns a response (or raises). Falls through to the next model only on a rate-limit (429)
    error; any other exception propagates immediately. Returns None if every model was
    rate-limited."""
    response = None
    for model in MODELS_TO_TRY:
        try:
            response = make_call(model)
            break
        except Exception as model_err:
            if "429" in str(model_err):
                print(f"  Rate-limited on {model}, trying next model...", flush=True)
                continue
            raise
    return response

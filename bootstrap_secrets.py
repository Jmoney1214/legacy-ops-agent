import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except Exception:
    pass

key = os.getenv("OPENAI_API_KEY")
if not key:
    raise RuntimeError("OPENAI_API_KEY not set")
try:
    from agents.models._openai_shared import set_default_openai_key
    set_default_openai_key(key)
except Exception:
    pass

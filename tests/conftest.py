"""Pytest fixtures: redirect LOG_DIR to a temp directory BEFORE `server.config`
is imported.

Do not import from `server.*` at the top of this file — the env vars below must
be set first (python-dotenv, called inside `server/config.py` at import time,
does NOT overwrite already-set env vars, so setting these here wins).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure the project root is importable when pytest is invoked from any cwd.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMP_LOG_DIR = Path(tempfile.mkdtemp(prefix="mqace-log-"))
os.environ.setdefault("LOG_DIR", str(_TMP_LOG_DIR))

# Pin the allow-lists to the narrow defaults regardless of the local .env.
os.environ.setdefault("MQ_ALLOWED_HOSTNAME_PREFIXES", "lod,loq,lot")
os.environ.setdefault("ACE_ALLOWED_HOSTNAME_PREFIXES", "lod,loq,lot")

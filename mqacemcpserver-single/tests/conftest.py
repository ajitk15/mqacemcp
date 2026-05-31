"""Pytest fixtures: redirect LOG_DIR to a temp directory before `server.config`
is imported. Mirrors the convention from the root repo — env vars must be set
first, so do not import from `server.*` here.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure the new server's package is importable when pytest is invoked from
# any working directory.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMP_LOG_DIR = Path(tempfile.mkdtemp(prefix="mqace-single-log-"))
os.environ.setdefault("LOG_DIR", str(_TMP_LOG_DIR))

# Point at the shared resource CSVs (parent repo's resources/) by default.
_RESOURCES = (_ROOT.parent / "resources").resolve()
os.environ.setdefault("RESOURCES_DIR", str(_RESOURCES))

# Pin the allow-lists to the narrow defaults regardless of what the local .env
# sets. Tests assert against the manifest's lopalhost / lodalhost entries being
# RESTRICTED — a permissive .env would silently turn that assertion into a live
# call. python-dotenv (called inside server/config.py at import time) does NOT
# overwrite already-set env vars, so setting these BEFORE server.* is imported
# wins.
os.environ["MQ_ALLOWED_HOSTNAME_PREFIXES"] = "lod,loq,lot"
os.environ["ACE_ALLOWED_HOSTNAME_PREFIXES"] = "lod,loq,lot"

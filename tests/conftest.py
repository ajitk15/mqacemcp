"""Pytest fixtures.

Each test session points LOG_DIR at a temp directory so tests never pollute
`<project>/logs/`. The patch happens before `server.config` is imported.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure project root is importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Redirect logs to a fresh temp directory before config is imported by any test.
_TMP_LOG_DIR = tempfile.mkdtemp(prefix="mqacemcp-test-logs-")
os.environ.setdefault("LOG_DIR", _TMP_LOG_DIR)
os.environ.setdefault("MQACE_LOG_LEVEL", "WARNING")

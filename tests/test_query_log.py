"""Tests for the per-call JSONL query log."""
from __future__ import annotations

import asyncio
import glob
import json
import os

import pytest

from server.config import LOG_DIR
from server.query_log import _bind_args, logged_tool, sanitize_args


def test_sanitize_args_redacts_every_hint():
    out = sanitize_args(
        {
            "qmgr_name": "QM1",
            "password": "hunter2",
            "secret_token": "abc",
            "api_key": "xyz",
            "auth": "u:p",
            "pwd": "x",
            "credential": "y",
            "PASSWORD": "y",
        }
    )
    assert out["qmgr_name"] == "QM1"
    for k in ("password", "secret_token", "api_key", "auth", "pwd", "credential", "PASSWORD"):
        assert out[k] == "[REDACTED]", k


def test_sanitize_args_handles_unjsonable_values():
    class Weird:
        def __repr__(self):
            return "<weird>"

    out = sanitize_args({"x": Weird()})
    assert out["x"] == "<weird>"


def test_bind_args_merges_positional_and_keyword():
    def sample(a, b, c=10):
        return None

    merged = _bind_args(sample, ("hi",), {"b": "there"})
    assert merged == {"a": "hi", "b": "there"}


def _read_today_jsonl() -> list[dict]:
    path_glob = os.path.join(str(LOG_DIR), "queries-*.jsonl")
    rows: list[dict] = []
    for p in sorted(glob.glob(path_glob)):
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                rows.append(json.loads(line))
    return rows


def test_logged_tool_sync_writes_jsonl_record():
    @logged_tool
    def my_sync_tool(name: str) -> str:
        return f"hello {name}"

    before = len(_read_today_jsonl())
    out = my_sync_tool("ada")
    after = _read_today_jsonl()

    assert out == "hello ada"
    assert len(after) == before + 1
    rec = after[-1]
    assert rec["tool"] == "my_sync_tool"
    assert rec["args"] == {"name": "ada"}
    assert rec["outcome"] == "success"
    assert rec["error"] is None
    assert rec["latency_ms"] >= 0
    assert rec["response_bytes"] == len("hello ada")


def test_logged_tool_async_writes_jsonl_record():
    @logged_tool
    async def my_async_tool(x: int) -> str:
        await asyncio.sleep(0)
        return str(x * 2)

    before = len(_read_today_jsonl())
    out = asyncio.run(my_async_tool(21))
    after = _read_today_jsonl()

    assert out == "42"
    assert len(after) == before + 1
    rec = after[-1]
    assert rec["tool"] == "my_async_tool"
    assert rec["args"] == {"x": 21}


def test_logged_tool_records_error_when_function_raises():
    @logged_tool
    def boom() -> str:
        raise RuntimeError("kaboom")

    before = len(_read_today_jsonl())
    with pytest.raises(RuntimeError, match="kaboom"):
        boom()

    rows = _read_today_jsonl()
    assert len(rows) == before + 1
    rec = rows[-1]
    assert rec["outcome"] == "error"
    assert "RuntimeError" in rec["error"]
    assert rec["response_bytes"] is None


def test_logged_tool_redacts_secret_kwargs_in_record():
    @logged_tool
    def authy(user: str, password: str) -> str:
        return f"hi {user}"

    authy("ada", password="hunter2")
    rec = _read_today_jsonl()[-1]
    assert rec["args"]["user"] == "ada"
    assert rec["args"]["password"] == "[REDACTED]"

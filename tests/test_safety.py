"""Tests for the shared safety primitives."""
from __future__ import annotations

from server.safety import is_hostname_allowed, is_modification_command


def test_hostname_allow_list_matches_prefix_case_insensitively():
    ok, msg = is_hostname_allowed("LODalhost", ["lod", "loq"])
    assert ok is True
    assert msg == ""


def test_hostname_allow_list_blocks_unmatched_host():
    ok, msg = is_hostname_allowed("prodhost01", ["lod", "loq", "lot"])
    assert ok is False
    assert "restricted" in msg
    assert "prodhost01" in msg


def test_hostname_allow_list_handles_empty_prefix_list():
    ok, msg = is_hostname_allowed("anything", [])
    assert ok is False
    assert "<none>" in msg


def test_is_modification_command_blocks_alter_define_delete():
    for verb in ("ALTER", "alter", "DEFINE", "DELETE", "  RESET QMGR", "STOP CHL"):
        assert is_modification_command(verb) is True, verb


def test_is_modification_command_allows_display():
    for cmd in ("DISPLAY QMGR", "display QLOCAL(*)", "  DIS QSTATUS"):
        assert is_modification_command(cmd) is False, cmd


def test_is_modification_command_handles_blank_input():
    assert is_modification_command("") is False
    assert is_modification_command("   ") is False

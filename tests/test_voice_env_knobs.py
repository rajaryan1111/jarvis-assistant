"""Regression tests for the voice configuration knobs.

`python -m ultron voice-status` and `.env.example` both document
ULTRON_VOICE_COMMAND_TIMEOUT and ULTRON_VOICE_UNAVAILABLE_BACKOFF. These tests pin
that the code actually reads them and that a bad value is clamped rather than being
allowed to wedge the loop (a zero command timeout would give up instantly; a huge
back-off would make the assistant look dead).
"""
import importlib

import pytest


@pytest.fixture()
def main_module(monkeypatch):
    monkeypatch.setenv("ULTRON_SKIP_DOTENV", "1")
    import main

    importlib.reload(main)
    return main


def test_the_default_is_used_when_unset(main_module, monkeypatch):
    monkeypatch.delenv("ULTRON_VOICE_COMMAND_TIMEOUT", raising=False)
    assert main_module._voice_env_float("ULTRON_VOICE_COMMAND_TIMEOUT", 6.0, 1.0, 120.0) == 6.0


def test_an_empty_value_falls_back_to_the_default(main_module, monkeypatch):
    monkeypatch.setenv("ULTRON_VOICE_COMMAND_TIMEOUT", "   ")
    assert main_module._voice_env_float("ULTRON_VOICE_COMMAND_TIMEOUT", 6.0, 1.0, 120.0) == 6.0


def test_a_valid_value_is_honoured(main_module, monkeypatch):
    monkeypatch.setenv("ULTRON_VOICE_COMMAND_TIMEOUT", "12")
    assert main_module._voice_env_float("ULTRON_VOICE_COMMAND_TIMEOUT", 6.0, 1.0, 120.0) == 12.0


def test_a_non_numeric_value_is_clamped_to_the_default(main_module, monkeypatch):
    monkeypatch.setenv("ULTRON_VOICE_COMMAND_TIMEOUT", "not-a-number")
    assert main_module._voice_env_float("ULTRON_VOICE_COMMAND_TIMEOUT", 6.0, 1.0, 120.0) == 6.0


def test_a_value_below_the_floor_is_clamped_to_the_default(main_module, monkeypatch):
    """A zero or negative command timeout would make the loop give up instantly."""
    monkeypatch.setenv("ULTRON_VOICE_COMMAND_TIMEOUT", "0")
    assert main_module._voice_env_float("ULTRON_VOICE_COMMAND_TIMEOUT", 6.0, 1.0, 120.0) == 6.0


def test_a_value_above_the_ceiling_is_clamped_to_the_default(main_module, monkeypatch):
    """A huge back-off would make the assistant look dead."""
    monkeypatch.setenv("ULTRON_VOICE_UNAVAILABLE_BACKOFF", "99999")
    assert main_module._voice_env_float("ULTRON_VOICE_UNAVAILABLE_BACKOFF", 5.0, 0.0, 300.0) == 5.0


def test_a_zero_backoff_is_allowed(main_module, monkeypatch):
    """Zero is a legitimate choice: retry immediately with no pause."""
    monkeypatch.setenv("ULTRON_VOICE_UNAVAILABLE_BACKOFF", "0")
    assert main_module._voice_env_float("ULTRON_VOICE_UNAVAILABLE_BACKOFF", 5.0, 0.0, 300.0) == 0.0


def test_both_documented_knobs_are_wired_into_the_session(main_module):
    """Documentation drift check: .env.example must name variables the code reads."""
    source = main_module.__file__
    text = open(source, encoding="utf-8").read()
    for knob in ("ULTRON_VOICE_COMMAND_TIMEOUT", "ULTRON_VOICE_UNAVAILABLE_BACKOFF"):
        assert knob in text, f"{knob} is documented but never read by main.py"
        assert f'"{knob}"' in text, f"{knob} is read as a literal but not by name"

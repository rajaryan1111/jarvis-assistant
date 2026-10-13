from unittest.mock import patch

import requests
import wikipedia


def test_wiki_summary_returns_verified_two_sentence_summary(legacy):
    with patch.object(
        legacy.wikipedia,
        "summary",
        return_value="Ada Lovelace was an English mathematician. She worked on Babbage's Analytical Engine.",
    ) as summary:
        res = legacy._tool_wiki_summary({"topic": "Ada Lovelace"})
        router_reply = legacy.wiki("who is Ada Lovelace")

    assert res.ok is True
    assert "Ada Lovelace" in res.message
    assert res.data == {"topic": "Ada Lovelace", "status": "verified", "source": "wikipedia"}
    assert "Ada Lovelace" in router_reply
    assert summary.call_count == 2


def test_wiki_summary_handles_disambiguation_truthfully(legacy):
    err = wikipedia.exceptions.DisambiguationError(
        "Mercury", ["Mercury (planet)", "Mercury (element)", "Mercury (mythology)"]
    )
    with patch.object(legacy.wikipedia, "summary", side_effect=err):
        res = legacy._tool_wiki_summary({"topic": "Mercury"})
        router_reply = legacy.wiki("what is Mercury")

    assert res.ok is False
    assert "ambiguous" in res.message
    assert "Mercury (planet)" in res.message
    assert res.data == {
        "topic": "Mercury",
        "status": "ambiguous",
        "options": ["Mercury (planet)", "Mercury (element)", "Mercury (mythology)"],
        "source": "wikipedia",
    }
    assert "ambiguous" in router_reply


def test_wiki_summary_handles_missing_page_and_empty_results(legacy):
    with patch.object(legacy.wikipedia, "summary", side_effect=wikipedia.exceptions.PageError("NonexistentTopicXYZ")):
        missing = legacy._tool_wiki_summary({"topic": "NonexistentTopicXYZ"})
        router_missing = legacy.wiki("tell me about NonexistentTopicXYZ")

    assert missing.ok is False
    assert missing.data == {"topic": "NonexistentTopicXYZ", "status": "not_found", "source": "wikipedia"}
    assert "could not find" in missing.message.lower()
    assert router_missing == "I could not find information on that."

    with patch.object(legacy.wikipedia, "summary", return_value="   "):
        empty = legacy._tool_wiki_summary({"topic": "BlankPage"})
    assert empty.ok is False
    assert empty.data == {"topic": "BlankPage", "status": "empty", "source": "wikipedia"}


def test_wiki_summary_handles_network_errors_without_leaking_exception_details(legacy, capsys):
    secret_exc = requests.RequestException(
        "https://en.wikipedia.org/w/api.php?api_key=sk-fake-secret-value-1234567890123456"  # secret-scan: allow
    )
    with patch.object(legacy.wikipedia, "summary", side_effect=secret_exc):
        res = legacy._tool_wiki_summary({"topic": "Python"})
        router_reply = legacy.wiki("what is Python")

    captured = capsys.readouterr().out
    assert res.ok is False
    assert res.data == {"topic": "Python", "status": "unavailable", "source": "wikipedia"}
    assert res.message == "Wikipedia is unavailable right now."
    assert router_reply == "Wikipedia is unavailable right now."
    assert "sk-fake-secret-value" not in captured

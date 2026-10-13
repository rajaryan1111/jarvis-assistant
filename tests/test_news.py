from unittest.mock import Mock, patch

import requests

from news import get_headlines

FAKE_KEY = "test-" + "news-key"


def test_returns_headline_titles():
    response = Mock()
    response.json.return_value = {
        "status": "ok",
        "articles": [
            {"title": "First headline"},
            {"title": None},
            {"title": "Second headline"},
        ],
    }

    with patch("news.requests.get", return_value=response) as get:
        with patch.dict("os.environ", {"NEWS_API_KEY": FAKE_KEY, "NEWS_COUNTRY": "in"}):
            assert get_headlines() == ["First headline", "Second headline"]
            get.assert_called_once_with(
                "https://newsapi.org/v2/top-headlines",
                params={"country": "in"},
                headers={"X-Api-Key": FAKE_KEY},
                timeout=10,
            )


def test_key_is_not_sent_in_query_string():
    response = Mock()
    response.json.return_value = {"status": "ok", "articles": []}
    with patch("news.requests.get", return_value=response) as get:
        with patch.dict("os.environ", {"NEWS_API_KEY": FAKE_KEY}):
            get_headlines()
    params = get.call_args.kwargs["params"]
    assert FAKE_KEY not in params.values()
    assert "apiKey" not in params


def test_returns_empty_list_without_api_key():
    with patch.dict("os.environ", {}, clear=True):
        assert get_headlines() == []


def test_placeholder_key_is_treated_as_missing():
    with patch.dict("os.environ", {"NEWS_API_KEY": "your_newsapi_key_here"}, clear=True):
        with patch("news.requests.get") as get:
            assert get_headlines() == []
            get.assert_not_called()


def test_handles_network_failures_without_crashing():
    with patch.dict("os.environ", {"NEWS_API_KEY": FAKE_KEY}):
        with patch("news.requests.get", side_effect=requests.RequestException("offline")):
            assert get_headlines() == []


def test_handles_unexpected_json_shapes_without_crashing():
    malformed_payloads = [
        ["not", "an", "object"],
        "not an object",
        {"status": "ok", "articles": "not a list"},
        {"status": "ok", "articles": [None, "not an article", 42]},
    ]
    with patch.dict("os.environ", {"NEWS_API_KEY": FAKE_KEY}):
        for payload in malformed_payloads:
            response = Mock()
            response.json.return_value = payload
            with patch("news.requests.get", return_value=response):
                assert get_headlines() == []

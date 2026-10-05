from unittest.mock import Mock, patch

import requests

from news import get_headlines


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
        with patch.dict("os.environ", {"NEWS_API_KEY": "test-key", "NEWS_COUNTRY": "in"}):
            assert get_headlines() == ["First headline", "Second headline"]
            get.assert_called_once_with(
                "https://newsapi.org/v2/top-headlines",
                params={"country": "in", "apiKey": "test-key"},
                timeout=10,
            )


def test_returns_empty_list_without_api_key():
    with patch.dict("os.environ", {}, clear=True):
        assert get_headlines() == []


def test_handles_network_failures_without_crashing():
    with patch.dict("os.environ", {"NEWS_API_KEY": "test-key"}):
        with patch("news.requests.get", side_effect=requests.RequestException("offline")):
            assert get_headlines() == []

from unittest.mock import Mock, patch

import requests


def test_cricket_returns_valid_match_summary(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "CRICKET_API_KEY", "fake-cricket-key")
    response = Mock()
    response.json.return_value = {
        "status": "success",
        "data": [
            {
                "name": "India vs Australia",
                "teams": ["India", "Australia"],
                "status": "India batting",
                "score": [{"inning": "India", "r": 120, "w": 3, "o": 15}],
            }
        ],
    }
    with patch("main.requests.get", return_value=response) as get:
        result = legacy.get_live_cricket_score("india live score")

    assert result == "India vs Australia. India: 120/3 in 15 overs. Status: India batting."
    response.raise_for_status.assert_called_once_with()
    assert get.call_args.kwargs["timeout"] == 8
    assert get.call_args.kwargs["params"] == {"apikey": "fake-cricket-key", "offset": 0}


def test_cricket_http_failure_returns_unavailable_message(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "CRICKET_API_KEY", "fake-cricket-key")
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("service unavailable")
    with patch("main.requests.get", return_value=response):
        result = legacy.get_live_cricket_score("live score")

    assert result == "I tried to fetch the live cricket score, but something went wrong, sir."
    response.json.assert_not_called()


def test_cricket_malformed_payloads_do_not_crash(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "CRICKET_API_KEY", "fake-cricket-key")
    malformed_payloads = [
        ["not", "an", "object"],
        {"status": "failure", "error": "rate limit"},
        {"status": "success", "data": "not a list"},
        {"status": "success", "data": [None, "bad match"]},
        {"status": "success", "data": [{"name": "Match", "score": [None, "bad innings"]}]},
    ]
    with patch("main.requests.get") as get:
        for payload in malformed_payloads:
            response = Mock()
            response.json.return_value = payload
            get.return_value = response
            result = legacy.get_live_cricket_score("live score")
            assert isinstance(result, str) and result


def test_cricket_without_key_does_not_make_request(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "CRICKET_API_KEY", "")
    with patch("main.requests.get") as get:
        result = legacy.get_live_cricket_score("live score")

    assert "API key is not configured" in result
    get.assert_not_called()

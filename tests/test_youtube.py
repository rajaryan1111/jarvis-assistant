from unittest.mock import Mock, patch

import requests


def test_youtube_search_checks_http_status_and_opens_video(legacy, monkeypatch):
    response = Mock()
    response.text = 'watch?v=abcdefghijk'
    with patch("main.requests.get", return_value=response) as get, patch.object(
        legacy, "_outcome", return_value=Mock(status="completed", message="Opened.")
    ) as open_url:
        result = legacy.play_youtube("test song")

    response.raise_for_status.assert_called_once_with()
    assert get.call_args.kwargs["timeout"] == 5
    assert result == "Playing test song on YouTube."
    assert open_url.call_args.args[:2] == (
        "web.open_url", {"url": "https://www.youtube.com/watch?v=abcdefghijk"}
    )


def test_youtube_http_error_falls_back_to_search_url(legacy):
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("blocked")
    with patch("main.requests.get", return_value=response), patch.object(
        legacy, "_outcome", return_value=Mock(status="completed", message="Opened.")
    ) as open_url:
        result = legacy.play_youtube("test song")

    assert result == "There was an issue, but I opened YouTube search for test song."
    assert open_url.call_args.args[1]["url"] == (
        "https://www.youtube.com/results?search_query=test%20song"
    )

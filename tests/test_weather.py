from unittest.mock import Mock, patch

import requests


def test_weather_uses_http_status_and_validates_payload(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "WEATHER_API_KEY", "test-weather-key")
    response = Mock()
    response.json.return_value = {
        "cod": 200,
        "main": {"temp": 24.8},
        "weather": [{"description": "light rain"}],
    }
    with patch("main.requests.get", return_value=response) as get:
        result = legacy._weather_report("Pune")

    assert result.ok is True
    assert result.data == {"city": "Pune", "temperature_c": 24, "description": "light rain"}
    response.raise_for_status.assert_called_once_with()
    get.assert_called_once_with(
        "https://api.openweathermap.org/data/2.5/weather",
        params={"q": "Pune", "appid": "test-weather-key", "units": "metric"},
        timeout=5,
    )


def test_weather_http_error_is_reported_as_unavailable(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "WEATHER_API_KEY", "test-weather-key")
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("request failed")
    with patch("main.requests.get", return_value=response):
        result = legacy._weather_report("Pune")

    assert result.ok is False
    assert result.message == "Sorry, I could not fetch the weather."
    response.json.assert_not_called()


def test_weather_malformed_payloads_are_not_reported_as_success(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "WEATHER_API_KEY", "test-weather-key")
    malformed_payloads = [
        ["not", "an", "object"],
        {"cod": 200, "main": {}, "weather": [{"description": "clear"}]},
        {"cod": 200, "main": {"temp": "warm"}, "weather": [{"description": "clear"}]},
        {"cod": 200, "main": {"temp": 20}, "weather": []},
        {"cod": 200, "main": {"temp": 20}, "weather": [None]},
        {"cod": 200, "main": {"temp": 20}, "weather": [{"description": None}]},
    ]
    for payload in malformed_payloads:
        response = Mock()
        response.json.return_value = payload
        with patch("main.requests.get", return_value=response):
            result = legacy._weather_report("Pune")
        assert result.ok is False
        assert "incomplete" in result.message or "Weather error" in result.message


def test_weather_without_key_does_not_make_request(legacy, monkeypatch):
    monkeypatch.setattr(legacy, "WEATHER_API_KEY", "")
    with patch("main.requests.get") as get:
        result = legacy._weather_report("Pune")
    assert result.ok is False
    get.assert_not_called()

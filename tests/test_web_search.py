from unittest.mock import Mock, patch

import requests


def test_web_search_decodes_html_entities_and_strips_markup(legacy):
    response = Mock()
    response.text = (
        '<a class="result__a" href="https://example.test">Tom &amp; Jerry</a>'
        '<a class="result__a" href="https://example.test/2"> <b>Second</b> result </a>'
    )
    with patch("main.requests.get", return_value=response) as get:
        titles = legacy._ddg_result_titles("tom and jerry")

    assert titles == ["Tom & Jerry", "Second result"]
    response.raise_for_status.assert_called_once_with()
    get.assert_called_once_with(
        "https://duckduckgo.com/html/?q=tom%20and%20jerry",
        timeout=5,
        headers={"User-Agent": "Mozilla/5.0"},
    )


def test_web_search_http_errors_return_no_results(legacy):
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("temporary failure")
    with patch("main.requests.get", return_value=response):
        assert legacy._ddg_result_titles("python") == []
    response.text = ""
    response.raise_for_status.assert_called_once_with()


def test_web_search_network_errors_return_no_results(legacy):
    with patch("main.requests.get", side_effect=requests.RequestException("offline")):
        assert legacy._ddg_result_titles("python") == []


def test_web_search_preserves_verified_urls_and_unwraps_ddg_redirects(legacy):
    response = Mock()
    response.text = (
        '<a rel="nofollow" class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2F&amp;rut=abc">'
        "Python 3 Documentation</a>"
        '<a href="https://peps.python.org/pep-0008/" class="result__a">PEP 8 Style Guide</a>'
        '<a class="result__a" href="javascript:alert(1)">Unverified Script Link</a>'
        '<a class="result__a" href="relative/path">Relative Link</a>'
    )
    with patch("main.requests.get", return_value=response):
        results = legacy._ddg_results("python docs")
        tool_out = legacy._tool_web_search({"query": "python docs"})

    assert results == [
        {"title": "Python 3 Documentation", "url": "https://docs.python.org/3/", "source": "duckduckgo"},
        {"title": "PEP 8 Style Guide", "url": "https://peps.python.org/pep-0008/", "source": "duckduckgo"},
        {"title": "Unverified Script Link", "url": None, "source": "duckduckgo"},
        {"title": "Relative Link", "url": None, "source": "duckduckgo"},
    ]
    assert tool_out.ok is True
    assert tool_out.data["titles"] == [
        "Python 3 Documentation",
        "PEP 8 Style Guide",
        "Unverified Script Link",
        "Relative Link",
    ]
    assert tool_out.data["results"] == results
    assert "Python 3 Documentation (https://docs.python.org/3/)" in tool_out.message
    assert "PEP 8 Style Guide (https://peps.python.org/pep-0008/)" in tool_out.message
    assert "javascript:" not in tool_out.message
    assert "relative/path" not in tool_out.message


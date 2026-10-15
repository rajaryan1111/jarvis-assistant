"""NewsAPI top headlines. The API key comes from the environment (``NEWS_API_KEY``)."""

import os

import requests

from ultron.config import get_credential

URL = "https://newsapi.org/v2/top-headlines"


def get_headlines():
    """Return headline titles, or [] if not configured or on any failure.

    The key is sent in the ``X-Api-Key`` header rather than the query string so it
    does not end up in URLs, exception messages or proxy logs.
    """
    api_key = get_credential("NEWS_API_KEY")
    if not api_key:
        return []

    try:
        response = requests.get(
            URL,
            params={"country": os.getenv("NEWS_COUNTRY", "in")},
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return []

    # A successful HTTP status does not guarantee a valid API payload. Treat an
    # unexpected JSON shape as unavailable data rather than crashing the caller.
    if not isinstance(data, dict) or data.get("status") != "ok":
        return []

    articles = data.get("articles")
    if not isinstance(articles, list):
        return []

    return [
        article["title"]
        for article in articles
        if isinstance(article, dict) and isinstance(article.get("title"), str) and article["title"].strip()
    ]

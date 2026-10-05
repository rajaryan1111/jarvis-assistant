import os

import requests

URL = "https://newsapi.org/v2/top-headlines"


def get_headlines():
    """Return headline titles without exposing API credentials in source code."""
    api_key = os.getenv("NEWS_API_KEY")
    if not api_key:
        return []

    try:
        response = requests.get(
            URL,
            params={"country": os.getenv("NEWS_COUNTRY", "in"), "apiKey": api_key},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return []

    if data.get("status") != "ok":
        return []

    return [article["title"] for article in data.get("articles", []) if article.get("title")]

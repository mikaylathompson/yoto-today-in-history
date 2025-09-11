from src.app.clients import wikimedia


def test_normalize_and_hash():
    feed = {
        "events": [
            {
                "id": "e1",
                "type": "event",
                "title": "Sample Event",
                "year": 1969,
                "extract": "Moon landing.",
                "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Apollo_11"}},
            }
        ]
    }
    items = wikimedia.normalize_feed(feed)
    assert items and items[0]["title"] == "Sample Event"
    h = wikimedia.feed_hash(feed)
    assert isinstance(h, str) and len(h) == 64

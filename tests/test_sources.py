from src.tools.search import canonicalize_url, normalize_sources


def test_canonicalize_url_removes_fragment_and_tracking_parameters():
    url = "HTTPS://Example.com/path?b=2&utm_source=newsletter&a=1#section"

    assert canonicalize_url(url) == "https://example.com/path?a=1&b=2"


def test_normalize_sources_assigns_content_snapshot_ids_and_bounds_evidence():
    raw = [
        {"title": "Document", "url": "https://example.com/doc?utm_campaign=test", "content": "A" * 2_000},
        {"title": "Document", "url": "https://example.com/doc", "content": "B" * 2_000},
    ]

    normalized = normalize_sources(raw, "fake", "web")

    assert normalized[0]["canonical_url"] == "https://example.com/doc"
    assert normalized[0]["source_id"] != normalized[1]["source_id"]
    assert len(normalized[0]["evidence_excerpt"]) == 1_500
    assert normalized[0]["content_hash"]


def test_normalize_sources_retains_a_no_url_source_with_content_hash():
    normalized = normalize_sources([{"title": "Offline note", "content": "evidence"}], "local", "note")

    assert normalized[0]["url"] is None
    assert normalized[0]["source_id"].startswith("src_")
    assert normalized[0]["provider"] == "local"

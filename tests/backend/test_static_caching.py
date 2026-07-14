"""
Tests for static-asset caching and HTML cache-busting (perf batch).

Behaviour under test:
  - HTML pages are served `no-cache` with an ETag and revalidate to 304.
  - Every local /static reference in a page is rewritten with ?v=<app-version>.
  - Versioned /static assets are cached immutable for a year.
  - Unversioned /static assets get a short cache.
"""
from __future__ import annotations


async def test_html_page_no_cache_with_etag(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers.get("etag")


async def test_html_assets_are_versioned(client):
    r = await client.get("/")
    body = r.text
    # Local assets get ?v=<version> appended at serve time.
    assert "/static/shared.css?v=" in body
    assert "/static/vendor/leaflet/leaflet.js?v=" in body
    # No external CDN references remain (libs are self-hosted).
    assert "unpkg.com" not in body
    assert "cdn.jsdelivr.net" not in body


async def test_html_etag_revalidates_to_304(client):
    r = await client.get("/")
    etag = r.headers["etag"]
    r2 = await client.get("/", headers={"If-None-Match": etag})
    assert r2.status_code == 304


async def test_versioned_static_asset_is_immutable(client):
    r = await client.get("/static/shared.css?v=1.2.3")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_unversioned_static_asset_short_cache(client):
    r = await client.get("/static/shared.css")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=600"


async def test_vendored_libraries_are_served(client):
    """The self-hosted libs must actually exist at their new paths."""
    for path in (
        "/static/vendor/leaflet/leaflet.js",
        "/static/vendor/leaflet/leaflet.css",
        "/static/vendor/chartjs/chart.umd.min.js",
        "/static/vendor/chartjs/chartjs-adapter-date-fns.bundle.min.js",
    ):
        r = await client.get(path)
        assert r.status_code == 200, path

from __future__ import annotations

import pytest

from tools.launcher import Launcher, LauncherError


def test_normalize_allows_http_and_https() -> None:
    assert Launcher.normalize_url("https://example.com") == "https://example.com"
    assert Launcher.normalize_url("http://example.com/path") == "http://example.com/path"


@pytest.mark.parametrize("url", ["example.com", "ftp://example.com", "https://", "file:///tmp/a"])
def test_normalize_blocks_unsafe_or_incomplete_urls(url: str) -> None:
    with pytest.raises(LauncherError):
        Launcher.normalize_url(url)


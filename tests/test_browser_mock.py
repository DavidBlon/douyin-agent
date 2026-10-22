from __future__ import annotations

from pathlib import Path

import pytest

from app.browser import BrowserManager
from app.config import AgentSettings


@pytest.mark.asyncio
async def test_mock_feed_read_like_comment_next(tmp_path):
    fixture = (Path(__file__).parent / "fixtures" / "mock_feed.html").resolve()
    settings = AgentSettings(auto_dm=False, auto_publish=False)
    browser = BrowserManager(
        settings,
        start_url=fixture.as_uri(),
        headless=True,
        profile_dir=tmp_path / "chrome-profile",
    )
    try:
        await browser.start()
        assert await browser.is_logged_in()

        v1 = await browser.read_current(include_comments=True)
        assert v1["aweme_id"] == "123456"
        assert v1["author"] == "作者甲"
        assert "复杂问题" in v1["description"]
        assert "tplv-dy-360p" in v1["cover"]
        assert "tos-cn-i-tsj2vxp0zn" not in v1["cover"]
        assert any("拆解方法" in c for c in v1["comments"])

        liked = await browser.like()
        assert liked["status"] == "sent"

        commented = await browser.comment("第三个例子的取舍很真实，回滚成本那段能展开吗？")
        assert commented["status"] == "sent"

        nxt = await browser.next_video()
        assert nxt == "654321"
        v2 = await browser.read_current(include_comments=False)
        assert v2["author"] == "作者乙"
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_live_or_unknown_card_is_skipped(tmp_path):
    fixture = (Path(__file__).parent / "fixtures" / "mock_live.html").resolve()
    settings = AgentSettings(auto_dm=False, auto_publish=False)
    browser = BrowserManager(
        settings,
        start_url=fixture.as_uri(),
        headless=True,
        profile_dir=tmp_path / "chrome-profile",
    )
    try:
        await browser.start()
        video = await browser.read_current(include_comments=True)
        assert video["is_live"] is True
        assert video["comments"] == []
        assert video["aweme_id"].startswith("web_")
    finally:
        await browser.close()

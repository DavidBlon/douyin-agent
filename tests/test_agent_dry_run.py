from __future__ import annotations

from pathlib import Path

import pytest

from app import agent, db
from app.browser import BrowserManager
from app.config import AgentSettings
from app.llm import Decision


class FakeDecider:
    def __init__(self, *args, **kwargs):
        pass

    async def decide(self, video, recent, counts, done):
        return (
            Decision(
                like=True,
                comment="第三个例子的取舍很真实，回滚成本那段能展开吗？",
                dm="",
                reason="测试判断",
                confidence=0.9,
            ),
            {"total_tokens": 10},
        )


@pytest.mark.asyncio
async def test_agent_dry_run_loop(tmp_path, temp_db, monkeypatch):
    fixture = (Path(__file__).parent / "fixtures" / "mock_feed.html").resolve()
    settings = AgentSettings(
        dry_run=True,
        auto_like=True,
        auto_comment=True,
        auto_dm=False,
        max_videos_per_run=2,
        min_gap_sec=3,
        likes_per_day=30,
        comments_per_day=8,
    )
    db.save_settings(settings)

    runner = agent.AgentRunner()
    runner.browser = BrowserManager(
        settings,
        start_url=fixture.as_uri(),
        headless=True,
        profile_dir=tmp_path / "chrome-profile",
    )
    monkeypatch.setattr(agent, "load_api_key", lambda: "test-key")
    monkeypatch.setattr(agent, "DeepSeekDecider", FakeDecider)

    async def no_wait():
        return True

    runner._sleep_gap = no_wait  # type: ignore[method-assign]
    try:
        result = await runner.run_once()
    finally:
        await runner.browser.close()

    assert result["ok"] is True
    assert result["picked"] == 4
    actions = db.list_actions(page=1, size=20)
    assert actions["total"] == 4
    assert {a["status"] for a in actions["items"]} == {"dry_run"}
    assert {a["kind"] for a in actions["items"]} == {"like", "comment"}
    assert not runner.running

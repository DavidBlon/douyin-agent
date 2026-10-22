"""Screenshot the control room against the real backend + mocked read models.

This does not touch Douyin or send any action. It only renders the UI.
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

from app.config import AgentSettings

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "preview-ui"
OUT.mkdir(exist_ok=True)
BASE = "http://127.0.0.1:8787"


def cover_data_uri() -> str:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
    <defs>
      <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#1b2330"/><stop offset=".55" stop-color="#11151c"/><stop offset="1" stop-color="#24131a"/>
      </linearGradient>
      <radialGradient id="r" cx=".7" cy=".3" r=".7"><stop offset="0" stop-color="#ff2d55" stop-opacity=".35"/><stop offset="1" stop-color="#ff2d55" stop-opacity="0"/></radialGradient>
    </defs>
    <rect width="1280" height="720" fill="url(#g)"/>
    <rect width="1280" height="720" fill="url(#r)"/>
    <g fill="none" stroke="#ffffff" stroke-opacity=".14">
      <path d="M90 540 C 300 380, 520 470, 690 300 S 1000 190, 1200 110"/>
      <path d="M90 580 C 330 430, 560 520, 720 350 S 1030 240, 1220 150"/>
      <circle cx="1010" cy="470" r="150"/>
      <circle cx="1010" cy="470" r="190"/>
    </g>
    <text x="90" y="120" fill="#e9edf3" font-size="46" font-family="Microsoft YaHei">越忙越穷？</text>
    <text x="90" y="170" fill="#8b93a1" font-size="22" font-family="Microsoft YaHei">三个项目复盘里的取舍与代价</text>
    <rect x="90" y="620" width="120" height="3" fill="#ff2d55"/>
    </svg>"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()


def mock_payloads():
    settings = AgentSettings(
        dry_run=False,
        auto_like=True,
        auto_comment=True,
        auto_dm=True,
        max_videos_per_run=8,
        likes_per_day=30,
        comments_per_day=8,
        dms_per_day=3,
        dm_allowlist=["真实项目笔记"],
    ).model_dump()
    settings["has_api_key"] = True
    base = datetime.now()
    now = base.isoformat(timespec="seconds")
    video = {
        "aweme_id": "7412345678901234567",
        "author": "真实项目笔记",
        "author_url": "https://www.douyin.com/user/demo",
        "title": "越忙越穷？三个项目复盘里的取舍",
        "description": "这期不讲鸡汤，拆三个真实项目：为什么看起来很努力，实际一直在补之前的坑；沉没成本怎么让人继续加码；以及什么时候应该停下来换路。",
        "url": "https://www.douyin.com/video/7412345678901234567",
        "cover": cover_data_uri(),
        "stats": {"like": "1.8万", "comment": "426"},
        "comments": [
            "第二个例子的回滚成本太高了，确实不是努力能解决的。",
            "想知道第三个项目最后有没有换路线。",
        ],
        "seen_at": now,
        "day": base.strftime("%Y-%m-%d"),
    }
    decision = {
        "like": True,
        "comment": "第二个例子里的回滚成本很高，这块有更具体的数字吗？",
        "dm": "",
        "reason": "内容跟用户关注的项目复盘相关；评论有具体追问，不做空泛互动。",
        "confidence": 0.86,
        "usage": {"prompt_tokens": 1460, "completion_tokens": 380, "total_tokens": 1840},
        "video_id": video["aweme_id"],
        "ts": now,
        "author": video["author"],
    }
    status = {
        "running": True,
        "stop_requested": False,
        "browser": {
            "started": True,
            "logged_in": True,
            "mock": False,
            "url": "https://www.douyin.com/",
            "risk": [],
            "profile": "C:\\Users\\Administrator\\Documents\\Qoder\\2026-09-24\\813ebd29\\douyin-agent\\data\\chrome-profile",
            "last_error": "",
        },
        "settings": settings,
        "has_api_key": True,
        "today": {"like": 3, "comment": 1, "dm": 0, "publish": 0},
        "current_video": video,
        "last_decision": decision,
    }
    actions = [
        {"id": 18, "kind": "comment", "video_id": "7412345678901234567", "target_name": "真实项目笔记", "target_url": "https://www.douyin.com/user/demo", "content": "第二个例子里的回滚成本很高，这块有更具体的数字吗？", "status": "sent", "policy": "", "result": "评论已发送", "screenshot": "", "dry_run": 0, "day": base.strftime("%Y-%m-%d"), "created_at": now, "executed_at": now},
        {"id": 17, "kind": "like", "video_id": "7412345678901234567", "target_name": "真实项目笔记", "target_url": "https://www.douyin.com/user/demo", "content": "", "status": "sent", "policy": "", "result": "已点击点赞，状态已确认", "screenshot": "", "dry_run": 0, "day": base.strftime("%Y-%m-%d"), "created_at": now, "executed_at": now},
        {"id": 16, "kind": "dm", "video_id": "7411111111111111111", "target_name": "路人甲", "target_url": "https://www.douyin.com/user/stranger", "content": "你好，看到你也在做独立开发，想交流一下。", "status": "blocked", "policy": "对方不在私信白名单里", "result": "", "screenshot": "", "dry_run": 0, "day": base.strftime("%Y-%m-%d"), "created_at": now, "executed_at": now},
        {"id": 15, "kind": "like", "video_id": "7402222222222222222", "target_name": "慢下来实验室", "target_url": "https://www.douyin.com/user/slow", "content": "", "status": "sent", "policy": "", "result": "已点击点赞，状态已确认", "screenshot": "", "dry_run": 0, "day": base.strftime("%Y-%m-%d"), "created_at": now, "executed_at": now},
        {"id": 14, "kind": "comment", "video_id": "7403333333333333333", "target_name": "产品碎碎念", "target_url": "https://www.douyin.com/user/product", "content": "这个拆解方式很适合拿去做周会复盘。", "status": "dry_run", "policy": "", "result": "", "screenshot": "", "dry_run": 1, "day": base.strftime("%Y-%m-%d"), "created_at": now, "executed_at": ""},
        {"id": 13, "kind": "comment", "video_id": "7404444444444444444", "target_name": "一个设计号", "target_url": "https://www.douyin.com/user/design", "content": "第三条的取舍我保留意见，但前半段很有用。", "status": "failed", "policy": "", "result": "没有找到评论输入框，可能页面改版", "screenshot": "", "dry_run": 0, "day": base.strftime("%Y-%m-%d"), "created_at": now, "executed_at": now},
    ]
    events = [
        {"id": 9, "ts": now, "level": "info", "kind": "run_started", "message": "开始刷一轮", "data": {"max_videos": 8, "dry_run": False}},
        {"id": 10, "ts": now, "level": "info", "kind": "video", "message": "正在看：真实项目笔记", "data": {"index": 1, "total": 8}},
        {"id": 11, "ts": now, "level": "info", "kind": "decision", "message": "模型判断完成", "data": decision},
        {"id": 12, "ts": now, "level": "info", "kind": "action", "message": "准备执行 like", "data": {"id": 17, "kind": "like"}},
        {"id": 13, "ts": now, "level": "info", "kind": "action_result", "message": "like → sent", "data": {"id": 17, "kind": "like", "status": "sent", "detail": "已点击点赞，状态已确认"}},
        {"id": 14, "ts": now, "level": "info", "kind": "action", "message": "准备执行 comment", "data": {"id": 18, "kind": "comment"}},
        {"id": 15, "ts": now, "level": "info", "kind": "action_result", "message": "comment → sent", "data": {"id": 18, "kind": "comment", "status": "sent", "detail": "评论已发送"}},
    ]
    return settings, status, actions, events


async def shot_empty(page, errors):
    await page.goto(BASE, wait_until="load")
    await page.wait_for_timeout(1300)
    await page.screenshot(path=str(OUT / "desktop-run-empty.png"))
    await page.get_by_role("button", name="动作记录").click()
    await page.wait_for_timeout(500)
    await page.screenshot(path=str(OUT / "desktop-actions-empty.png"))
    await page.locator(".nav-item").filter(has_text="设置").click()
    await page.wait_for_timeout(500)
    await page.screenshot(path=str(OUT / "desktop-settings-empty.png"), full_page=True)


async def shot_mobile(page, errors):
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(BASE, wait_until="load")
    await page.wait_for_timeout(1200)
    await page.screenshot(path=str(OUT / "mobile-run-empty.png"))
    overflow = await page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    errors.append(f"mobile overflow={overflow}")
    await page.get_by_role("button", name="动作记录").click()
    await page.wait_for_timeout(400)
    await page.screenshot(path=str(OUT / "mobile-actions-empty.png"))
    await page.locator(".nav-item").filter(has_text="设置").click()
    await page.wait_for_timeout(400)
    overflow2 = await page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    errors.append(f"mobile settings overflow={overflow2}")
    await page.screenshot(path=str(OUT / "mobile-settings.png"), full_page=True)


async def shot_populated(context, errors):
    settings, status, actions, events = mock_payloads()

    async def fulfill_status(route):
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(status, ensure_ascii=False))

    async def fulfill_settings(route):
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(settings, ensure_ascii=False))

    async def fulfill_actions(route):
        await route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": actions, "total": len(actions), "page": 1, "size": 20, "pages": 1}, ensure_ascii=False))

    async def fulfill_events(route):
        await route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": events}, ensure_ascii=False))

    page = await context.new_page()
    await page.route("**/api/status", fulfill_status)
    await page.route("**/api/settings", fulfill_settings)
    await page.route("**/api/actions**", fulfill_actions)
    await page.route("**/api/events/recent**", fulfill_events)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    await page.goto(BASE, wait_until="load")
    await page.wait_for_timeout(1200)
    await page.screenshot(path=str(OUT / "desktop-run-populated.png"))
    await page.get_by_role("button", name="动作记录").click()
    await page.wait_for_timeout(500)
    await page.screenshot(path=str(OUT / "desktop-actions-populated.png"))
    row = page.locator(".actions-table tbody tr").first
    if await row.count():
        await row.click()
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(OUT / "desktop-action-drawer.png"))
        await page.keyboard.press("Escape")
    await page.locator(".nav-item").filter(has_text="设置").click()
    await page.wait_for_timeout(400)
    await page.screenshot(path=str(OUT / "desktop-settings-populated.png"), full_page=True)

    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(BASE, wait_until="load")
    await page.wait_for_timeout(900)
    await page.screenshot(path=str(OUT / "mobile-run-populated.png"))
    m_overflow = await page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    errors.append(f"mobile populated overflow={m_overflow}")
    await page.locator(".nav-item").filter(has_text="动作记录").click()
    await page.wait_for_timeout(400)
    await page.screenshot(path=str(OUT / "mobile-actions-populated.png"))
    card = page.locator(".action-card").first
    if await card.count():
        await card.click()
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(OUT / "mobile-action-drawer.png"))
        await page.keyboard.press("Escape")
    await page.close()


async def main():
    errors: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = await context.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
        await shot_empty(page, errors)
        await shot_mobile(page, errors)
        await context.close()

        context2 = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        await shot_populated(context2, errors)
        await context2.close()
        await browser.close()
    print("errors:", errors)
    print("screenshots:", sorted(p.name for p in OUT.glob("*.png")))


asyncio.run(main())

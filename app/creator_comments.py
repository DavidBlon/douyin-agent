from __future__ import annotations

import re
from typing import Any

from . import db, policy
from .config import AgentSettings
from .events import bus
from .llm import DeepSeekDecider, LLMError

URL = "https://creator.douyin.com/creator-micro/interactive/comment"
KEY = "creator-comments"
ROW_RE = re.compile(r"^(?P<author>\S+)\s+(?P<time>[^\s]+前|刚刚)\s+(?P<comment>.*?)\s+\d+\s+回复")


def recent(text: str) -> bool:
    value = (text or "").strip()
    if "刚刚" in value:
        return True
    match = re.search(r"(\d+)\s*分钟前", value)
    if match:
        return int(match.group(1)) <= 180
    match = re.search(r"(\d+)\s*小时前", value)
    if match:
        return int(match.group(1)) <= 3
    return False


async def expand(page) -> None:
    for _ in range(8):
        clicked = await page.evaluate(
            """() => {
                const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                const node = [...document.querySelectorAll('div,span')].find(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && /^查看\\d+条回复$/.test(clean(el.innerText));
                });
                if (!node) return false;
                node.click();
                return true;
            }"""
        )
        if not clicked:
            return
        await page.wait_for_timeout(650)


async def read_rows(page) -> list[dict[str, Any]]:
    data = await page.evaluate(
        """() => {
            const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
            const visible = el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            };
            return [...document.querySelectorAll('div,span')]
                .filter(el => visible(el) && clean(el.innerText) === '回复')
                .map((el, index) => {
                    let row = el;
                    for (let i = 0; i < 6 && row; i++, row = row.parentElement) {
                        if (clean(row.innerText).length > 12) break;
                    }
                    const text = clean(row ? row.innerText : '');
                    el.setAttribute('data-dy-creator-reply', String(index));
                    return { index, text: text.slice(0, 260) };
                });
        }"""
    )
    out: list[dict[str, Any]] = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        match = ROW_RE.match(str(item.get("text") or ""))
        if not match:
            continue
        out.append(
            {
                "index": int(item.get("index") or 0),
                "text": str(item.get("text") or ""),
                "author": match.group("author"),
                "time": match.group("time"),
                "comment": match.group("comment"),
            }
        )
    return out


async def send(page, index: int, text: str) -> tuple[str, str]:
    try:
        await page.locator(f'[data-dy-creator-reply="{index}"]').first.click(timeout=5000)
        await page.wait_for_timeout(900)
        editor = page.locator('div.input-d24X73[placeholder^="回复"]').last
        if not await editor.count():
            return "failed", "回复输入框没有出现"
        await editor.click(timeout=4000)
        await page.keyboard.insert_text(text)
        await page.wait_for_timeout(500)
        button = page.locator(".douyin-creator-interactive-button").filter(has_text="发送").last
        if not await button.count():
            return "failed", "没有找到发送按钮"
        await button.click(timeout=5000)
        await page.wait_for_timeout(1600)
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if text[:10] in body:
            return "sent", "创作者中心评论回复已发送"
        return "uncertain", "已点击发送，未读到确认"
    except Exception as exc:
        return "failed", str(exc)[:200]


async def collect(page, settings: AgentSettings) -> list[dict[str, Any]]:
    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(4000)
    await expand(page)
    items = await read_rows(page)
    for item in items[:10]:
        print(
            f"[creator] {item['author']!r} {item['time']!r} {item['comment'][:40]!r}",
            flush=True,
        )
    agent = (settings.agent_name or "").strip() or "我"
    return [
        item
        for item in items
        if item["author"] != agent
        and item["comment"].strip()
        and recent(item["time"])
    ]


async def reply_one(page, settings: AgentSettings, decider: DeepSeekDecider, item: dict[str, Any]) -> int:
    author = item["author"]
    comment = item["comment"]
    if db.comment_reply_exists(comment, max_age_seconds=86400):
        return 0
    context = {
        "title": "",
        "video_author": (settings.agent_name or "").strip() or "我",
        "notification": item["text"],
        "contact": author,
        "comment": comment,
        "our_comment": "",
    }
    try:
        decision, _usage = await decider.reply("comment", context, db.recent_replies(6))
    except LLMError as exc:
        await bus.emit("reply", f"评论回复模型调用失败：{author}", {"error": str(exc)[:200]}, level="error")
        return 0
    reply = policy.clean_text(decision.reply, settings.max_comment_chars)
    if not reply:
        return 0
    check = policy.check_comment_reply(author, reply, settings)
    if not check.allowed:
        await bus.emit("reply", f"评论回复被策略拦截：{check.reason}", {"contact": author}, level="warn")
        return 0
    if settings.dry_run:
        await bus.emit("reply", f"预演评论回复 → {author}：{reply}", {"contact": author, "dry_run": True})
        return 0
    status, detail = await send(page, item["index"], reply)
    db.add_reply(
        kind="reply_comment",
        target_key=KEY,
        incoming=comment,
        reply=reply,
        status=status,
    )
    action_id = db.add_action(
        kind="reply_comment",
        video_id="",
        target_name=author,
        target_url=URL,
        content=comment,
        status=status,
        policy=decision.reason,
        dry_run=False,
    )
    db.update_action(action_id, result=detail[:400], executed_at=db.now_iso())
    await bus.emit(
        "reply",
        f"已回复评论 {author}：{reply}" if status == "sent" else f"评论回复{status}：{author}",
        {"contact": author, "comment": comment[:120], "reply": reply, "status": status},
        level="info" if status == "sent" else "warn",
    )
    return 1 if status in ("sent", "uncertain") else 0


async def run(browser, settings: AgentSettings) -> int:
    page = await browser._new_fan_page()
    try:
        items = await collect(page, settings)
        if not items:
            return 0
        decider = DeepSeekDecider(settings)
        done = 0
        for item in items[:2]:
            done += await reply_one(page, settings, decider, item)
        return done
    finally:
        try:
            await page.close()
        except Exception:
            pass

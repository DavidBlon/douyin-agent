from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Any

from . import compliance, db
from .agent import runner
from .config import MEDIA_DIR
from .events import bus
from .llm import DeepSeekDecider, LLMError

CONTENT_DIR = MEDIA_DIR / "content"
COVER_DIR = CONTENT_DIR / "covers"
VIDEO_DIR = CONTENT_DIR / "videos"
UPLOAD_DIR = CONTENT_DIR / "uploads"
ASSET_COVER_DIR = Path(__file__).resolve().parent / "assets" / "covers"

COVER_STYLES: dict[str, tuple[str, str]] = {
    "desk": ("notes-desk.jpg", "书桌"),
    "tech": ("ai-network.jpg", "科技"),
    "whale": ("whale.jpg", "鲸鱼"),
    "notes": ("notes-flow.jpg", "纸页"),
    "workplace": ("workplace.jpg", "职场"),
    "study": ("study.jpg", "学习"),
    "food": ("food.jpg", "美食"),
    "city": ("city.jpg", "城市"),
    "review": ("review.jpg", "测评"),
    "studio": ("studio.jpg", "口播"),
    "music": ("music.jpg", "音乐"),
}

CONTENT_FIELDS = (
    "plan_date", "slot", "pillar", "topic", "angle", "script", "caption",
    "hashtags", "cover_text", "cover_bg", "music_note", "source_notes",
    "publish_at", "video_path",
)


def ensure_content_dirs() -> None:
    for path in (CONTENT_DIR, COVER_DIR, VIDEO_DIR, UPLOAD_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^0-9A-Za-z._-]+", "_", name or "file")
    return name[-80:] or "file"


def media_path(relative: str) -> Path | None:
    relative = (relative or "").strip().replace("\\", "/").lstrip("/")
    if not relative:
        return None
    path = (MEDIA_DIR / relative).resolve()
    try:
        path.relative_to(MEDIA_DIR.resolve())
    except ValueError:
        return None
    return path


def _public_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    item = dict(plan)
    item["cover_url"] = f"/media/{item['cover_path']}" if item.get("cover_path") else ""
    item["video_url"] = f"/media/{item['video_path']}" if item.get("video_path") else ""
    return item


async def generate_plan(
    *,
    brief: str,
    pillar: str = "",
    plan_date: str = "",
    slot: str = "",
) -> dict[str, Any]:
    settings = db.load_settings()
    references = db.list_works_with_latest(6)
    decider = DeepSeekDecider(settings)
    draft, usage = await decider.generate_content(
        brief,
        pillar=pillar,
        plan_date=plan_date or db.today_key(),
        reference_works=references,
    )
    notes = draft.notes.strip()
    if draft.continuity.strip():
        notes = (notes + "\n人设延续：" + draft.continuity.strip()).strip()
    hashtags = [
        str(tag).strip().lstrip("#")
        for tag in (draft.hashtags or [])
        if str(tag).strip().lstrip("#")
    ][:6]
    plan_id = db.create_content_plan(
        {
            "plan_date": plan_date or db.today_key(),
            "slot": slot,
            "pillar": draft.pillar or pillar,
            "topic": draft.topic,
            "angle": draft.angle,
            "script": draft.script,
            "caption": draft.caption,
            "hashtags": hashtags,
            "cover_text": draft.cover_text,
            "source_notes": notes,
            "status": "draft",
        }
    )
    plan = db.get_content_plan(plan_id)
    cover_error = ""
    if plan:
        try:
            path = await render_cover(plan)
            plan = db.update_content_plan(plan_id, cover_path=path) or plan
        except Exception as exc:
            cover_error = str(exc)[:200]
    await bus.emit(
        "content",
        f"生成内容计划：{(plan or {}).get('topic') or brief[:40]}",
        {"plan_id": plan_id, "usage": usage, "cover_error": cover_error},
    )
    result = _public_plan(plan) or {}
    result["usage"] = usage
    result["cover_error"] = cover_error
    return result


def _pick_cover_style(plan: dict[str, Any]) -> str:
    requested = str(plan.get("cover_bg") or "").strip()
    if requested in COVER_STYLES:
        return requested
    text = " ".join(
        str(plan.get(key) or "")
        for key in ("topic", "pillar", "angle", "cover_text")
    ).lower()
    if any(word in text for word in ("鲸", "海洋", "深海", "鲸鱼娘")):
        return "whale"
    if any(word in text for word in ("美食", "米饭", "吃", "餐", "食谱", "探店", "下饭")):
        return "food"
    if any(word in text for word in ("学习", "读书", "知识", "考试", "课程", "书单", "阅读")):
        return "study"
    if any(word in text for word in ("笔记", "文章", "写作", "整理", "纸", "记录")):
        return "notes"
    if any(word in text for word in ("职场", "工作", "办公", "效率", "简历", "面试", "打工人", "同事")):
        return "workplace"
    if any(word in text for word in ("城市", "旅行", "夜景", "街", "生活", "散步", "雨天", "通勤")):
        return "city"
    if any(word in text for word in ("音乐", "歌单", "耳机", "播放器", "简云", "听歌", "歌曲", "唱片")):
        return "music"
    if any(
        word in text
        for word in (
            "开箱", "手机", "相机", "数码", "耳机", "硬件", "平板", "iphone",
            "android", "测评", "评测",
        )
    ):
        return "review"
    if any(word in text for word in ("观点", "口播", "聊天", "思考", "访谈", "播客", "话题", "评论")):
        return "studio"
    if any(word in text for word in ("ai", "模型", "数据", "代码", "工具", "科技", "智能")):
        return "tech"
    return "desk"


def _cover_data_url(style: str) -> str:
    filename = COVER_STYLES.get(style, COVER_STYLES["desk"])[0]
    path = ASSET_COVER_DIR / filename
    if not path.exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def cover_style(plan: dict[str, Any]) -> str:
    return _pick_cover_style(plan)


def cover_background_path(plan: dict[str, Any]) -> Path:
    return ASSET_COVER_DIR / COVER_STYLES.get(cover_style(plan), COVER_STYLES["desk"])[0]


async def render_cover(plan: dict[str, Any]) -> str:
    ensure_content_dirs()
    browser = runner.browser
    await browser.ensure_started()
    page = await browser.new_page()
    page.set_default_timeout(20000)
    title = str(plan.get("cover_text") or plan.get("topic") or "今天聊点什么").strip()
    angle = str(plan.get("angle") or plan.get("caption") or "").strip()
    pillar = str(plan.get("pillar") or "").strip()
    name = str(db.load_settings().agent_name or "内容计划").strip()
    date = str(plan.get("plan_date") or "").strip()
    style = _pick_cover_style(plan)
    photo = _cover_data_url(style)
    photo_css = f'background-image: url("{photo}");' if photo else ""
    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; width: 1080px; height: 1440px; overflow: hidden; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
    background: #05070d; color: #f4f7ff;
  }}
  .cover {{
    position: relative; width: 1080px; height: 1440px; padding: 92px 84px 76px;
    background:
      radial-gradient(900px 620px at 88% 8%, rgba(37,244,238,.20), transparent 60%),
      radial-gradient(760px 720px at 4% 96%, rgba(254,44,85,.16), transparent 62%),
      linear-gradient(145deg, #0a1020 0%, #05070d 55%, #0b0d16 100%);
    overflow: hidden;
  }}
  .photo {{ position: absolute; inset: 0; background-size: cover; background-position: center; {photo_css} }}
  .scrim {{
    position: absolute; inset: 0;
    background:
      linear-gradient(90deg, rgba(5,7,13,.97) 0%, rgba(5,7,13,.86) 38%, rgba(5,7,13,.42) 74%, rgba(5,7,13,.20) 100%),
      linear-gradient(0deg, rgba(5,7,13,.92) 0%, rgba(5,7,13,0) 42%),
      linear-gradient(180deg, rgba(5,7,13,.55) 0%, rgba(5,7,13,0) 28%);
  }}
  .grid {{
    position: absolute; inset: 0; opacity: .28;
    background-image:
      linear-gradient(rgba(255,255,255,.05) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,.05) 1px, transparent 1px);
    background-size: 72px 72px;
    mask-image: linear-gradient(to bottom, rgba(0,0,0,.8), transparent 80%);
  }}
  .side {{
    position: absolute; right: 58px; top: 150px; writing-mode: vertical-rl;
    font-size: 24px; letter-spacing: 12px; color: rgba(255,255,255,.34);
  }}
  .top {{ position: relative; display: flex; align-items: center; justify-content: space-between; }}
  .pill {{ border: 1px solid rgba(37,244,238,.65); background: rgba(5,7,13,.35); color: #25F4EE; padding: 10px 18px; border-radius: 999px; font-size: 26px; letter-spacing: 2px; backdrop-filter: blur(6px); }}
  .date {{ font: 22px/1.4 ui-monospace, Consolas, monospace; color: rgba(255,255,255,.58); }}
  .rule {{ position: relative; height: 8px; width: 132px; background: #FE2C55; margin-top: 120px; border-radius: 99px; }}
  .title {{
    position: relative; margin-top: 56px; max-width: 780px;
    font-size: 122px; line-height: 1.08; letter-spacing: 2px; font-weight: 900;
    text-wrap: balance; overflow-wrap: anywhere;
    text-shadow: 0 8px 34px rgba(0,0,0,.55);
  }}
  .angle {{ position: relative; margin-top: 54px; max-width: 680px; color: rgba(255,255,255,.72); font-size: 34px; line-height: 1.65; }}
  .bottom {{ position: absolute; left: 84px; right: 84px; bottom: 66px; display: flex; align-items: flex-end; justify-content: space-between; }}
  .brand {{ font-size: 30px; color: rgba(255,255,255,.82); letter-spacing: 2px; }}
  .brand b {{ color: #FE2C55; }}
  .mark {{ font: 19px/1.4 ui-monospace, Consolas, monospace; color: rgba(255,255,255,.36); text-align: right; }}
  .style {{ color: rgba(255,255,255,.32); font-size: 20px; letter-spacing: 4px; }}
</style></head>
<body><div class="cover">
  <div class="photo"></div><div class="scrim"></div><div class="grid"></div>
  <div class="side">{html.escape(pillar or "CONTENT")}</div>
  <div class="top"><div class="pill">{html.escape(pillar or "内容计划")}</div><div class="date">{html.escape(date)}</div></div>
  <div class="rule"></div>
  <div class="title">{html.escape(title)}</div>
  <div class="angle">{html.escape(angle[:90])}</div>
  <div class="bottom"><div class="brand"><b>●</b> {html.escape(name)}<div class="style">AI COVER · {html.escape(COVER_STYLES[style][1])}</div></div><div class="mark">DOUYIN<br>CONTENT PLAN</div></div>
</div></body></html>"""
    await page.set_content(html_doc, wait_until="load")
    try:
        await page.set_viewport_size({"width": 1080, "height": 1440})
    except Exception:
        pass
    await page.wait_for_timeout(450)
    path = COVER_DIR / f"plan_{int(plan.get('id') or 0)}_{db.today_key()}.png"
    await page.screenshot(path=str(path))
    try:
        await page.close()
    except Exception:
        pass
    return str(path.relative_to(MEDIA_DIR)).replace("\\", "/")


async def approve_plan(plan_id: int) -> dict[str, Any]:
    plan = db.get_content_plan(plan_id)
    if not plan:
        raise ValueError("内容计划不存在")
    report = compliance.check_plan(plan)
    if report["blocking"]:
        updated = db.update_content_plan(
            plan_id, status="needs_review", compliance=report, error="合规检查未通过"
        )
        await bus.emit(
            "content",
            f"内容未通过合规检查：{plan.get('topic') or plan_id}",
            {"plan_id": plan_id, "blocking": report["blocking"]},
            level="warn",
        )
        return _public_plan(updated) or plan
    updated = db.update_content_plan(
        plan_id, status="approved", compliance=report, error=""
    )
    await bus.emit(
        "content",
        f"内容已通过审批：{plan.get('topic') or plan_id}",
        {"plan_id": plan_id, "warnings": report["warnings"]},
    )
    return _public_plan(updated) or plan


async def reject_plan(plan_id: int, reason: str = "") -> dict[str, Any]:
    plan = db.update_content_plan(
        plan_id, status="rejected", error=reason[:300] or "已驳回"
    )
    if not plan:
        raise ValueError("内容计划不存在")
    await bus.emit("content", f"内容已驳回：{plan.get('topic') or plan_id}", {"plan_id": plan_id})
    return _public_plan(plan) or plan


def save_upload(plan_id: int, kind: str, filename: str, data: bytes) -> str:
    ensure_content_dirs()
    suffix = Path(filename or "").suffix.lower()
    allowed = {
        "video": {".mp4", ".mov", ".m4v", ".webm"},
        "cover": {".png", ".jpg", ".jpeg", ".webp"},
    }
    if suffix not in allowed.get(kind, set()):
        raise ValueError(f"不支持的文件类型：{suffix or '未知'}")
    folder = VIDEO_DIR if kind == "video" else UPLOAD_DIR
    path = folder / f"plan_{plan_id}_{db.now_iso().replace(':', '').replace('-', '')}{suffix}"
    path.write_bytes(data)
    return str(path.relative_to(MEDIA_DIR)).replace("\\", "/")


def list_plans(date_from: str = "", date_to: str = "", status: str = "") -> list[dict[str, Any]]:
    return [_public_plan(plan) or {} for plan in db.list_content_plans(date_from, date_to, status)]


def get_plan(plan_id: int) -> dict[str, Any] | None:
    return _public_plan(db.get_content_plan(plan_id))


def update_plan(plan_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    plan = db.get_content_plan(plan_id)
    if not plan:
        return None
    updates = {key: fields[key] for key in CONTENT_FIELDS if key in fields}
    if updates and plan.get("status") not in ("idea", "draft", "rejected"):
        updates["status"] = "review"
        updates["compliance"] = {}
    updated = db.update_content_plan(plan_id, **updates)
    return _public_plan(updated)


def media_rel_from_path(value: str) -> str:
    value = (value or "").strip().replace("\\", "/")
    if value.startswith("/media/"):
        return value[len("/media/"):]
    return value


async def prepare_publish(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate before handing the plan to the browser publisher."""
    if not plan:
        raise ValueError("内容计划不存在")
    if plan.get("status") != "approved":
        raise ValueError("内容还没有通过审批")
    path = media_path(media_rel_from_path(str(plan.get("video_path") or "")))
    if path is None or not path.exists():
        raise ValueError("还没有上传可发布的视频文件")
    report = compliance.check_plan(plan)
    if report["blocking"]:
        raise ValueError("内容合规检查未通过")
    return {"plan": plan, "video": path, "compliance": report}

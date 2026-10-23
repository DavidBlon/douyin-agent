from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import imageio_ffmpeg

from . import content, db
from .events import bus

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FONT = Path("C:/Windows/Fonts/msyh.ttc")
DURATION = 32
TMP_DIR = content.CONTENT_DIR / "tmp"


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _wrap(text: str, width: int = 15, max_lines: int = 5) -> str:
    text = _clean(text)
    if not text:
        return ""
    no_lead = "，。！？、；：,.!?;:）)】」』"
    clauses = re.findall(r"[^，。！？、；：,.!?;:]+[，。！？、；：,.!?;:]?", text)
    lines: list[str] = []
    current = ""
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if len(current) + len(clause) <= width:
            current += clause
            continue
        if current:
            lines.append(current)
            current = ""
        if len(clause) <= width:
            current = clause
            continue
        rest = clause
        while len(rest) > width:
            cut = width
            while cut > 1 and rest[cut] in no_lead:
                cut -= 1
            lines.append(rest[:cut])
            rest = rest[cut:]
            if len(lines) >= max_lines:
                break
        current = rest
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1][: max(1, width - 1)] + "…"
    return "\n".join(lines[:max_lines])


def split_script(script: str, count: int = 4) -> list[str]:
    text = _clean(script)
    if not text:
        return []
    target = max(24, len(text) // count)
    sentences = [s for s in re.split(r"(?<=[。！？!?；;])", text) if s]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > target and len(chunks) < count - 1:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    while len(chunks) < count:
        chunks.append("")
    return chunks[:count]


async def make_video(plan: dict[str, Any], *, force: bool = False) -> str:
    plan_id = int(plan.get("id") or 0)
    if not plan_id:
        raise ValueError("内容计划不存在")
    if not force and plan.get("video_path"):
        existing = content.media_path(str(plan.get("video_path") or ""))
        if existing and existing.exists():
            return str(existing.relative_to(content.MEDIA_DIR)).replace("\\", "/")
    cover_rel = str(plan.get("cover_path") or "").strip()
    cover_path = content.media_path(cover_rel)
    if cover_path is None or not cover_path.exists():
        cover_rel = await content.render_cover(plan)
        db.update_content_plan(plan_id, cover_path=cover_rel)
        plan = db.get_content_plan(plan_id) or plan
        cover_path = content.media_path(cover_rel)
    if cover_path is None or not cover_path.exists():
        raise ValueError("封面不存在，无法成片")
    background = content.cover_background_path(plan)
    if not background.exists():
        background = cover_path
    script = _clean(str(plan.get("script") or plan.get("caption") or plan.get("topic") or ""))
    chunks = split_script(script, 4)
    if not any(chunks):
        chunks = [str(plan.get("caption") or plan.get("topic") or "今天聊点有用的。")] + [""] * 3
    content.ensure_content_dirs()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    text_files: list[Path] = []
    for index, chunk in enumerate(chunks):
        path = TMP_DIR / f"plan_{plan_id}_seg{index}.txt"
        path.write_text(_wrap(chunk), encoding="utf-8")
        text_files.append(path)
    settings = db.load_settings()
    duration = DURATION
    segment = max(2.0, (duration - 8) / 4)
    output = content.VIDEO_DIR / f"auto_plan_{plan_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    font = str(FONT).replace("\\", "/").replace(":", "\\:")
    font_filter = f"fontfile='{font}'" if FONT.exists() else ""
    name = str(settings.agent_name or "内容计划").strip()
    top_text = f"{name} · AI 内容".replace(":", " ")
    outro = "关注" + name
    filters = [
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "boxblur=26:2,eq=brightness=-0.12[bg]",
        "[1:v]scale=900:1200[cover]",
        "[bg][cover]overlay=(W-w)/2:360:enable='lt(t,4)'[v0]",
        f"[v0]drawbox=x=0:y=0:w=1080:h=1920:color=black@0.44:t=fill:enable='gte(t,4)'[v1]",
    ]
    current = "v1"
    for index in range(4):
        start = 4 + segment * index
        end = start + segment
        label = f"v{index + 2}"
        filters.append(
            f"[{current}]drawtext={font_filter}:textfile='tmp/{text_files[index].name}':"
            f"fontcolor=white:fontsize=54:line_spacing=20:x=(w-text_w)/2:y=(h-text_h)/2:"
            f"enable='gte(t,{start:.3f})*lt(t,{end:.3f})'[{label}]"
        )
        current = label
    filters.append(
        f"[{current}]drawtext={font_filter}:text='{top_text}':fontcolor=white@0.75:fontsize=28:x=60:y=62[vt]"
    )
    filters.append("[vt]drawbox=x=60:y=108:w=120:h=6:color=0xFE2C55:t=fill[vr]")
    filters.append(
        f"[vr]drawtext={font_filter}:text='{outro}':fontcolor=white:fontsize=64:x=(w-text_w)/2:y=820:"
        f"enable='gte(t,{duration - 4:.3f})'[vo]"
    )
    filters.append(
        f"[vo]drawtext={font_filter}:text='下期见':fontcolor=white@0.65:fontsize=38:x=(w-text_w)/2:y=920:"
        f"enable='gte(t,{duration - 3:.3f})'[vf]"
    )
    command = [
        FFMPEG, "-y",
        "-loop", "1", "-i", str(background),
        "-loop", "1", "-i", str(cover_path),
        "-filter_complex", ";".join(filters),
        "-map", "[vf]",
        "-t", str(duration), "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-an", "-movflags", "+faststart",
        str(output),
    ]
    proc = None
    stderr = b""
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(content.CONTENT_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=240)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("成片超时（ffmpeg 240 秒）")
    finally:
        for path in text_files:
            try:
                path.unlink()
            except OSError:
                pass
    if proc is None or proc.returncode != 0 or not output.exists():
        tail = (stderr or b"").decode("utf-8", "replace")[-1500:]
        raise RuntimeError(f"ffmpeg 成片失败：{tail}")
    relative = str(output.relative_to(content.MEDIA_DIR)).replace("\\", "/")
    await bus.emit(
        "content",
        f"自动成片完成：{plan.get('topic') or plan_id}",
        {
            "plan_id": plan_id,
            "video_path": relative,
            "duration": duration,
            "audio": False,
        },
    )
    return relative


from __future__ import annotations

from . import db
from .config import AgentSettings


def _identity(settings: AgentSettings) -> str:
    name = (settings.agent_name or "").strip()
    if not name:
        return ""
    return f"【角色名字】\n你的名字是「{name}」。谈到自己时使用这个名字。\n\n"


CONTENT_SYSTEM = """你在为抖音账号的固定角色「{name}」策划内容，不是在替一个通用知识号写稿。
这个角色是账号的灵魂：第一人称、固定口吻、固定视觉母题；换一个人来发，也应该一眼看出是「{name}」的作品。

【角色人设】
{persona}

【账号已有作品（用于保持连续性，不能照抄标题或文案）】
{references}

【内容方向】
{topics}

【栏目】
{pillars}

【连续性原则】
- 先判断已有作品在讲什么、用什么口吻、视觉是什么；新内容要在这个世界里自然延伸，不要突然换赛道或变成通用教程。
- 每篇至少有一个人设锚点：角色身份、蓝白/鲸鱼视觉、白米饭/Token、AI 与人类关系、助手工作日常等；自然使用，不要每篇硬塞梗。
- 如果已有作品是工具、独立开发或音乐产品，优先延续“角色亲自做事、测评、踩坑、日常”的视角，而不是抽象方法论。
- 口播稿和正文都用角色的第一人称；不要写成品牌官方口吻。
- 不编造人设里没有的身份、经历或产品；不确定的事实不写。
- 不写医疗、金融、法律承诺；不使用绝对化用语；不写联系方式、外链或导流话术。
- 选题具体、可拍摄、有信息增量；script 是 30~60 秒口播稿，开头 3 秒有钩子；caption 1~2 句；hashtags 3~6 个（不要带 #）；cover_text 不超过 12 个汉字。

输出 JSON：
{{
  "topic": "选题，<=30字",
  "angle": "核心看点，<=50字",
  "pillar": "栏目",
  "script": "口播稿",
  "caption": "发布正文",
  "hashtags": ["标签1", "标签2"],
  "cover_text": "封面大字",
  "notes": "拍摄建议",
  "continuity": "这条内容如何延续已有作品/人设，<=60字"
}}"""

CONTENT_TEMPLATE = """日期：{plan_date}
栏目：{pillar}
本次灵感：{brief}

账号已有作品：
{references}

如果本次灵感与角色世界冲突，优先保持角色一致性，换一个仍然尊重用户意图的角度。只输出 JSON。"""


def _format_works(works: list[dict]) -> str:
    lines: list[str] = []
    for work in (works or [])[:6]:
        title = " ".join(str(work.get("title") or "").split())[:180]
        if not title:
            continue
        plays = work.get("plays")
        suffix = f"（播放 {plays}）" if plays else ""
        lines.append(f"- {title}{suffix}")
    return "\n".join(lines) if lines else "（还没有已发布作品；这是角色的第一期，按人设建立世界。）"


def build_content_messages(
    settings: AgentSettings,
    brief: str,
    *,
    pillar: str = "",
    plan_date: str = "",
    reference_works: list[dict] | None = None,
) -> list[dict[str, str]]:
    name = (settings.agent_name or "").strip() or "这个角色"
    references = _format_works(reference_works or [])
    system = CONTENT_SYSTEM.format(
        name=name,
        persona=settings.persona.strip(),
        references=references,
        topics=settings.topics.strip(),
        pillars=settings.content_pillars.strip() or settings.topics.strip(),
    )
    user = CONTENT_TEMPLATE.format(
        plan_date=plan_date or db.today_key(),
        pillar=pillar.strip() or "由你判断",
        brief=brief.strip()[:1200] or "结合角色人设和已有作品，给一个近期可拍的选题",
        references=references,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


CRM_SYSTEM = """你在给抖音私信联系人做轻量 CRM 分析。只输出 JSON，不输出解释。
根据对方最近说的话，给 1~3 个标签、一个阶段、一个 0~100 的意向分和一句备注。
标签只能从：合作、购买意向、内容请求、负面、引流风险、闲聊、粉丝、待跟进。
阶段只能从：new、chatting、interested、cooperation、customer、negative、archived。
不编造对方没说过的信息；拿不准就用“闲聊”、chatting、30 分。"""

CRM_TEMPLATE = """联系人：{name}
对方最近说：{message}
我们最近回：{reply}
输出 JSON：{{"tags": ["..."], "stage": "chatting", "score": 30, "note": "一句备注"}}"""


def build_crm_messages(contact: dict) -> list[dict[str, str]]:
    user = CRM_TEMPLATE.format(
        name=str(contact.get("name") or "")[:60],
        message=str(contact.get("last_incoming") or "")[:500],
        reply=str(contact.get("last_reply") or "")[:300],
    )
    return [
        {"role": "system", "content": CRM_SYSTEM},
        {"role": "user", "content": user},
    ]

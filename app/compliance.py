from __future__ import annotations

import re
from typing import Any

from . import db

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_CONTACT_WORDS = ("微信", "加微", "加V", "加v", "vx", "VX", "QQ", "二维码", "扫码", "手机号", "电话")
_ABSOLUTE_WORDS = (
    "顶级", "绝对", "100%", "百分百", "保证", "永久", "稳赚",
    "零风险", "包治", "根治", "治愈", "药到病除", "全网最低", "史上最",
)
_ABSOLUTE_PATTERNS = (
    re.compile(r"最(?:好|佳|强|低|高|大|小|便宜|有效|快|专业|权威|正宗|先进|畅销|受欢迎|值得|划算|顶级|优)"),
    re.compile(r"第一(?!步|次|个|条|章|部分|时间|天|周|月|年|阶段|件事|期|批|轮|遍)"),
    re.compile(r"排名第一|销量第一|全网第一"),
)
_RISK_WORDS = {
    "医疗": ("治疗", "治愈", "药效", "处方", "医院", "医生推荐", "降血糖", "降血压"),
    "金融": ("稳赚", "保本", "收益保证", "内幕", "荐股", "炒币", "贷款包过"),
    "法律": ("包赢", "保证胜诉", "关系案", "花钱捞人"),
    "成人": ("裸聊", "约炮", "色情", "成人视频"),
}
_AD_WORDS = ("价格", "售价", "下单", "购买", "优惠", "团购", "链接", "橱窗", "带货", "品牌方")


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def check_text(text: str) -> dict[str, list[dict[str, str]]]:
    blocking: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    body = (text or "").strip()
    if not body:
        return {"blocking": blocking, "warnings": warnings}
    for word in _CONTACT_WORDS:
        if word in body:
            blocking.append(_issue("contact", f"含导流/联系方式词：{word}"))
            break
    if _PHONE_RE.search(body):
        blocking.append(_issue("phone", "含手机号"))
    if _URL_RE.search(body):
        blocking.append(_issue("link", "含外部链接"))
    for word in _ABSOLUTE_WORDS:
        if word in body:
            blocking.append(_issue("absolute", f"含绝对化/承诺用语：{word}"))
            break
    else:
        hit = next((m.group(0) for p in _ABSOLUTE_PATTERNS for m in p.finditer(body)), "")
        if hit:
            blocking.append(_issue("absolute", f"含绝对化/承诺用语：{hit}"))
    for category, words in _RISK_WORDS.items():
        hit = next((w for w in words if w in body), "")
        if hit:
            blocking.append(_issue("risk", f"含{category}高风险表述：{hit}"))
    if any(word in body for word in _AD_WORDS) and "#广告" not in body:
        warnings.append(_issue("ad", "疑似商业内容，建议在文案中标注 #广告"))
    return {"blocking": blocking, "warnings": warnings}


def check_plan(plan: dict[str, Any]) -> dict[str, Any]:
    blocking: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    seen: set[str] = set()

    def merge(result: dict[str, list[dict[str, str]]]) -> None:
        for item in result["blocking"]:
            key = item["code"] + item["message"]
            if key not in seen:
                seen.add(key)
                blocking.append(item)
        for item in result["warnings"]:
            key = item["code"] + item["message"]
            if key not in seen:
                seen.add(key)
                warnings.append(item)

    merge(check_text(str(plan.get("topic") or "")))
    merge(check_text(str(plan.get("caption") or "")))
    merge(check_text(str(plan.get("script") or "")))
    merge(check_text(str(plan.get("cover_text") or "")))
    hashtags = plan.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [hashtags]
    merge(check_text(" ".join(str(x) for x in hashtags)))

    cover_text = str(plan.get("cover_text") or "").strip()
    if len(cover_text) > 14:
        warnings.append(_issue("cover", "封面大字超过 14 个字符，可能显示不全"))
    if not str(plan.get("video_path") or "").strip():
        warnings.append(_issue("video", "还没有上传视频文件，发布前必须补上"))
    if not str(plan.get("music_note") or "").strip():
        warnings.append(_issue("music", "未填写音乐来源/授权说明，发布前请确认音乐可用"))
    if not str(plan.get("source_notes") or "").strip():
        warnings.append(_issue("source", "未填写素材来源，建议补充拍摄/引用说明"))
    if not cover_text:
        warnings.append(_issue("cover_missing", "还没有封面文案"))

    return {
        "ok": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "checked_at": db.now_iso(),
    }

from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from .config import AgentSettings, load_api_key
from .content_prompts import build_content_messages, build_crm_messages
from .prompts import build_messages, build_reply_messages, build_share_messages

_THINK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_RETRY_HINT = (
    "上一次回复没有形成合法 JSON（可能带了思考过程，或被长度截断）。"
    "不要输出任何思考过程，不要使用 Markdown 代码块，直接输出一个字段齐全的 JSON 对象。"
)


class LLMError(RuntimeError):
    pass


class Decision(BaseModel):
    like: bool = False
    comment: str = ""
    dm: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


class ReplyDecision(BaseModel):
    reply: str = ""
    share: bool = False
    share_query: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


class ShareDecision(BaseModel):
    interesting: bool = False
    intro: str = ""
    reason: str = ""
    score: float = Field(default=0.0, ge=0, le=1)


class ContentDraft(BaseModel):
    topic: str = ""
    angle: str = ""
    pillar: str = ""
    script: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    cover_text: str = ""
    notes: str = ""
    continuity: str = ""


class CrmInsight(BaseModel):
    tags: list[str] = Field(default_factory=list)
    stage: str = "chatting"
    score: int = Field(default=30, ge=0, le=100)
    note: str = ""


def _extract_json_text(raw: str) -> str:
    """从思考模型的回复里取出 JSON 主体，屏蔽 <think> 块与代码围栏。"""
    text = (raw or "").strip()
    if not text:
        return ""
    text = _THINK_RE.sub("", text)
    lower = text.lower()
    unclosed = lower.find("<think")
    if unclosed != -1:
        brace = text.find("{", unclosed)
        text = text[brace:] if brace != -1 else text[:unclosed]
    text = text.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return ""
    return text[start : end + 1].strip()


def _parse_decision(raw: str) -> Decision:
    text = _extract_json_text(raw)
    if not text:
        raise ValueError("回复里没有 JSON 对象")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    return Decision.model_validate(data)


def _parse_reply(raw: str) -> ReplyDecision:
    text = _extract_json_text(raw)
    if not text:
        raise ValueError("回复里没有 JSON 对象")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    return ReplyDecision.model_validate(data)


def _parse_share(raw: str) -> ShareDecision:
    text = _extract_json_text(raw)
    if not text:
        raise ValueError("回复里没有 JSON 对象")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    return ShareDecision.model_validate(data)


def _parse_content(raw: str) -> ContentDraft:
    text = _extract_json_text(raw)
    if not text:
        raise ValueError("回复里没有 JSON 对象")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    return ContentDraft.model_validate(data)


def _parse_crm(raw: str) -> CrmInsight:
    text = _extract_json_text(raw)
    if not text:
        raise ValueError("回复里没有 JSON 对象")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    return CrmInsight.model_validate(data)


def _message_text(resp: Any) -> str:
    message = resp.choices[0].message
    text = (message.content or "").strip()
    if not text:
        text = str(getattr(message, "reasoning_content", "") or "").strip()
    return text


def _usage_of(resp: Any) -> dict[str, int]:
    usage_obj = getattr(resp, "usage", None)
    if not usage_obj:
        return {}
    return {
        "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
        "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
        "total_tokens": getattr(usage_obj, "total_tokens", 0),
    }


def _preview(raw: str) -> str:
    body = _extract_json_text(raw)
    if body:
        return body[:200]
    cleaned = _THINK_RE.sub("", raw or "").strip()
    if cleaned:
        return cleaned[:200]
    if raw and raw.strip():
        return "只输出了思考过程，没有形成 JSON 正文"
    return "返回内容为空"


class DeepSeekDecider:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.api_key = load_api_key()
        if not self.api_key:
            raise LLMError("还没有配置 API Key：去设置里填，或设置 DEEPSEEK_API_KEY 环境变量。")
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=settings.base_url or "https://api.deepseek.com/v1",
            timeout=60.0,
            max_retries=1,
        )

    async def _request(self, messages: list[dict[str, str]]) -> Any:
        return await self.client.chat.completions.create(
            model=self.settings.model,
            messages=messages,
            temperature=self.settings.temperature,
            max_tokens=1800,
            response_format={"type": "json_object"},
        )

    async def decide(
        self,
        video: dict[str, Any],
        recent: list[dict[str, Any]],
        counts: dict[str, int],
        done: list[str],
    ) -> tuple[Decision, dict[str, Any]]:
        messages = build_messages(video, self.settings, recent, counts, done)
        try:
            resp = await self._request(messages)
        except Exception as exc:
            raise LLMError(f"模型调用失败：{exc}") from exc

        raw = _message_text(resp)
        try:
            decision = _parse_decision(raw)
        except (ValueError, ValidationError):
            retry_messages = [
                *messages,
                {"role": "assistant", "content": raw[:1500]},
                {"role": "user", "content": _RETRY_HINT},
            ]
            try:
                resp = await self._request(retry_messages)
            except Exception as exc:
                raise LLMError(f"模型调用失败：{exc}") from exc
            raw = _message_text(resp)
            try:
                decision = _parse_decision(raw)
            except (ValueError, ValidationError) as exc:
                raise LLMError(f"模型没有返回合法 JSON（重试后仍失败）：{_preview(raw)}") from exc

        usage_obj = getattr(resp, "usage", None)
        usage = {}
        if usage_obj:
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                "total_tokens": getattr(usage_obj, "total_tokens", 0),
            }
        return decision, usage

    async def reply(
        self,
        kind: str,
        context: dict[str, Any],
        recent: list[dict[str, Any]],
    ) -> tuple[ReplyDecision, dict[str, Any]]:
        messages = build_reply_messages(kind, context, self.settings, recent)
        try:
            resp = await self._request(messages)
        except Exception as exc:
            raise LLMError(f"模型调用失败：{exc}") from exc

        raw = _message_text(resp)
        try:
            decision = _parse_reply(raw)
        except (ValueError, ValidationError):
            retry_messages = [
                *messages,
                {"role": "assistant", "content": raw[:1500]},
                {"role": "user", "content": _RETRY_HINT},
            ]
            try:
                resp = await self._request(retry_messages)
            except Exception as exc:
                raise LLMError(f"模型调用失败：{exc}") from exc
            raw = _message_text(resp)
            try:
                decision = _parse_reply(raw)
            except (ValueError, ValidationError) as exc:
                raise LLMError(f"模型没有返回合法回复 JSON（重试后仍失败）：{_preview(raw)}") from exc

        usage_obj = getattr(resp, "usage", None)
        usage = {}
        if usage_obj:
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                "total_tokens": getattr(usage_obj, "total_tokens", 0),
            }
        return decision, usage

    async def share(
        self,
        video: dict[str, Any],
        image_data_url: str = "",
        topics_override: str = "",
    ) -> tuple[ShareDecision, dict[str, Any]]:
        messages = build_share_messages(video, self.settings, image_data_url, topics_override)
        try:
            resp = await self._request(messages)
        except Exception as exc:
            raise LLMError(f"模型调用失败：{exc}") from exc

        raw = _message_text(resp)
        try:
            decision = _parse_share(raw)
        except (ValueError, ValidationError):
            retry_messages = [
                *messages,
                {"role": "assistant", "content": raw[:1500]},
                {"role": "user", "content": _RETRY_HINT},
            ]
            try:
                resp = await self._request(retry_messages)
            except Exception as exc:
                raise LLMError(f"模型调用失败：{exc}") from exc
            raw = _message_text(resp)
            try:
                decision = _parse_share(raw)
            except (ValueError, ValidationError) as exc:
                raise LLMError(f"模型没有返回合法分享 JSON（重试后仍失败）：{_preview(raw)}") from exc

        usage_obj = getattr(resp, "usage", None)
        usage = {}
        if usage_obj:
            usage = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                "total_tokens": getattr(usage_obj, "total_tokens", 0),
            }
        return decision, usage

    async def ping(self) -> dict[str, Any]:
        try:
            resp = await self.client.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {"role": "system", "content": "只回复 JSON，不要输出思考过程。"},
                    {"role": "user", "content": '请输出 {"ok": true}'},
                ],
                temperature=0,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            return {"ok": True, "text": _message_text(resp)[:100]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def generate_content(
        self,
        brief: str,
        *,
        pillar: str = "",
        plan_date: str = "",
        reference_works: list[dict[str, Any]] | None = None,
    ) -> tuple[ContentDraft, dict[str, int]]:
        messages = build_content_messages(
            self.settings,
            brief,
            pillar=pillar,
            plan_date=plan_date,
            reference_works=reference_works,
        )
        try:
            resp = await self._request(messages)
        except Exception as exc:
            raise LLMError(f"模型调用失败：{exc}") from exc
        raw = _message_text(resp)
        try:
            draft = _parse_content(raw)
        except (ValueError, ValidationError):
            retry_messages = [
                *messages,
                {"role": "assistant", "content": raw[:1500]},
                {"role": "user", "content": _RETRY_HINT},
            ]
            try:
                resp = await self._request(retry_messages)
            except Exception as exc:
                raise LLMError(f"模型调用失败：{exc}") from exc
            raw = _message_text(resp)
            try:
                draft = _parse_content(raw)
            except (ValueError, ValidationError) as exc:
                raise LLMError(f"模型没有返回合法内容 JSON（重试后仍失败）：{_preview(raw)}") from exc
        return draft, _usage_of(resp)

    async def analyze_contact(
        self, contact: dict[str, Any]
    ) -> tuple[CrmInsight, dict[str, int]]:
        messages = build_crm_messages(contact)
        try:
            resp = await self._request(messages)
        except Exception as exc:
            raise LLMError(f"模型调用失败：{exc}") from exc
        raw = _message_text(resp)
        try:
            insight = _parse_crm(raw)
        except (ValueError, ValidationError):
            retry_messages = [
                *messages,
                {"role": "assistant", "content": raw[:1200]},
                {"role": "user", "content": _RETRY_HINT},
            ]
            try:
                resp = await self._request(retry_messages)
            except Exception as exc:
                raise LLMError(f"模型调用失败：{exc}") from exc
            raw = _message_text(resp)
            try:
                insight = _parse_crm(raw)
            except (ValueError, ValidationError) as exc:
                raise LLMError(f"模型没有返回合法 CRM JSON（重试后仍失败）：{_preview(raw)}") from exc
        return insight, _usage_of(resp)

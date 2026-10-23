from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

from . import selectors
from .config import (
    DEBUG_DIR,
    MEDIA_DIR,
    PROFILE_DIR,
    AgentSettings,
    env,
)


UNFOLLOW_BUTTON_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const el of document.querySelectorAll('button,[role="button"],div,span')) {
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10 || r.left < innerWidth * 0.5 || r.top < 0 || r.top > 320) continue;
    const text = clean(el.innerText);
    if (!['相互关注', '已关注', '互相关注'].includes(text)) continue;
    if ([...el.children].some(c => clean(c.innerText) === text)) continue;
    out.push({el, area: r.width * r.height});
  }
  out.sort((a, b) => a.area - b.area);
  const found = out[0];
  if (!found) return false;
  const button = found.el.closest('button,[role="button"]') || found.el;
  button.setAttribute('data-dy-agent-unfollow', '1');
  return true;
}
"""

UNFOLLOW_STATE_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const hits = [];
  for (const el of document.querySelectorAll('button,[role="button"],div,span')) {
    const r = el.getBoundingClientRect();
    if (!visible(el) || r.left < innerWidth * 0.5 || r.top < 0 || r.top > 320) continue;
    const text = clean(el.innerText);
    if (!['相互关注', '已关注', '互相关注', '关注'].includes(text)) continue;
    if ([...el.children].some(c => clean(c.innerText) === text)) continue;
    hits.push(text);
  }
  return {
    following: hits.some(x => ['相互关注', '已关注', '互相关注'].includes(x)),
    followable: hits.includes('关注'),
    hits,
  };
}
"""

FOLLOW_BUTTON_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const el of document.querySelectorAll('button,[role="button"],div,span')) {
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10 || r.left < innerWidth * 0.5 || r.top < 0 || r.top > 320) continue;
    const text = clean(el.innerText);
    if (!['关注', '回关'].includes(text)) continue;
    if ([...el.children].some(c => clean(c.innerText) === text)) continue;
    out.push({el, area: r.width * r.height});
  }
  out.sort((a, b) => a.area - b.area);
  const found = out[0];
  if (!found) return false;
  const button = found.el.closest('button,[role="button"]') || found.el;
  button.setAttribute('data-dy-agent-follow-profile', '1');
  return true;
}
"""

FANS_SCROLL_JS = r"""
() => {
  const modal = document.querySelector('[data-e2e="user-fans-container"]');
  let el = modal;
  while (el) {
    const style = getComputedStyle(el);
    if (el.scrollHeight > el.clientHeight + 20 && /(auto|scroll)/.test(style.overflowY)) {
      el.scrollTop = el.scrollHeight;
      return true;
    }
    el = el.parentElement;
  }
  return false;
}
"""


class BrowserError(RuntimeError):
    pass


class LoginRequired(BrowserError):
    pass


class RiskControl(BrowserError):
    pass


class ActionError(BrowserError):
    pass


READ_JS = r"""
() => {
  const style = el => getComputedStyle(el);
  const shown = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = style(el);
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight &&
      s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) > 0.05;
  };
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = style(el);
    return r.width > 160 && r.height > 120 && r.bottom > 0 && r.top < innerHeight &&
      s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) > 0.05;
  };
  const area = el => {
    const r = el.getBoundingClientRect();
    return Math.max(0, r.width) * Math.max(0, r.height);
  };
  const text = el => (el && el.innerText ? el.innerText.trim() : '');
  const videos = [...document.querySelectorAll('video')].filter(visible);
  if (!videos.length) {
    const active = document.querySelector('[data-e2e="feed-active-video"]');
    if (!active) return null;
    videos.push(active);
  }
  videos.sort((a, b) => area(b) - area(a));
  const video = videos[0];
  let scope = video.closest('[data-e2e="feed-active-video"]') ||
    video.closest('[data-e2e="feed-item"]') ||
    video.closest('[data-e2e="video-info"]');
  if (!scope) {
    scope = video;
    for (let i = 0; i < 12 && scope && scope.parentElement; i++) {
      scope = scope.parentElement;
      if (scope.querySelector('[data-e2e="video-desc"], [data-e2e="feed-video-nickname"], a[href*="/video/"], a[href*="/note/"]')) break;
    }
  }
  scope = scope || document;
  const visibleFirst = (rawSelectors) => {
    for (const sel of rawSelectors) {
      const nodes = [...scope.querySelectorAll(sel)].filter(shown);
      if (nodes.length) return nodes[0];
    }
    return null;
  };
  const cleanCount = (raw) => {
    const m = String(raw || '').match(/\d+(?:\.\d+)?\s*[万wW]?/);
    return m ? m[0].replace(/\s+/g, '') : '';
  };
  const countNear = (...rawSelectors) => {
    const el = visibleFirst(rawSelectors);
    if (!el) return '';
    const parentText = el.parentElement ? text(el.parentElement) : '';
    return cleanCount(parentText) || cleanCount(text(el));
  };
  const descriptionEl = visibleFirst([
    '[data-e2e="video-desc"]', '[data-e2e="video-title"]',
    '[data-e2e="desc"]', '.video-desc', 'h1'
  ]);
  const authorEl = visibleFirst([
    '[data-e2e="feed-video-nickname"]', '[data-e2e="video-author"]',
    '[data-e2e="author-name"]', 'a[href*="/user/"]'
  ]);
  const authorLinkEl = visibleFirst(['a[data-e2e="video-avatar"]', 'a[href*="/user/"]']);
  const videoLinkEl = visibleFirst([
    'a[href*="/video/"]', 'a[href*="/note/"]'
  ]);
  const coverScore = (img) => {
    const src = img.currentSrc || img.src || '';
    if (!src || src.startsWith('data:')) return -1;
    let score = 0;
    if (src.includes('biz_tag=pcweb_cover')) score += 100;
    if (src.includes('sc=origin_cover')) score += 30;
    if (/tplv-dy-(?:360p|720p|1080p)/.test(src)) score += 40;
    if (src.includes('/aweme-avatar/')) score -= 500;
    if (src.includes('obj/tos-cn-i-tsj2vxp0zn')) score -= 300;
    const w = img.naturalWidth || img.width || 0;
    const h = img.naturalHeight || img.height || 0;
    if (w >= 480 && h >= 270) score += 25;
    else if (w >= 200 && h >= 200) score += 10;
    return score;
  };
  const pickCover = (root) => {
    let best = null;
    let bestScore = 0;
    for (const img of root.querySelectorAll('img')) {
      const s = coverScore(img);
      if (s > bestScore) {
        best = img;
        bestScore = s;
      }
    }
    return best;
  };
  const coverEl = pickCover(scope) || (scope !== document ? pickCover(document) : null);
  const scopeText = text(scope);
  let description = text(descriptionEl).replace(/\n?\s*(展开|收起)\s*$/g, '').trim();
  if (!description) {
    const lines = scopeText.split('\n').map(x => x.trim()).filter(Boolean);
    description = lines.filter(x => x.length > 2 && !/^\d+(\.\d+)?[万wW]?$/.test(x)).slice(0, 8).join('\n');
  }
  const href = (videoLinkEl && videoLinkEl.href) || location.href;
  const scopeVid = (scope.getAttribute && scope.getAttribute('data-e2e-vid')) ||
    (scope.querySelector('[data-e2e-video-id]') && scope.querySelector('[data-e2e-video-id]').getAttribute('data-e2e-video-id')) || '';
  const infoEl = visibleFirst(['[data-e2e="video-info"][data-e2e-aweme-id]']);
  const infoVid = (infoEl && infoEl.getAttribute('data-e2e-aweme-id')) || '';
  const idMatch = href.match(/\/(?:video|note|slides)\/(\d+)/) ||
    location.href.match(/(?:modal_id|aweme_id)=(\d+)/);
  return {
    aweme_id: scopeVid || infoVid || (idMatch ? idMatch[1] : ''),
    url: href,
    is_live: !scopeVid && !infoVid && !descriptionEl && !authorEl,
    author: text(authorEl).replace(/^@/, '').trim(),
    author_url: (authorLinkEl && authorLinkEl.href) || '',
    title: (description.split('\n')[0] || '').slice(0, 300),
    description: description.slice(0, 2500),
    cover: (video.poster || (coverEl && coverEl.src) || ''),
    stats: {
      like: countNear('[data-e2e="video-player-digg"]', '[data-e2e="like-count"]', '[data-e2e="video-like-count"]'),
      comment: countNear('[data-e2e="feed-comment-icon"]', '[data-e2e="comment-count"]', '[data-e2e="video-comment-count"]'),
    },
  };
}
"""

MARK_SCOPE_JS = r"""
() => {
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 160 && r.height > 120 && r.bottom > 0 && r.top < innerHeight &&
      s.display !== 'none' && s.visibility !== 'hidden';
  };
  const area = el => {
    const r = el.getBoundingClientRect();
    return Math.max(0, r.width) * Math.max(0, r.height);
  };
  const videos = [...document.querySelectorAll('video')].filter(visible).sort((a, b) => area(b) - area(a));
  if (!videos.length) return false;
  let scope = videos[0].closest('[data-e2e="feed-active-video"]') ||
    videos[0].closest('[data-e2e="feed-item"]') ||
    videos[0].closest('[data-e2e="video-info"]');
  if (!scope) {
    scope = videos[0];
    for (let i = 0; i < 12 && scope && scope.parentElement; i++) {
      scope = scope.parentElement;
      if (scope.querySelector('[data-e2e="video-desc"], [data-e2e="feed-video-nickname"], a[href*="/video/"], a[href*="/note/"], [data-e2e="like-count"]')) break;
    }
  }
  if (!scope || !scope.setAttribute) return false;
  document.querySelectorAll('[data-dy-agent-current]').forEach(el => el.removeAttribute('data-dy-agent-current'));
  scope.setAttribute('data-dy-agent-current', '1');
  return true;
}
"""

FANS_LIST_JS = r"""
(limit) => {
  const modal = document.querySelector('[data-e2e="user-fans-container"]');
  if (!modal) return {items: [], total: 0};
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const fix = s => {
    s = s || '';
    return s.startsWith('//') ? 'https:' + s : s;
  };
  const rows = [...modal.querySelectorAll('.OlxToPIh')];
  const out = [];
  const seen = new Set();
  for (const row of rows) {
    const name = norm(row.querySelector('.Zce7SWd3')?.innerText || '').split('\n')[0];
    const bio = norm(row.querySelector('.ceyzKypd')?.innerText || '');
    const img = row.querySelector('img');
    const avatar = fix(img ? (img.currentSrc || img.src) : '');
    const link = row.querySelector('a[href*="/user/"]');
    const url = fix(link ? link.href : '');
    const statusBtn = row.querySelector('.G93_q6Y6 .semi-button');
    const status = norm(statusBtn ? statusBtn.innerText : '');
    const key = url || name;
    if (!name || seen.has(key)) continue;
    seen.add(key);
    out.push({
      name,
      bio,
      avatar,
      profile_url: url,
      status,
      following: Boolean(status) && !status.includes('回关'),
    });
    if (out.length >= limit) break;
  }
  const text = norm(modal.innerText || '');
  const match = text.match(/粉丝\s*\((\d+)\)/);
  return {items: out, total: match ? Number(match[1]) : out.length};
}
"""

FANS_FIND_JS = r"""
({name, url}) => {
  const modal = document.querySelector('[data-e2e="user-fans-container"]');
  if (!modal) return {error: 'no_modal'};
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const cleanUrl = u => (u || '').replace(/^https?:/, '').replace(/\/$/, '');
  const target = cleanUrl(url);
  const rows = [...modal.querySelectorAll('.OlxToPIh')];
  let row = null;
  let rowName = '';
  const readName = r => norm(r.querySelector('.Zce7SWd3')?.innerText || '').split('\n')[0];
  const readUrl = r => cleanUrl(r.querySelector('a[href*="/user/"]')?.href || '');
  if (target) {
    for (const r of rows) {
      if (readUrl(r) === target) { row = r; rowName = readName(r); break; }
    }
  }
  if (!row && name) {
    for (const r of rows) {
      if (readName(r) === name) { row = r; rowName = readName(r); break; }
    }
  }
  if (!row) return {error: 'not_found'};
  const buttons = [...row.querySelectorAll('.G93_q6Y6 .semi-button')];
  const btn = buttons.find(b => norm(b.innerText) === '回关');
  if (!btn) return {error: 'already', name: rowName};
  row.setAttribute('data-dy-agent-fan', '1');
  btn.setAttribute('data-dy-agent-follow', '1');
  return {error: '', name: rowName, url: readUrl(row)};
}
"""

MENTION_OPTION_JS = r"""
(name) => {
  const norm = s => (s || '').replace(/\s+/g, '').replace(/…/g, '');
  const target = norm(name);
  if (!target) return null;
  const out = [];
  for (const el of document.querySelectorAll('span,div')) {
    const text = norm(el.innerText);
    const r = el.getBoundingClientRect();
    if (!text || text !== target || r.width <= 0 || r.height <= 0) continue;
    if (r.x < innerWidth * 0.55 || r.y < innerHeight * 0.35) continue;
    const leaf = ![...el.children].some(child => norm(child.innerText) === text);
    if (!leaf) continue;
    // 只认 @ 下拉里的联系人行，避免点到同名评论作者
    const row = el.closest('.dTwsCivh, .QLIVVPgY');
    if (!row) continue;
    out.push({x: r.x, y: r.y, w: r.width, h: r.height});
  }
  out.sort((a, b) => (a.w * a.h) - (b.w * b.h));
  return out[0] || null;
}
"""

CURRENT_ID_JS = r"""
() => {
  const m = location.href.match(/(?:modal_id|aweme_id)=(\d+)/);
  if (m) return m[1];
  const shown = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 160 && r.height > 120 && r.bottom > 0 && r.top < innerHeight &&
      s.display !== 'none' && s.visibility !== 'hidden';
  };
  const active = [...document.querySelectorAll('[data-e2e="feed-active-video"][data-e2e-vid]')].find(shown);
  if (active) return active.getAttribute('data-e2e-vid') || '';
  const info = [...document.querySelectorAll('[data-e2e="video-info"][data-e2e-aweme-id]')].find(shown);
  if (info) return info.getAttribute('data-e2e-aweme-id') || '';
  const area = el => {
    const r = el.getBoundingClientRect();
    return Math.max(0, r.width) * Math.max(0, r.height);
  };
  const videos = [...document.querySelectorAll('video')].filter(shown).sort((a, b) => area(b) - area(a));
  if (!videos.length) return '';
  const scope = videos[0].closest('[data-e2e="feed-active-video"], [data-e2e="feed-item"]');
  const vid = scope && scope.getAttribute('data-e2e-vid');
  if (vid) return vid;
  if (scope) {
    const link = scope.querySelector('a[href*="/video/"], a[href*="/note/"]');
    const mm = link && link.href.match(/\/(?:video|note)\/(\d+)/);
    if (mm) return mm[1];
  }
  let node = videos[0];
  for (let i = 0; i < 12 && node && node.parentElement; i++) {
    node = node.parentElement;
    const link = node.querySelector('a[href*="/video/"], a[href*="/note/"]');
    const mm = link && link.href.match(/\/(?:video|note)\/(\d+)/);
    if (mm) return mm[1];
  }
  return '';
}
"""

COMMENTS_JS = r"""
() => {
  const panels = [
    ...document.querySelectorAll('[data-e2e="comment-list"], [data-e2e="comment-panel"], [class*="comment-list"]')
  ];
  const panel = panels.find(p => p.getBoundingClientRect().height > 80) || null;
  if (!panel) return [];
  const nodes = [
    ...panel.querySelectorAll('[data-e2e="comment-item"], [class*="comment-item"]')
  ];
  const out = [];
  for (const n of nodes) {
    const t = (n.innerText || '').trim();
    if (t && !out.includes(t)) out.push(t.slice(0, 240));
    if (out.length >= 5) break;
  }
  return out;
}
"""

RELATION_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const labels = ['互相关注', '已关注', '关注'];
  const hits = [];
  const nodes = document.querySelectorAll('button, [role="button"], a, span, div');
  for (const el of nodes) {
    if (hits.length >= 12) break;
    if (!visible(el)) continue;
    const t = clean(el.innerText);
    if (t && t.length <= 10 && labels.some(label => t.includes(label))) {
      if (el.children.length && el.tagName === 'DIV') {
        const childHit = [...el.children].some(c => labels.some(label => clean(c.innerText).includes(label)));
        if (childHit) continue;
      }
      hits.push({text: t, tag: el.tagName, cls: String(el.className || '').slice(0, 80)});
    }
  }
  return {
    mutual: hits.some(x => x.text.includes('互相关注')),
    following: hits.some(x => x.text.includes('互相关注') || x.text.includes('已关注')),
    hits,
    url: location.href,
  };
}
"""

NAV_BADGES_JS = r"""
() => {
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const isRed = value => {
    const bg = (value || '').replace(/\s/g, '');
    return bg === 'rgb(254,44,85)' || bg === 'rgb(255,44,85)' || bg === 'rgb(255,45,85)';
  };
  const pseudoBadge = node => {
    for (const p of ['::before', '::after']) {
      const ps = getComputedStyle(node, p);
      if (!ps || ps.content === 'none' || ps.display === 'none') continue;
      if (!isRed(ps.backgroundColor)) continue;
      const w = parseFloat(ps.width), h = parseFloat(ps.height);
      if (w > 0 && h > 0 && w <= 18 && h <= 18) return true;
    }
    return false;
  };
  const isBadge = node => {
    if (!visible(node)) return false;
    const r = node.getBoundingClientRect();
    const s = getComputedStyle(node);
    const cls = typeof node.className === 'string' ? node.className : '';
    const text = (node.innerText || '').trim();
    if (isRed(s.backgroundColor) && r.width <= 18 && r.height <= 18) return true;
    if (pseudoBadge(node)) return true;
    if (/unread|badge|dot|count/i.test(cls) && r.width <= 40 && r.height <= 24) return true;
    return /^\d+$/.test(text) && r.width <= 34 && r.height <= 22;
  };
  const hasBadge = root => {
    if (!root) return false;
    for (const node of root.querySelectorAll('*')) {
      if (isBadge(node)) return true;
    }
    return isBadge(root);
  };
  const imEntry = document.querySelector('[data-e2e="im-entry"]');
  let im = -1;
  let imDebug = '';
  if (imEntry) {
    const parent = imEntry.parentElement;
    const rp = parent ? parent.getBoundingClientRect() : null;
    const re = imEntry.getBoundingClientRect();
    const small = rp && rp.width * rp.height <= Math.max(1, re.width * re.height) * 4;
    im = hasBadge(small ? parent : imEntry) ? 1 : 0;
    imDebug = `${imEntry.className || ''}|${(imEntry.outerHTML || '').slice(0, 700)}`;
  }
  const header = document.querySelector('#douyin-header') || document.querySelector('header');
  let notice = -1;
  const debug = [];
  if (header) {
    let found = false;
    for (const node of header.querySelectorAll('*')) {
      if (imEntry && (imEntry === node || imEntry.contains(node) || node.contains(imEntry))) continue;
      if (isBadge(node)) {
        found = true;
        if (debug.length < 8) {
          const r = node.getBoundingClientRect();
          debug.push({
            tag: node.tagName,
            cls: typeof node.className === 'string' ? node.className.slice(0, 60) : '',
            w: Math.round(r.width),
            h: Math.round(r.height),
            text: (node.innerText || '').trim().slice(0, 12),
          });
        }
      }
    }
    notice = found ? 1 : 0;
  }
  return { im, notice, avatar: -1, debug, im_debug: imDebug };
}
"""

CONVERSATIONS_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const shown = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 80 && r.height > 30 && r.bottom > 0 && r.top < innerHeight &&
      s.display !== 'none' && s.visibility !== 'hidden';
  };
  const items = [...document.querySelectorAll('[data-e2e="conversation-item"]')];
  const out = [];
  items.forEach((el, index) => {
    const rowRect = el.getBoundingClientRect();
    const rightSide = node => {
      const r = node.getBoundingClientRect();
      return r.left + r.width / 2 > rowRect.left + rowRect.width * 0.6;
    };
    const titleEl = el.querySelector('.conversationConversationItemtitle');
    const previewEl = el.querySelector('.ConversationItemHinttextBox');
    let unreadEl = el.querySelector('.ConversationItemUnReadCount');
    if (unreadEl && !rightSide(unreadEl)) unreadEl = null;
    if (!unreadEl) {
      unreadEl = [...el.querySelectorAll('[class*="UnRead" i],[class*="unread" i],[class*="Badge" i],[class*="badge" i],[class*="Dot" i],[class*="dot" i]')]
        .find(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && rightSide(e); }) || null;
    }
    const timeEl = el.querySelector('[class*="ConversationItem"] [class*="time"], [class*="ConversationItem"] [class*="Time"], [class*="conversation"] [class*="time"]');
    const img = el.querySelector('img');
    const title = clean(titleEl && titleEl.innerText);
    if (!title) return;
    const unreadMatch = clean(unreadEl && unreadEl.innerText).match(/\d+/);
    let unread = unreadMatch ? Number(unreadMatch[0]) : (unreadEl ? 1 : 0);
    let unreadDebug = unreadEl ? `node:${unreadEl.className}|${clean(unreadEl.innerText)}|${Math.round(unreadEl.getBoundingClientRect().width)}x${Math.round(unreadEl.getBoundingClientRect().height)}` : '';
    if (!unread) {
      // 有的红点没有数字，只渲染成一个小红圆点。
      for (const node of el.querySelectorAll('*')) {
        if (node.tagName === 'IMG') continue;
        const s = getComputedStyle(node);
        const r = node.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0 || r.width > 14 || r.height > 14) continue;
        if (parseFloat(s.borderRadius) < 3) continue;
        if (!rightSide(node)) continue;
        const bg = s.backgroundColor.replace(/\s/g, '');
        if (bg === 'rgb(254,44,85)' || bg === 'rgb(255,44,85)' || bg === 'rgb(255,45,85)') {
          unread = 1;
          unreadDebug = `red:${node.className}|${bg}|${Math.round(r.width)}x${Math.round(r.height)}|x=${Math.round(r.left)}`;
          break;
        }
      }
    }
    const r = el.getBoundingClientRect();
    out.push({
      index,
      title,
      preview: clean(previewEl && previewEl.innerText).slice(0, 120),
      unread,
      unread_debug: unreadDebug,
      is_group: !!el.querySelector('.commonConversationIconnoDrag') || title.includes('群'),
      visible: r.width > 0 && r.height > 0,
      avatar: (img && img.src) || '',
      time: clean(timeEl && timeEl.innerText).slice(0, 24),
    });
  });
  return out;
}
"""

CHAT_MESSAGES_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const list = document.querySelector('.messageMessageListlist') || document;
  const items = [...list.querySelectorAll('[data-e2e="msg-item-content"]')];
  return items.map((el, index) => {
    const box = el.closest('[class*="messageMessageBox"]');
    const timeEl = box && box.querySelector('.MessageBoxTimetimeLayout');
    const img = box && box.querySelector('img');
    return {
      index,
      text: clean(el.innerText).slice(0, 600),
      time: clean(timeEl && timeEl.innerText).slice(0, 30),
      avatar: (img && img.src) || '',
    };
  }).filter(x => x.text);
}
"""

CHAT_TRANSCRIPT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  const list = document.querySelector('.messageMessageListlist') || document;
  const listRect = list.getBoundingClientRect();
  const outerOf = el => el.closest('.messageMessageBoxmessageBox') || el.closest('[class*="messageMessageBox"]') || el;
  const isAvatarImg = img => {
    const cls = typeof img.className === 'string' ? img.className : '';
    return /avatar/i.test(cls) || !!img.closest('[class*="authorAvatar"]');
  };
  const items = [...list.querySelectorAll('[data-e2e="msg-item-content"]')].map((el, index) => {
    const outer = outerOf(el);
    const timeEl = outer.querySelector('.MessageBoxTimetimeLayout');
    const contentRect = el.getBoundingClientRect();
    const images = [...el.querySelectorAll('img')]
      .filter(img => !isAvatarImg(img))
      .map(img => {
        const r = img.getBoundingClientRect();
        return {
          url: img.currentSrc || img.src || '',
          w: Math.round(r.width),
          h: Math.round(r.height),
          nw: img.naturalWidth || 0,
          nh: img.naturalHeight || 0,
        };
      }).filter(x => x.url && !x.url.startsWith('data:'));
    const videos = [...el.querySelectorAll('video')].map(v => ({
      src: v.currentSrc || v.src || '',
      poster: v.poster || '',
      duration: Number.isFinite(v.duration) ? Math.round(v.duration) : 0,
      w: Math.round(v.getBoundingClientRect().width),
      h: Math.round(v.getBoundingClientRect().height),
    }));
    const links = [...el.querySelectorAll('a[href]')].map(a => a.href).filter(Boolean).slice(0, 4);
    const avatarImg = [...outer.querySelectorAll('img')].find(img => !el.contains(img) && img.src && !isAvatarImg(img));
    const senderAvatar = [...outer.querySelectorAll('img')].find(img => !el.contains(img) && img.src);
    const sideAvatar = [...outer.querySelectorAll('img')].find(img => !el.contains(img));
    const avatarRect = sideAvatar ? sideAvatar.getBoundingClientRect() : null;
    const hasPlay = !!el.querySelector('[class*="play" i], [class*="Play"], [aria-label*="播放"], svg[class*="play" i]');
    const hasVideo = videos.length > 0 || hasPlay || !!el.querySelector('.MessageItemShareAwemecontainer') || [...el.querySelectorAll('[class*="video" i]')].length > 0;
    const videoLink = links.find(u => /\/video\/|\/note\//.test(u)) || '';
    const mineByAvatar = avatarRect && avatarRect.width > 0 && contentRect.width > 0
      ? (avatarRect.left + avatarRect.width / 2) > (contentRect.left + contentRect.width / 2)
      : null;
    return {
      index,
      top: Math.round(contentRect.top),
      text: clean(el.innerText),
      time: clean(timeEl && timeEl.innerText),
      avatar: (senderAvatar && senderAvatar.src) || (avatarImg && avatarImg.src) || '',
      mine: mineByAvatar !== null
        ? mineByAvatar
        : contentRect.width > 0 && (contentRect.left + contentRect.width / 2) > (listRect.left + listRect.width / 2),
      images,
      videos,
      links,
      is_video: hasVideo || !!videoLink,
      video_link: videoLink,
      is_system: false,
    };
  });
  const systems = [...list.querySelectorAll('.messageMessageBoxisFullRowCenterMessage')].map((el, index) => {
    const outer = outerOf(el);
    const timeEl = outer.querySelector('.MessageBoxTimetimeLayout');
    const r = el.getBoundingClientRect();
    return {
      index: 10000 + index,
      top: Math.round(r.top),
      text: clean(el.innerText),
      time: clean(timeEl && timeEl.innerText),
      avatar: '',
      mine: false,
      images: [],
      videos: [],
      links: [],
      is_video: false,
      video_link: '',
      is_system: true,
    };
  }).filter(x => x.text);
  return [...items, ...systems];
}
"""

NOTIFICATION_ROWS_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const root = document.querySelector('[data-e2e="listDlgTest-container"]');
  if (!root) return [];
  const cards = [...root.querySelectorAll('[data-e2e="user-name-card"]')];
  const out = [];
  for (const card of cards) {
    const link = card.closest('a[href*="/user/"]');
    let row = card;
    let text = '';
    for (let i = 0; i < 8 && row && row.parentElement; i++) {
      row = row.parentElement;
      text = clean(row.innerText);
      if (text.length > 6 && (text.includes('互相关注') || text.includes('关注了你') || text.includes('回复') || text.includes('评论') || text.includes('赞了'))) break;
      if (text.length > 500) break;
    }
    const name = clean(card.innerText).replace(/^@/, '');
    if (!name) continue;
    const preEls = [...row.querySelectorAll('pre')];
    const comment = clean(preEls.length ? preEls[preEls.length - 1].innerText : '').slice(0, 300);
    const thumbImg = [...row.querySelectorAll('img')].reverse().find(img => {
      const r = img.getBoundingClientRect();
      return r.width >= 40 && r.height >= 40;
    });
    const kind = text.includes('评论了你的作品')
      ? 'comment'
      : text.includes('关注了你')
        ? 'follow'
        : text.includes('赞了')
          ? 'like'
          : 'other';
    out.push({
      name,
      url: (link && link.href) || '',
      text: text.slice(0, 320),
      mutual: text.includes('互相关注'),
      comment,
      thumb: (thumbImg && thumbImg.src) || '',
      kind,
    });
  }
  return out;
}
"""

RISK_JS = r"""
() => {
  const text = document.body ? document.body.innerText : '';
  const needle = ['安全验证', '完成验证', '拖动滑块', '访问过于频繁', '请完成下列验证', '网络环境存在风险', '点击两个形状相同'];
  return needle.filter(x => text.includes(x));
}
"""


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


class BrowserManager:
    def __init__(
        self,
        settings: AgentSettings,
        *,
        start_url: str = "",
        headless: bool | None = None,
        profile_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.start_url = start_url or env.mock_url or ""
        self.headless = env.headless if headless is None else headless
        self.profile_dir = profile_dir or PROFILE_DIR
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.activity_page: Page | None = None
        self.activity_at = 0.0
        self.last_error = ""
        self._chrome_pids: set[int] = set()
        self._watch_task: asyncio.Task | None = None
        self._stay_visible = False
        self._shots = MEDIA_DIR / "shots"
        self._shots.mkdir(parents=True, exist_ok=True)

    @property
    def is_mock(self) -> bool:
        return bool(self.start_url and not self.start_url.startswith("http"))

    async def ensure_started(self) -> None:
        if self.context and self.page and not self.page.is_closed():
            return
        if self.context:
            try:
                await self.context.cookies("https://www.douyin.com")
            except Exception:
                self.context = None
                self.page = None
        await self.start()

    async def ensure_feed(self) -> None:
        """If the current tab is not a video feed/detail, open the configured feed."""
        if self.is_mock:
            return
        page = await self._ensure_page()
        try:
            ready = await page.evaluate(
                """() => !!document.querySelector(
                    '[data-e2e="feed-active-video"] video, [data-e2e="video-info"][data-e2e-aweme-id]'
                )"""
            )
        except Exception:
            ready = False
        if ready:
            return
        target = self.settings.feed_url or "https://www.douyin.com/?recommend=1&from_nav=1"
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            self.last_error = f"打开推荐流失败：{exc}"
        await page.wait_for_timeout(2500)
        try:
            await page.mouse.move(600, 400)
            await page.wait_for_timeout(300)
        except Exception:
            pass

    async def start(self, show: bool | None = None) -> None:
        if self.context:
            if show is not None:
                self._stay_visible = show
                await self._window_mode("show" if show else "hide")
                if show:
                    await self._focus_window()
            return
        self._pw = await async_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        visible = (not self.settings.silent_browser) if show is None else show
        launch_args = [
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
        ]
        if not visible:
            # 先画到屏幕外，再隐藏；避免默认位置启动时闪一下。
            launch_args += ["--window-position=-32000,-32000", "--window-size=1440,920"]
        params = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1440, "height": 920},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=launch_args,
        )
        channel = env.chrome_channel.strip()
        print(
            f"[browser] launch headless={self.headless} channel={channel or 'bundled'} profile={self.profile_dir}",
            flush=True,
        )
        try:
            self.context = await self._pw.chromium.launch_persistent_context(
                channel=channel or None, **params
            )
        except Exception as first:
            try:
                self.context = await self._pw.chromium.launch_persistent_context(**params)
            except Exception as second:
                await self._pw.stop()
                self._pw = None
                self.last_error = f"启动 Chrome 失败：{second}"
                raise BrowserError(
                    "启动 Chrome 失败。请确认已安装 Google Chrome，"
                    "或在 .env 里把 CHROME_CHANNEL 改成可用的通道。"
                    f"（{second} / {first}）"
                ) from second

        print(f"[browser] context pages={len(self.context.pages)}", flush=True)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.page.set_default_timeout(15000)
        target = self.start_url or self.settings.feed_url
        try:
            await self.page.goto(target, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            self.last_error = f"打开页面失败：{exc}"
        await self.page.wait_for_timeout(2200)
        if self.page.is_closed():
            await self.close()
            raise BrowserError("Chrome 启动了，但页面立即关闭；请查看 data/server.log。")
        try:
            await self.page.bring_to_front()
            await self.page.evaluate("1")
        except Exception as exc:
            await self.close()
            raise BrowserError(f"Chrome 页面不可用：{exc}") from exc
        visible = (not self.settings.silent_browser) if show is None else show
        self._stay_visible = visible
        if visible:
            await self._window_mode("show")
            await self._focus_window()
        else:
            await self._window_mode("hide")
        self.last_error = ""
        if os.name == "nt" and not self._watch_task:
            self._watch_task = asyncio.create_task(self._silent_watch())
        print(f"[browser] ready url={self.page.url} pages={len(self.context.pages)} silent={not visible}", flush=True)

    async def close(self) -> None:
        task = self._watch_task
        self._watch_task = None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._stay_visible = False
        self._chrome_pids.clear()
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        self.context = None
        self.page = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._pw = None

    async def _ensure_page(self) -> Page:
        await self.ensure_started()
        assert self.page is not None
        if self.page.is_closed():
            self.page = await self.new_page()
        return self.page

    async def new_page(self, track: bool = True) -> Page:
        if not self.context:
            raise BrowserError("浏览器上下文不可用")
        page = await self.context.new_page()
        page.set_default_timeout(15000)
        if track:
            self.activity_page = page
            self.activity_at = time.monotonic()
        if self.settings.silent_browser and not self._stay_visible:
            if not self._chrome_pids:
                await self._window_mode("hide")
            else:
                await asyncio.sleep(0.15)
                self._hide_windows_now()
        return page

    async def set_window_visible(self, visible: bool) -> None:
        self._stay_visible = visible
        if not self.context:
            return
        await self._window_mode("show" if visible else "hide")
        if visible:
            try:
                await self._focus_window()
            except Exception:
                pass

    async def _silent_watch(self) -> None:
        while True:
            try:
                await asyncio.sleep(0.8)
            except asyncio.CancelledError:
                return
            if not self.context:
                return
            if self.settings.silent_browser and not self._stay_visible:
                self._hide_windows_now()

    def _hide_windows_now(self) -> None:
        if os.name != "nt" or not self._chrome_pids:
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            pids = set(self._chrome_pids)

            def callback(hwnd: int, _: int) -> bool:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in pids:
                    return True
                name = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(hwnd, name, 128)
                if name.value.startswith("Chrome_WidgetWin_"):
                    user32.ShowWindow(hwnd, 0)
                return True

            user32.EnumWindows(enum_proc(callback), 0)
        except Exception:
            pass

    async def _window_mode(self, mode: str) -> None:
        if os.name != "nt":
            return
        helper = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "browser_window.ps1")
        if not os.path.exists(helper):
            return

        def run() -> set[int]:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper, "-Mode", mode],
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
            )
            out = (proc.stdout or b"").decode("utf-8", "ignore")
            match = re.search(r"pids=([\d,]+)", out)
            if not match:
                return set()
            return {int(x) for x in match.group(1).split(",") if x}

        try:
            pids = await asyncio.to_thread(run)
            if pids:
                self._chrome_pids = pids
        except Exception:
            pass

    async def _focus_window(self) -> None:
        """Best-effort: bring the dedicated Chrome window forward on Windows."""
        if os.name != "nt":
            return

        def activate() -> None:
            helper = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "focus_browser.ps1")
            if os.path.exists(helper):
                subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper],
                    timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    capture_output=True,
                )
                return
            script = (
                "$ws = New-Object -ComObject WScript.Shell; "
                "try { $ws.AppActivate('抖音') | Out-Null } catch {}"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
            )

        try:
            await asyncio.to_thread(activate)
        except Exception:
            pass

    async def cookies_names(self) -> set[str]:
        if self.is_mock:
            return {"mock"}
        if not self.context:
            return set()
        try:
            cookies = await self.context.cookies("https://www.douyin.com")
        except Exception:
            return set()
        return {str(c.get("name") or "") for c in cookies}

    async def is_logged_in(self) -> bool:
        if self.is_mock:
            return True
        names = await self.cookies_names()
        return bool(names & {"sessionid", "sessionid_ss", "sid_guard"})

    async def risk_texts(self) -> list[str]:
        if self.is_mock or not self.page:
            return []
        try:
            texts = await self.page.evaluate(RISK_JS)
            if isinstance(texts, list):
                return [str(t) for t in texts]
        except Exception:
            pass
        return []

    async def assert_safe(self) -> None:
        if self.is_mock:
            return
        if not await self.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音。先点“打开登录浏览器”，用手机扫码登录。")
        hits = await self.risk_texts()
        if hits:
            raise RiskControl("页面出现风控/验证提示：" + "、".join(hits))

    async def read_current(self, include_comments: bool | None = False) -> dict[str, Any]:
        page = await self._ensure_page()
        await self.assert_safe()
        # 作者、文案浮层只在鼠标移到视频上时才出现在 DOM 里。
        try:
            await page.mouse.move(600, 400)
            await page.wait_for_timeout(400)
        except Exception:
            pass
        data = await page.evaluate(READ_JS)
        if not data:
            shot = await self.screenshot("no_video")
            raise ActionError(
                "没有在页面上识别到视频。可能还在登录/推荐页加载，或抖音改版了。"
                f"已保存截图：{shot}"
            )
        data = dict(data)
        if not data.get("aweme_id"):
            # 直播/非普通视频没有作品 id；不要给它编一个 id 继续点赞评论。
            data["is_live"] = True
            seed = f"{data.get('url')}|{data.get('author')}|{data.get('description')}"
            data["aweme_id"] = "web_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:18]
        if not data.get("url"):
            data["url"] = page.url
        if include_comments is None:
            include_comments = self.settings.auto_comment
        if data.get("is_live"):
            data["comments"] = []
        elif include_comments:
            try:
                data["comments"] = await self.read_comments()
            except Exception:
                data["comments"] = []
        else:
            data.setdefault("comments", [])
        return data

    async def read_comments(self) -> list[str]:
        page = await self._ensure_page()
        opened = await self._open_comments()
        if not opened:
            return []
        try:
            found = await page.evaluate(COMMENTS_JS)
            return [str(x) for x in (found or []) if str(x).strip()][:5]
        finally:
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(250)
            except Exception:
                pass

    async def open_inbox(self, page: Page) -> bool:
        async def is_open() -> bool:
            try:
                return bool(
                    await page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-e2e="im-dialog"]');
                            if (el) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 100 && r.height > 100) return true;
                            }
                            return [...document.querySelectorAll('[data-e2e="conversation-item"]')].some(el => {
                                const r = el.getBoundingClientRect();
                                return r.width > 80 && r.height > 30;
                            });
                        }"""
                    )
                )
            except Exception:
                return False

        if await is_open():
            return True
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        except Exception:
            pass
        entry = page.locator('[data-e2e="im-entry"]')
        for i in range(await entry.count()):
            try:
                if await entry.nth(i).is_visible():
                    await entry.nth(i).click()
                    await page.wait_for_timeout(900)
                    if await is_open():
                        return True
                    break
            except Exception:
                continue
        try:
            await page.evaluate(
                """() => {
                    const el = [...document.querySelectorAll('[data-e2e="im-entry"]')].find(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    });
                    if (el) el.click();
                }"""
            )
            await page.wait_for_timeout(900)
        except Exception:
            pass
        return await is_open()

    async def nav_badges(self, page: Page | None = None) -> dict[str, int]:
        target = page or self.page
        if target is None or target.is_closed():
            return {"im": -1, "notice": -1, "avatar": -1}
        try:
            data = await target.evaluate(NAV_BADGES_JS)
        except Exception:
            return {"im": -1, "notice": -1, "avatar": -1}
        out = {"im": -1, "notice": -1, "avatar": -1}
        for key in out:
            try:
                out[key] = int((data or {}).get(key, -1))
            except Exception:
                out[key] = -1
        return out

    async def list_conversations(self, page: Page) -> list[dict[str, Any]]:
        if not await self.open_inbox(page):
            return []
        try:
            data = await page.evaluate(CONVERSATIONS_JS)
        except Exception:
            return []
        return [dict(x) for x in (data or []) if isinstance(x, dict)]

    async def _click_conversation_row(self, page: Page, item) -> bool:
        try:
            # 会话列表会实时刷新，等待“稳定”会偶发超时；直接滚到中心、等列表落定再按坐标点击。
            try:
                await page.mouse.move(8, 8)
            except Exception:
                pass
            try:
                await item.evaluate("el => el.scrollIntoView({ block: 'center' })")
            except Exception:
                pass
            await page.wait_for_timeout(500)
            box = await item.bounding_box()
            if not box:
                return False
            points: list[tuple[float, float]] = [
                (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            ]
            for sel in (".conversationConversationItemtitle", "img"):
                try:
                    loc = item.locator(sel).first
                    if await loc.count():
                        b = await loc.bounding_box()
                        if b:
                            points.append((b["x"] + b["width"] / 2, b["y"] + b["height"] / 2))
                except Exception:
                    continue
            for x, y in points:
                await page.evaluate(
                    "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = 'none'; }"
                )
                covered = False
                hit_info = ""
                try:
                    hit_info = await page.evaluate(
                        """(pt) => {
                            const hit = document.elementFromPoint(pt.x, pt.y);
                            if (!hit) return 'none';
                            const cls = typeof hit.className === 'string' ? hit.className : '';
                            return `${hit.tagName}.${cls.slice(0, 60)}`;
                        }""",
                        {"x": x, "y": y},
                    )
                    covered = not bool(
                        await item.evaluate(
                            """(el, pt) => {
                                const hit = document.elementFromPoint(pt.x, pt.y);
                                return !!hit && (el === hit || el.contains(hit));
                            }""",
                            {"x": x, "y": y},
                        )
                    )
                except Exception:
                    pass
                if covered:
                    print(f"[chat] row point covered by {hit_info}", flush=True)
                    continue
                # 顶部栏的悬浮层会盖住较低的会话行，点击期间临时让 header 穿透。
                try:
                    await page.mouse.click(x, y)
                finally:
                    try:
                        await page.evaluate(
                            "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                        )
                    except Exception:
                        pass
                return True
            try:
                await page.evaluate(
                    "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                )
            except Exception:
                pass
            print(
                f"[chat] conversation row covered at ({points[0][0]:.0f},{points[0][1]:.0f}) hit={hit_info}",
                flush=True,
            )
            return False
        except Exception as exc:
            print(f"[chat] click conversation row failed: {str(exc)[:240]!r}", flush=True)
            return False

    async def chat_header_title(self, page: Page) -> str:
        try:
            return (await page.locator(".StackLayoutStackChatHeadertitle").first.inner_text()).strip()
        except Exception:
            return ""

    async def chat_list_visible(self, page: Page) -> bool:
        items = page.locator('[data-e2e="conversation-item"]')
        try:
            count = await items.count()
        except Exception:
            return False
        for index in range(min(count, 6)):
            item = items.nth(index)
            try:
                box = await item.bounding_box()
                if not box or box["width"] < 40 or box["height"] < 20:
                    continue
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                hit = await item.evaluate(
                    """(el, pt) => {
                        const node = document.elementFromPoint(pt.x, pt.y);
                        return !!node && (el === node || el.contains(node));
                    }""",
                    {"x": x, "y": y},
                )
                if hit:
                    return True
            except Exception:
                continue
        return False

    async def chat_back_to_list(self, page: Page) -> bool:
        """Douyin 私信是浮动聊天窗；切换会话前要先点左上角返回，露出会话列表。"""
        if await self.chat_list_visible(page):
            return True
        points: list[tuple[float, float]] = []
        try:
            info = await page.evaluate(
                """() => {
                    const title = document.querySelector('.StackLayoutStackChatHeadertitle');
                    if (!title) return null;
                    let panel = title;
                    for (let i = 0; i < 12 && panel.parentElement; i++) {
                        const r = panel.getBoundingClientRect();
                        if (r.width >= 260 && r.height >= 250) break;
                        panel = panel.parentElement;
                    }
                    const tr = title.getBoundingClientRect();
                    const pr = panel.getBoundingClientRect();
                    return {
                        panelLeft: pr.left, panelTop: pr.top, panelWidth: pr.width, panelHeight: pr.height,
                        titleLeft: tr.left, titleY: tr.top + tr.height / 2,
                    };
                }"""
            )
        except Exception:
            info = None
        if info:
            title_left = float(info["titleLeft"])
            title_y = float(info["titleY"])
            panel_left = float(info["panelLeft"])
            points.extend(
                [
                    (title_left - 50, title_y),
                    (title_left - 36, title_y),
                    (panel_left + 22, title_y),
                    (panel_left + 32, title_y),
                    (panel_left + 44, title_y),
                ]
            )
        for x, y in points[:6]:
            try:
                await page.mouse.click(x, y)
            except Exception:
                continue
            await page.wait_for_timeout(650)
            if await self.chat_list_visible(page):
                return True
        print(f"[chat] back-to-list failed points={points[:6]} info={info}", flush=True)
        return False

    async def open_conversation(self, page: Page, index: int) -> bool:
        loc = page.locator('[data-e2e="conversation-item"]').nth(index)
        if not await self._click_conversation_row(page, loc):
            return False
        await page.wait_for_timeout(1300)
        return True

    async def open_conversation_by_title(self, page: Page, title: str) -> bool:
        wanted = (title or "").strip()
        if not wanted:
            return False
        header_now = await self.chat_header_title(page)
        if header_now and (header_now == wanted or wanted in header_now):
            return True
        try:
            if await page.locator('[data-e2e="conversation-item"]').count() == 0:
                await self.open_inbox(page)
                await page.wait_for_timeout(700)
        except Exception:
            pass
        if not await self.chat_list_visible(page):
            recovered = await self.chat_back_to_list(page)
            if not recovered:
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(500)
                    await self.open_inbox(page)
                    await page.wait_for_timeout(800)
                    recovered = await self.chat_list_visible(page)
                    if not recovered:
                        recovered = await self.chat_back_to_list(page)
                except Exception:
                    recovered = False
            if not recovered:
                print(f"[chat] cannot reveal conversation list for {wanted!r}", flush=True)
                return False
        items = page.locator('[data-e2e="conversation-item"]')
        count = 0
        last_exact: list[int] = []
        last_loose: list[int] = []
        for _attempt in range(3):
            try:
                count = await items.count()
            except Exception:
                count = 0
            exact: list[int] = []
            loose: list[int] = []
            for index in range(count):
                try:
                    title_el = items.nth(index).locator('.conversationConversationItemtitle')
                    if not await title_el.count():
                        continue
                    name = (await title_el.inner_text()).strip()
                except Exception:
                    continue
                if name == wanted:
                    exact.append(index)
                elif wanted and (wanted in name or name in wanted):
                    loose.append(index)
            last_exact, last_loose = exact, loose
            for index in exact + loose:
                item = items.nth(index)
                if not await self._click_conversation_row(page, item):
                    continue
                for _ in range(16):
                    await page.wait_for_timeout(250)
                    header = await self.chat_header_title(page)
                    if header and (header == wanted or wanted in header):
                        await page.wait_for_timeout(350)
                        return True
            try:
                await page.mouse.move(8, 8)
                await page.wait_for_timeout(350)
            except Exception:
                pass
        header = await self.chat_header_title(page)
        print(
            f"[chat] open conversation failed title={wanted!r} count={count} "
            f"exact={last_exact} loose={last_loose} header={header!r} url={page.url[:120]!r}",
            flush=True,
        )
        try:
            await page.screenshot(path=str(self._shots / f"chat_open_fail_{_now_tag()}.png"))
        except Exception:
            pass
        return False

    async def reply_comment_on_work(
        self,
        work_url: str,
        author: str,
        comment: str,
        reply_text: str,
    ) -> dict[str, Any]:
        page = await self.new_page()
        page.set_default_timeout(20000)
        shot_name = f"reply_comment_{_now_tag()}.png"
        shot_path = self._shots / shot_name
        try:
            await page.goto(work_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            opened = False
            for sel in (
                '[data-e2e="feed-comment-icon"]',
                '[data-e2e="comment-icon"]',
                '[data-e2e="video-comment"]',
            ):
                loc = page.locator(sel)
                for i in range(await loc.count()):
                    try:
                        if not await loc.nth(i).is_visible():
                            continue
                        box = await loc.nth(i).bounding_box()
                        if not box:
                            continue
                        await page.evaluate(
                            "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = 'none'; }"
                        )
                        try:
                            await page.mouse.click(
                                box["x"] + box["width"] / 2,
                                box["y"] + box["height"] / 2,
                            )
                        finally:
                            await page.evaluate(
                                "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                            )
                        opened = True
                        break
                    except Exception:
                        continue
                if opened:
                    break
            if not opened:
                return {"status": "failed", "detail": "没有打开评论面板"}
            await page.wait_for_timeout(1600)

            target = await page.evaluate(
                """([author, comment]) => {
                  const controls = [...document.querySelectorAll('span, div')].filter(el => {
                    if ((el.innerText || '').trim() !== '回复') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                  });
                  const hit = controls.find(el => {
                    const item = el.closest('[data-e2e="comment-item"]');
                    const text = ((item && item.innerText) || '').replace(/\\s+/g, ' ');
                    return text.includes(author) && (!comment || text.includes(comment));
                  }) || controls[0];
                  if (!hit) return null;
                  hit.setAttribute('data-dy-agent-reply', '1');
                  const r = hit.getBoundingClientRect();
                  return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                }""",
                [author, comment],
            )
            if not target:
                return {"status": "failed", "detail": "没有找到这条评论的回复按钮"}
            await page.evaluate(
                "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = 'none'; }"
            )
            try:
                await page.mouse.click(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
            finally:
                await page.evaluate(
                    "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                )
            await page.wait_for_timeout(800)

            editor = None
            editors = page.locator('.public-DraftEditor-content')
            for i in range(await editors.count()):
                if await editors.nth(i).is_visible():
                    editor = editors.nth(i)
                    break
            if editor is None:
                for sel in (
                    '#comment-input-container [contenteditable="true"]',
                    '.comment-input-container [contenteditable="true"]',
                    'textarea',
                ):
                    loc = page.locator(sel)
                    for i in range(await loc.count()):
                        if await loc.nth(i).is_visible():
                            editor = loc.nth(i)
                            break
                    if editor is not None:
                        break
            if editor is None:
                return {"status": "failed", "detail": "没有找到回复输入框"}
            try:
                await editor.click(timeout=4000)
                await page.keyboard.insert_text(reply_text)
                await page.wait_for_timeout(700)
            except Exception as exc:
                return {"status": "failed", "detail": f"填写回复失败：{str(exc)[:160]}"}

            send = await self._find_visible_in(page, selectors.COMMENT_SEND)
            if not send:
                await page.evaluate(
                    """() => {
                      const spans = [...document.querySelectorAll('span, button')].filter(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width < 8 || r.height < 8) return false;
                        const html = el.outerHTML || '';
                        return html.includes('#FE2C55') &&
                          !!el.closest('#comment-input-container, .comment-input-container, .commentInput-right-ct');
                      });
                      const el = spans[spans.length - 1];
                      if (el) el.setAttribute('data-dy-agent-send', '1');
                    }"""
                )
                if await page.locator('[data-dy-agent-send="1"]').count():
                    send = page.locator('[data-dy-agent-send="1"]').first
            if not send:
                await page.screenshot(path=str(shot_path))
                return {"status": "failed", "detail": f"没有找到发送按钮。截图：/media/shots/{shot_name}"}
            try:
                await send.click(timeout=5000)
            except Exception as exc:
                await page.screenshot(path=str(shot_path))
                return {"status": "failed", "detail": f"点击发送失败：{str(exc)[:160]}"}
            await page.wait_for_timeout(1700)
            try:
                await page.screenshot(path=str(shot_path))
            except Exception:
                pass
            verified = False
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                verified = reply_text[:12] in body
            except Exception:
                pass
            return {
                "status": "sent" if verified else "uncertain",
                "detail": "评论回复已发送" if verified else "已点击发送，但未读到确认",
                "screenshot": f"/media/shots/{shot_name}",
            }
        except Exception as exc:
            try:
                await page.screenshot(path=str(shot_path))
            except Exception:
                pass
            return {
                "status": "failed",
                "detail": f"评论回复失败：{str(exc)[:200]}",
                "screenshot": f"/media/shots/{shot_name}",
            }
        finally:
            if not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass

    async def read_chat_messages(self, page: Page) -> list[dict[str, Any]]:
        try:
            data = await page.evaluate(CHAT_MESSAGES_JS)
        except Exception:
            return []
        return [dict(x) for x in (data or []) if isinstance(x, dict) and str(x.get("text") or "").strip()]

    async def read_chat_transcript(self, page: Page) -> list[dict[str, Any]]:
        try:
            data = await page.evaluate(CHAT_TRANSCRIPT_JS)
        except Exception:
            return []
        return [dict(x) for x in (data or []) if isinstance(x, dict)]

    async def send_chat_message(self, page: Page, text: str) -> dict[str, Any]:
        box = None
        for _ in range(12):
            box = await self._find_visible_in(page, selectors.DM_INPUT, prefer_last=True)
            if box:
                break
            await page.wait_for_timeout(300)
        if not box:
            raise ActionError("没有找到私信输入框")
        draft = await self._editor_text(box)
        if draft:
            raise ActionError("私信输入框里已有未发送草稿，已停止，避免混入。")
        try:
            await box.click()
            await page.keyboard.insert_text(text)
            await page.wait_for_timeout(350)
        except Exception as exc:
            raise ActionError(f"填写私信失败：{exc}") from exc
        send = await self._find_visible_in(page, selectors.DM_SEND)
        if not send:
            raise ActionError("没有找到私信发送按钮")
        try:
            await send.click()
        except Exception as exc:
            raise ActionError(f"发送私信失败：{exc}") from exc
        await page.wait_for_timeout(1100)
        verified = False
        try:
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            verified = text[:18] in body
        except Exception:
            pass
        return {
            "status": "sent" if verified else "uncertain",
            "detail": "私信已发送" if verified else "已点击私信发送，但未读到确认",
        }

    async def share_current_video(
        self, page: Page, recipient: str, expected_id: str = ""
    ) -> dict[str, Any]:
        """Use the video's native share panel to send it to an existing contact."""
        panel_count_js = """() => [...document.querySelectorAll('input[placeholder="搜索"]')].filter(e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0
                && e.getAttribute('data-e2e') !== 'searchbar-input'
                && !e.closest('[data-e2e="searchbar"]');
        }).length"""

        async def panel_count() -> int:
            try:
                return int(await page.evaluate(panel_count_js))
            except Exception:
                return 0

        async def debug_shot(prefix: str, extra: dict[str, Any]) -> None:
            debug = dict(extra)
            try:
                debug.update(
                    await page.evaluate(
                        """() => ({
                            url: location.href,
                            share_count: document.querySelectorAll('[data-e2e="video-player-share"]').length,
                            share_visible: [...document.querySelectorAll('[data-e2e="video-player-share"]')].filter(e => {
                                const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;
                            }).length,
                        })"""
                    )
                )
            except Exception:
                pass
            shot_name = f"{prefix}_{_now_tag()}.png"
            try:
                await page.screenshot(path=str(self._shots / shot_name))
            except Exception:
                shot_name = ""
            raise ActionError(f"{extra.get('reason') or '分享失败'}（{debug}）截图：/media/shots/{shot_name}")

        try:
            await page.mouse.move(600, 400)
            await page.wait_for_timeout(300)
        except Exception:
            pass
        # 顶部搜索框不能当作分享面板；只有面板真的开着才收起来，避免误关视频弹层。
        if await panel_count():
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(400)
            except Exception:
                pass

        clicked = False
        for _ in range(2):
            clicked = await page.evaluate(
                """() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    const inView = el => {
                        const r = el.getBoundingClientRect();
                        return r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight;
                    };
                    let els = [...document.querySelectorAll('[data-e2e="video-player-share"]')]
                        .filter(e => visible(e) && inView(e));
                    // 推荐流会预渲染相邻视频，必须锁定“当前激活视频”里的分享按钮。
                    const active = document.querySelector('[data-e2e="feed-active-video"]');
                    if (active) {
                        const inside = els.filter(e => active.contains(e));
                        if (inside.length) els = inside;
                    }
                    els.sort((a, b) => {
                        const ra = a.getBoundingClientRect();
                        const rb = b.getBoundingClientRect();
                        return Math.abs(ra.top + ra.height / 2 - innerHeight / 2)
                            - Math.abs(rb.top + rb.height / 2 - innerHeight / 2);
                    });
                    const el = els[0];
                    if (!el) return null;
                    let vid = '';
                    let node = el;
                    for (let i = 0; i < 12 && node; i++, node = node.parentElement) {
                        const holder = node.matches && node.matches('[data-e2e="feed-active-video"][data-e2e-vid]')
                            ? node
                            : (node.querySelector ? node.querySelector('[data-e2e="feed-active-video"][data-e2e-vid]') : null);
                        if (holder) { vid = holder.getAttribute('data-e2e-vid') || ''; break; }
                    }
                    if (!vid) {
                        const info = document.querySelector('[data-e2e="video-info"][data-e2e-aweme-id]');
                        vid = info ? (info.getAttribute('data-e2e-aweme-id') || '') : '';
                    }
                    el.click();
                    return { clicked: true, vid };
                }"""
            )
            if isinstance(clicked, dict):
                clicked_vid = str(clicked.get("vid") or "")
                clicked = bool(clicked.get("clicked"))
                if expected_id and clicked_vid and clicked_vid != expected_id:
                    raise ActionError(
                        f"分享按钮属于视频 {clicked_vid}，与介绍的 {expected_id} 不一致，已取消分享"
                    )
            if not clicked:
                break
            for _ in range(24):
                await page.wait_for_timeout(250)
                if await panel_count():
                    return await self._share_pick_contact(page, recipient, panel_count_js)
        if not clicked:
            await debug_shot("share_missing", {"reason": "没有找到视频分享按钮"})
        await debug_shot("share_panel_missing", {"reason": "分享面板没有打开"})

    async def _share_pick_contact(self, page: Page, recipient: str, panel_count_js: str) -> dict[str, Any]:
        inputs = page.locator('input[placeholder="搜索"]')
        chosen = None
        for i in range(await inputs.count()):
            try:
                loc = inputs.nth(i)
                if not await loc.is_visible():
                    continue
                if await loc.get_attribute("data-e2e") == "searchbar-input":
                    continue
                chosen = loc
            except Exception:
                continue
        if chosen is None:
            return {"status": "failed", "detail": "分享面板没有可用的联系人搜索框"}
        try:
            await chosen.fill(recipient)
            await page.wait_for_timeout(1200)
        except Exception:
            pass
        result = await page.evaluate(
            """(name) => {
                const inputs = [...document.querySelectorAll('input[placeholder="搜索"]')].filter(e => {
                    const r = e.getBoundingClientRect();
                    return r.width > 0 && r.height > 0
                        && e.getAttribute('data-e2e') !== 'searchbar-input'
                        && !e.closest('[data-e2e="searchbar"]');
                });
                const input = inputs[inputs.length - 1];
                let panel = null;
                let node = input ? input.parentElement : null;
                for (let i = 0; i < 10 && node; i++, node = node.parentElement) {
                    if ((node.innerText || '').includes('分享给朋友')) { panel = node; break; }
                }
                const scopes = panel ? [panel] : [document];
                for (const scope of scopes) {
                    const names = [...scope.querySelectorAll('span')]
                        .filter(el => (el.innerText || '').trim() === name);
                    for (const nameEl of names) {
                        let row = nameEl;
                        for (let i = 0; i < 8 && row; i++) {
                            row = row.parentElement;
                            if (!row) break;
                            const button = [...row.querySelectorAll('div,button,span')].find(el => {
                                const r = el.getBoundingClientRect();
                                return (el.innerText || '').trim() === '分享' && r.width > 0 && r.height > 0;
                            });
                            if (button) { button.click(); return { clicked: true, scoped: !!panel }; }
                        }
                    }
                }
                return { clicked: false, scoped: !!panel };
            }""",
            recipient,
        )
        clicked = bool((result or {}).get("clicked"))
        verified = False
        for _ in range(16):
            await page.wait_for_timeout(250)
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                body = ""
            if any(word in body for word in ("分享成功", "已分享", "发送成功")):
                verified = True
                break
            try:
                remaining = int(await page.evaluate(panel_count_js))
            except Exception:
                remaining = 1
            if clicked and remaining == 0:
                verified = True
                break
        if not clicked:
            return {"status": "failed", "detail": f"分享面板里没有找到「{recipient}」"}
        return {
            "status": "sent" if verified else "uncertain",
            "detail": "原生视频分享已发送" if verified else "已点击分享，但未读到成功提示",
        }

    async def _open_fans_modal(self, page: Page) -> bool:
        await page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        try:
            hits = await page.evaluate(RISK_JS)
        except Exception:
            hits = []
        if hits:
            raise RiskControl("页面出现风控/验证提示：" + "、".join(str(x) for x in hits))
        trigger = page.locator('[data-e2e="user-info-fans"]')
        clicked = False
        for i in range(await trigger.count()):
            try:
                if await trigger.nth(i).is_visible():
                    await trigger.nth(i).click(timeout=8000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            try:
                clicked = bool(
                    await page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-e2e="user-info-fans"]');
                            if (!el) return false;
                            el.click();
                            return true;
                        }"""
                    )
                )
            except Exception:
                clicked = False
        if not clicked:
            return False
        for _ in range(25):
            await page.wait_for_timeout(300)
            box = page.locator('[data-e2e="user-fans-container"]')
            if not await box.count():
                continue
            try:
                if not await box.first.is_visible():
                    continue
            except Exception:
                continue
            # 先把列表滚到底，确保后面的粉丝节点也加载出来。
            for _ in range(2):
                try:
                    await page.evaluate(FANS_SCROLL_JS)
                except Exception:
                    pass
                await page.wait_for_timeout(350)
            return True
        return False

    async def list_fans(self, limit: int = 24) -> dict[str, Any]:
        if self.is_mock:
            return {"items": [], "total": 0}
        if not self.context:
            raise BrowserError("浏览器还没有启动")
        if not await self.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        page = await self.new_page()
        try:
            if not await self._open_fans_modal(page):
                raise ActionError("没有打开粉丝列表，可能页面改版了")
            data = await page.evaluate(FANS_LIST_JS, max(1, min(int(limit), 60)))
            return dict(data or {"items": [], "total": 0})
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _follow_via_profile(self, page: Page, name: str, profile_url: str) -> dict[str, Any]:
        """有主页链接时直接去对方主页点关注/回关，比打开粉丝弹窗快很多。"""
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
        state: dict[str, Any] = {}
        for _ in range(10):
            state = dict(await page.evaluate(UNFOLLOW_STATE_JS) or {})
            if state.get("hits"):
                break
            await page.wait_for_timeout(300)
        try:
            hits = await page.evaluate(RISK_JS)
        except Exception:
            hits = []
        if hits:
            raise RiskControl("页面出现风控/验证提示：" + "、".join(str(x) for x in hits))
        if state.get("following"):
            return {"status": "already", "detail": "已经关注或互关，无需重复操作", "name": name}
        if not await page.evaluate(FOLLOW_BUTTON_JS):
            return {"status": "failed", "detail": "主页上没有找到关注/回关按钮", "name": name}
        btn = page.locator('[data-dy-agent-follow-profile="1"]').first
        try:
            await btn.click(timeout=5000)
        except Exception as exc:
            return {"status": "failed", "detail": f"关注按钮点击失败：{str(exc)[:160]}", "name": name}
        following = False
        for _ in range(8):
            await page.wait_for_timeout(250)
            state = dict(await page.evaluate(UNFOLLOW_STATE_JS) or {})
            if state.get("following"):
                following = True
                break
        try:
            hits = await page.evaluate(RISK_JS)
        except Exception:
            hits = []
        if hits:
            return {"status": "uncertain", "detail": "回关后页面出现验证提示，请手动确认结果", "name": name}
        return {
            "status": "sent" if following else "uncertain",
            "detail": "已回关" if following else "已点击回关，但未读到状态变化",
            "name": name,
            "profile_url": profile_url,
        }

    async def follow_fan(self, name: str, profile_url: str = "") -> dict[str, Any]:
        name = (name or "").strip()
        profile_url = (profile_url or "").strip()
        if not name and not profile_url:
            raise ActionError("缺少粉丝账号")
        if self.is_mock:
            return {"status": "dry_run", "detail": "预演，未回关", "name": name}
        if not self.context:
            raise BrowserError("浏览器还没有启动")
        if not await self.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        page = await self.new_page()
        try:
            if profile_url:
                return await self._follow_via_profile(page, name, profile_url)
            if not await self._open_fans_modal(page):
                raise ActionError("没有打开粉丝列表，可能页面改版了")
            found = await page.evaluate(
                FANS_FIND_JS, {"name": name, "url": profile_url}
            )
            found = dict(found or {})
            if found.get("error") == "already":
                return {
                    "status": "already",
                    "detail": "已经关注或互关，无需重复操作",
                    "name": found.get("name") or name,
                }
            if found.get("error"):
                return {
                    "status": "failed",
                    "detail": "粉丝列表里没有找到这个账号",
                    "name": name,
                }
            btn = page.locator('[data-dy-agent-follow="1"]').first
            try:
                await btn.scroll_into_view_if_needed(timeout=5000)
                await btn.click(timeout=5000)
            except Exception as exc:
                return {
                    "status": "failed",
                    "detail": f"回关按钮点击失败：{str(exc)[:160]}",
                    "name": found.get("name") or name,
                }
            state = ""
            for _ in range(8):
                await page.wait_for_timeout(250)
                try:
                    state = str(
                        await page.evaluate(
                            """() => {
                                const row = document.querySelector('[data-dy-agent-fan="1"]');
                                if (!row) return '';
                                const button = [...row.querySelectorAll('.G93_q6Y6 .semi-button')][0];
                                return button ? (button.innerText || '').trim() : '';
                            }"""
                        )
                        or ""
                    )
                except Exception:
                    state = ""
                if state and state != "回关":
                    break
            try:
                hits = await page.evaluate(RISK_JS)
            except Exception:
                hits = []
            if hits:
                return {
                    "status": "uncertain",
                    "detail": "回关后页面出现验证提示，请手动确认结果",
                    "name": found.get("name") or name,
                }
            verified = bool(state and state != "回关")
            return {
                "status": "sent" if verified else "uncertain",
                "detail": "已回关" if verified else "已点击回关，但未读到状态变化",
                "name": found.get("name") or name,
                "profile_url": found.get("url") or profile_url,
                "state": state,
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def unfollow_fan(self, name: str, profile_url: str = "") -> dict[str, Any]:
        name = (name or "").strip()
        profile_url = (profile_url or "").strip()
        if not profile_url:
            raise ActionError("缺少粉丝主页链接")
        if self.is_mock:
            return {"status": "dry_run", "detail": "预演，未取关", "name": name}
        if not self.context:
            raise BrowserError("浏览器还没有启动")
        if not await self.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        page = await self.new_page()
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1200)
            try:
                hits = await page.evaluate(RISK_JS)
            except Exception:
                hits = []
            if hits:
                raise RiskControl("页面出现风控/验证提示：" + "、".join(str(x) for x in hits))
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                body = ""
            if name and name not in body:
                # 昵称在部分主页模板里渲染较慢，不能只凭一次 innerText 判断走错页面。
                for _ in range(3):
                    await page.wait_for_timeout(300)
                    try:
                        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    except Exception:
                        body = ""
                    if name in body:
                        break
                if name not in body:
                    if "用户不存在" in body or "账号已注销" in body or "/user/" not in page.url:
                        return {"status": "failed", "detail": "打开的不是有效的粉丝主页"}
            found = await page.evaluate(UNFOLLOW_BUTTON_JS)
            if not found:
                return {
                    "status": "skipped",
                    "detail": "主页没有已关注/互关按钮，可能已经取关",
                    "name": name,
                    "profile_url": profile_url,
                }
            btn = page.locator('[data-dy-agent-unfollow="1"]').first
            try:
                await btn.scroll_into_view_if_needed(timeout=5000)
                await btn.click(timeout=5000)
            except Exception as exc:
                return {
                    "status": "failed",
                    "detail": f"取关按钮点击失败：{str(exc)[:160]}",
                    "name": name,
                    "profile_url": profile_url,
                }
            await page.wait_for_timeout(400)
            clicked_menu = False
            for label in ("取消关注", "不再关注", "取关"):
                try:
                    loc = page.get_by_text(label, exact=True)
                    for i in range(await loc.count()):
                        if await loc.nth(i).is_visible():
                            await loc.nth(i).click(timeout=4000)
                            clicked_menu = True
                            break
                except Exception:
                    continue
                if clicked_menu:
                    break
            await page.wait_for_timeout(350)
            confirmed = await page.evaluate(
                r"""() => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const dialogs = [...document.querySelectorAll('[role="dialog"], .semi-modal, [class*="modal"], [class*="dialog"]')]
                        .filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        });
                    for (const dialog of dialogs) {
                        const text = dialog.innerText || '';
                        if (!/取消关注|不再关注|取关/.test(text)) continue;
                        const button = [...dialog.querySelectorAll('button')]
                            .find(el => ['确定', '确认'].includes(clean(el.innerText)));
                        if (button) {
                            button.setAttribute('data-dy-agent-confirm-unfollow', '1');
                            return true;
                        }
                    }
                    return false;
                }"""
            )
            if confirmed:
                try:
                    confirm_btn = page.locator('[data-dy-agent-confirm-unfollow="1"]').first
                    await confirm_btn.click(timeout=4000)
                    await page.wait_for_timeout(450)
                except Exception:
                    pass
            await page.wait_for_timeout(400)
            state = await page.evaluate(UNFOLLOW_STATE_JS)
            state = dict(state or {})
            if not state.get("following") and (state.get("followable") or not state.get("hits")):
                return {
                    "status": "sent",
                    "detail": "已取关",
                    "name": name,
                    "profile_url": profile_url,
                }
            return {
                "status": "uncertain",
                "detail": "已点击取关，但主页仍显示已关注，请刷新确认",
                "name": name,
                "profile_url": profile_url,
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def read_notifications(self, page: Page, filter_text: str = "全部消息") -> list[dict[str, Any]]:
        trigger = page.locator('[data-e2e="something-button"]').filter(has_text="通知")
        clicked = False
        for i in range(await trigger.count()):
            try:
                if await trigger.nth(i).is_visible():
                    await trigger.nth(i).click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return []
        await page.wait_for_timeout(900)
        if filter_text and filter_text != "全部消息":
            current = page.locator('[data-e2e="cur-filter"]')
            try:
                if await current.count():
                    await current.first.click()
                    await page.wait_for_timeout(450)
                    await page.evaluate(
                        """(label) => {
                            const list = document.querySelector('[data-e2e="cur-dropdown-list"]');
                            if (!list) return false;
                            const opt = [...list.querySelectorAll('div')].find(el => (el.textContent || '').trim() === label);
                            if (!opt) return false;
                            opt.click();
                            return true;
                        }""",
                        filter_text,
                    )
                    await page.wait_for_timeout(1100)
            except Exception:
                pass
        try:
            data = await page.evaluate(NOTIFICATION_ROWS_JS)
        except Exception:
            data = []
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass
        return [dict(x) for x in (data or []) if isinstance(x, dict)]

    async def _find_visible(self, raw_selectors: list[str], *, prefer_last: bool = False):
        page = await self._ensure_page()
        for sel in raw_selectors:
            try:
                loc = page.locator(sel)
                count = await loc.count()
            except Exception:
                continue
            indexes = range(count - 1, -1, -1) if prefer_last else range(min(count, 12))
            for i in indexes:
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        return item
                except Exception:
                    continue
        return None

    async def _editor_text(self, box) -> str:
        try:
            raw = await box.inner_text()
        except Exception:
            return ""
        return (raw or "").replace("\u200b", "").strip()

    async def _mark_current(self) -> None:
        try:
            await (await self._ensure_page()).evaluate(MARK_SCOPE_JS)
        except Exception:
            pass

    async def _open_comments(self) -> bool:
        page = await self._ensure_page()
        if await self._find_visible(selectors.COMMENT_PANEL):
            return True
        await self._mark_current()
        scoped = [f'[data-dy-agent-current="1"] {sel}' for sel in selectors.COMMENT_OPEN]
        trigger = await self._find_visible(scoped + selectors.COMMENT_OPEN)
        if not trigger:
            return False
        try:
            await trigger.click()
        except Exception:
            return False
        await page.wait_for_timeout(1200)
        return bool(await self._find_visible(selectors.COMMENT_PANEL))

    async def current_id(self) -> str:
        page = await self._ensure_page()
        try:
            value = await page.evaluate(CURRENT_ID_JS)
            return str(value or "")
        except Exception:
            return ""

    async def next_video(self) -> str:
        page = await self._ensure_page()
        before = await self.current_id()
        # 1) keyboard feed navigation
        try:
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(1600)
            after = await self.current_id()
            if after and after != before:
                return after
        except Exception:
            pass
        # 2) wheel
        try:
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1600)
            after = await self.current_id()
            if after and after != before:
                return after
        except Exception:
            pass
        # 3) PageDown
        try:
            await page.keyboard.press("PageDown")
            await page.wait_for_timeout(1600)
            after = await self.current_id()
            if after and after != before:
                return after
        except Exception:
            pass
        # 4) feed next arrow（直播页对键盘不敏感，这个更可靠）
        try:
            arrow = page.locator('[data-e2e="video-switch-next-arrow"]')
            for i in range(await arrow.count()):
                try:
                    if await arrow.nth(i).is_visible():
                        await arrow.nth(i).click()
                        await page.wait_for_timeout(1600)
                        after = await self.current_id()
                        if after and after != before:
                            return after
                        break
                except Exception:
                    continue
        except Exception:
            pass
        # 5) 直播/异常卡片可能卡住键盘滚动：刷新推荐流，换一批内容，不终止整轮。
        if not self.is_mock:
            try:
                await page.goto(
                    self.settings.feed_url or "https://www.douyin.com/?recommend=1&from_nav=1",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                await page.wait_for_timeout(2200)
                return await self.current_id()
            except Exception:
                return ""
        raise ActionError("推荐流没有滑动到下一条，已停止，避免在同一个视频上重复操作。")

    async def screenshot(self, tag: str = "action") -> str:
        page = await self._ensure_page()
        name = f"{tag}_{_now_tag()}.png"
        path = self._shots / name
        try:
            await page.screenshot(path=str(path), full_page=False)
            return f"/media/shots/{name}"
        except Exception:
            return ""

    async def _like_state(self, loc) -> bool:
        try:
            return bool(
                await loc.evaluate(
                    """el => {
                       const state = el.getAttribute('data-e2e-state') || '';
                       if (state.includes('no-digged')) return false;
                       if (state.includes('digged')) return true;
                       return el.getAttribute('aria-pressed') === 'true' ||
                         /(?:^|\\s)(liked|active)(?:\\s|$)/i.test(el.className || '') ||
                         !!(el.querySelector && el.querySelector('.liked'));
                     }"""
                )
            )
        except Exception:
            return False

    async def like(self) -> dict[str, Any]:
        page = await self._ensure_page()
        await self.assert_safe()
        await self._mark_current()
        scoped = [f'[data-dy-agent-current="1"] {sel}' for sel in selectors.LIKE]
        btn = await self._find_visible(scoped + selectors.LIKE)
        if not btn:
            shot = await self.screenshot("like_missing")
            raise ActionError(f"没有找到点赞按钮，可能页面改版。截图：{shot}")
        if await self._like_state(btn):
            return {"status": "skipped", "detail": "已经是点赞状态"}
        try:
            await btn.click()
        except Exception as exc:
            raise ActionError(f"点赞点击失败：{exc}") from exc
        await page.wait_for_timeout(650)
        state = await self._like_state(btn)
        shot = await self.screenshot("like")
        return {
            "status": "sent" if state else "uncertain",
            "detail": "已点击点赞" + ("，状态已确认" if state else "，未读到确认状态"),
            "screenshot": shot,
        }

    async def _insert_comment_mention(self, box, name: str) -> bool:
        page = await self._ensure_page()
        name = (name or "").strip()
        if not name:
            return False

        async def find_option():
            try:
                return await page.evaluate(MENTION_OPTION_JS, name)
            except Exception:
                return None

        try:
            await box.click()
            await page.keyboard.type("@")
            option = None
            for i in range(6):
                await page.wait_for_timeout(400)
                option = await find_option()
                if option:
                    break
                if i == 2 and len(name) > 3:
                    await page.keyboard.type(name[:4])
            if option:
                await page.mouse.click(
                    float(option["x"]) + float(option["w"]) / 2,
                    float(option["y"]) + float(option["h"]) / 2,
                )
                await page.wait_for_timeout(650)
                chips = await box.locator(".douyin_mention_word, [class*='mention_word']").count()
                if chips:
                    await box.focus()
                    await page.keyboard.press("End")
                    return True
            await box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(200)
            return False
        except Exception:
            try:
                await box.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
            except Exception:
                pass
            return False

    async def comment(self, text: str, mention: str = "") -> dict[str, Any]:
        page = await self._ensure_page()
        await self.assert_safe()
        if not await self._open_comments():
            shot = await self.screenshot("comment_missing")
            raise ActionError(f"没有找到评论输入框，可能页面改版。截图：{shot}")
        trigger = await self._find_visible(selectors.COMMENT_INPUT_TRIGGER)
        if trigger:
            try:
                await trigger.click()
                await page.wait_for_timeout(400)
            except Exception:
                pass
        box = await self._find_visible(selectors.COMMENT_INPUT, prefer_last=True)
        if not box:
            shot = await self.screenshot("comment_input_missing")
            raise ActionError(f"评论输入框不可见。截图：{shot}")
        draft = await self._editor_text(box)
        if draft:
            shot = await self.screenshot("comment_draft")
            raise ActionError(f"评论输入框里已有未发送草稿，已停止，避免混入。截图：{shot}")
        mention_used = False
        try:
            await box.click()
            if mention:
                mention_used = await self._insert_comment_mention(box, mention)
                if not mention_used:
                    await page.keyboard.insert_text(f"@{mention} ")
            await page.keyboard.insert_text(text)
            await page.wait_for_timeout(350)
        except Exception as exc:
            raise ActionError(f"填写评论失败：{exc}") from exc
        send = await self._find_visible(selectors.COMMENT_SEND)
        if not send:
            shot = await self.screenshot("comment_send_missing")
            raise ActionError(f"没有找到评论发送按钮。截图：{shot}")
        try:
            await send.click()
        except Exception as exc:
            raise ActionError(f"发送评论失败：{exc}") from exc
        await page.wait_for_timeout(1100)
        verified = False
        try:
            current = (await box.inner_text()).strip() if await box.is_visible() else ""
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            probe = text[:24].strip()
            verified = (not current) or bool(probe and probe in body) or bool(mention and mention in body)
        except Exception:
            pass
        shot = await self.screenshot("comment")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        note = ""
        if mention:
            note = "；已 @ 接收账号" if mention_used else "；@ 为纯文本（未选中联系人）"
        return {
            "status": "sent" if verified else "uncertain",
            "detail": ("评论已发送" if verified else "已点击发送，但未读到确认状态") + note,
            "screenshot": shot,
        }

    async def author_relation(self, author_url: str, author_name: str = "") -> dict[str, Any]:
        """Open the author profile in a background tab and read the follow relation."""
        if self.is_mock:
            return {"mutual": False, "following": False, "hits": []}
        if not author_url:
            return {"mutual": False, "following": False, "hits": []}
        assert self.context is not None
        page = await self.new_page()
        try:
            await page.goto(author_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1800)
            if not await self.is_logged_in():
                raise LoginRequired("登录态不可用，无法核对互关关系")
            data = await page.evaluate(RELATION_JS)
            if author_name and author_name.strip():
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                if author_name not in body:
                    raise ActionError(f"打开的作者页不是「{author_name}」，已停止私信。")
            return dict(data or {})
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def dm(self, author_url: str, author_name: str, text: str) -> dict[str, Any]:
        if not author_url:
            raise ActionError("没有作者主页，无法私信")
        assert self.context is not None
        page = await self.new_page()
        active = page
        page.set_default_timeout(15000)
        try:
            await page.goto(author_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2200)
            if not await self.is_logged_in():
                raise LoginRequired("登录态不可用，无法私信")
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            if author_name and author_name.strip() and author_name not in body:
                raise ActionError(f"打开的作者页不是「{author_name}」，已停止私信。")
            btn = await self._find_visible(selectors.DM_BUTTON)
            if not btn:
                shot_path = self._shots / f"dm_missing_{_now_tag()}.png"
                await page.screenshot(path=str(shot_path))
                raise ActionError(f"作者页没有找到私信按钮。截图：/media/shots/{shot_path.name}")
            before_pages = set(self.context.pages)
            await btn.click()
            await page.wait_for_timeout(1000)
            # 私信可能开在当前页，也可能开了新页面；只认这次点击新开的页面。
            opened = [p for p in self.context.pages if p not in before_pages]
            if opened:
                active = opened[-1]
                await active.wait_for_timeout(700)
            box = await self._find_visible_in(active, selectors.DM_INPUT, prefer_last=True)
            if not box:
                raise ActionError("没有找到私信输入框")
            draft = await self._editor_text(box)
            if draft:
                raise ActionError("私信输入框里已有未发送草稿，已停止，避免混入。")
            await box.click()
            await active.keyboard.insert_text(text)
            await active.wait_for_timeout(250)
            send = await self._find_visible_in(active, selectors.DM_SEND)
            if not send:
                raise ActionError("没有找到私信发送按钮")
            await send.click()
            await active.wait_for_timeout(900)
            body_after = await active.evaluate("() => document.body ? document.body.innerText : ''")
            verified = text[:18] in body_after
            shot_name = f"dm_{_now_tag()}.png"
            await active.screenshot(path=str(self._shots / shot_name))
            return {
                "status": "sent" if verified else "uncertain",
                "detail": "私信已发送" if verified else "已点击私信发送，但未读到确认",
                "screenshot": f"/media/shots/{shot_name}",
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass
            if active is not page:
                try:
                    await active.close()
                except Exception:
                    pass

    async def _find_visible_in(self, page: Page, raw_selectors: list[str], *, prefer_last: bool = False):
        for sel in raw_selectors:
            try:
                loc = page.locator(sel)
                count = await loc.count()
            except Exception:
                continue
            indexes = range(count - 1, -1, -1) if prefer_last else range(min(count, 12))
            for i in indexes:
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        return item
                except Exception:
                    continue
        return None

    async def snapshot(self, tag: str = "manual") -> dict[str, Any]:
        page = await self._ensure_page()
        stamp = _now_tag()
        html_path = DEBUG_DIR / f"{tag}_{stamp}.html"
        png_path = self._shots / f"{tag}_{stamp}.png"
        html_path.write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(png_path), full_page=False)
        return {
            "url": page.url,
            "html": f"/debug/{html_path.name}",
            "screenshot": f"/media/shots/{png_path.name}",
        }

    async def status(self) -> dict[str, Any]:
        started = bool(self.context and self.page and not self.page.is_closed())
        logged = await self.is_logged_in() if started else False
        return {
            "started": started,
            "logged_in": logged,
            "mock": self.is_mock,
            "headless": self.headless,
            "url": self.page.url if started and self.page else "",
            "risk": [],
            "profile": str(self.profile_dir),
            "last_error": self.last_error,
        }

    async def open_login(self) -> dict[str, Any]:
        # 登录扫码必须让人看见；后台静默只用于登录后的运行。
        if self.headless:
            await self.close()
        self.headless = False
        if self.context and (not self.page or self.page.is_closed()):
            await self.close()
        if not self.context or not self.page:
            self.start_url = ""
            await self.start(show=True)
        else:
            await self.set_window_visible(True)
            try:
                await self.page.bring_to_front()
                await self._focus_window()
            except Exception:
                pass
        if self.page and "douyin.com" not in self.page.url:
            try:
                await self.page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
                await self.page.wait_for_timeout(1500)
                await self.page.bring_to_front()
                await self.set_window_visible(True)
                await self._focus_window()
            except Exception:
                pass
        result = await self.status()
        if not result.get("started"):
            raise BrowserError(
                "浏览器进程没有保持运行。请查看 douyin-agent/data/server.log；"
                "如果多次失败，先关闭残留的 Chrome 再试。"
            )
        return result

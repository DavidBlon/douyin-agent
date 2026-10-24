"""在抖音页面上 evaluate 的 JS 片段。

按域分组：粉丝/关注、推荐流读取与标记、评论、@ 下拉、互关关系、
导航红点、会话列表、私信消息、通知行、风控文案。选择器优先改 app/selectors.py，
这里只放必须跑在页面里的逻辑。
"""
from __future__ import annotations


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
  }  const entryBadge = el => {
    if (!el) return -1;
    let node = el;
    for (let i = 0; i < 3 && node; i++, node = node.parentElement) {
      const r = node.getBoundingClientRect();
      if (r.width > 180 || r.height > 120) break;
      if (hasBadge(node)) return 1;
    }
    return 0;
  };
  const findByLabel = label => {
    const matches = [...document.querySelectorAll('a,button,div,span,li')]
      .filter(el => visible(el) && (el.innerText || '').trim() === label)
      .sort((a, b) => {
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        return ra.width * ra.height - rb.width * rb.height;
      });
    if (matches[0]) return matches[0];
    return document.querySelector(`[aria-label*="${label}"],[title*="${label}"]`);
  };
  const header = document.querySelector('#douyin-header') || document.querySelector('header');
  const noticeEntry = findByLabel('通知');
  const imLabelEntry = findByLabel('消息');
  const imLabelBadge = entryBadge(imLabelEntry);
  if (imLabelBadge === 1) im = 1;
  else if (im === -1) im = imLabelBadge;
  let notice = entryBadge(noticeEntry);
  const debug = [];
  if (noticeEntry) {
    const r = noticeEntry.getBoundingClientRect();
    debug.push({
      tag: 'NOTICE_ENTRY',
      cls: typeof noticeEntry.className === 'string' ? noticeEntry.className.slice(0, 60) : '',
      w: Math.round(r.width),
      h: Math.round(r.height),
      text: (noticeEntry.innerText || '').trim().slice(0, 12),
    });
  }
  if (imLabelEntry) {
    const r = imLabelEntry.getBoundingClientRect();
    debug.push({
      tag: 'MSG_ENTRY',
      cls: typeof imLabelEntry.className === 'string' ? imLabelEntry.className.slice(0, 60) : '',
      w: Math.round(r.width),
      h: Math.round(r.height),
      text: (imLabelEntry.innerText || '').trim().slice(0, 12),
    });
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
    const kind = (text.includes('评论了你的作品') || text.includes('回复了你的评论') || text.includes('评论中提到了你'))
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


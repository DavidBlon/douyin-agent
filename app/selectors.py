"""DOM selectors.

These are deliberately kept in one file: Douyin's class names change often.
The data-e2e names are the most stable anchors. After the first real login,
use the in-app "保存 DOM 快照" button and adjust this file if a selector misses.
"""

LIKE = [
    '[data-e2e="video-player-digg"]',
    '[data-e2e="like-icon"]',
    '[data-e2e="video-like"]',
    '[data-e2e="like-button"]',
    'button[aria-label*="点赞"]',
    '[aria-label*="点赞"]',
]

COMMENT_OPEN = [
    '[data-e2e="feed-comment-icon"]',
    '[data-e2e="comment-icon"]',
    '[data-e2e="video-comment"]',
    'button[aria-label*="评论"]',
    '[aria-label*="评论"]',
]

COMMENT_INPUT_TRIGGER = [
    '.comment-input-container',
    '.comment-input-inner-container',
    '[data-e2e="comment-input"]',
    '[data-e2e="comment-input"] [contenteditable="true"]',
]

COMMENT_INPUT = [
    '.comment-input-container .public-DraftEditor-content',
    '[data-e2e="comment-input"] [contenteditable="true"]',
    'div[contenteditable="true"]',
    '[data-e2e="comment-input"]',
    'textarea[placeholder*="评论"]',
    'textarea',
]

COMMENT_SEND = [
    '[data-e2e="comment-send"]',
    '.comment-input-container span:has(svg path[fill="#FE2C55"])',
    '.comment-input-container .commentInput-right-ct span:last-child',
    'button:has-text("发送")',
    'button:has-text("发布")',
]

COMMENT_PANEL = [
    '[data-e2e="comment-list"]',
    '[data-e2e="comment-panel"]',
    '[class*="comment-list"]',
]

COMMENT_ITEM = [
    '[data-e2e="comment-item"]',
    '[class*="comment-item"]',
]

DM_BUTTON = [
    '[data-e2e="user-dm"]',
    'button:has-text("私信")',
    'button:has-text("发私信")',
    '[aria-label*="私信"]',
]

DM_INPUT = [
    '[data-e2e="msg-input"] [contenteditable="true"]',
    '[data-e2e="msg-input"]',
    '.messageEditorinputArea[contenteditable="true"]',
    '[data-e2e="message-input"] [contenteditable="true"]',
    '[data-e2e="message-input"]',
    'div[contenteditable="true"]',
    'textarea[placeholder*="消息"]',
    'textarea',
]

DM_SEND = [
    '.e2e-send-msg-btn',
    'svg.messageMsgInputpublishBtn',
    '[data-e2e="message-send"]',
    'button:has-text("发送")',
]

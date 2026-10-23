import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft,
  LoaderCircle,
  MessageCircle,
  Play,
  RefreshCw,
  Search,
  Send,
  Users,
  X,
} from "lucide-react";
import { api } from "../api";
import type { ChatConversation, ChatMedia, ChatMessage, ChatSummary } from "../types";

function unreadLabel(count: number) {
  return count > 99 ? "99+" : String(count);
}

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), ms);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timer);
        reject(error);
      }
    );
  });
}

function Avatar({ src, name, size = 42 }: { src?: string; name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  const style = { width: size, height: size };
  if (!src || failed) {
    return (
      <span className="chat-avatar chat-avatar-fallback" style={style}>
        {(name || "?").trim().slice(0, 1)}
      </span>
    );
  }
  return (
    <img
      className="chat-avatar"
      style={style}
      src={src}
      alt={name}
      referrerPolicy="no-referrer"
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

interface Viewer {
  kind: "image" | "video";
  src: string;
  cover?: string;
  pageUrl?: string;
  title?: string;
}

export function ChatDock() {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"list" | "chat">("list");
  const [summary, setSummary] = useState<ChatSummary | null>(null);
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [active, setActive] = useState<ChatConversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const [loadingList, setLoadingList] = useState(false);
  const [loadingChat, setLoadingChat] = useState(false);
  const [sending, setSending] = useState(false);
  const [listError, setListError] = useState("");
  const [chatError, setChatError] = useState("");
  const [resolvingId, setResolvingId] = useState("");
  const [viewer, setViewer] = useState<Viewer | null>(null);
  const [viewerError, setViewerError] = useState(false);

  const activeKeyRef = useRef("");
  const sendingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const stickRef = useRef(true);
  const msgSigRef = useRef("");

  const unread = summary?.unread_total || 0;

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const next = await api.chatSummary();
        if (alive) setSummary(next);
      } catch {
        /* transient */
      }
    };
    void tick();
    const timer = window.setInterval(tick, 12000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    return () => {
      void api.chatLeave().catch(() => {});
    };
  }, []);

  const loadConversations = useCallback(async (refresh: boolean) => {
    setLoadingList(true);
    try {
      const res = await withTimeout(api.chatConversations(refresh), 25000, "会话加载超时，请重试");
      setConversations(res.conversations);
      setSummary(res);
      setListError("");
    } catch (e) {
      setListError((e as Error).message);
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void loadConversations(false);
    const warm = window.setTimeout(() => {
      void api.chatConversations(true).catch(() => {});
    }, 350);
    const timer = window.setInterval(() => {
      void loadConversations(false);
    }, 25000);
    return () => {
      window.clearTimeout(warm);
      window.clearInterval(timer);
    };
  }, [open, loadConversations]);

  useEffect(() => {
    if (!open || view !== "chat" || !active) return;
    const key = active.key;
    let alive = true;
    const timer = window.setInterval(async () => {
      if (sendingRef.current) return;
      try {
        const res = await api.chatMessages(key);
        if (!alive || activeKeyRef.current !== key) return;
        const sig = res.messages.map((m) => m.id).join("|");
        if (sig === msgSigRef.current) return;
        msgSigRef.current = sig;
        setMessages(res.messages);
        setActive(res.conversation);
        setConversations((prev) =>
          prev.map((item) => (item.key === key ? { ...item, ...res.conversation, unread: 0 } : item))
        );
      } catch {
        /* keep the current transcript on transient failures */
      }
    }, 9000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [open, view, active]);

  useEffect(() => {
    if (view !== "chat") return;
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, loadingChat, view]);

  const loadMessages = useCallback(async (conv: ChatConversation, showLoading: boolean) => {
    if (showLoading) setLoadingChat(true);
    setChatError("");
    try {
      const res = await withTimeout(api.chatMessages(conv.key), 45000, "消息加载超时，请重试");
      if (activeKeyRef.current !== conv.key) return;
      msgSigRef.current = res.messages.map((m) => m.id).join("|");
      setMessages(res.messages);
      setActive(res.conversation);
      setConversations((prev) =>
        prev.map((item) => (item.key === conv.key ? { ...item, ...res.conversation, unread: 0 } : item))
      );
    } catch (e) {
      if (activeKeyRef.current === conv.key) setChatError((e as Error).message);
    } finally {
      if (showLoading) setLoadingChat(false);
    }
  }, []);

  const openConversation = useCallback(
    (conv: ChatConversation) => {
      setView("chat");
      setActive(conv);
      activeKeyRef.current = conv.key;
      msgSigRef.current = "";
      setMessages([]);
      setChatError("");
      setViewer(null);
      stickRef.current = true;
      setConversations((prev) => prev.map((item) => (item.key === conv.key ? { ...item, unread: 0 } : item)));
      setSummary((prev) =>
        prev ? { ...prev, unread_total: Math.max(0, prev.unread_total - conv.unread) } : prev
      );
      void loadMessages(conv, true);
    },
    [loadMessages]
  );

  const leaveChat = useCallback(() => {
    setView("list");
    setActive(null);
    activeKeyRef.current = "";
    setMessages([]);
    setChatError("");
    setViewer(null);
    void api.chatLeave().catch(() => {});
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setView("list");
    setActive(null);
    activeKeyRef.current = "";
    setMessages([]);
    setChatError("");
    setViewer(null);
    void api.chatLeave().catch(() => {});
  }, []);

  useEffect(() => {
    if (!viewer && !open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (viewer) {
        setViewer(null);
        return;
      }
      close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewer, open, close]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || !active || active.is_group || sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    setChatError("");
    try {
      const res = await withTimeout(api.chatSend(active.key, text), 45000, "发送耗时较长，请稍后在会话里确认是否已发出");
      if (activeKeyRef.current !== active.key) return;
      msgSigRef.current = res.messages.map((m) => m.id).join("|");
      setMessages(res.messages);
      setActive(res.conversation);
      setConversations((prev) =>
        prev.map((item) =>
          item.key === active.key
            ? { ...item, preview: text, time: "刚刚", unread: 0 }
            : item
        )
      );
      setDraft("");
      if (inputRef.current) inputRef.current.style.height = "38px";
      stickRef.current = true;
    } catch (e) {
      setChatError((e as Error).message);
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }, [active, draft]);

  const playVideo = useCallback(
    async (msg: ChatMessage, media: ChatMedia) => {
      if (!active) return;
      const cover = media.cover || "";
      setViewerError(false);
      if (media.video_url) {
        setViewer({ kind: "video", src: media.video_url, cover, title: msg.text, pageUrl: media.url });
        return;
      }
      if (!cover) {
        setChatError("这条视频没有封面，暂时打不开");
        return;
      }
      if (resolvingId) return;
      setResolvingId(msg.id);
      setChatError("");
      try {
        const res = await withTimeout(api.chatVideo(active.key, cover), 45000, "视频解析超时，请重试");
        setViewerError(false);
        setViewer({
          kind: "video",
          src: res.video_url,
          cover: res.cover || cover,
          title: msg.text,
          pageUrl: res.page_url,
        });
        if (!res.video_url) setChatError("视频直链没取到，可以点“在抖音打开”");
      } catch (e) {
        setChatError((e as Error).message);
      } finally {
        setResolvingId("");
      }
    },
    [active, resolvingId]
  );

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return conversations;
    return conversations.filter((item) =>
      `${item.title} ${item.preview}`.toLowerCase().includes(query)
    );
  }, [conversations, search]);

  const renderMedia = (msg: ChatMessage, media: ChatMedia, index: number) => {
    if (media.kind === "emoji") {
      return (
        <img
          key={`${msg.id}-m-${index}`}
          className="chat-emoji"
          src={media.url}
          alt="表情"
          referrerPolicy="no-referrer"
          loading="lazy"
        />
      );
    }
    if (media.kind === "image") {
      return (
        <button
          key={`${msg.id}-m-${index}`}
          className="chat-image"
          type="button"
          onClick={() => {
            setViewerError(false);
            setViewer({ kind: "image", src: media.url });
          }}
          title="查看图片"
        >
          <img src={media.url} alt="图片" referrerPolicy="no-referrer" loading="lazy" />
        </button>
      );
    }
    const ratio = media.width && media.height ? `${media.width} / ${media.height}` : "3 / 4";
    const portrait = !media.width || !media.height || media.height >= media.width;
    return (
      <button
        key={`${msg.id}-m-${index}`}
        className="chat-video-card"
        type="button"
        style={{ aspectRatio: ratio, width: portrait ? 152 : 232, maxWidth: "100%" }}
        onClick={() => void playVideo(msg, media)}
        title="播放视频"
      >
        {media.cover ? <img src={media.cover} alt="视频封面" referrerPolicy="no-referrer" loading="lazy" /> : <span className="chat-video-empty" />}
        <span className="chat-play">
          {resolvingId === msg.id ? <LoaderCircle size={28} className="spin" /> : <Play size={30} fill="currentColor" />}
        </span>
        {resolvingId === msg.id ? <span className="chat-video-loading">正在解析视频…</span> : null}
      </button>
    );
  };

  return (
    <>
      <button
        className={`chat-ball ${open ? "is-open" : ""}`}
        onClick={() => (open ? close() : setOpen(true))}
        type="button"
        aria-label={open ? "收起私信" : "打开私信"}
        aria-expanded={open}
        aria-controls="chat-dock-panel"
        title="私信"
      >
        <MessageCircle size={23} />
        {unread > 0 ? <span className="chat-badge">{unreadLabel(unread)}</span> : null}
      </button>

      {open ? (
        <section className="chat-panel" id="chat-dock-panel" aria-label="私信">
          {view === "list" ? (
            <>
              <header className="chat-head">
                <div className="chat-head-title">
                  <MessageCircle size={16} />
                  <h3>消息</h3>
                  {unread > 0 ? <span className="chat-title-badge">{unreadLabel(unread)}</span> : null}
                </div>
                <div className="chat-head-actions">
                  <button
                    className="chat-icon-btn"
                    onClick={() => void loadConversations(true)}
                    disabled={loadingList}
                    type="button"
                    title="刷新会话"
                  >
                    <RefreshCw size={15} className={loadingList ? "spin" : ""} />
                  </button>
                  <button className="chat-icon-btn" onClick={close} type="button" title="关闭">
                    <X size={16} />
                  </button>
                </div>
              </header>

              <div className="chat-search">
                <div className="chat-search-wrap">
                  <Search size={14} />
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="搜索"
                    aria-label="搜索会话"
                  />
                </div>
              </div>

              <div className="chat-list">
                {loadingList && !conversations.length ? (
                  <div className="chat-loading">
                    <LoaderCircle size={16} className="spin" /> 正在读取私信…
                  </div>
                ) : null}
                {listError && !conversations.length ? (
                  <div className="chat-error">
                    <p>{listError}</p>
                    <button className="btn btn-ghost" onClick={() => void loadConversations(true)} type="button">
                      重试
                    </button>
                  </div>
                ) : null}
                {!loadingList && !listError && !filtered.length ? <div className="chat-empty">暂无会话</div> : null}
                {filtered.map((conv) => (
                  <button
                    key={conv.key}
                    className="chat-conv"
                    onClick={() => openConversation(conv)}
                    type="button"
                  >
                    <Avatar src={conv.avatar} name={conv.title} size={42} />
                    <span className="chat-conv-main">
                      <span className="chat-conv-top">
                        <span className="chat-conv-name">{conv.title}</span>
                        {conv.is_group ? <Users size={11} className="chat-group-icon" /> : null}
                        <span className="chat-conv-time">{conv.time}</span>
                      </span>
                      <span className="chat-conv-bottom">
                        <span className="chat-conv-preview">{conv.preview || (conv.is_group ? "群聊" : "")}</span>
                        {conv.unread > 0 ? (
                          <span className="chat-conv-badge">{unreadLabel(conv.unread)}</span>
                        ) : null}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <>
              <header className="chat-head chat-head-chat">
                <button className="chat-icon-btn" onClick={leaveChat} type="button" title="返回会话列表">
                  <ChevronLeft size={18} />
                </button>
                <Avatar src={active?.avatar} name={active?.title || ""} size={30} />
                <div className="chat-peer">
                  <strong>{active?.title || "私信"}</strong>
                  {active?.is_group ? <span className="chat-group-chip">群聊</span> : null}
                </div>
                <div className="chat-head-actions">
                  <button
                    className="chat-icon-btn"
                    onClick={() => active && void loadMessages(active, true)}
                    disabled={loadingChat}
                    type="button"
                    title="刷新消息"
                  >
                    <RefreshCw size={15} className={loadingChat ? "spin" : ""} />
                  </button>
                  <button className="chat-icon-btn" onClick={close} type="button" title="关闭">
                    <X size={16} />
                  </button>
                </div>
              </header>

              <div
                className="chat-messages"
                ref={scrollRef}
                onScroll={(event) => {
                  const el = event.currentTarget;
                  stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
                }}
              >
                {loadingChat && !messages.length ? (
                  <div className="chat-loading">
                    <LoaderCircle size={16} className="spin" /> 正在读取消息…
                  </div>
                ) : null}
                {chatError && !messages.length && !loadingChat ? (
                  <div className="chat-error">
                    <p>{chatError}</p>
                    {active ? (
                      <button className="btn btn-ghost" onClick={() => void loadMessages(active, true)} type="button">
                        重试
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {!loadingChat && !chatError && !messages.length ? <div className="chat-empty">还没有消息</div> : null}

                {messages.map((msg, index) => {
                  const previous = messages[index - 1];
                  const showTime = Boolean(msg.time) && (!previous || previous.time !== msg.time);
                  if (msg.system) {
                    return (
                      <div key={msg.id} className="chat-system-wrap">
                        {showTime ? <div className="chat-time">{msg.time}</div> : null}
                        <div className="chat-system">{msg.text}</div>
                      </div>
                    );
                  }
                  const video = msg.media.find((item) => item.kind === "video");
                  const otherMedia = msg.media.filter((item) => item.kind !== "video");
                  const caption = video ? msg.text : "";
                  const bubbleText = otherMedia.length && !msg.text ? "" : msg.text;
                  const selfAvatar = messages.find((m) => !m.incoming && m.avatar)?.avatar || "";
                  return (
                    <div key={msg.id} className={`chat-msg ${msg.incoming ? "in" : "out"}`}>
                      {showTime ? <div className="chat-time">{msg.time}</div> : null}
                      <div className="chat-msg-row">
                        <Avatar
                          src={msg.avatar || (msg.incoming ? active?.avatar : selfAvatar)}
                          name={msg.incoming ? active?.title || "对方" : "我"}
                          size={28}
                        />
                        <div className={`chat-msg-body ${video ? "has-video" : ""}`}>
                          {bubbleText && !video ? <div className="chat-bubble">{bubbleText}</div> : null}
                          {video ? (
                            <div className={`chat-video-wrap ${msg.incoming ? "in" : "out"}`}>
                              {renderMedia(msg, video, 0)}
                              {caption ? <span className="chat-media-caption">{caption}</span> : null}
                            </div>
                          ) : null}
                          {otherMedia.length ? (
                            <div className="chat-msg-media">
                              {otherMedia.map((media, mediaIndex) => renderMedia(msg, media, mediaIndex + (video ? 1 : 0)))}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {chatError && messages.length ? <div className="chat-inline-error">{chatError}</div> : null}

              {active?.is_group ? (
                <div className="chat-group-note">群聊暂不支持在这里发送</div>
              ) : (
                <div className="chat-composer">
                  <textarea
                    ref={inputRef}
                    value={draft}
                    rows={1}
                    placeholder="发送消息"
                    aria-label="发送消息"
                    onChange={(event) => {
                      setDraft(event.target.value);
                      const el = event.currentTarget;
                      el.style.height = "38px";
                      el.style.height = `${Math.min(el.scrollHeight, 96)}px`;
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                        event.preventDefault();
                        void send();
                      }
                    }}
                  />
                  <button
                    className="chat-send"
                    onClick={() => void send()}
                    disabled={!draft.trim() || sending}
                    type="button"
                    title="发送"
                  >
                    {sending ? <LoaderCircle size={16} className="spin" /> : <Send size={17} />}
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      ) : null}

      {viewer ? (
        <div className="chat-viewer" onClick={() => setViewer(null)}>
          <button className="chat-viewer-close" onClick={() => setViewer(null)} type="button" title="关闭">
            <X size={18} />
          </button>
          {viewer.kind === "image" ? (
            <img
              className="chat-viewer-media"
              src={viewer.src}
              alt="图片"
              referrerPolicy="no-referrer"
              onClick={(event) => event.stopPropagation()}
            />
          ) : viewer.src && !viewerError ? (
            <video
              className="chat-viewer-media"
              src={viewer.src}
              poster={viewer.cover}
              controls
              autoPlay
              playsInline
              onClick={(event) => event.stopPropagation()}
              onError={() => setViewerError(true)}
            />
          ) : (
            <div className="chat-viewer-fallback" onClick={(event) => event.stopPropagation()}>
              {viewer.cover ? <img src={viewer.cover} alt="视频封面" referrerPolicy="no-referrer" /> : null}
              <p>{viewerError ? "当前浏览器无法播放这个视频，可以在抖音打开" : "这个视频暂时无法内嵌播放"}</p>
            </div>
          )}
          {viewer.pageUrl ? (
            <a
              className="chat-viewer-link"
              href={viewer.pageUrl}
              target="_blank"
              rel="noreferrer"
              onClick={(event) => event.stopPropagation()}
            >
              在抖音打开
            </a>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

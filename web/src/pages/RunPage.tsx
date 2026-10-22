import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Check,
  Chrome,
  Circle,
  ExternalLink,
  Heart,
  Info,
  MessageCircle,
  Pause,
  Play,
  RefreshCw,
  Reply,
  ScanLine,
  Send,
  SlidersHorizontal,
  Square,
  UserMinus,
  UserPlus,
  Users,
  WandSparkles,
} from "lucide-react";
import { api } from "../api";
import type { AgentEvent, Fan, FansResponse, RunProgress, Settings, Status, Video } from "../types";
import { EmptyState, QuotaBar, TypeBadge, Toggle } from "../components/ui";
import { ChatDock } from "../components/ChatDock";

interface RunPageProps {
  status: Status | null;
  settings: Settings | null;
  events: AgentEvent[];
  progress: RunProgress | null;
  busy: string;
  onRun: () => void;
  onStop: () => void;
  onOpenBrowser: () => void;
  onToggleDryRun: (next: boolean) => void;
  onCheckReplier: () => void;
  onGoSettings: () => void;
}

function fmtTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function Cover({ video }: { video: Video }) {
  return (
    <div className="video-cover">
      <div className="cover-fallback">
        <Play size={24} fill="currentColor" />
      </div>
      {video.cover ? (
        <div
          className="cover-backdrop"
          style={{ backgroundImage: `url(${video.cover})` }}
          aria-hidden="true"
        />
      ) : null}
      {video.cover ? (
        <img
          src={video.cover}
          alt=""
          referrerPolicy="no-referrer"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
        />
      ) : null}
      <div className="cover-shade" />
      <div className="cover-meta">
        <span className="cover-author">@{video.author || "未知作者"}</span>
        <span className="mono">{video.aweme_id}</span>
      </div>
    </div>
  );
}

function VideoPanel({ video }: { video: Video | null }) {
  if (!video) {
    return (
      <section className="panel video-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">CURRENT</span>
            <h2>当前视频</h2>
          </div>
        </div>
        <EmptyState icon={ScanLine} title="还没有读到视频" />
      </section>
    );
  }
  const link = video.url?.startsWith("http") ? video.url : "";
  const comments = (video.comments || []).slice(0, 2);
  return (
    <section className="panel video-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">CURRENT</span>
          <h2>当前视频</h2>
        </div>
        {link ? (
          <a className="text-link" href={link} target="_blank" rel="noreferrer">
            打开抖音 <ExternalLink size={13} />
          </a>
        ) : null}
      </div>
      <Cover video={video} />
      <div className="video-copy">
        <h3>{video.title || video.description || "（无标题）"}</h3>
        {video.description && video.description !== video.title ? (
          <p className="video-desc">{video.description}</p>
        ) : null}
        <div className="video-stats">
          <span>
            <Heart size={13} /> {video.stats?.like || "—"}
          </span>
          <span>
            <MessageCircle size={13} /> {video.stats?.comment || "—"}
          </span>
          <span className="video-id mono">{video.aweme_id}</span>
        </div>
        {comments.length ? (
          <div className="comment-peek">
            <span className="eyebrow">COMMENTS</span>
            {comments.map((c, i) => (
              <p key={i}>{c}</p>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function DecisionPanel({ status, settings }: { status: Status | null; settings: Settings | null }) {
  const decision = status?.last_decision;
  if (!decision) {
    return (
      <section className="panel decision-panel">
        <div className="panel-head">
          <div>
            <span className="eyebrow">DECISION</span>
            <h2>AI 判断</h2>
          </div>
        </div>
        <div className="decision-empty">
          <EmptyState icon={WandSparkles} title="还没有判断" />
          <div className="decision-hints">
            <span>文案 + 封面</span>
            <span>评论互动</span>
            <span>额度与白名单</span>
          </div>
        </div>
      </section>
    );
  }
  const dry = Boolean(settings?.dry_run);
  return (
    <section className="panel decision-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">DECISION</span>
          <h2>AI 判断</h2>
        </div>
        <span className={`mini-chip ${dry ? "chip-amber" : "chip-green"}`}>
          {dry ? "预演，不发出" : "自动执行"}
        </span>
      </div>
      <div className="decision-actions">
        {decision.like ? <TypeBadge kind="like" /> : null}
        {decision.comment ? <TypeBadge kind="comment" /> : null}
        {decision.dm ? <TypeBadge kind="dm" /> : null}
        {!decision.like && !decision.comment && !decision.dm ? (
          <span className="mini-chip chip-muted">跳过</span>
        ) : null}
      </div>
      <p className="decision-reason">{decision.reason || "（没有写理由）"}</p>
      {decision.comment ? (
        <div className="draft">
          <span className="draft-label">评论草稿</span>
          <p>{decision.comment}</p>
        </div>
      ) : null}
      {decision.dm ? (
        <div className="draft">
          <span className="draft-label">私信草稿</span>
          <p>{decision.dm}</p>
        </div>
      ) : null}
      <div className="decision-foot">
        <span>
          置信度 <b className="mono">{Math.round((decision.confidence || 0) * 100)}%</b>
        </span>
        {decision.usage?.total_tokens ? (
          <span>
            tokens <b className="mono">{decision.usage.total_tokens}</b>
          </span>
        ) : null}
      </div>
    </section>
  );
}

function QuotaPanel({ status, settings }: { status: Status | null; settings: Settings | null }) {
  const today = status?.today || { like: 0, comment: 0, dm: 0, publish: 0 };
  return (
    <section className="panel quota-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">TODAY</span>
          <h2>今日额度</h2>
        </div>
      </div>
      <div className="quota-list">
        <QuotaBar label="点赞" used={today.like} max={settings?.likes_per_day ?? 0} tone="rose" />
        <QuotaBar label="评论" used={today.comment} max={settings?.comments_per_day ?? 0} tone="cyan" />
        <QuotaBar label="主动私信" used={today.dm} max={settings?.dms_per_day ?? 0} tone="violet" />
      </div>
    </section>
  );
}

function eventIcon(ev: AgentEvent) {
  if (ev.level === "error") return <AlertTriangle size={14} />;
  if (ev.kind === "action_result") return <Check size={14} />;
  if (ev.kind === "action") return <Activity size={14} />;
  if (ev.kind === "reply_cycle" || ev.kind === "reply_status") return <Reply size={14} />;
  if (ev.kind === "reply") return <Reply size={14} />;
  if (ev.kind === "video") return <Play size={14} />;
  if (ev.kind === "decision") return <WandSparkles size={14} />;
  if (ev.kind === "browser") return <Chrome size={14} />;
  if (ev.level === "warn") return <Info size={14} />;
  return <Circle size={8} fill="currentColor" />;
}


function AutoReplyPanel({
  status,
  busy,
  onCheck,
  onGoSettings,
}: {
  status: Status | null;
  busy: string;
  onCheck: () => void;
  onGoSettings: () => void;
}) {
  const replier = status?.replier;
  const enabled = Boolean(replier?.enabled);
  const running = Boolean(replier?.running);
  const last = replier?.last_check ? fmtTime(replier.last_check) : "还没检查";
  return (
    <section className="panel auto-reply-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">AUTOREPLY</span>
          <h2>自动回复</h2>
        </div>
        <span className={`live-inline ${running ? "on" : ""}`}>
          <i />
          {running ? "常驻运行" : enabled ? "已启用" : "已关闭"}
        </span>
      </div>
      <div className="reply-summary">
        <div>
          <span>最近检查</span>
          <strong className="mono">{last}</strong>
        </div>
        <div>
          <span>今日回复</span>
          <strong>{replier?.today.dm ?? 0}<em> 条</em></strong>
        </div>
        <div>
          <span>今日评论</span>
          <strong>{replier?.today.comment ?? 0}<em> 条</em></strong>
        </div>
      </div>
      {replier?.last_error ? <p className="reply-error">{replier.last_error}</p> : null}
      <div className="reply-actions">
        {!enabled ? (
          <button className="btn btn-secondary" onClick={onGoSettings} type="button">去设置开启</button>
        ) : (
          <button
            className="btn btn-secondary"
            onClick={onCheck}
            disabled={busy === "replier" || Boolean(replier?.busy)}
            type="button"
          >
            <RefreshCw size={14} /> {replier?.busy ? "检查中…" : "立即检查一次"}
          </button>
        )}
      </div>
    </section>
  );
}

function ActivityPanel({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const visible = events.filter((e) => !["hello", "ping"].includes(e.kind)).slice(-28).reverse();
  return (
    <section className="panel activity-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">LIVE</span>
          <h2>实时动作</h2>
        </div>
        <span className={`live-inline ${running ? "on" : "idle"}`}>
          <i />
          {running ? "运行中" : "待命"}
        </span>
      </div>
      <div className="activity-list" role="log" aria-live="polite">
        {visible.length === 0 ? (
          <EmptyState icon={Activity} title="还没有动作" />
        ) : (
          visible.map((ev) => (
            <div key={`${ev.id}-${ev.ts}`} className={`event-row ev-${ev.level}`}>
              <span className="event-time mono">{fmtTime(ev.ts)}</span>
              <span className="event-icon">{eventIcon(ev)}</span>
              <div className="event-copy">
                <span>{ev.message}</span>
                {ev.kind === "action_result" && ev.data?.detail ? (
                  <small>{String(ev.data.detail)}</small>
                ) : null}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function FansPanel({
  loggedIn,
  onOpenBrowser,
}: {
  loggedIn: boolean;
  onOpenBrowser: () => void;
}) {
  const [data, setData] = useState<FansResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState("");
  const [unfollowArm, setUnfollowArm] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!unfollowArm) return;
    const timer = window.setTimeout(() => setUnfollowArm(""), 3000);
    return () => window.clearTimeout(timer);
  }, [unfollowArm]);

  const load = useCallback(async () => {
    if (!loggedIn) return;
    setLoading(true);
    setError("");
    try {
      setData(await api.fans());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [loggedIn]);

  useEffect(() => {
    if (loggedIn) void load();
  }, [loggedIn, load]);

  const follow = async (fan: Fan) => {
    const key = fan.profile_url || fan.name;
    if (pending) return;
    setPending(key);
    setNotice("");
    try {
      const res = await api.followFan(fan.name, fan.profile_url);
      if (["sent", "already", "uncertain"].includes(res.status)) {
        setData((prev) =>
          prev
            ? {
                ...prev,
                items: prev.items.map((item) =>
                  (item.profile_url || item.name) === key
                    ? { ...item, following: true, status: "相互关注" }
                    : item
                ),
              }
            : prev
        );
      } else {
        setNotice(res.detail || `回关 ${fan.name} 失败`);
      }
    } catch (e) {
      setNotice(`回关失败：${(e as Error).message}`);
    } finally {
      setPending("");
    }
  };

  const unfollow = async (fan: Fan) => {
    const key = fan.profile_url || fan.name;
    if (pending) return;
    setUnfollowArm("");
    setPending(key);
    setNotice("");
    try {
      const res = await api.unfollowFan(fan.name, fan.profile_url);
      if (["sent", "uncertain", "skipped"].includes(res.status)) {
        setData((prev) =>
          prev
            ? {
                ...prev,
                items: prev.items.map((item) =>
                  (item.profile_url || item.name) === key
                    ? { ...item, following: false, status: "回关" }
                    : item
                ),
              }
            : prev
        );
      } else {
        setNotice(res.detail || `取关 ${fan.name} 失败`);
      }
    } catch (e) {
      setNotice(`取关失败：${(e as Error).message}`);
    } finally {
      setPending("");
    }
  };

  const total = data?.total ?? data?.items.length ?? 0;

  return (
    <section className="panel fans-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">FANS</span>
          <h2>粉丝回关</h2>
        </div>
        <div className="fans-head-actions">
          {loggedIn ? <span className="mini-chip chip-muted">{total} 位粉丝</span> : null}
          <button
            className="btn btn-ghost fans-refresh"
            onClick={() => void load()}
            disabled={!loggedIn || loading}
            type="button"
          >
            <RefreshCw size={14} className={loading ? "spin" : ""} /> {loading ? "读取中…" : "刷新"}
          </button>
        </div>
      </div>

      {!loggedIn ? (
        <div className="fans-empty">
          <button className="btn btn-secondary" onClick={onOpenBrowser} type="button">
            <Chrome size={14} /> 打开浏览器
          </button>
        </div>
      ) : null}

      {loggedIn && error ? (
        <div className="fans-empty">
          <p className="fans-error">{error}</p>
          <button className="btn btn-ghost" onClick={() => void load()} type="button">重试</button>
        </div>
      ) : null}

      {loggedIn && loading && !data ? <div className="fans-placeholder">正在读取粉丝列表…</div> : null}

      {loggedIn && !loading && data && data.items.length === 0 ? (
        <EmptyState icon={Users} title="还没有读到粉丝" />
      ) : null}

      {loggedIn && data && data.items.length ? (
        <div className="fans-strip">
          {data.items.map((fan) => {
            const key = fan.profile_url || fan.name;
            const isPending = pending === key;
            return (
              <article className="fan-card" key={key}>
                <a
                  className="fan-avatar"
                  href={fan.profile_url || undefined}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={fan.name}
                >
                  <span>{fan.name.slice(0, 1)}</span>
                  {fan.avatar ? (
                    <img
                      src={fan.avatar}
                      alt=""
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      onError={(e) => {
                        e.currentTarget.style.display = "none";
                      }}
                    />
                  ) : null}
                </a>
                <div className="fan-meta">
                  <a
                    className="fan-name"
                    href={fan.profile_url || undefined}
                    target="_blank"
                    rel="noreferrer"
                    title={fan.name}
                  >
                    {fan.name}
                  </a>
                  <small title={fan.bio}>{fan.bio || "抖音粉丝"}</small>
                </div>
                {fan.following ? (
                  <button
                    className={`fan-follow done ${unfollowArm === key ? "confirm" : ""}`}
                    onClick={() => {
                      if (unfollowArm === key) {
                        void unfollow(fan);
                      } else {
                        setUnfollowArm(key);
                      }
                    }}
                    disabled={Boolean(pending)}
                    title="点击两次取关，防误触"
                    type="button"
                  >
                    {unfollowArm === key ? (
                      <><UserMinus size={12} /> 确认取关</>
                    ) : fan.status.includes("相互") ? (
                      "已互关"
                    ) : (
                      "已关注"
                    )}
                  </button>
                ) : (
                  <button
                    className="fan-follow"
                    onClick={() => void follow(fan)}
                    disabled={Boolean(pending)}
                    type="button"
                  >
                    <UserPlus size={12} /> {isPending ? "回关中…" : "回关"}
                  </button>
                )}
              </article>
            );
          })}
        </div>
      ) : null}

      {notice ? <p className="fans-notice">{notice}</p> : null}
    </section>
  );
}

export function RunPage({
  status,
  settings,
  events,
  progress,
  busy,
  onRun,
  onStop,
  onOpenBrowser,
  onToggleDryRun,
  onCheckReplier,
  onGoSettings,
}: RunPageProps) {
  const browser = status?.browser;
  const running = Boolean(status?.running);
  const logged = Boolean(browser?.started && browser?.logged_in);
  const hasKey = Boolean(status?.has_api_key);
  const canRun = hasKey && logged && !running && !busy;
  const dry = Boolean(settings?.dry_run);
  const progressPct = progress && progress.total > 0 ? Math.min(100, Math.round((progress.index / progress.total) * 100)) : 0;

  return (
    <div className="page">
      {!hasKey || !logged ? (
        <div className="setup-strip">
          {!hasKey ? (
            <div className="setup-item">
              <div className="setup-icon"><WandSparkles size={18} /></div>
              <div>
                <strong>还没有模型密钥</strong>
              </div>
              <button className="btn btn-secondary" onClick={onGoSettings} type="button">
                去设置
              </button>
            </div>
          ) : null}
          {!logged ? (
            <div className="setup-item">
              <div className="setup-icon"><Chrome size={18} /></div>
              <div>
                <strong>浏览器还没有登录</strong>
              </div>
              <button className="btn btn-secondary" onClick={onOpenBrowser} disabled={busy === "browser"} type="button">
                {busy === "browser" ? "打开中…" : browser?.started ? "打开登录页" : "打开浏览器"}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      <section className="run-control panel">
        <div className="run-state-block">
          <span className="eyebrow">RUN STATE</span>
          <div className="run-state">
            <i className={`state-dot ${running ? "on" : "standby"}`} />
            <strong>{running ? "正在刷" : status?.stop_requested ? "正在停止" : "待命"}</strong>
            {dry ? <span className="mini-chip chip-amber">预演模式</span> : null}
          </div>
          {running && progress ? (
            <div className="run-progress">
              <div className="run-progress-head">
                <span>第 {progress.index} / {progress.total} 条</span>
                <span className="mono">{progressPct}%</span>
              </div>
              <div className="progress-track"><i style={{ width: `${progressPct}%` }} /></div>
            </div>
          ) : null}
        </div>
        <div className="run-actions">
          <div className="run-actions-main">
            {running ? (
              <button className="btn btn-danger" onClick={onStop} disabled={status?.stop_requested} type="button">
                <Square size={15} /> {status?.stop_requested ? "正在停止…" : "停止"}
              </button>
            ) : (
              <button className="btn btn-primary" onClick={onRun} disabled={!canRun} type="button">
                <Play size={15} fill="currentColor" /> 开始一轮
              </button>
            )}
            <button className="btn btn-secondary" onClick={onOpenBrowser} disabled={busy === "browser"} type="button">
              <Chrome size={15} /> {logged ? "打开浏览器" : "登录浏览器"}
            </button>
          </div>
          <div className="dry-row">
            <strong>预演模式</strong>
            <Toggle checked={dry} onChange={onToggleDryRun} disabled={busy === "settings"} label="预演模式" />
          </div>
        </div>
      </section>

      <div className="run-grid">
        <div className="run-main">
          <VideoPanel video={status?.current_video || null} />
          <DecisionPanel status={status} settings={settings} />
        </div>
        <aside className="run-side">
          <AutoReplyPanel status={status} busy={busy} onCheck={onCheckReplier} onGoSettings={onGoSettings} />
          <QuotaPanel status={status} settings={settings} />
          <ActivityPanel events={events} running={running} />
        </aside>
      </div>

      <FansPanel loggedIn={logged} onOpenBrowser={onOpenBrowser} />
      <ChatDock />
    </div>
  );
}

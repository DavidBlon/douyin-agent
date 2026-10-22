import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ExternalLink,
  ListChecks,
  Shield,
  SlidersHorizontal,
  Square,
  Wifi,
  WifiOff,
} from "lucide-react";
import { actionKindLabel, api, statusLabel } from "./api";
import type {
  Action,
  ActionsResponse,
  AgentEvent,
  RunProgress,
  Settings,
  Status,
  Tab,
  ToastMessage,
  Video,
} from "./types";
import { Drawer, StatusBadge, Toast, TypeBadge } from "./components/ui";
import { RunPage } from "./pages/RunPage";
import { ActionsPage } from "./pages/ActionsPage";
import { SettingsPage } from "./pages/SettingsPage";

const navItems: { tab: Tab; label: string; icon: typeof Activity }[] = [
  { tab: "run", label: "运行台", icon: Activity },
  { tab: "actions", label: "动作记录", icon: ListChecks },
  { tab: "settings", label: "设置", icon: SlidersHorizontal },
];

const pageMeta: Record<Tab, string> = {
  run: "运行台",
  actions: "动作记录",
  settings: "设置",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("run");
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [actions, setActions] = useState<ActionsResponse | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [actionPage, setActionPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<Action | null>(null);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [busy, setBusy] = useState("");
  const [live, setLive] = useState(false);
  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState("");
  const [loadingActions, setLoadingActions] = useState(false);
  const [toast, setToast] = useState<ToastMessage | null>(null);

  const toastTimer = useRef<number | undefined>(undefined);
  const statusTimer = useRef<number | undefined>(undefined);
  const actionTimer = useRef<number | undefined>(undefined);
  const actionsReq = useRef(0);
  const refreshRef = useRef<{ status: () => void; actions: () => void }>({ status: () => {}, actions: () => {} });

  const notify = useCallback((text: string, tone: ToastMessage["tone"] = "ok") => {
    setToast({ text, tone });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 3800);
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const next = await api.status();
      setStatus(next);
      setBootError("");
      setReady(true);
    } catch (e) {
      setBootError((e as Error).message);
    }
  }, []);

  const refreshSettings = useCallback(async () => {
    try {
      const next = await api.settings();
      setSettings(next);
    } catch (e) {
      notify(`读取设置失败：${(e as Error).message}`, "error");
    }
  }, [notify]);

  const loadActions = useCallback(async () => {
    const req = ++actionsReq.current;
    setLoadingActions(true);
    try {
      const next = await api.actions(actionPage, 20, "", statusFilter);
      if (req === actionsReq.current) setActions(next);
    } catch (e) {
      if (req === actionsReq.current) notify(`读取动作失败：${(e as Error).message}`, "error");
    } finally {
      if (req === actionsReq.current) setLoadingActions(false);
    }
  }, [actionPage, statusFilter, notify]);

  refreshRef.current = {
    status: () => { void refreshStatus(); },
    actions: () => { if (tab === "actions") void loadActions(); },
  };

  const scheduleRefresh = useCallback(() => {
    window.clearTimeout(statusTimer.current);
    statusTimer.current = window.setTimeout(() => { refreshRef.current.status(); }, 350);
    window.clearTimeout(actionTimer.current);
    actionTimer.current = window.setTimeout(() => { refreshRef.current.actions(); }, 500);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [st, se, ev] = await Promise.all([api.status(), api.settings(), api.eventsRecent(80)]);
        if (cancelled) return;
        setStatus(st);
        setSettings(se);
        setEvents(ev.items || []);
        setReady(true);
      } catch (e) {
        if (!cancelled) setBootError((e as Error).message);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const es = new EventSource("/api/events");
    es.onopen = () => {
      setLive(true);
      refreshRef.current.status();
    };
    es.onerror = () => setLive(false);
    es.onmessage = (event) => {
      try {
        const ev = JSON.parse(event.data) as AgentEvent;
        if (["hello", "ping"].includes(ev.kind)) return;
        setEvents((prev) => [...prev, ev].slice(-160));
        if (ev.kind === "run_started") setProgress(null);
        if (ev.kind === "video" && ev.data?.video) {
          const video = ev.data.video as Video;
          const index = Number(ev.data.index) || 0;
          const total = Number(ev.data.total) || 0;
          setProgress({ index, total });
          setStatus((prev) => (prev ? { ...prev, current_video: video } : prev));
        }
        if (ev.kind === "decision") {
          setStatus((prev) => (prev ? { ...prev, last_decision: ev.data as unknown as Status["last_decision"] } : prev));
        }
        if (ev.level === "error") notify(ev.message, "error");
        if (["run_started", "run_done", "run_state", "action", "action_result", "browser", "settings", "error"].includes(ev.kind)) {
          scheduleRefresh();
        }
      } catch {
        /* ignore malformed event */
      }
    };
    return () => es.close();
  }, [notify, scheduleRefresh]);

  useEffect(() => {
    if (!ready || tab !== "actions") return;
    void loadActions();
  }, [ready, tab, actionPage, statusFilter, loadActions]);

  useEffect(() => {
    if (!status?.browser.started || status.browser.logged_in) return;
    const timer = window.setInterval(() => { refreshRef.current.status(); }, 3000);
    return () => window.clearInterval(timer);
  }, [status?.browser.started, status?.browser.logged_in]);

  const handleRun = useCallback(async () => {
    if (!status?.has_api_key) {
      notify("先配置 DeepSeek API Key，再去设置页。", "warn");
      setTab("settings");
      return;
    }
    if (!status?.browser.started || !status?.browser.logged_in) {
      notify("浏览器还没登录抖音。先打开登录浏览器扫码。", "warn");
      return;
    }
    setBusy("run");
    try {
      await api.run();
      notify("已开始一轮，动作会实时出现在下面。", "ok");
      window.setTimeout(() => { void refreshStatus(); }, 500);
    } catch (e) {
      notify(`启动失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }, [status?.has_api_key, status?.browser.started, status?.browser.logged_in, notify, refreshStatus]);

  const handleStop = useCallback(async () => {
    setBusy("stop");
    try {
      await api.stop();
      notify("已发出停止请求，当前动作完成后停。", "warn");
      window.setTimeout(() => { void refreshStatus(); }, 400);
    } catch (e) {
      notify(`停止失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }, [notify, refreshStatus]);

  const handleCheckReplier = useCallback(async () => {
    setBusy("replier");
    try {
      const result = await api.checkReplier();
      if (result.skipped) {
        notify("自动回复这轮跳过：" + result.skipped, "warn");
      } else {
        notify(`检查完成：私信回复 ${result.dm ?? 0} 条，评论回复 ${result.comment ?? 0} 条。`, "ok");
      }
      window.setTimeout(() => { void refreshStatus(); }, 500);
    } catch (e) {
      notify(`检查失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }, [notify, refreshStatus]);

  const handleOpenBrowser = useCallback(async () => {
    setBusy("browser");
    try {
      const res = await api.openBrowser();
      await refreshStatus();
      if (!res.started) {
        notify(res.last_error || "浏览器进程没有保持运行，查看 data/server.log。", "error");
        return;
      }
      notify(
        res.logged_in
          ? "浏览器已打开，登录态有效。"
          : "浏览器已打开。请查看任务栏或其他显示器上的 Chrome 窗口扫码。",
        "ok",
      );
    } catch (e) {
      notify(`打开浏览器失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }, [notify, refreshStatus]);

  const handleSnapshot = useCallback(async () => {
    setBusy("snapshot");
    try {
      const res = await api.snapshot();
      notify("DOM 快照已保存，可在 data/debug 下查看。", "ok");
      console.info("snapshot", res);
    } catch (e) {
      notify(`保存快照失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }, [notify]);

  const handleHideBrowser = useCallback(async () => {
    setBusy("browser");
    try {
      await api.hideBrowser();
      await refreshStatus();
      notify("浏览器已隐藏，后台任务继续运行", "ok");
    } catch (e) {
      notify(`隐藏失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }, [notify, refreshStatus]);

  const handleCloseBrowser = useCallback(async () => {
    setBusy("browser");
    try {
      await api.closeBrowser();
      notify("浏览器已关闭", "warn");
      await refreshStatus();
    } catch (e) {
      notify(`关闭失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }, [notify, refreshStatus]);

  const handleToggleDryRun = useCallback(async (next: boolean) => {
    if (!settings) return;
    setBusy("settings");
    try {
      const saved = await api.saveSettings({ ...settings, dry_run: next });
      setSettings(saved);
      setStatus((prev) => (prev ? { ...prev, settings: saved } : prev));
      notify(next ? "已开启预演模式" : "已关闭预演，动作会真实发送", next ? "ok" : "warn");
    } catch (e) {
      notify(`切换失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }, [settings, notify]);

  const handleSaved = useCallback(async (next: Settings) => {
    setSettings(next);
    await refreshStatus();
  }, [refreshStatus]);

  const changeStatus = (next: string) => {
    setStatusFilter(next);
    setActionPage(1);
  };

  if (bootError) {
    return (
      <div className="boot-screen">
        <div className="boot-card">
          <div className="brand-mark logo large"><img src="/logo.png" alt="抖音智能体" /></div>
          <h1>连不上本地后端</h1>
          <p>{bootError}</p>
          <code>uv run uvicorn app.main:app --port 8787</code>
          <button className="btn btn-primary" onClick={() => { setBootError(""); void refreshStatus(); }}>重试</button>
        </div>
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="boot-screen">
        <div className="boot-loading"><span className="state-dot on" /> 正在连接本机智能体…</div>
      </div>
    );
  }

  const running = Boolean(status?.running);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark logo"><img src="/logo.png" alt="抖音智能体" /></div>
          <div>
            <strong>抖音智能体</strong>
            <small>LOCAL · v0.1</small>
          </div>
        </div>

        <nav className="nav" aria-label="主导航">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.tab}
                className={`nav-item ${tab === item.tab ? "active" : ""}`}
                onClick={() => setTab(item.tab)}
                type="button"
              >
                <Icon size={17} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-foot">
          <div className="foot-line"><Shield size={14} /> 单账号 · 只在本机</div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-title">
            <strong>{pageMeta[tab]}</strong>
          </div>
          <div className="topbar-right">
            <span className={`live-pill ${live ? "live" : ""}`}>
              {live ? <Wifi size={13} /> : <WifiOff size={13} />}
              {live ? "实时连接" : "重连中"}
            </span>
            <span className={`mini-chip ${status?.browser.logged_in ? "chip-green" : "chip-muted"}`}>
              {status?.browser.logged_in ? "账号已登录" : "账号未登录"}
            </span>
            <span className="mini-chip chip-blue">{settings?.model || "deepseek-flash"}</span>
            <span className={`mini-chip ${settings?.dry_run ? "chip-amber" : "chip-red"}`}>
              {settings?.dry_run ? "预演" : "真实发送"}
            </span>
            {running ? (
              <button className="btn btn-danger top-stop" onClick={handleStop} disabled={busy === "stop"} type="button">
                <Square size={13} /> 停止
              </button>
            ) : null}
          </div>
        </header>

        <main className="content">
          {tab === "run" ? (
            <RunPage
              status={status}
              settings={settings}
              events={events}
              progress={progress}
              busy={busy}
              onRun={handleRun}
              onStop={handleStop}
              onOpenBrowser={handleOpenBrowser}
              onToggleDryRun={handleToggleDryRun}
              onCheckReplier={handleCheckReplier}
              onGoSettings={() => setTab("settings")}
            />
          ) : null}
          {tab === "actions" ? (
            <ActionsPage
              data={actions}
              page={actionPage}
              statusFilter={statusFilter}
              loading={loadingActions}
              onStatus={changeStatus}
              onPage={setActionPage}
              onSelect={setSelected}
            />
          ) : null}
          {tab === "settings" ? (
            <SettingsPage
              settings={settings}
              status={status}
              busy={busy}
              onSaved={handleSaved}
              onRefreshStatus={() => { void refreshStatus(); }}
              notify={notify}
              onOpenBrowser={handleOpenBrowser}
              onSnapshot={handleSnapshot}
              onHideBrowser={handleHideBrowser}
              onCloseBrowser={handleCloseBrowser}
            />
          ) : null}
        </main>
      </div>

      <Drawer
        open={Boolean(selected)}
        title={selected ? `#${selected.id} · ${actionKindLabel(selected.kind)}` : ""}
        subtitle={selected ? `${selected.created_at.replace("T", " ")} · ${statusLabel(selected.status)}` : ""}
        onClose={() => setSelected(null)}
      >
        {selected ? (
          <div className="drawer-content">
            <div className="drawer-badges">
              <TypeBadge kind={selected.kind} />
              <StatusBadge status={selected.status} />
              {selected.dry_run ? <span className="mini-chip chip-amber">预演</span> : null}
            </div>
            <dl className="detail-list">
              <div><dt>对象</dt><dd>{selected.target_name || selected.video_id || "—"}</dd></div>
              <div><dt>视频 ID</dt><dd className="mono">{selected.video_id || "—"}</dd></div>
              {selected.target_url ? (
                <div>
                  <dt>主页</dt>
                  <dd><a href={selected.target_url} target="_blank" rel="noreferrer">打开主页 <ExternalLink size={12} /></a></dd>
                </div>
              ) : null}
              <div><dt>创建</dt><dd>{selected.created_at.replace("T", " ")}</dd></div>
              <div><dt>执行</dt><dd>{selected.executed_at ? selected.executed_at.replace("T", " ") : "—"}</dd></div>
            </dl>
            {selected.content ? (
              <div className="drawer-quote">
                <span className="eyebrow">{selected.kind === "comment" ? "COMMENT" : selected.kind === "dm" ? "MESSAGE" : "CONTENT"}</span>
                <p>{selected.content}</p>
              </div>
            ) : null}
            {selected.policy ? <div className="drawer-note"><strong>策略</strong><p>{selected.policy}</p></div> : null}
            {selected.result ? <div className="drawer-note"><strong>结果</strong><p>{selected.result}</p></div> : null}
            {selected.screenshot ? (
              <div className="drawer-shot-wrap">
                <span className="eyebrow">SCREENSHOT</span>
                <img className="drawer-shot" src={selected.screenshot} alt="动作截图" />
              </div>
            ) : null}
            {/^\d+$/.test(selected.video_id) ? (
              <a className="btn btn-secondary drawer-link" href={`https://www.douyin.com/video/${selected.video_id}`} target="_blank" rel="noreferrer">
                打开这条视频 <ExternalLink size={14} />
              </a>
            ) : null}
          </div>
        ) : null}
      </Drawer>

      <Toast message={toast} />
    </div>
  );
}

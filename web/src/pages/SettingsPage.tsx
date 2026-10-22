import { useEffect, useMemo, useState } from "react";
import {
  Camera,
  Check,
  Chrome,
  KeyRound,
  Reply,
  Save,
  Share2,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { api } from "../api";
import type { Settings, Status, ToastMessage } from "../types";
import { Toggle, Select } from "../components/ui";

const replyScopeOptions = [
  { value: "mutual", label: "仅互关 + 白名单" },
  { value: "existing", label: "所有已有 1 对 1 会话" },
  { value: "allowlist", label: "仅白名单" },
];

interface SettingsPageProps {
  settings: Settings | null;
  status: Status | null;
  busy: string;
  onSaved: (settings: Settings) => void;
  onRefreshStatus: () => void;
  notify: (text: string, tone?: ToastMessage["tone"]) => void;
  onOpenBrowser: () => void;
  onSnapshot: () => void;
  onHideBrowser: () => void;
  onCloseBrowser: () => void;
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  suffix,
  wide = false,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  min: number;
  max: number;
  suffix?: string;
  wide?: boolean;
}) {
  return (
    <label className={`field ${wide ? "field-wide" : ""}`}>
      <span>{label}</span>
      <div className="number-wrap">
        <input
          className="input"
          type="number"
          min={min}
          max={max}
          value={Number.isFinite(value) ? value : 0}
          onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || 0)))}
        />
        {suffix ? <em>{suffix}</em> : null}
      </div>
    </label>
  );
}

function SwitchRow({
  title,
  checked,
  onChange,
}: {
  title: string;
  desc?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="switch-row">
      <strong>{title}</strong>
      <Toggle checked={checked} onChange={onChange} label={title} />
    </div>
  );
}

export function SettingsPage({
  settings,
  status,
  busy,
  onSaved,
  onRefreshStatus,
  notify,
  onOpenBrowser,
  onSnapshot,
  onHideBrowser,
  onCloseBrowser,
}: SettingsPageProps) {
  const [form, setForm] = useState<Settings | null>(settings);
  const [keyInput, setKeyInput] = useState("");
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setForm(settings);
    setDirty(false);
  }, [settings]);

  const hasKey = Boolean(status?.has_api_key || settings?.has_api_key);
  const browser = status?.browser;
  const allowlistText = useMemo(() => (form?.dm_allowlist || []).join("\n"), [form?.dm_allowlist]);
  const blocklistText = useMemo(() => (form?.comment_blocklist || []).join("\n"), [form?.comment_blocklist]);

  if (!form) {
    return (
      <div className="page">
      </div>
    );
  }

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
    setDirty(true);
  }

  async function save() {
    if (!form) return;
    setSaving(true);
    try {
      const next = await api.saveSettings({ ...form, has_api_key: undefined });
      onSaved(next);
      setDirty(false);
      notify("设置已保存", "ok");
    } catch (e) {
      notify(`保存失败：${(e as Error).message}`, "error");
    } finally {
      setSaving(false);
    }
  }

  async function saveKey() {
    if (!keyInput.trim()) return;
    try {
      await api.saveKey(keyInput.trim());
      setKeyInput("");
      onRefreshStatus();
      notify("API Key 已保存到本机", "ok");
    } catch (e) {
      notify(`保存失败：${(e as Error).message}`, "error");
    }
  }

  async function clearKey() {
    try {
      await api.deleteKey();
      onRefreshStatus();
      notify("API Key 已清除", "warn");
    } catch (e) {
      notify(`清除失败：${(e as Error).message}`, "error");
    }
  }

  async function testModel() {
    setTestResult(null);
    try {
      const res = await api.testLLM();
      setTestResult({ ok: Boolean(res.ok), text: res.ok ? "模型连通正常" : res.error || "调用失败" });
    } catch (e) {
      setTestResult({ ok: false, text: (e as Error).message });
    }
  }

  return (
    <div className="page settings-page">
      <section className="settings-card">
        <header className="settings-card-head">
          <div className="settings-title"><Chrome size={17} /> 账号与浏览器</div>
          <span className={`status-line ${browser?.logged_in ? "ok" : ""}`}>
            <i /> {browser?.logged_in ? "已登录" : browser?.started ? "等待登录" : "未启动"}
          </span>
        </header>
        <div className="browser-row">
          <div>
            <strong>专用 Chrome 配置</strong>
            <code className="path">{browser?.profile || "data/chrome-profile"}</code>
          </div>
          <div className="browser-actions">
            <button className="btn btn-secondary" onClick={onOpenBrowser} disabled={busy === "browser"} type="button">
              <Chrome size={14} /> {browser?.logged_in ? "打开浏览器" : "登录浏览器"}
            </button>
            <button className="btn btn-ghost" onClick={onSnapshot} disabled={!browser?.started || busy === "snapshot"} type="button">
              <Camera size={14} /> {busy === "snapshot" ? "保存中…" : "存 DOM 快照"}
            </button>
            {browser?.started ? (
              <>
                <button className="btn btn-ghost" onClick={onHideBrowser} disabled={busy === "browser"} type="button">隐藏</button>
                <button className="btn btn-ghost" onClick={onCloseBrowser} type="button">关闭</button>
              </>
            ) : null}
          </div>
        </div>
        {!browser?.started && browser?.last_error ? <p className="form-error">{browser.last_error}</p> : null}
        <div className="switch-list">
          <SwitchRow
            title="后台静默运行"
            desc="扫码登录时才显示窗口；登录后离屏启动并隐藏，不闪窗、不占桌面。遇到验证可点“打开浏览器”显示，处理完点“隐藏”。"
            checked={form.silent_browser}
            onChange={(v) => set("silent_browser", v)}
          />
        </div>
      </section>

      <section className="settings-card">
        <header className="settings-card-head">
          <div className="settings-title"><KeyRound size={17} /> 模型</div>
          <span className={`mini-chip ${hasKey ? "chip-green" : "chip-muted"}`}>
            {hasKey ? "Key 已配置" : "未配置"}
          </span>
        </header>
        <div className="key-row">
          <input
            className="input"
            type="password"
            autoComplete="off"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder={hasKey ? "输入新 Key 可替换已保存的" : "sk-cp-…（Coding Plan）或 sk-…（DeepSeek）"}
          />
          <button className="btn btn-secondary" onClick={saveKey} disabled={!keyInput.trim()} type="button">
            保存 Key
          </button>
          {hasKey ? <button className="btn btn-ghost" onClick={clearKey} type="button">清除</button> : null}
        </div>
        <div className="preset-row">
          <span className="eyebrow">快速配置</span>
          <button
            className="btn btn-ghost"
            type="button"
            onClick={() => {
              set("model", "MiniMax-M3.1-Flash");
              set("base_url", "https://api.minimax.cn/v1");
            }}
          >
            MiniMax Coding Plan
          </button>
          <button
            className="btn btn-ghost"
            type="button"
            onClick={() => {
              set("model", "deepseek-flash");
              set("base_url", "https://api.deepseek.com/v1");
            }}
          >
            DeepSeek
          </button>
        </div>
        <p className="form-hint">
          MiniMax Coding Plan：Key 必须是 Coding Plan / Token Plan 页面生成的 <b>sk-cp-</b> 开头订阅 Key，
          普通 <b>sk-api-</b> Key 不走订阅额度；Base URL 用 <b>https://api.minimax.cn/v1</b>，Group ID 留空。
        </p>
        <p className="form-hint">
          DeepSeek：Base URL 用 <b>https://api.deepseek.com/v1</b>，模型 <b>deepseek-flash</b>（当前 V4.1 Flash，支持视觉，可看封面）；
          Key 要用 DeepSeek 开放平台生成的 <b>sk-…</b>。MiniMax 的 <b>sk-cp-</b> Key 不能用于 DeepSeek，
          切换供应商后需要重新保存对应的 Key。
        </p>
        <div className="form-grid two">
          <label className="field">
            <span>模型</span>
            <input className="input" value={form.model} onChange={(e) => set("model", e.target.value)} />
          </label>
          <label className="field">
            <span>Base URL</span>
            <input className="input" value={form.base_url} onChange={(e) => set("base_url", e.target.value)} />
          </label>
        </div>
        <div className="inline-actions">
          <button className="btn btn-ghost" onClick={testModel} type="button" disabled={!hasKey}>
            <Sparkles size={14} /> 测试模型
          </button>
          {testResult ? (
            <span className={`test-result ${testResult.ok ? "ok" : "bad"}`}>
              {testResult.ok ? <Check size={13} /> : <X size={13} />} {testResult.text}
            </span>
          ) : null}
        </div>
      </section>

      <section className="settings-card">
        <header className="settings-card-head">
          <div className="settings-title"><SlidersHorizontal size={17} /> 动作边界</div>
          <span className="mini-chip chip-muted">单账号</span>
        </header>
        <div className="switch-list">
          <SwitchRow
            title="自动点赞"
            desc="只对真正相关的视频点赞，受每日额度限制。"
            checked={form.auto_like}
            onChange={(v) => set("auto_like", v)}
          />
          <SwitchRow
            title="自动评论"
            desc="评论由模型起草，发送前仍会过敏感词、长度、去重和视频级检查。"
            checked={form.auto_comment}
            onChange={(v) => set("auto_comment", v)}
          />
          <SwitchRow
            title="评论时 @ 接收账号"
            desc={`在评论开头 @ ${form.share_target || "兴趣分享接收账号"}；会从抖音 @ 下拉列表里选人，选不到时退化为纯文本 @。`}
            checked={form.comment_mention}
            onChange={(v) => set("comment_mention", v)}
          />
          <SwitchRow
            title="主动私信"
            desc="只对白名单或互关账号；不做陌生人批量私信。"
            checked={form.auto_dm}
            onChange={(v) => set("auto_dm", v)}
          />
          <SwitchRow
            title="允许私信互关账号"
            desc="遇到互关作者时，后台打开作者主页核对「互相关注」，通过后才允许发送。"
            checked={form.dm_allow_mutual}
            onChange={(v) => set("dm_allow_mutual", v)}
          />
          <SwitchRow
            title="预演模式"
            desc="只读取、判断并记录动作，不点击任何发送按钮。建议第一次运行时打开。"
            checked={form.dry_run}
            onChange={(v) => set("dry_run", v)}
          />
        </div>

        <div className="form-grid four">
          <NumberField label="每轮视频数" value={form.max_videos_per_run} min={1} max={30} onChange={(v) => set("max_videos_per_run", v)} suffix="条" />
          <NumberField label="点赞 / 天" value={form.likes_per_day} min={0} max={300} onChange={(v) => set("likes_per_day", v)} suffix="次" />
          <NumberField label="评论 / 天" value={form.comments_per_day} min={0} max={80} onChange={(v) => set("comments_per_day", v)} suffix="条" />
          <NumberField label="主动私信 / 天" value={form.dms_per_day} min={0} max={30} onChange={(v) => set("dms_per_day", v)} suffix="条" />
        </div>
        <div className="form-grid four">
          <NumberField label="动作间隔" value={form.min_gap_sec} min={3} max={300} onChange={(v) => set("min_gap_sec", v)} suffix="秒" />
          <NumberField label="评论上限" value={form.max_comment_chars} min={10} max={300} onChange={(v) => set("max_comment_chars", v)} suffix="字" />
          <NumberField label="私信上限" value={form.max_dm_chars} min={10} max={500} onChange={(v) => set("max_dm_chars", v)} suffix="字" />
          <NumberField label="同一人私信" value={form.dm_per_contact_per_day} min={1} max={5} onChange={(v) => set("dm_per_contact_per_day", v)} suffix="次/天" />
        </div>

        <div className="form-grid two">
          <label className="field">
            <span>私信白名单</span>
            <textarea
              className="textarea"
              rows={5}
              value={allowlistText}
              onChange={(e) => set("dm_allowlist", e.target.value.split("\n").map((x) => x.trim()))}
              placeholder="一行一个昵称或主页链接。留空也可以，互关账号会单独核对。"
            />
          </label>
          <label className="field">
            <span>评论 / 私信受限词</span>
            <textarea
              className="textarea"
              rows={5}
              value={blocklistText}
              onChange={(e) => set("comment_blocklist", e.target.value.split("\n").map((x) => x.trim()))}
            />
          </label>
        </div>

        <label className="field">
          <span>人设与说话方式</span>
          <textarea className="textarea" rows={4} value={form.persona} onChange={(e) => set("persona", e.target.value)} />
        </label>
        <label className="field">
          <span>关注方向</span>
          <input className="input" value={form.topics} onChange={(e) => set("topics", e.target.value)} />
        </label>
      </section>

      <section className="settings-card">
        <header className="settings-card-head">
          <div className="settings-title"><Reply size={17} /> 自动回复</div>
          <span className="mini-chip chip-muted">常驻后台</span>
        </header>
        <div className="switch-list">
          <SwitchRow
            title="启用自动回复"
            desc="后台常驻检查；关掉后立即停止轮询。"
            checked={form.auto_reply}
            onChange={(v) => set("auto_reply", v)}
          />
          <SwitchRow
            title="回复私信"
            desc="只处理 1 对 1 的未读会话，群聊不碰；先看范围设置。"
            checked={form.auto_reply_dms}
            onChange={(v) => set("auto_reply_dms", v)}
          />
          <SwitchRow
            title="回复评论"
            desc="检查评论/回复通知；检测到会提示。发送路径仍需真实通知校准，不会乱发。"
            checked={form.auto_reply_comments}
            onChange={(v) => set("auto_reply_comments", v)}
          />
        </div>

        <div className="form-grid two">
          <div className="field">
            <span>私信回复范围</span>
            <Select
              value={form.dm_reply_scope}
              options={replyScopeOptions}
              onChange={(value) => set("dm_reply_scope", value as Settings["dm_reply_scope"])}
              ariaLabel="私信回复范围"
            />
          </div>
          <NumberField
            label="检查间隔"
            value={form.reply_interval_sec}
            min={20}
            max={600}
            onChange={(v) => set("reply_interval_sec", v)}
            suffix="秒"
          />
        </div>
      </section>

      <section className="settings-card">
        <header className="settings-card-head">
          <div className="settings-title"><Share2 size={17} /> 兴趣分享</div>
          <span className="mini-chip chip-muted">抖音原生分享</span>
        </header>
        <div className="switch-list">
          <SwitchRow
            title="按时间自动分享"
            desc="到点扫描推荐流，先发一段视频介绍，再用原生分享发视频卡片。"
            checked={form.auto_share}
            onChange={(v) => set("auto_share", v)}
          />
        </div>
        <div className="form-grid two">
          <label className="field">
            <span>分享时间点 1</span>
            <input
              className="input"
              type="time"
              value={form.share_time_1}
              onChange={(e) => set("share_time_1", e.target.value)}
            />
          </label>
          <label className="field">
            <span>分享时间点 2</span>
            <input
              className="input"
              type="time"
              value={form.share_time_2}
              onChange={(e) => set("share_time_2", e.target.value)}
            />
          </label>
        </div>
        <div className="form-grid two">
          <NumberField
            label="每次最多分享"
            value={form.share_max_per_run}
            min={1}
            max={5}
            onChange={(v) => set("share_max_per_run", v)}
            suffix="条"
          />
          <label className="field">
            <span>最低匹配分</span>
            <div className="number-wrap">
              <input
                className="input"
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={form.share_min_score}
                onChange={(e) =>
                  set("share_min_score", Math.max(0, Math.min(1, Number(e.target.value) || 0)))
                }
              />
              <em>分</em>
            </div>
          </label>
        </div>
        <label className="field">
          <span>分享接收账号</span>
          <input
            className="input"
            value={form.share_target}
            onChange={(e) => set("share_target", e.target.value)}
            placeholder="昵称或主页链接，例如 Nuyoahs"
          />
        </label>
        <label className="field">
          <span>分享兴趣</span>
          <textarea
            className="textarea"
            rows={3}
            value={form.share_topics}
            onChange={(e) => set("share_topics", e.target.value)}
            placeholder="例如：AI 工具、独立开发、商业案例、城市生活、历史冷知识"
          />
        </label>
      </section>

      {dirty ? (
        <button
          className="btn btn-primary floating-save"
          onClick={save}
          disabled={saving || busy === "settings"}
          type="button"
        >
          <Save size={16} /> {saving ? "保存中…" : "保存设置"}
        </button>
      ) : null}

      <div className="save-bar">
        <span className={dirty ? "dirty" : "saved"}>
          <i /> {dirty ? "有未保存的修改" : "设置已保存"}
        </span>
        <button className="btn btn-primary" onClick={save} disabled={saving || busy === "settings"} type="button">
          <Save size={15} /> {saving ? "保存中…" : "保存设置"}
        </button>
      </div>
    </div>
  );
}

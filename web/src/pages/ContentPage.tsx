import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarDays,
  Check,
  ExternalLink,
  Image as ImageIcon,
  RefreshCw,
  Save,
  Send,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { api, statusLabel } from "../api";
import type { ContentPlan, Settings, ToastMessage } from "../types";
import { EmptyState, Select } from "../components/ui";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function splitTags(value: string): string[] {
  return value
    .split(/[#\s,，、]+/)
    .map((x) => x.trim())
    .filter(Boolean)
    .slice(0, 8);
}

function fmtTime(value: string): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

const coverBgOptions = [
  { value: "", label: "自动选背景" },
  { value: "desk", label: "书桌夜景" },
  { value: "tech", label: "科技网络" },
  { value: "whale", label: "深海鲸鱼" },
  { value: "notes", label: "纸页笔记" },
  { value: "workplace", label: "职场办公" },
  { value: "study", label: "学习阅读" },
  { value: "food", label: "美食米饭" },
  { value: "city", label: "城市夜景" },
  { value: "review", label: "数码测评" },
  { value: "studio", label: "口播录音" },
  { value: "music", label: "音乐耳机" },
];

interface Props {
  settings: Settings | null;
  notify: (text: string, tone?: ToastMessage["tone"]) => void;
}

export function ContentPage({ settings, notify }: Props) {
  const [plans, setPlans] = useState<ContentPlan[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [form, setForm] = useState<ContentPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [brief, setBrief] = useState("");
  const [planDate, setPlanDate] = useState(today());
  const [pillar, setPillar] = useState("");
  const [tagText, setTagText] = useState("");
  const [publishPreview, setPublishPreview] = useState<{ status: string; detail?: string } | null>(null);
  const videoInput = useRef<HTMLInputElement | null>(null);
  const coverInput = useRef<HTMLInputElement | null>(null);

  const pillars = useMemo(() => {
    const raw = (settings?.content_pillars || "").split(/[、,，\n]+/).map((x) => x.trim()).filter(Boolean);
    return [{ value: "", label: "自动判断" }, ...raw.map((x) => ({ value: x, label: x }))];
  }, [settings?.content_pillars]);

  const load = useCallback(async () => {
    try {
      const res = await api.contentPlans();
      const items = res.items || [];
      setPlans(items);
      setSelected((current) => current ?? (items[0]?.id ?? null));
    } catch (e) {
      notify(`读取内容失败：${(e as Error).message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) {
      setForm(null);
      return;
    }
    const found = plans.find((p) => p.id === selected) || null;
    setForm(found ? { ...found, hashtags: [...(found.hashtags || [])] } : null);
    setTagText(found ? (found.hashtags || []).join(" ") : "");
    setPublishPreview(null);
  }, [selected, plans]);

  function replacePlan(next: ContentPlan) {
    setPlans((prev) => prev.map((p) => (p.id === next.id ? next : p)));
    setForm({ ...next, hashtags: [...(next.hashtags || [])] });
    setSelected(next.id);
  }

  function set<K extends keyof ContentPlan>(key: K, value: ContentPlan[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function generate() {
    if (!settings?.has_api_key) {
      notify("先配置 API Key 再生成内容", "warn");
      return;
    }
    setBusy("generate");
    try {
      const res = await api.contentGenerate({
        brief: brief.trim(),
        pillar: pillar || undefined,
        plan_date: planDate || today(),
      });
      setBrief("");
      setSelected(res.plan.id);
      await load();
      replacePlan(res.plan);
      if (res.plan.cover_url) notify("内容计划和封面已生成", "ok");
      else notify("内容计划已生成，封面需要手动重试", "warn");
    } catch (e) {
      notify(`生成失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function save() {
    if (!form) return;
    setBusy("save");
    try {
      const res = await api.contentUpdate(form.id, {
        plan_date: form.plan_date,
        slot: form.slot,
        pillar: form.pillar,
        topic: form.topic,
        angle: form.angle,
        script: form.script,
        caption: form.caption,
        hashtags: splitTags(tagText),
        cover_text: form.cover_text,
        music_note: form.music_note,
        source_notes: form.source_notes,
        publish_at: form.publish_at,
      });
      replacePlan(res.plan);
      notify("内容已保存", "ok");
    } catch (e) {
      notify(`保存失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function regenerateCover() {
    if (!form) return;
    setBusy("cover");
    try {
      const res = await api.contentCover(form.id);
      replacePlan(res.plan);
      notify("封面已重新生成", "ok");
    } catch (e) {
      notify(`生成封面失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function changeBackground(value: string) {
    if (!form) return;
    setForm({ ...form, cover_bg: value });
    setBusy("cover");
    try {
      await api.contentUpdate(form.id, { cover_bg: value });
      const res = await api.contentCover(form.id);
      replacePlan(res.plan);
      notify(value ? "封面背景已切换" : "已改为自动选背景", "ok");
    } catch (e) {
      notify(`切换背景失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function approve() {
    if (!form) return;
    setBusy("approve");
    try {
      const res = await api.contentApprove(form.id);
      replacePlan(res.plan);
      const blocking = res.plan.compliance?.blocking || [];
      if (blocking.length) notify(`未通过：${blocking[0].message}`, "warn");
      else notify("审批通过", "ok");
    } catch (e) {
      notify(`审批失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function reject() {
    if (!form) return;
    setBusy("reject");
    try {
      const res = await api.contentReject(form.id, "人工驳回");
      replacePlan(res.plan);
      notify("已驳回", "warn");
    } catch (e) {
      notify(`驳回失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function upload(kind: "video" | "cover", file: File | undefined) {
    if (!form || !file) return;
    setBusy("upload");
    try {
      const res = await api.contentUpload(form.id, kind, file);
      replacePlan(res.plan);
      notify(kind === "video" ? "视频已上传" : "封面已上传", "ok");
    } catch (e) {
      notify(`上传失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
      if (videoInput.current) videoInput.current.value = "";
      if (coverInput.current) coverInput.current.value = "";
    }
  }

  async function checkPublish() {
    if (!form) return;
    setBusy("publish-check");
    try {
      const res = await api.contentPublish(form.id, false);
      setPublishPreview({ status: res.status, detail: res.detail });
      notify(res.detail || "检查通过", res.status === "ready" ? "ok" : "warn");
    } catch (e) {
      notify(`发布检查失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function confirmPublish() {
    if (!form) return;
    setBusy("publish");
    try {
      const res = await api.contentPublish(form.id, true);
      setPublishPreview({ status: res.status, detail: res.detail });
      notify(res.detail || "已提交发布", res.status === "published" ? "ok" : "warn");
      await load();
    } catch (e) {
      notify(`发布失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function openCreator() {
    setBusy("creator");
    try {
      const res = await api.contentCreatorOpen();
      window.open(res.screenshot, "_blank", "noopener");
      notify("已打开创作者中心预览", "ok");
    } catch (e) {
      notify(`打开创作者中心失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function showCreator() {
    setBusy("creator-show");
    try {
      await api.contentCreatorShow();
      notify("浏览器已显示，请在创作者中心上传并发布", "ok");
    } catch (e) {
      notify(`打开创作者中心失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  const grouped = useMemo(() => {
    const map = new Map<string, ContentPlan[]>();
    for (const plan of plans) {
      const key = plan.plan_date || "未定日期";
      map.set(key, [...(map.get(key) || []), plan]);
    }
    return [...map.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
  }, [plans]);

  const blocking = form?.compliance?.blocking || [];
  const warnings = form?.compliance?.warnings || [];

  return (
    <div className="page">
      <div className="content-layout">
        <aside className="panel content-side">
          <div className="content-generate">
            <div className="panel-head">
              <h2>生成内容</h2>
              <Sparkles size={15} className="muted-icon" />
            </div>
            <div className="content-generate-body">
              <textarea
                className="textarea"
                rows={3}
                placeholder="想拍什么？给一个灵感，或留空让 AI 选题"
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
              />
              <div className="content-generate-row">
                <Select
                  value={pillar}
                  options={pillars}
                  onChange={setPillar}
                  ariaLabel="内容栏目"
                  className="content-pillar-select"
                />
                <input
                  className="input"
                  type="date"
                  value={planDate}
                  onChange={(e) => setPlanDate(e.target.value)}
                  aria-label="计划日期"
                />
                <button className="btn btn-primary" type="button" disabled={Boolean(busy)} onClick={() => { void generate(); }}>
                  <Sparkles size={14} /> {busy === "generate" ? "生成中" : "生成"}
                </button>
              </div>
            </div>
          </div>

          <div className="plan-list">
            {loading ? (
              <div className="table-skeleton">{[0, 1, 2].map((i) => <div key={i} />)}</div>
            ) : grouped.length === 0 ? (
              <EmptyState icon={CalendarDays} title="还没有内容计划">
                给一个灵感，先生成第一条。
              </EmptyState>
            ) : (
              grouped.map(([date, items]) => (
                <div className="plan-group" key={date}>
                  <div className="plan-group-head">
                    <span className="mono">{date}</span>
                    <span className="muted">{items.length}</span>
                  </div>
                  {items.map((plan) => (
                    <button
                      className={`plan-card ${selected === plan.id ? "active" : ""}`}
                      type="button"
                      key={plan.id}
                      onClick={() => setSelected(plan.id)}
                    >
                      {plan.cover_url ? (
                        <img src={plan.cover_url} alt="" referrerPolicy="no-referrer" />
                      ) : (
                        <div className="plan-cover-empty"><ImageIcon size={16} /></div>
                      )}
                      <div className="plan-card-main">
                        <strong>{plan.topic || "未命名内容"}</strong>
                        <span className="muted">{plan.pillar || "未分栏"}</span>
                      </div>
                      <span className={`plan-status ps-${plan.status}`}>{statusLabel(plan.status)}</span>
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>
        </aside>

        <section className="panel content-editor">
          {!form ? (
            <EmptyState icon={CalendarDays} title="选择一条内容">
              左侧可以生成或打开已有计划。
            </EmptyState>
          ) : (
            <>
              <div className="content-editor-head">
                <div>
                  <span className={`plan-status ps-${form.status}`}>{statusLabel(form.status)}</span>
                  <h2>{form.topic || "未命名内容"}</h2>
                  <span className="muted mono">#{form.id} · {fmtTime(form.updated_at)}</span>
                </div>
                <div className="content-editor-actions">
                  <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void save(); }}>
                    <Save size={14} /> 保存
                  </button>
                  <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void regenerateCover(); }}>
                    <RefreshCw size={14} /> 换封面
                  </button>
                </div>
              </div>

              <div className="editor-grid">
                <div className="editor-preview">
                  {form.cover_url ? (
                    <img src={form.cover_url} alt="封面预览" referrerPolicy="no-referrer" />
                  ) : (
                    <div className="cover-placeholder"><ImageIcon size={22} /> 还没有封面</div>
                  )}
                  <div className="cover-bg-row">
                    <span>AI 背景</span>
                    <Select
                      value={form.cover_bg || ""}
                      options={coverBgOptions}
                      onChange={(value) => { void changeBackground(value); }}
                      ariaLabel="封面背景"
                    />
                  </div>
                  <div className="cover-actions">
                    <button className="btn btn-secondary" type="button" onClick={() => coverInput.current?.click()}>
                      <Upload size={14} /> 上传封面
                    </button>
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      hidden
                      ref={coverInput}
                      onChange={(e) => { void upload("cover", e.target.files?.[0]); }}
                    />
                  </div>
                  <div className="video-box">
                    {form.video_url ? (
                      <video src={form.video_url} controls preload="metadata" />
                    ) : (
                      <div className="video-placeholder">还没有上传视频</div>
                    )}
                    <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => videoInput.current?.click()}>
                      <Upload size={14} /> {form.video_path ? "替换视频" : "上传视频"}
                    </button>
                    <input
                      type="file"
                      accept="video/mp4,video/quicktime,video/webm"
                      hidden
                      ref={videoInput}
                      onChange={(e) => { void upload("video", e.target.files?.[0]); }}
                    />
                  </div>
                </div>

                <div className="editor-fields">
                  <label className="field">
                    <span>选题</span>
                    <input className="input" value={form.topic} onChange={(e) => set("topic", e.target.value)} />
                  </label>
                  <div className="editor-two">
                    <label className="field">
                      <span>日期</span>
                      <input className="input" type="date" value={form.plan_date} onChange={(e) => set("plan_date", e.target.value)} />
                    </label>
                    <label className="field">
                      <span>栏目</span>
                      <input className="input" value={form.pillar} onChange={(e) => set("pillar", e.target.value)} />
                    </label>
                  </div>
                  <label className="field">
                    <span>核心看点</span>
                    <input className="input" value={form.angle} onChange={(e) => set("angle", e.target.value)} />
                  </label>
                  <label className="field">
                    <span>口播稿</span>
                    <textarea className="textarea" rows={8} value={form.script} onChange={(e) => set("script", e.target.value)} />
                  </label>
                  <label className="field">
                    <span>发布正文</span>
                    <textarea className="textarea" rows={3} value={form.caption} onChange={(e) => set("caption", e.target.value)} />
                  </label>
                  <div className="editor-two">
                    <label className="field">
                      <span>封面大字</span>
                      <input className="input" value={form.cover_text} onChange={(e) => set("cover_text", e.target.value)} />
                    </label>
                    <label className="field">
                      <span>话题标签</span>
                      <input className="input" value={tagText} onChange={(e) => setTagText(e.target.value)} placeholder="用空格分隔" />
                    </label>
                  </div>
                  <div className="editor-two">
                    <label className="field">
                      <span>音乐/授权说明</span>
                      <input className="input" value={form.music_note} onChange={(e) => set("music_note", e.target.value)} />
                    </label>
                    <label className="field">
                      <span>计划发布时间</span>
                      <input
                        className="input"
                        type="datetime-local"
                        value={form.publish_at ? form.publish_at.slice(0, 16) : ""}
                        onChange={(e) => set("publish_at", e.target.value)}
                      />
                    </label>
                  </div>
                  <label className="field">
                    <span>素材来源 / 拍摄建议</span>
                    <input className="input" value={form.source_notes} onChange={(e) => set("source_notes", e.target.value)} />
                  </label>
                </div>
              </div>

              <div className="compliance-box">
                <div className="compliance-head">
                  <strong>合规与审批</strong>
                  <div className="content-editor-actions">
                    <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void approve(); }}>
                      <Check size={14} /> 通过审批
                    </button>
                    <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void reject(); }}>
                      <X size={14} /> 驳回
                    </button>
                  </div>
                </div>
                {blocking.length === 0 && warnings.length === 0 ? (
                  <p className="muted">还没有检查结果，点“通过审批”会先跑合规检查。</p>
                ) : (
                  <div className="compliance-list">
                    {blocking.map((item, i) => <p className="issue issue-block" key={`b${i}`}>{item.message}</p>)}
                    {warnings.map((item, i) => <p className="issue issue-warn" key={`w${i}`}>{item.message}</p>)}
                  </div>
                )}
                {form.error ? <p className="inline-error">{form.error}</p> : null}
              </div>

              <div className="publish-box">
                <div>
                  <strong>发布</strong>
                  <p className="muted">
                    {form.status === "approved" ? "已通过审批，可提交到创作者中心。" : "先保存并通过审批，再提交发布。"}
                  </p>
                </div>
                <div className="publish-actions">
                  <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void openCreator(); }}>
                    <ExternalLink size={14} /> 创作者中心
                  </button>
                  <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void showCreator(); }}>
                    <ExternalLink size={14} /> 手动发布
                  </button>
                  <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void checkPublish(); }}>
                    <Send size={14} /> 发布检查
                  </button>
                  {publishPreview?.status === "ready" ? (
                    <button className="btn btn-primary" type="button" disabled={Boolean(busy)} onClick={() => { void confirmPublish(); }}>
                      <Send size={14} /> {busy === "publish" ? "发布中" : "确认发布"}
                    </button>
                  ) : null}
                </div>
              </div>
              {publishPreview ? (
                <p className={`publish-result ${publishPreview.status === "published" ? "ok" : ""}`}>
                  {publishPreview.detail || publishPreview.status}
                </p>
              ) : null}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

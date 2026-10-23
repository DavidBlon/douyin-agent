import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Save, Search, Send, Sparkles, UserRound, X } from "lucide-react";
import { api } from "../api";
import type { Contact, CrmOverview, ToastMessage } from "../types";
import { EmptyState, Select } from "../components/ui";

const STAGE_LABELS: Record<string, string> = {
  new: "新联系人",
  chatting: "聊天中",
  interested: "有意向",
  cooperation: "合作",
  customer: "客户",
  negative: "负面",
  archived: "归档",
};

const stageOptions = [
  { value: "", label: "全部阶段" },
  ...Object.entries(STAGE_LABELS).map(([value, label]) => ({ value, label })),
];

const editStageOptions = Object.entries(STAGE_LABELS).map(([value, label]) => ({ value, label }));

function fmtTime(value: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.replace("T", " ");
  return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

interface Props {
  notify: (text: string, tone?: ToastMessage["tone"]) => void;
}

export function CrmPage({ notify }: Props) {
  const [items, setItems] = useState<Contact[]>([]);
  const [overview, setOverview] = useState<CrmOverview | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState<Contact | null>(null);
  const [q, setQ] = useState("");
  const [stage, setStage] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await api.crmContacts(q, stage);
      setItems(res.items || []);
      setOverview(res.overview);
      setSelected((current) => {
        if (current && res.items.some((c) => c.target_key === current)) return current;
        return res.items[0]?.target_key ?? null;
      });
    } catch (e) {
      notify(`读取联系人失败：${(e as Error).message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [notify, q, stage]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const found = items.find((c) => c.target_key === selected) || null;
    setForm(found ? { ...found, tags: [...(found.tags || [])] } : null);
    setTagInput("");
    setMessage("");
  }, [selected, items]);

  function replaceContact(next: Contact) {
    setItems((prev) => prev.map((c) => (c.target_key === next.target_key ? next : c)));
    setForm({ ...next, tags: [...(next.tags || [])] });
  }

  async function save() {
    if (!form) return;
    setBusy("save");
    try {
      const res = await api.crmUpdate(form.target_key, {
        tags: form.tags,
        stage: form.stage,
        note: form.note,
        score: form.score,
      });
      replaceContact(res.contact);
      notify("联系人已保存", "ok");
    } catch (e) {
      notify(`保存失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function analyze() {
    if (!form) return;
    setBusy("analyze");
    try {
      const res = await api.crmAnalyze(form.target_key);
      replaceContact(res.contact);
      notify("已完成 CRM 分析", "ok");
    } catch (e) {
      notify(`分析失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  async function send() {
    if (!form || !message.trim()) return;
    setBusy("send");
    try {
      await api.chatSend(form.target_key, message.trim());
      const next = { ...form, last_reply: message.trim() };
      replaceContact(next);
      setMessage("");
      notify("消息已发送", "ok");
    } catch (e) {
      notify(`发送失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  function addTag() {
    const value = tagInput.trim();
    if (!form || !value) return;
    if (!form.tags.includes(value)) {
      setForm({ ...form, tags: [...form.tags, value] });
    }
    setTagInput("");
  }

  function removeTag(tag: string) {
    if (!form) return;
    setForm({ ...form, tags: form.tags.filter((x) => x !== tag) });
  }

  const stageCounts = useMemo(() => overview?.stages || {}, [overview]);

  return (
    <div className="page">
      <div className="crm-layout">
        <aside className="panel contact-list">
          <div className="contact-toolbar">
            <div className="search-box">
              <Search size={14} />
              <input
                className="input"
                placeholder="搜索昵称或消息"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
            <Select value={stage} options={stageOptions} onChange={setStage} ariaLabel="跟进阶段" />
          </div>
          <div className="contact-stats">
            <span>全部 {overview?.total ?? 0}</span>
            <span>意向 {stageCounts.interested ?? 0}</span>
            <span>合作 {stageCounts.cooperation ?? 0}</span>
            <span>负面 {stageCounts.negative ?? 0}</span>
          </div>
          <div className="contact-scroll">
            {loading ? (
              <div className="table-skeleton">{[0, 1, 2, 3].map((i) => <div key={i} />)}</div>
            ) : items.length === 0 ? (
              <EmptyState icon={UserRound} title="还没有联系人">
                私信同步后会自动出现在这里。
              </EmptyState>
            ) : (
              items.map((contact) => (
                <button
                  className={`contact-card ${selected === contact.target_key ? "active" : ""}`}
                  type="button"
                  key={contact.target_key}
                  onClick={() => setSelected(contact.target_key)}
                >
                  {contact.avatar ? (
                    <img src={contact.avatar} alt="" referrerPolicy="no-referrer" />
                  ) : (
                    <div className="contact-avatar-empty"><UserRound size={16} /></div>
                  )}
                  <div className="contact-card-main">
                    <div>
                      <strong>{contact.name || "未命名"}</strong>
                      <span className={`crm-stage stage-${contact.stage}`}>{STAGE_LABELS[contact.stage] || contact.stage}</span>
                    </div>
                    <p>{contact.last_incoming || contact.last_reply || "暂无消息"}</p>
                    <small className="mono">{fmtTime(contact.updated_at)}{contact.score > 0 ? ` · ${contact.score} 分` : ""}</small>
                  </div>
                </button>
              ))
            )}
          </div>
        </aside>

        <section className="panel contact-detail">
          {!form ? (
            <EmptyState icon={UserRound} title="选择一个联系人">
              左侧列表可以搜索、筛选和打开联系人。
            </EmptyState>
          ) : (
            <>
              <div className="contact-detail-head">
                {form.avatar ? (
                  <img src={form.avatar} alt="" referrerPolicy="no-referrer" />
                ) : (
                  <div className="contact-avatar-empty large"><UserRound size={22} /></div>
                )}
                <div>
                  <h2>{form.name || "未命名"}</h2>
                  <span className="muted mono">{form.target_key}</span>
                </div>
                <div className="contact-detail-actions">
                  <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void analyze(); }}>
                    <Sparkles size={14} /> {busy === "analyze" ? "分析中" : "AI 分析"}
                  </button>
                  <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void save(); }}>
                    <Save size={14} /> 保存
                  </button>
                </div>
              </div>

              <div className="contact-messages">
                <div>
                  <span>对方最近说</span>
                  <p>{form.last_incoming || "—"}</p>
                </div>
                <div>
                  <span>我们最近回</span>
                  <p>{form.last_reply || "—"}</p>
                </div>
              </div>

              <div className="contact-form">
                <div className="editor-two">
                  <label className="field">
                    <span>跟进阶段</span>
                    <Select
                      value={form.stage}
                      options={editStageOptions}
                      onChange={(value) => setForm({ ...form, stage: value })}
                      ariaLabel="跟进阶段"
                    />
                  </label>
                  <label className="field">
                    <span>意向分</span>
                    <input
                      className="input"
                      type="number"
                      min={0}
                      max={100}
                      value={form.score}
                      onChange={(e) => setForm({ ...form, score: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })}
                    />
                  </label>
                </div>
                <div className="field">
                  <span>标签</span>
                  <div className="tag-box">
                    {form.tags.map((tag) => (
                      <span className="tag-chip" key={tag}>
                        {tag}
                        <button type="button" aria-label={`删除 ${tag}`} onClick={() => removeTag(tag)}><X size={11} /></button>
                      </span>
                    ))}
                    <input
                      className="tag-input"
                      placeholder="加标签，回车"
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addTag();
                        }
                      }}
                    />
                  </div>
                </div>
                <label className="field">
                  <span>备注</span>
                  <textarea
                    className="textarea"
                    rows={4}
                    value={form.note}
                    onChange={(e) => setForm({ ...form, note: e.target.value })}
                  />
                </label>
              </div>

              <div className="contact-composer">
                <input
                  className="input"
                  placeholder="给这个联系人发一条私信"
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void send();
                    }
                  }}
                />
                <button className="btn btn-primary" type="button" disabled={Boolean(busy) || !message.trim()} onClick={() => { void send(); }}>
                  <Send size={14} /> {busy === "send" ? "发送中" : "发送"}
                </button>
              </div>
              <div className="contact-foot">
                <span className="muted mono">最近更新 {fmtTime(form.updated_at)}</span>
                <button className="text-link" type="button" onClick={() => { void load(); }}>
                  <RefreshCw size={12} /> 刷新
                </button>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

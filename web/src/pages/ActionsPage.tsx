import { ChevronLeft, ChevronRight, Film, ListChecks } from "lucide-react";
import type { Action, ActionsResponse } from "../types";
import { StatusBadge, TypeBadge, EmptyState, Select } from "../components/ui";
import { actionKindLabel } from "../api";

interface ActionsPageProps {
  data: ActionsResponse | null;
  page: number;
  statusFilter: string;
  loading: boolean;
  onStatus: (status: string) => void;
  onPage: (page: number) => void;
  onSelect: (action: Action) => void;
}

const statuses = [
  { value: "", label: "全部状态" },
  { value: "sent", label: "已发送" },
  { value: "dry_run", label: "预演" },
  { value: "blocked", label: "被拦截" },
  { value: "failed", label: "失败" },
  { value: "uncertain", label: "待确认" },
  { value: "running", label: "执行中" },
];

function fmt(ts: string): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.replace("T", " ");
  return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function targetName(a: Action): string {
  if (a.target_name) return a.target_name;
  if (a.target_url) return a.target_url.replace(/\/$/, "").split("/").pop() || a.target_url;
  return a.video_id || "—";
}

export function ActionsPage({
  data,
  page,
  statusFilter,
  loading,
  onStatus,
  onPage,
  onSelect,
}: ActionsPageProps) {
  const items = data?.items || [];
  return (
    <div className="page">
      <section className="panel actions-panel">
        <div className="filter-bar">
          <Select
            value={statusFilter}
            options={statuses}
            onChange={onStatus}
            ariaLabel="状态筛选"
            className="actions-status-select"
          />
          <span className="count-text mono">{data?.total ?? 0} 条</span>
        </div>

        {loading && !data ? (
          <div className="table-skeleton">
            {[0, 1, 2, 3, 4].map((i) => <div key={i} />)}
          </div>
        ) : items.length === 0 ? (
          <EmptyState icon={ListChecks} title="还没有动作记录" />
        ) : (
          <>
            <div className="table-wrap">
              <table className="data-table actions-table">
                <thead>
                  <tr>
                    <th style={{ width: 118 }}>时间</th>
                    <th style={{ width: 96 }}>类型</th>
                    <th style={{ width: 150 }}>对象</th>
                    <th>内容 / 原因</th>
                    <th style={{ width: 96 }}>状态</th>
                    <th style={{ width: 185 }}>结果</th>
                    <th style={{ width: 44 }} aria-label="截图" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((a) => (
                    <tr key={a.id} tabIndex={0} onClick={() => onSelect(a)} onKeyDown={(e) => { if (e.key === "Enter") onSelect(a); }}>
                      <td className="mono muted">{fmt(a.executed_at || a.created_at)}</td>
                      <td><TypeBadge kind={a.kind} /></td>
                      <td><span className="target-name">{targetName(a)}</span></td>
                      <td>
                        <span className={`cell-content ${!a.content ? "muted" : ""}`}>
                          {a.content || (a.kind === "like" ? "—" : a.policy || "—")}
                        </span>
                      </td>
                      <td><StatusBadge status={a.status} /></td>
                      <td><span className="cell-result">{a.result || a.policy || "—"}</span></td>
                      <td>{a.screenshot ? <Film size={14} className="muted-icon" /> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="action-card-list">
              {items.map((a) => (
                <button className="action-card" key={a.id} onClick={() => onSelect(a)} type="button">
                  <div className="action-card-head">
                    <TypeBadge kind={a.kind} />
                    <StatusBadge status={a.status} />
                  </div>
                  <strong>{targetName(a)}</strong>
                  <p>{a.content || a.policy || (a.kind === "like" ? "点赞动作" : "—")}</p>
                  <div className="action-card-foot">
                    <span className="mono">{fmt(a.executed_at || a.created_at)}</span>
                    <span>{a.result || (a.screenshot ? "有截图" : "")}</span>
                  </div>
                </button>
              ))}
            </div>

            <div className="pagination">
              <span className="muted mono">
                第 {data?.page || page} / {data?.pages || 1} 页 · 共 {data?.total || 0} 条
              </span>
              <div className="pagination-buttons">
                <button
                  className="btn btn-icon"
                  type="button"
                  disabled={(data?.page || 1) <= 1}
                  onClick={() => onPage((data?.page || 1) - 1)}
                  aria-label="上一页"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  className="btn btn-icon"
                  type="button"
                  disabled={(data?.page || 1) >= (data?.pages || 1)}
                  onClick={() => onPage((data?.page || 1) + 1)}
                  aria-label="下一页"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

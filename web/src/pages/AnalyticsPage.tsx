import { useCallback, useEffect, useState } from "react";
import { BarChart3, Eye, Heart, RefreshCw, Users } from "lucide-react";
import { api } from "../api";
import type { AnalyticsOverview, ToastMessage } from "../types";
import { EmptyState } from "../components/ui";

function fmtNum(value?: number): string {
  const n = Number(value || 0);
  if (n >= 100000000) return `${(n / 100000000).toFixed(1)}亿`;
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return String(n);
}

function shortDay(day: string): string {
  return day ? day.slice(5) : "—";
}

interface Props {
  notify: (text: string, tone?: ToastMessage["tone"]) => void;
}

export function AnalyticsPage({ notify }: Props) {
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await api.analyticsOverview());
    } catch (e) {
      notify(`读取数据失败：${(e as Error).message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => {
    void load();
  }, [load]);

  async function refresh(detail: boolean) {
    setBusy(detail ? "detail" : "refresh");
    try {
      await api.analyticsRefresh(detail, 30);
      await load();
      notify(detail ? "作品数据已深度刷新" : "作品数据已刷新", "ok");
    } catch (e) {
      notify(`刷新失败：${(e as Error).message}`, "error");
    } finally {
      setBusy("");
    }
  }

  const account = data?.account;
  const totals = data?.totals;
  const trend = data?.trend || [];
  const maxPlays = Math.max(1, ...trend.map((x) => Number(x.plays || 0)));
  const works = data?.works || [];

  return (
    <div className="page">
      <section className="metric-grid">
        <div className="metric-card">
          <div className="metric-icon"><Users size={16} /></div>
          <span>粉丝</span>
          <strong>{fmtNum(account?.followers)}</strong>
          <small>{account?.day ? `数据日 ${account.day}` : "等待刷新"}</small>
        </div>
        <div className="metric-card">
          <div className="metric-icon"><BarChart3 size={16} /></div>
          <span>作品</span>
          <strong>{fmtNum(data?.work_count)}</strong>
          <small>最近数据日 {account?.day || "—"}</small>
        </div>
        <div className="metric-card">
          <div className="metric-icon"><Eye size={16} /></div>
          <span>总播放</span>
          <strong>{fmtNum(totals?.plays)}</strong>
          <small>已抓到的最新快照合计</small>
        </div>
        <div className="metric-card">
          <div className="metric-icon"><Heart size={16} /></div>
          <span>总互动</span>
          <strong>{fmtNum((totals?.likes || 0) + (totals?.comments || 0) + (totals?.shares || 0))}</strong>
          <small>点赞 + 评论 + 分享</small>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>近 14 天播放</h2>
          </div>
          <div className="page-head-actions">
            <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void refresh(false); }}>
              <RefreshCw size={14} className={busy === "refresh" ? "spin" : ""} /> 刷新
            </button>
            <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => { void refresh(true); }}>
              <RefreshCw size={14} className={busy === "detail" ? "spin" : ""} /> 深度刷新
            </button>
          </div>
        </div>
        <div className="analytics-body">
          {trend.length === 0 ? (
            <EmptyState icon={BarChart3} title="还没有作品数据">
              点“刷新”读取自己的作品和公开数据。
            </EmptyState>
          ) : (
            <div className="trend-chart">
              {trend.map((row) => (
                <div className="trend-col" key={row.day} title={`${row.day} 播放 ${row.plays}`}>
                  <span className="trend-value mono">{fmtNum(row.plays)}</span>
                  <div className="trend-track">
                    <i style={{ height: `${Math.max(4, Math.round((Number(row.plays || 0) / maxPlays) * 100))}%` }} />
                  </div>
                  <span className="trend-day mono">{shortDay(row.day)}</span>
                </div>
              ))}
            </div>
          )}
          {data?.status?.last_error ? <p className="inline-error">{data.status.last_error}</p> : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>作品表现</h2>
          <span className="muted mono">{loading ? "读取中" : `${works.length} 条`}</span>
        </div>
        {loading ? (
          <div className="table-skeleton">{[0, 1, 2, 3].map((i) => <div key={i} />)}</div>
        ) : works.length === 0 ? (
          <EmptyState icon={Eye} title="还没有作品快照">
            先刷新一次，之后每天会保留一条快照。
          </EmptyState>
        ) : (
          <div className="work-table">
            <div className="work-row work-head">
              <span>作品</span><span>发布</span><span>播放</span><span>点赞</span><span>评论</span><span>分享</span>
            </div>
            {works.map((work) => (
              <a className="work-row" href={work.url || "#"} target="_blank" rel="noreferrer" key={work.aweme_id}>
                <div className="work-main">
                  {work.cover ? (
                    <img src={work.cover} alt="" referrerPolicy="no-referrer" loading="lazy" />
                  ) : (
                    <div className="work-cover-empty" />
                  )}
                  <div>
                    <strong>{work.title || work.aweme_id}</strong>
                    <small className="mono">{work.aweme_id}</small>
                  </div>
                </div>
                <span className="mono muted">{work.published_at || work.stats_day || "—"}</span>
                <span className="mono">{fmtNum(work.plays)}</span>
                <span className="mono">{fmtNum(work.likes)}</span>
                <span className="mono">{fmtNum(work.comments)}</span>
                <span className="mono">{fmtNum(work.shares)}</span>
              </a>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

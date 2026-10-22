import type {
  ActionsResponse,
  ActionKind,
  AgentEvent,
  FansResponse,
  FollowFanResponse,
  Settings,
  Status,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  status: () => request<Status>("/api/status"),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (settings: Settings) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(settings) }),
  saveKey: (apiKey: string) =>
    request<{ ok: boolean; has_api_key: boolean }>("/api/settings/api-key", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteKey: () =>
    request<{ ok: boolean; has_api_key: boolean }>("/api/settings/api-key", { method: "DELETE" }),
  testLLM: () => request<{ ok: boolean; text?: string; error?: string }>("/api/llm/test", { method: "POST" }),
  run: () => request<{ ok: boolean; accepted: boolean }>("/api/run", { method: "POST" }),
  stop: () => request<{ ok: boolean }>("/api/stop", { method: "POST" }),
  checkReplier: () => request<{ ok: boolean; dm?: number; comment?: number; skipped?: string }>("/api/replier/check", { method: "POST" }),
  checkSharer: () => request<{ ok: boolean; shared?: number; scanned?: number; skipped?: string }>("/api/share/check", { method: "POST" }),
  openBrowser: () => request<{ started: boolean; logged_in: boolean; headless?: boolean; url?: string; last_error?: string }>("/api/browser/open", { method: "POST" }),
  hideBrowser: () => request<{ started: boolean; logged_in: boolean }>("/api/browser/hide", { method: "POST" }),
  fans: () => request<FansResponse>("/api/fans"),
  followFan: (name: string, profile_url: string) =>
    request<FollowFanResponse>("/api/fans/follow", {
      method: "POST",
      body: JSON.stringify({ name, profile_url }),
    }),
  unfollowFan: (name: string, profile_url: string) =>
    request<FollowFanResponse>("/api/fans/unfollow", {
      method: "POST",
      body: JSON.stringify({ name, profile_url }),
    }),
  snapshot: () => request<{ url: string; html: string; screenshot: string }>("/api/browser/snapshot", { method: "POST" }),
  closeBrowser: () => request<{ ok: boolean }>("/api/browser/close", { method: "POST" }),
  actions: (page: number, size: number, kind: string, status: string) => {
    const q = new URLSearchParams({ page: String(page), size: String(size) });
    if (kind) q.set("kind", kind);
    if (status) q.set("status", status);
    return request<ActionsResponse>(`/api/actions?${q.toString()}`);
  },
  eventsRecent: (limit = 80) => request<{ items: AgentEvent[] }>(`/api/events/recent?limit=${limit}`),
};

export function actionKindLabel(kind: ActionKind | string): string {
  const labels: Record<string, string> = {
    like: "点赞",
    comment: "评论",
    dm: "私信",
    publish: "发布",
    reply_dm: "私信回复",
    reply_comment: "评论回复",
    share: "视频分享",
    follow: "回关",
    unfollow: "取关",
  };
  return labels[kind] || "发布";
}

export function statusLabel(status: string): string {
  return (
    {
      running: "执行中",
      queued: "排队",
      sent: "已发送",
      uncertain: "待确认",
      failed: "失败",
      blocked: "被拦截",
      skipped: "已跳过",
      dry_run: "预演",
      draft: "草稿",
    }[status] || status
  );
}

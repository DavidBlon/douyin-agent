import type {
  ActionsResponse,
  ActionKind,
  AgentEvent,
  AnalyticsOverview,
  ChatConversationsResponse,
  ChatMessagesResponse,
  ChatSendResponse,
  ChatSummary,
  ChatVideoResponse,
  ContentPlan,
  CrmContactsResponse,
  Contact,
  FansResponse,
  FollowFanResponse,
  Settings,
  Status,
} from "./types";

const TOKEN_KEY = "douyin-agent-token";

export const AUTH_REQUIRED_EVENT = "douyin-agent-auth-required";

export function getToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token.trim());
  } catch {
    /* storage unavailable */
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

/** EventSource / WebSocket 不能自定义请求头，只能把令牌放进查询参数。 */
export function tokenQuery(): string {
  const token = getToken();
  return token ? `token=${encodeURIComponent(token)}` : "";
}

function handle401(): void {
  clearToken();
  window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
  });
  if (res.status === 401) {
    handle401();
  }
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

async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    method: "POST",
    body: form,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (res.status === 401) {
    handle401();
  }
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
  checkReplier: () => request<{ ok: boolean; dm?: number; comment?: number; skipped?: string; queued?: boolean; detail?: string }>("/api/replier/check", { method: "POST" }),
  chatSummary: () => request<ChatSummary>("/api/chat/summary"),
  chatConversations: (refresh = false) =>
    request<ChatConversationsResponse>(`/api/chat/conversations?refresh=${refresh ? 1 : 0}`),
  chatMessages: (key: string) =>
    request<ChatMessagesResponse>(`/api/chat/messages?key=${encodeURIComponent(key)}`),
  chatSend: (key: string, text: string) =>
    request<ChatSendResponse>("/api/chat/send", {
      method: "POST",
      body: JSON.stringify({ key, text }),
    }),
  chatVideo: (key: string, cover: string) =>
    request<ChatVideoResponse>("/api/chat/video", {
      method: "POST",
      body: JSON.stringify({ key, cover }),
    }),
  chatLeave: () => request<{ ok: boolean }>("/api/chat/leave", { method: "POST" }),
  checkSharer: () => request<{ ok: boolean; shared?: number; scanned?: number; skipped?: string }>("/api/share/check", { method: "POST" }),
  openBrowser: () => request<{ started: boolean; logged_in: boolean; headless?: boolean; url?: string; last_error?: string }>("/api/browser/open", { method: "POST" }),
  hideBrowser: () => request<{ started: boolean; logged_in: boolean }>("/api/browser/hide", { method: "POST" }),
  fans: (refresh = false) => request<FansResponse>(`/api/fans${refresh ? "?refresh=1" : ""}`),
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
  analyticsOverview: () => request<AnalyticsOverview>("/api/analytics/overview"),
  analyticsRefresh: (detail = false, limit = 30) =>
    request<{ ok: boolean; works?: number; followers?: number; [key: string]: unknown }>("/api/analytics/refresh", {
      method: "POST",
      body: JSON.stringify({ detail, limit }),
    }),
  workHistory: (awemeId: string) =>
    request<{ ok: boolean; items: Array<Record<string, unknown>> }>(`/api/analytics/works/${awemeId}/history`),
  contentPlans: (status = "") => {
    const q = new URLSearchParams();
    if (status) q.set("status", status);
    return request<{ ok: boolean; items: ContentPlan[] }>(`/api/content/plans?${q.toString()}`);
  },
  contentGenerate: (body: { brief: string; pillar?: string; plan_date?: string; slot?: string }) =>
    request<{ ok: boolean; plan: ContentPlan }>("/api/content/generate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  contentUpdate: (id: number, body: Partial<ContentPlan>) =>
    request<{ ok: boolean; plan: ContentPlan }>(`/api/content/plans/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  contentCover: (id: number) =>
    request<{ ok: boolean; plan: ContentPlan; cover_url: string }>(`/api/content/plans/${id}/cover`, { method: "POST" }),
  contentApprove: (id: number) =>
    request<{ ok: boolean; plan: ContentPlan }>(`/api/content/plans/${id}/approve`, { method: "POST" }),
  contentReject: (id: number, reason = "") =>
    request<{ ok: boolean; plan: ContentPlan }>(`/api/content/plans/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  contentPublish: (id: number, confirm = false) =>
    request<{ ok?: boolean; status: string; detail?: string; screenshot?: string; compliance?: unknown }>(
      `/api/content/plans/${id}/publish`,
      { method: "POST", body: JSON.stringify({ confirm }) },
    ),
  contentUpload: (id: number, kind: "video" | "cover", file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ ok: boolean; plan: ContentPlan; path: string; url: string }>(
      `/api/content/plans/${id}/upload?kind=${kind}`,
      form,
    );
  },
  contentCreatorOpen: () =>
    request<{ ok: boolean; url: string; title: string; screenshot: string }>("/api/content/creator/open", {
      method: "POST",
    }),
  contentCreatorShow: () =>
    request<{ ok: boolean; url: string; title: string; screenshot: string; kept_open: boolean; visible: boolean }>(
      "/api/content/creator/show",
      { method: "POST" },
    ),
  crmContacts: (q = "", stage = "", tag = "") => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (stage) params.set("stage", stage);
    if (tag) params.set("tag", tag);
    return request<CrmContactsResponse>(`/api/crm/contacts?${params.toString()}`);
  },
  crmContact: (key: string) =>
    request<{ ok: boolean; contact: Contact }>(`/api/crm/contacts/${encodeURIComponent(key)}`),
  crmUpdate: (key: string, body: { tags?: string[]; stage?: string; note?: string; score?: number }) =>
    request<{ ok: boolean; contact: Contact }>(`/api/crm/contacts/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  crmAnalyze: (key: string) =>
    request<{ ok: boolean; contact: Contact; usage?: Record<string, number> }>(
      `/api/crm/contacts/${encodeURIComponent(key)}/analyze`,
      { method: "POST" },
    ),
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
      idea: "想法",
      review: "待审核",
      needs_review: "待修改",
      approved: "已通过",
      rejected: "已驳回",
      publishing: "发布中",
      published: "已发布",
      ready: "可发布",
    }[status] || status
  );
}

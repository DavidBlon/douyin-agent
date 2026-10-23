export type Tab = "run" | "content" | "analytics" | "crm" | "actions" | "settings";
export type ActionKind = "like" | "comment" | "dm" | "publish" | "reply_dm" | "reply_comment" | "share" | "follow" | "unfollow";
export type ActionStatus =
  | "running"
  | "queued"
  | "sent"
  | "uncertain"
  | "failed"
  | "blocked"
  | "dry_run"
  | "draft";

export interface BrowserStatus {
  started: boolean;
  logged_in: boolean;
  mock: boolean;
  headless?: boolean;
  url: string;
  risk: string[];
  profile: string;
  last_error: string;
}

export interface Video {
  aweme_id: string;
  author: string;
  author_url: string;
  title: string;
  description: string;
  url: string;
  cover: string;
  stats: { like?: string; comment?: string; [key: string]: string | undefined };
  comments: string[];
  seen_at?: string;
  day?: string;
}

export interface Decision {
  like: boolean;
  comment: string;
  dm: string;
  reason: string;
  confidence: number;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  video_id?: string;
  ts?: string;
  author?: string;
}

export interface Settings {
  dry_run: boolean;
  auto_like: boolean;
  auto_comment: boolean;
  auto_dm: boolean;
  auto_publish: boolean;
  publish_require_approval: boolean;
  auto_operate: boolean;
  auto_content: boolean;
  auto_video: boolean;
  content_generate_time: string;
  publish_time: string;
  max_videos_per_run: number;
  likes_per_day: number;
  comments_per_day: number;
  dms_per_day: number;
  posts_per_day: number;
  min_gap_sec: number;
  max_comment_chars: number;
  comment_mention: boolean;
  max_dm_chars: number;
  dm_per_contact_per_day: number;
  dm_allow_mutual: boolean;
  auto_reply: boolean;
  auto_reply_dms: boolean;
  auto_reply_comments: boolean;
  reply_interval_sec: number;
  dm_reply_scope: "mutual" | "existing" | "allowlist";
  share_target: string;
  share_topics: string;
  auto_share: boolean;
  share_time_1: string;
  share_time_2: string;
  share_max_per_run: number;
  share_min_score: number;
  silent_browser: boolean;
  agent_name: string;
  persona: string;
  topics: string;
  content_pillars: string;
  crm_auto_tag: boolean;
  dm_allowlist: string[];
  comment_blocklist: string[];
  model: string;
  base_url: string;
  temperature: number;
  feed_url: string;
  media_dir: string;
  auto_interval_min: number;
  quiet_hours: string;
  updated_at: string;
  has_api_key?: boolean;
}

export interface Status {
  running: boolean;
  stop_requested: boolean;
  browser: BrowserStatus;
  settings: Settings;
  has_api_key: boolean;
  today: Record<ActionKind, number>;
  current_video: Video | null;
  last_decision: Decision | null;
  replier?: ReplierStatus;
  sharer?: SharerStatus;
  analytics?: AnalyticsStatus;
  publisher?: PublisherStatus;
  planner?: PlannerStatus;
  operator?: OperatorStatus;
  crm?: CrmOverview;
}

export interface PlannerStatus {
  enabled: boolean;
  running: boolean;
  busy: boolean;
  last_check: string;
  last_error: string;
  last_result: Record<string, unknown>;
}

export interface OperatorStatus {
  enabled: boolean;
  running: boolean;
  busy: boolean;
  interval_min: number;
  last_error: string;
  last_result: Record<string, unknown>;
}

export interface AnalyticsStatus {
  busy: boolean;
  last_refresh: string;
  last_error: string;
  last_result: { works?: number; followers?: number; [key: string]: unknown };
}

export interface PublisherStatus {
  enabled: boolean;
  running: boolean;
  busy: boolean;
  last_error: string;
  last_result: Record<string, unknown>;
}

export interface AnalyticsWork {
  aweme_id: string;
  title: string;
  cover: string;
  url: string;
  kind: string;
  published_at: string;
  stats_day?: string;
  plays?: number;
  likes?: number;
  comments?: number;
  shares?: number;
  favorites?: number;
}

export interface AnalyticsOverview {
  ok: boolean;
  account: {
    day: string;
    followers: number;
    following: number;
    likes: number;
    works: number;
  };
  totals: { plays: number; likes: number; comments: number; shares: number };
  work_count: number;
  works: AnalyticsWork[];
  trend: { day: string; plays: number; likes: number; comments: number; shares: number }[];
  updated_at: string;
  status?: AnalyticsStatus;
}

export interface ComplianceIssue {
  code: string;
  message: string;
}

export interface ComplianceReport {
  ok?: boolean;
  blocking?: ComplianceIssue[];
  warnings?: ComplianceIssue[];
  checked_at?: string;
}

export interface ContentPlan {
  id: number;
  plan_date: string;
  slot: string;
  pillar: string;
  topic: string;
  angle: string;
  script: string;
  caption: string;
  hashtags: string[];
  cover_text: string;
  cover_bg: string;
  cover_path: string;
  cover_url: string;
  video_path: string;
  video_url: string;
  music_note: string;
  source_notes: string;
  status: string;
  compliance: ComplianceReport;
  publish_at: string;
  aweme_id: string;
  error: string;
  created_at: string;
  updated_at: string;
}

export interface Contact {
  target_key: string;
  name: string;
  avatar: string;
  tags: string[];
  stage: string;
  note: string;
  score: number;
  last_incoming: string;
  last_reply: string;
  last_seen: string;
  updated_at: string;
}

export interface CrmOverview {
  total: number;
  stages: Record<string, number>;
  recent: Contact[];
}

export interface CrmContactsResponse {
  ok: boolean;
  items: Contact[];
  overview: CrmOverview;
}

export interface SharerStatus {
  enabled: boolean;
  running: boolean;
  busy: boolean;
  target: string;
  times: string[];
  last_check: string;
  last_error: string;
  last_result: { shared?: number; scanned?: number; target?: string };
  today: number;
}

export interface ReplierStatus {
  enabled: boolean;
  running: boolean;
  busy: boolean;
  last_check: string;
  last_error: string;
  last_result: { dm?: number; comment?: number };
  today: { dm: number; comment: number };
}

export interface ChatConversation {
  key: string;
  index: number;
  title: string;
  preview: string;
  unread: number;
  is_group: boolean;
  avatar: string;
  time: string;
}

export interface ChatMedia {
  kind: "video" | "image" | "emoji";
  url: string;
  cover?: string;
  video_url?: string;
  width?: number;
  height?: number;
  duration?: number;
}

export interface ChatMessage {
  id: string;
  text: string;
  time: string;
  incoming: boolean;
  avatar: string;
  media: ChatMedia[];
  system: boolean;
}

export interface ChatSummary {
  ok: boolean;
  unread_total: number;
  conversation_count: number;
  updated_at: string;
  age_sec: number | null;
  error: string;
  page_open: boolean;
}

export interface ChatConversationsResponse extends ChatSummary {
  conversations: ChatConversation[];
  cached: boolean;
}

export interface ChatMessagesResponse {
  ok: boolean;
  conversation: ChatConversation;
  messages: ChatMessage[];
}

export interface ChatSendResponse extends ChatMessagesResponse {
  status: string;
  detail: string;
  action_id?: number;
}

export interface ChatVideoResponse {
  ok: boolean;
  video_url: string;
  cover: string;
  page_url: string;
  cached: boolean;
}

export interface Action {
  id: number;
  kind: ActionKind;
  video_id: string;
  target_name: string;
  target_url: string;
  content: string;
  status: ActionStatus;
  policy: string;
  result: string;
  screenshot: string;
  dry_run: number;
  day: string;
  created_at: string;
  executed_at: string;
}

export interface Fan {
  name: string;
  bio: string;
  avatar: string;
  profile_url: string;
  status: string;
  following: boolean;
}

export interface FansResponse {
  items: Fan[];
  total: number;
}

export interface FollowFanResponse {
  status: string;
  detail?: string;
  name?: string;
  profile_url?: string;
  action_id?: number | null;
}

export interface ActionsResponse {
  items: Action[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface AgentEvent {
  id: number;
  ts: string;
  level: "info" | "warn" | "error" | string;
  kind: string;
  message: string;
  data: Record<string, any>;
}

export interface RunProgress {
  index: number;
  total: number;
}

export interface ToastMessage {
  text: string;
  tone: "ok" | "warn" | "error";
}

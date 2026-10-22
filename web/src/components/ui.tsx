import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Check,
  ChevronDown,
  CornerUpRight,
  Heart,
  MessageCircle,
  Reply,
  Send,
  Share2,
  Upload,
  UserMinus,
  UserPlus,
  X,
  type LucideIcon,
} from "lucide-react";
import type { ActionKind, ToastMessage } from "../types";
import { actionKindLabel, statusLabel } from "../api";

const kindIcon: Record<string, LucideIcon> = {
  like: Heart,
  comment: MessageCircle,
  dm: Send,
  publish: Upload,
  reply_dm: Reply,
  reply_comment: CornerUpRight,
  share: Share2,
  follow: UserPlus,
  unfollow: UserMinus,
};

export function TypeBadge({ kind }: { kind: ActionKind | string }) {
  const Icon = kindIcon[kind] || Upload;
  return (
    <span className={`type-badge type-${kind}`}>
      <Icon size={12} strokeWidth={2.2} />
      {actionKindLabel(kind)}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`status-badge st-${status}`}>{statusLabel(status)}</span>;
}

export function Select({
  value,
  options,
  onChange,
  ariaLabel,
  className = "",
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  ariaLabel?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selected = options.find((option) => option.value === value);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className={`ts-select ${className}`} ref={rootRef}>
      <button
        type="button"
        className="ts-select-trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span>{selected?.label ?? "请选择"}</span>
        <ChevronDown size={13} className={open ? "open" : ""} />
      </button>
      {open ? (
        <div className="ts-select-menu" role="listbox" aria-label={ariaLabel}>
          {options.map((option, index) => (
            <button
              key={`${option.value}-${index}`}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={option.value === value ? "selected" : ""}
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
            >
              <span>{option.label}</span>
              {option.value === value ? <Check size={12} /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  disabled = false,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      className={`toggle ${checked ? "on" : ""}`}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}

export function QuotaBar({
  label,
  used,
  max,
  tone = "rose",
}: {
  label: string;
  used: number;
  max: number;
  tone?: "rose" | "cyan" | "violet";
}) {
  const pct = max <= 0 ? 0 : Math.min(100, Math.round((used / max) * 100));
  return (
    <div className="quota">
      <div className="quota-head">
        <span>{label}</span>
        <span className="mono">
          {used} / {max}
        </span>
      </div>
      <div className="quota-track">
        <i className={`tone-${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={22} strokeWidth={1.7} />
      </div>
      <strong>{title}</strong>
      {children ? <p>{children}</p> : null}
    </div>
  );
}

export function Drawer({
  open,
  title,
  subtitle,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="drawer-wrap">
      <button className="drawer-overlay" aria-label="关闭" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <header className="drawer-head">
          <div>
            <h3>{title}</h3>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </header>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

export function Toast({ message }: { message: ToastMessage | null }) {
  if (!message) return null;
  return <div className={`toast toast-${message.tone}`}>{message.text}</div>;
}

export function PageHead({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-head-actions">{actions}</div> : null}
    </header>
  );
}

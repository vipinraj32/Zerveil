import { useState, type ReactNode } from "react";
import { CAMPAIGN_TONE, STATE_INFO, humanize } from "../format";
import type { CampaignStatus, DealState } from "../types";

export function StateBadge({ state }: { state: DealState }) {
  const info = STATE_INFO[state];
  return (
    <span className={`badge tone-${info.tone}`} title={info.hint}>
      {info.label}
    </span>
  );
}

export function CampaignBadge({ status }: { status: CampaignStatus }) {
  return <span className={`badge tone-${CAMPAIGN_TONE[status] ?? "neutral"}`}>{humanize(status)}</span>;
}

export function Card({
  title,
  actions,
  children,
  className = "",
  id,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`card ${className}`} id={id}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat" title={hint}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "bad" | "good"; children: ReactNode }) {
  return <div className={`notice tone-${tone}`}>{children}</div>;
}

export function ErrorText({ error }: { error: string | null | undefined }) {
  return error ? (
    <p className="error" role="alert">
      {error}
    </p>
  ) : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted spinner" aria-live="polite">
      {label}
    </p>
  );
}

/** Button that runs an async action, disables itself meanwhile and reports failures inline. */
export function ActionButton({
  onClick,
  children,
  variant = "primary",
  disabled,
  title,
}: {
  onClick: () => Promise<unknown>;
  children: ReactNode;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean;
  title?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="action">
      <button
        type="button"
        className={`btn btn-${variant}`}
        disabled={disabled || busy}
        title={title}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await onClick();
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Working…" : children}
      </button>
      {error && <span className="error inline-error">{error}</span>}
    </span>
  );
}

/** A shortened address or key; the full value is in the tooltip. Shielded addresses have no explorer page. */
export function Address({ value }: { value?: string | null }) {
  if (!value) return <span className="muted">—</span>;
  const label = value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
  return (
    <span className="mono" title={value}>
      {label}
    </span>
  );
}

export function Avatar({ src, name, size = 32 }: { src?: string; name: string; size?: number }) {
  return src ? (
    <img className="avatar" src={src} alt="" width={size} height={size} referrerPolicy="no-referrer" />
  ) : (
    <span className="avatar avatar-fallback" style={{ width: size, height: size }} aria-hidden>
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

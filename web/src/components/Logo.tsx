import { useId } from "react";

/** Zerveil mark: a shield ("veil") with a Z cut through it, violet → cyan. Inline SVG, no asset files. */
export function LogoMark({ size = 28, title }: { size?: number; title?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className="zx-mark" width={size} height={size} viewBox="0 0 64 64" fill="none" role={title ? "img" : undefined} aria-hidden={title ? undefined : true}>
      {title && <title>{title}</title>}
      <defs>
        <linearGradient id={`zv-${id}`} x1="8" y1="4" x2="56" y2="60" gradientUnits="userSpaceOnUse">
          <stop stopColor="#8b5cf6" />
          <stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <path d="M32 3 55 11v19c0 15-10 26-23 31C19 56 9 45 9 30V11L32 3Z" fill={`url(#zv-${id})`} />
      <path d="M21 21h22v5L28.5 39H43v5H21v-5l14.5-13H21v-5Z" fill="#0b0a1a" />
    </svg>
  );
}

/** Mark + wordmark. The wordmark uses the current text colour; `tone` pins it for fixed surfaces. */
export function Logo({ size = 28, tone = "auto", className = "" }: { size?: number; tone?: "auto" | "light" | "dark"; className?: string }) {
  const color = tone === "light" ? "#0b0a1a" : tone === "dark" ? "#f5f3ff" : undefined;
  return (
    <span className={`zx-logo ${className}`} style={{ display: "inline-flex", alignItems: "center", gap: Math.round(size * 0.3), color }}>
      <LogoMark size={size} />
      <span className="zx-wordmark" style={{ fontSize: Math.round(size * 0.78), fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1, fontFamily: "'Plus Jakarta Sans', Figtree, sans-serif" }}>
        Zerveil
      </span>
    </span>
  );
}

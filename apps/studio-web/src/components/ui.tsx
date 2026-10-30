import { useEffect, useState } from "react";
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  Ref,
  SelectHTMLAttributes,
} from "react";

/* Shared UI kit — own the code (shadcn-style), zero dependencies.
 * Every visual element in the app should compose from these. */

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean;
  danger?: boolean;
  small?: boolean;
};

export function Btn({ active, danger, small, style, className, ...rest }: BtnProps) {
  // Press/hover/shadow physics live in CSS (.kv-btn) so touch, mouse,
  // and keyboard share them — inline styles only carry color variants.
  // (Inline box-shadow/transform used to sit here and silently overrode
  // the stylesheet's hover lift and press sink.)
  return (
    <button
      className={["kv-btn", className].filter(Boolean).join(" ")}
      style={{
        background: active ? "var(--kv-raised)" : "var(--kv-surface)",
        color: danger ? "var(--kv-danger)" : "var(--kv-text)",
        border: `1px solid ${active ? "var(--kv-accent)" : "var(--kv-border-strong)"}`,
        borderRadius: "var(--kv-radius-sm)",
        padding: small ? "4px 8px" : "6px 12px",
        minHeight: 32,
        fontSize: 13,
        cursor: rest.disabled ? "default" : "pointer",
        opacity: rest.disabled ? 0.5 : 1,
        transition:
          "background-color var(--kv-dur-fast) var(--kv-ease-out), transform var(--kv-dur-fast) var(--kv-ease-out), box-shadow var(--kv-dur-fast) var(--kv-ease-out)",
        ...style,
      }}
      onMouseEnter={(e) => {
        if (!rest.disabled) {
          e.currentTarget.style.background = active
            ? "var(--kv-raised)"
            : "var(--kv-border)";
        }
        rest.onMouseEnter?.(e);
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = active
          ? "var(--kv-raised)"
          : "var(--kv-surface)";
        rest.onMouseLeave?.(e);
      }}
      {...rest}
    />
  );
}

export function PrimaryBtn({
  style,
  small,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { small?: boolean }) {
  return (
    <Btn
      small={small}
      style={{
        background: "var(--kv-accent)",
        color: "var(--kv-accent-ink)",
        border: "1px solid var(--kv-accent)",
        fontWeight: 700,
        ...style,
      }}
      {...rest}
    />
  );
}

export function TextInput({
  style,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return (
    <input
      style={{
        background: "var(--kv-inset)",
        color: "var(--kv-text)",
        border: "1px solid var(--kv-border-strong)",
        borderRadius: "var(--kv-radius-sm)",
        padding: "6px 8px",
        fontSize: 13,
        ...style,
      }}
      {...rest}
    />
  );
}

export function SelectInput({
  style,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      style={{
        background: "var(--kv-inset)",
        color: "var(--kv-text)",
        border: "1px solid var(--kv-border-strong)",
        borderRadius: "var(--kv-radius-sm)",
        padding: "6px 8px",
        fontSize: 13,
        ...style,
      }}
      {...rest}
    />
  );
}

export function Card({
  children,
  style,
}: {
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        background: "var(--kv-surface)",
        border: "1px solid var(--kv-border)",
        borderRadius: "var(--kv-radius)",
        padding: 16,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3
      style={{
        fontSize: 12,
        fontWeight: 700,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        margin: "0 0 8px",
        color: "var(--kv-muted)",
      }}
    >
      {children}
    </h3>
  );
}

export function Chip({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        fontSize: 12,
        background: "var(--kv-raised)",
        border: "1px solid var(--kv-border)",
        borderRadius: 999,
        padding: "2px 10px",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd
      style={{
        fontFamily: "var(--kv-mono)",
        fontSize: 11,
        background: "var(--kv-inset)",
        border: "1px solid var(--kv-border-strong)",
        borderBottomWidth: 2,
        borderRadius: 4,
        padding: "1px 6px",
      }}
    >
      {children}
    </kbd>
  );
}

export function EmptyState({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children?: ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px dashed var(--kv-border-strong)",
        borderRadius: "var(--kv-radius)",
        padding: "32px 24px",
        textAlign: "center",
        color: "var(--kv-muted)",
      }}
    >
      <div style={{ fontSize: 15, fontWeight: 600, color: "var(--kv-text)" }}>
        {title}
      </div>
      {hint && <div style={{ fontSize: 13, marginTop: 6 }}>{hint}</div>}
      {children && <div style={{ marginTop: 12 }}>{children}</div>}
    </div>
  );
}

export function Notice({
  tone,
  children,
}: {
  tone: "info" | "warn" | "error";
  children: ReactNode;
}) {
  if (!children) return null;
  const border =
    tone === "error"
      ? "var(--kv-danger)"
      : tone === "warn"
        ? "var(--kv-warn-border)"
        : "var(--kv-border-strong)";
  return (
    <div
      role={tone === "info" ? "status" : "alert"}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontSize: 13,
        background: tone === "warn" ? "var(--kv-warn-bg)" : "var(--kv-surface)",
        border: `1px solid ${border}`,
        borderRadius: "var(--kv-radius-sm)",
        padding: "6px 12px",
      }}
    >
      {children}
    </div>
  );
}

export function Spinner({ delayMs = 350 }: { delayMs?: number }) {
  // Delayed appearance: fast operations should never flash a spinner.
  const [show, setShow] = useState(delayMs <= 0);
  useEffect(() => {
    if (delayMs <= 0) return;
    const t = setTimeout(() => setShow(true), delayMs);
    return () => clearTimeout(t);
  }, [delayMs]);
  if (!show) return null;
  return (
    <span
      className="kv-spin"
      aria-label="Loading"
      style={{
        display: "inline-block",
        width: 12,
        height: 12,
        borderRadius: "50%",
        border: "2px solid var(--kv-border-strong)",
        borderTopColor: "var(--kv-accent)",
      }}
    />
  );
}

export function Skeleton({
  width = "100%",
  height = 14,
  style,
}: {
  width?: number | string;
  height?: number | string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className="kv-skeleton"
      aria-hidden
      style={{ width, height, ...style }}
    />
  );
}

export function ChapterHeader({
  no,
  title,
  hint,
  kanji,
}: {
  no: string;
  title: string;
  hint?: string;
  kanji?: string;
}) {
  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        alignItems: "baseline",
        gap: 12,
        marginBottom: 16,
        flexWrap: "wrap",
      }}
    >
      <span className="kv-chapter-no">CHAPTER {no}</span>
      <h2 className="kv-sfx" style={{ fontSize: 22, margin: 0 }}>
        {title}
      </h2>
      {hint && (
        <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>{hint}</span>
      )}
      {kanji && (
        <span aria-hidden className="kv-kanji sm">
          {kanji}
        </span>
      )}
    </div>
  );
}

const BURST_PATH =
  "M50 0 L61 28 L92 12 L74 40 L100 50 L74 60 L92 88 L61 72 " +
  "L50 100 L39 72 L8 88 L26 60 L0 50 L26 40 L8 12 L39 28 Z";

export function ImpactBurst({
  text,
  size = 120,
}: {
  text: string;
  size?: number;
}) {
  return (
    <div
      role="img"
      aria-label={text}
      style={{
        position: "relative",
        width: size,
        height: size,
        flexShrink: 0,
      }}
    >
      <svg
        viewBox="0 0 100 100"
        aria-hidden
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          filter: "drop-shadow(4px 4px 0 rgba(0,0,0,0.6))",
        }}
      >
        <path d={BURST_PATH} fill="var(--kv-accent)" stroke="#000" strokeWidth={2} />
      </svg>
      <span
        className="kv-sfx"
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          fontSize: Math.max(10, size / 7),
          color: "#fff",
          padding: "20%",
          lineHeight: 1.05,
        }}
      >
        {text}
      </span>
    </div>
  );
}

/**
 * Icon-only action button: glyph visible, text exposed via title tooltip
 * (bubble) and aria-label. Use for unambiguous transport/editing actions;
 * keep real labels on anything that carries meaning (Generate, Export…).
 */
export function IconBtn({
  label,
  active,
  danger,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  active?: boolean;
  danger?: boolean;
}) {
  return (
    <Btn small active={active} danger={danger} title={label} aria-label={label} {...rest}>
      <span aria-hidden="true">{children}</span>
    </Btn>
  );
}

export function SfxBtn({
  small,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { small?: boolean }) {
  // Click punch: the same ink-stamp slam as hover, fired on activation so
  // the loudest action answers presses as well as hovers — including
  // keyboard activation, which fires onClick too. ClassList restart (not
  // state) so rapid repeat presses slam every time, not just the first.
  return (
    <button
      className={`kv-sfxbtn${small ? " small" : ""}${className ? ` ${className}` : ""}`}
      {...rest}
      onClick={(e) => {
        const el = e.currentTarget;
        el.classList.remove("punch");
        void el.offsetWidth;
        el.classList.add("punch");
        rest.onClick?.(e);
      }}
    />
  );
}

export function Kanji({ char }: { char: string }) {
  return (
    <span aria-hidden className="kv-kanji">
      {char}
    </span>
  );
}

export function ImpactFlash({ burstKey }: { burstKey: number }) {
  if (!burstKey) return null;
  if (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  )
    return null;
  return <div key={burstKey} className="kv-impact" aria-hidden />;
}

export function StatusBar({ items }: { items: string[] }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        flexWrap: "wrap",
        fontFamily: "var(--kv-mono)",
        fontSize: 12,
        color: "var(--kv-faint)",
        padding: "8px 4px 0",
      }}
    >
      {items.map((item) => (
        <span key={item}>{item}</span>
      ))}
    </div>
  );
}

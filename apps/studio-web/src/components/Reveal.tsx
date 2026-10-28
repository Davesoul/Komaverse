import { useEffect, type ReactNode } from "react";

/** True when the OS asks for reduced motion — all animation must obey it. */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Scroll-reveal scope (top-design pattern): any [data-reveal] descendant is
 * armed (hidden start state) and revealed once on intersection. Content
 * without JS stays visible — the hidden state is only ever added by JS.
 * Optional data-delay="ms" staggers siblings.
 */
export function useRevealScope<T extends HTMLElement>(
  ref: React.RefObject<T | null>,
) {
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const els = Array.from(root.querySelectorAll("[data-reveal]"));
    if (prefersReducedMotion() || !("IntersectionObserver" in window)) {
      els.forEach((el) => el.classList.add("is-visible"));
      return;
    }
    els.forEach((el) => {
      el.classList.add("kv-reveal-armed");
      const delay = el.getAttribute("data-delay");
      if (delay) {
        (el as HTMLElement).style.setProperty("--kv-reveal-delay", `${delay}ms`);
      }
    });
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [ref]);
}

/** Convenience wrapper — equivalent to a bare div with data-reveal. */
export function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  return (
    <div data-reveal data-delay={delay} className={className}>
      {children}
    </div>
  );
}

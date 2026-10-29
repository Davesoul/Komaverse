import { useEffect } from "react";

/**
 * Kinetic headings: every `h2.lv-h2` in scope splits into words once and
 * rises word by word on first intersection (anime.js, already a dep).
 * Static text stays put when IO is missing or the animation library
 * fails — splitting never hides content by itself.
 */
export function useKineticScope<T extends HTMLElement>(
  ref: React.RefObject<T | null>,
) {
  useEffect(() => {
    const root = ref.current;
    if (!root || !("IntersectionObserver" in window)) return;
    const heads = Array.from(root.querySelectorAll("h2.lv-h2"));
    if (!heads.length) return;
    heads.forEach((h) => {
      const text = h.textContent ?? "";
      h.innerHTML = "";
      text
        .split(/\s+/)
        .filter(Boolean)
        .forEach((word, i) => {
          if (i > 0) h.appendChild(document.createTextNode(" "));
          const span = document.createElement("span");
          span.className = "kv-kword";
          span.textContent = word;
          h.appendChild(span);
        });
    });
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const h = entry.target as HTMLElement;
          io.unobserve(h);
          import("animejs")
            .then(({ animate, stagger }) => {
              animate(h.querySelectorAll(":scope .kv-kword"), {
                opacity: [0, 1],
                y: [16, 0],
                duration: 550,
                delay: stagger(45),
                ease: "outExpo",
              });
            })
            .catch(() => {});
        });
      },
      { threshold: 0.4 },
    );
    heads.forEach((h) => io.observe(h));
    return () => io.disconnect();
  }, [ref]);
}

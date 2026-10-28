import { useEffect, useRef, type ReactNode } from "react";
import { prefersReducedMotion } from "./Reveal";

/* Magnetic hover: the child leans toward the pointer on a spring.
 * Lean port of the react-bits Magnet pattern: per-instance pointer tracking,
 * rAF spring integration, touch + reduced-motion guards. Transform only. */
export function Magnet({
  children,
  strength = 3,
  maxOffset = 14,
  padding = 80,
}: {
  children: ReactNode;
  strength?: number;
  maxOffset?: number;
  padding?: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const wrap = wrapRef.current;
    const inner = innerRef.current;
    if (!wrap || !inner || prefersReducedMotion()) return;
    const stiffness = 170;
    const damping = 16;
    let tx = 0;
    let ty = 0;
    let x = 0;
    let y = 0;
    let vx = 0;
    let vy = 0;
    let raf = 0;
    let last = 0;
    let pointer: { x: number; y: number } | null = null;

    const frame = (now: number) => {
      raf = 0;
      const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
      last = now;
      if (pointer) {
        const rect = wrap.getBoundingClientRect();
        const dx = pointer.x - (rect.left + rect.width / 2);
        const dy = pointer.y - (rect.top + rect.height / 2);
        const outside = Math.hypot(
          Math.max(0, Math.abs(dx) - rect.width / 2),
          Math.max(0, Math.abs(dy) - rect.height / 2),
        );
        if (outside <= padding) {
          const pull = padding > 0 ? 1 - outside / padding : 1;
          let nx = (dx / strength) * pull;
          let ny = (dy / strength) * pull;
          const len = Math.hypot(nx, ny);
          if (len > maxOffset) {
            nx *= maxOffset / len;
            ny *= maxOffset / len;
          }
          tx = nx;
          ty = ny;
        } else {
          tx = 0;
          ty = 0;
        }
      } else {
        tx = 0;
        ty = 0;
      }
      const steps = Math.ceil(dt / (1 / 120));
      const h = dt / steps;
      for (let i = 0; i < steps; i++) {
        vx += (stiffness * (tx - x) - damping * vx) * h;
        vy += (stiffness * (ty - y) - damping * vy) * h;
        x += vx * h;
        y += vy * h;
      }
      inner.style.transform = `translate3d(${x.toFixed(2)}px, ${y.toFixed(2)}px, 0)`;
      const resting =
        Math.abs(tx - x) < 0.05 &&
        Math.abs(ty - y) < 0.05 &&
        Math.abs(vx) < 0.05 &&
        Math.abs(vy) < 0.05;
      if (!resting) {
        raf = requestAnimationFrame(frame);
      } else {
        last = 0;
        x = tx;
        y = ty;
        vx = 0;
        vy = 0;
      }
    };
    const wake = () => {
      if (!raf) raf = requestAnimationFrame(frame);
    };
    const onMove = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      pointer = { x: e.clientX, y: e.clientY };
      wake();
    };
    const onLeave = () => {
      pointer = null;
      wake();
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    window.addEventListener("blur", onLeave);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("blur", onLeave);
    };
  }, [strength, maxOffset, padding]);

  return (
    <div ref={wrapRef} style={{ display: "inline-block" }}>
      <div ref={innerRef} style={{ willChange: "transform" }}>
        {children}
      </div>
    </div>
  );
}

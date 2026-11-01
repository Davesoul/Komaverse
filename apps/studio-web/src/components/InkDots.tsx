import { useEffect, useRef } from "react";
import { prefersReducedMotion } from "./Reveal";

/* Hero ink field: a slow cloud of paper-ink dots with a few signal-red
 * embers drifting over the backdrop reels. Pointer parallax + a gentle
 * repel near the cursor make it feel alive; it never takes clicks.
 * Renders only while the hero is near the viewport, parks otherwise.
 * Any failure removes it quietly. */
const COUNT = 1300;
const RED_FRACTION = 0.06;

export default function InkDots() {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let cancelled = false;
    let cleanup: (() => void) | null = null;

    const start = () => {
      if (cancelled) return;
      void (async () => {
        try {
          const THREE = await import("three");
          if (cancelled || !hostRef.current) return;
          cleanup = build(THREE);
          if (cancelled) cleanup?.();
        } catch {
          cleanup?.();
        }
      })();
    };
    // Idle start: the hero paints first, the dots join when the
    // browser has a free moment.
    const w = window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    let idleId = 0;
    let timer = 0;
    if (w.requestIdleCallback) idleId = w.requestIdleCallback(start, { timeout: 2500 });
    else timer = window.setTimeout(start, 1500);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const build = (THREE: any) => {
      const el = hostRef.current!;
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
      renderer.setSize(el.clientWidth, el.clientHeight, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      renderer.domElement.style.display = "block";
      el.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 60);
      camera.position.z = 9;

      // Soft round sprite: PointsMaterial without a map draws hard
      // squares, which would read as pixels, not ink.
      const sprite = (() => {
        const c = document.createElement("canvas");
        c.width = c.height = 64;
        const g = c.getContext("2d")!;
        const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
        grad.addColorStop(0, "rgba(255,255,255,1)");
        grad.addColorStop(0.55, "rgba(255,255,255,0.7)");
        grad.addColorStop(1, "rgba(255,255,255,0)");
        g.fillStyle = grad;
        g.fillRect(0, 0, 64, 64);
        const t = new THREE.CanvasTexture(c);
        return t;
      })();

      // Flattened cloud: wide, shallow, deeper than it is tall — a field
      // of ink the hero text floats in front of.
      const pos = new Float32Array(COUNT * 3);
      const base = new Float32Array(COUNT * 3);
      const col = new Float32Array(COUNT * 3);
      const paper = new THREE.Color(0xf4efe4);
      const red = new THREE.Color(0xff5a5f);
      const seed = (_i: number) => Math.random();
      for (let i = 0; i < COUNT; i++) {
        const r = 3.2 + seed(i) * 5.2;
        const th = seed(i + COUNT) * Math.PI * 2;
        const ph = Math.acos(2 * seed(i + COUNT * 2) - 1);
        const flat = 0.45;
        base[i * 3] = r * Math.sin(ph) * Math.cos(th) * 1.7;
        base[i * 3 + 1] = r * Math.cos(ph) * flat;
        base[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th) * 0.7 - 2;
        const c = seed(i + COUNT * 3) < RED_FRACTION ? red : paper;
        // Dim with depth so the cloud has volume, not glitter.
        const dim = 0.35 + 0.65 * Math.min(1, (base[i * 3 + 2] + 5) / 6);
        col[i * 3] = c.r * dim;
        col[i * 3 + 1] = c.g * dim;
        col[i * 3 + 2] = c.b * dim;
      }
      pos.set(base);
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
      const mat = new THREE.PointsMaterial({
        size: 0.075,
        map: sprite,
        vertexColors: true,
        transparent: true,
        opacity: 0.55,
        depthWrite: false,
        sizeAttenuation: true,
      });
      const cloud = new THREE.Points(geo, mat);
      scene.add(cloud);

      const reduced = prefersReducedMotion();
      const mouse = { x: 0, y: 0, active: false };
      let raf = 0;
      let lastT = performance.now();
      let spin = 0;
      let visible = true;

      const render = (t: number) => {
        // Whole-cloud drift (cheap) + per-point repel near the pointer.
        spin += 0.00045;
        cloud.rotation.y = spin + mouse.x * 0.12;
        cloud.rotation.x = mouse.y * 0.08;
        if (mouse.active && !reduced) {
          const arr = geo.attributes.position.array as Float32Array;
          const R2 = 2.6 * 2.6;
          for (let i = 0; i < COUNT; i++) {
            const bx = base[i * 3];
            const by = base[i * 3 + 1];
            // Pointer in cloud plane, scaled to the cloud's spread.
            const dx = bx - mouse.x * 7;
            const dy = by - mouse.y * 4;
            const d2 = dx * dx + dy * dy;
            let ox = 0;
            let oy = 0;
            if (d2 < R2 && d2 > 0.0001) {
              const d = Math.sqrt(d2);
              const push = ((2.6 - d) / 2.6) * 0.55;
              ox = (dx / d) * push;
              oy = (dy / d) * push;
            }
            // Ease toward the repelled spot; base restores on leave.
            const k = 0.08;
            arr[i * 3] += (bx + ox + Math.sin(t * 0.0004 + i) * 0.05 - arr[i * 3]) * k;
            arr[i * 3 + 1] += (by + oy + Math.cos(t * 0.0005 + i * 1.7) * 0.05 - arr[i * 3 + 1]) * k;
            arr[i * 3 + 2] = base[i * 3 + 2];
          }
          geo.attributes.position.needsUpdate = true;
        }
        renderer.render(scene, camera);
      };

      const loop = () => {
        const now = performance.now();
        if (!document.hidden && visible) render(now);
        lastT = now;
        raf = requestAnimationFrame(loop);
      };

      const onMove = (e: PointerEvent) => {
        mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
        mouse.y = -((e.clientY / window.innerHeight) * 2 - 1);
        mouse.active = true;
      };
      const onLeave = () => {
        mouse.active = false;
      };
      // Hero-only: park the loop once the hero scrolls away.
      const onScroll = () => {
        visible = window.scrollY < window.innerHeight * 1.2;
        if (reduced && visible) render(performance.now());
      };
      const ro = new ResizeObserver(() => {
        const wpx = el.clientWidth;
        const hpx = el.clientHeight;
        if (!wpx || !hpx) return;
        renderer.setSize(wpx, hpx, false);
        camera.aspect = wpx / hpx;
        camera.updateProjectionMatrix();
      });
      ro.observe(el);
      camera.aspect = el.clientWidth / Math.max(el.clientHeight, 1);
      camera.updateProjectionMatrix();

      window.addEventListener("pointermove", onMove, { passive: true });
      document.documentElement.addEventListener("pointerleave", onLeave);
      window.addEventListener("scroll", onScroll, { passive: true });
      onScroll();
      if (reduced) render(performance.now());
      else loop();

      return () => {
        cancelAnimationFrame(raf);
        window.removeEventListener("pointermove", onMove);
        document.documentElement.removeEventListener("pointerleave", onLeave);
        window.removeEventListener("scroll", onScroll);
        ro.disconnect();
        geo.dispose();
        mat.dispose();
        sprite.dispose();
        renderer.dispose();
        renderer.domElement.remove();
      };
    };

    return () => {
      cancelled = true;
      if (idleId && w.cancelIdleCallback) w.cancelIdleCallback(idleId);
      window.clearTimeout(timer);
      cleanup?.();
    };
  }, []);

  return (
    <div
      ref={hostRef}
      aria-hidden="true"
      style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
    />
  );
}

import { useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "./Reveal";
import { PIXEL, createStage } from "../lib/models3d";

/* Scroll companion: the drawing-tablet pen (wacom) traveling the whole
 * landing page, docking into the instrument panel's pen rest when it is
 * on screen. One model only — no swaps, so scrolling never parses or
 * hands off mid-flight. Travel pose (drift, turn, bob) follows page
 * scroll; docked pose is a slow turntable the visitor can drag. Authored
 * PBR textures (color/roughness/metal; normals skipped by design), lit in
 * code (paper key + red rim + room environment). Frustum-fit scaling per
 * aspect so nothing ever clips a frame edge. Pointer-transparent while
 * traveling, interactive while docked; hidden on narrow screens.
 * The other models live permanently inside the instrument section
 * (InstrumentModels) — the traveler rests beside them, never over them.
 * Any failure removes it quietly. */
const PEN = { src: "/models/wacom-pen.fbx", skin: "wacom", tilt: -0.3 };

export default function ScrollCompanion({
  dockRef,
  onDock,
}: {
  dockRef: React.RefObject<HTMLDivElement | null>;
  onDock: (docked: boolean) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [enabled] = useState(
    () => typeof window === "undefined" || window.innerWidth >= 720,
  );
  const [gone, setGone] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    const host = hostRef.current;
    if (!host) return;
    let cancelled = false;
    let cleanup: (() => void) | null = null;

    const start = async () => {
      try {
        const THREE = await import("three");
        const { FBXLoader } = await import("three/addons/loaders/FBXLoader.js");
        const { RoomEnvironment } = await import(
          "three/addons/environments/RoomEnvironment.js"
        );
        if (cancelled || !hostRef.current) return;
        cleanup = await build(THREE, FBXLoader, RoomEnvironment);
        if (cancelled) cleanup();
      } catch {
        cleanup?.();
        if (!cancelled) setGone(true);
      }
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const build = async (THREE: any, FBXLoader: any, RoomEnvironment: any) => {
      const el = hostRef.current!;
      const stage = createStage(THREE, RoomEnvironment, el, 1.5);
      const { renderer, scene, camera, canvas } = stage;
      canvas.style.cursor = "grab";
      canvas.style.touchAction = "pan-y";

      const manager = new THREE.LoadingManager();
      manager.setURLModifier((url: string) =>
        /normal\./i.test(String(url)) ? PIXEL : url,
      );
      const loader = new FBXLoader(manager);

      const group = new THREE.Group();
      scene.add(group);

      // Single pen, loaded once up front: parse + textures happen before
      // first paint, never mid-scroll.
      const object = await loader.loadAsync(PEN.src);
      if (cancelled) {
        stage.dispose();
        return () => {};
      }
      await stage.skin(PEN.skin, object);
      if (cancelled) {
        stage.dispose();
        return () => {};
      }
      object.rotation.z = PEN.tilt;
      const box = new THREE.Box3().setFromObject(object);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      object.position.sub(center);
      group.add(object);
      // 0.70 fill keeps the whole pen inside the frame at every drift
      // extreme, ends included.
      let baseScale = stage.fitScale(size, camera.aspect, 0.7);
      // Eased toward baseScale every frame: the glide changes the frame
      // aspect, and stepping the pen size discretely reads as a pop.
      let scaleCur = baseScale;
      group.scale.setScalar(scaleCur);

      const reduced = prefersReducedMotion();
      let lastT = performance.now();
      let raf = 0;
      let spin = 0.5;
      let yawUser = 0;
      let pitchUser = 0;
      let tiltX = 0;
      let tiltY = 0;
      let dragging = false;
      let lastX = 0;
      let lastY = 0;
      let docked = false;
      const geo = { x: 0, y: 0, w: 0, h: 0 };
      // Compositor-friendly stage: position with transform, never
      // left/top, and skip writes that did not change.
      el.style.willChange = "transform";
      const written = { x: "", y: "", w: "", h: "" };

      // Page height changes only with layout (resize, late fonts), never
      // with scrolling — cache it instead of forcing layout per frame.
      let pageMax = 0;
      const refreshPageMax = () => {
        pageMax = document.documentElement.scrollHeight - window.innerHeight;
      };
      refreshPageMax();

      // Pen-rest geometry, document-relative so the per-frame viewport
      // position is pure arithmetic (scrollY is layout-free). Refreshed
      // on layout events, never in the hot loop.
      const restBox = { top: -1, left: 0, w: 0, h: 0 };
      const refreshRestBox = () => {
        const d = dockRef.current;
        if (!d) {
          restBox.top = -1;
          return;
        }
        const r = d.getBoundingClientRect();
        if (r.width <= 0) {
          restBox.top = -1;
          return;
        }
        restBox.top = r.top + window.scrollY;
        restBox.left = r.left;
        restBox.w = r.width;
        restBox.h = r.height;
      };
      refreshRestBox();

      const pageProgress = () =>
        pageMax > 0 ? Math.min(1, Math.max(0, window.scrollY / pageMax)) : 0;

      // Travel rect (right rail) vs rest rect (pen rest zone). Returns
      // the target geometry and whether the rest owns the stage.
      // Thresholds have hysteresis: entering needs the panel well inside
      // the viewport, leaving needs it clearly out — hovering at the edge
      // never flaps between the two layers.
      const targetGeo = () => {
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        if (restBox.top >= 0) {
          const top = restBox.top - window.scrollY;
          const bottom = top + restBox.h;
          const inside = docked
            ? !(top > vh * 0.85 || bottom < vh * 0.15)
            : top < vh * 0.7 && bottom > vh * 0.3;
          if (inside) {
            return {
              docked: true,
              x: restBox.left,
              y: top,
              w: restBox.w,
              h: restBox.h,
            };
          }
        }
        const w = Math.min(400, Math.max(240, vw * 0.26));
        const h = (w * 4) / 3;
        const margin = Math.min(56, Math.max(8, vw * 0.04));
        return { docked: false, x: vw - margin - w, y: vh / 2 - h / 2, w, h };
      };
      // Last geometry target, for glide-settled checks.
      const tgt = { x: 0, y: 0, w: 0, h: 0 };
      // One layer forever: the stage stays behind page content and the
      // rest zone is transparent, so docking needs no z cut and no fade —
      // the pen simply glides in (passing behind the dark lineup zone on
      // the way reads as flying behind the set). Pointer events alone
      // toggle: the transparent zones let clicks fall through to the
      // canvas while docked.
      el.style.zIndex = "-1";
      const applyGeo = (instant: boolean) => {
        const t = targetGeo();
        tgt.x = t.x;
        tgt.y = t.y;
        tgt.w = t.w;
        tgt.h = t.h;
        if (t.docked !== docked) {
          docked = t.docked;
          el.style.pointerEvents = docked ? "auto" : "none";
          if (!cancelled) onDock(docked);
        }
        if (instant) {
          geo.x = t.x;
          geo.y = t.y;
          geo.w = t.w;
          geo.h = t.h;
        } else {
          // Slow glide so the flight between rail and rest reads as travel.
          const k = 0.06;
          geo.x += (t.x - geo.x) * k;
          geo.y += (t.y - geo.y) * k;
          geo.w += (t.w - geo.w) * k;
          geo.h += (t.h - geo.h) * k;
        }
        // Position glides sub-pixel (compositor-only transform); size
        // steps whole pixels (width/height force layout, and 1px steps are
        // invisible mid-flight).
        const sx = geo.x.toFixed(1);
        const sy = geo.y.toFixed(1);
        const sw = Math.round(geo.w).toFixed(0);
        const sh = Math.round(geo.h).toFixed(0);
        if (sx !== written.x || sy !== written.y) {
          written.x = sx;
          written.y = sy;
          el.style.transform = `translate3d(${sx}px, ${sy}px, 0)`;
        }
        if (sw !== written.w || sh !== written.h) {
          written.w = sw;
          written.h = sh;
          el.style.width = `${sw}px`;
          el.style.height = `${sh}px`;
        }
      };

      // Vertical drift stays well inside the frame: full travel at the
      // page ends plus the idle bob must never push a pen tip past the
      // canvas edge (the fit leaves ~14% headroom per side).
      const poseTravel = (p: number, t: number) => ({
        ry: 0.5 + p * Math.PI * 4 + spin,
        rx: -0.1 + Math.sin(p * Math.PI * 2) * 0.18,
        x: Math.sin(p * Math.PI * 3) * 0.45,
        y: (0.5 - p) * 0.6 + (reduced ? 0 : Math.sin(t * 0.8) * 0.08),
      });
      const poseDock = () => ({
        ry: spin * 2 + yawUser + tiltY,
        rx: -0.1 + pitchUser + tiltX,
        x: 0,
        y: 0,
      });

      const blendRef = { v: 0 };
      const render = () => {
        const now = performance.now();
        const dt = Math.min((now - lastT) / 1000, 0.05);
        lastT = now;
        const t = now / 1000;
        if (!reduced) spin += dt * 0.22;
        applyGeo(false);
        const p = pageProgress();
        const a = poseTravel(p, t);
        const b = poseDock();
        // Ease between travel and docked poses over ~0.5s of blend.
        blendRef.v += ((docked ? 1 : 0) - blendRef.v) * (reduced ? 1 : Math.min(1, dt * 5));
        const k = blendRef.v;
        group.rotation.y = a.ry + (b.ry - a.ry) * k;
        group.rotation.x = a.rx + (b.rx - a.rx) * k;
        group.position.x = a.x * (1 - k);
        group.position.y = a.y * (1 - k);
        scaleCur += (baseScale - scaleCur) * (reduced ? 1 : Math.min(1, dt * 5));
        group.scale.setScalar(scaleCur);
        renderer.render(scene, camera);
      };
      // Idle render-skip: after 4s of no scroll or drag, park the loop
      // instead of burning frames on a settled scene.
      let lastActive = performance.now();
      let lastRefresh = 0;
      const loop = () => {
        if (!document.hidden) {
          const now = performance.now();
          const idle = now - lastActive > 4000 && !dragging;
          if (idle) lastT = now;
          else {
            // Layout heartbeat while active: late fonts or resizes move
            // the page under us; re-measure at most every 5s, never in
            // the per-frame path itself.
            if (now - lastRefresh > 5000) {
              lastRefresh = now;
              refreshPageMax();
              refreshRestBox();
            }
            render();
          }
        }
        raf = requestAnimationFrame(loop);
      };

      // Reduced motion runs no frame loop: scroll kicks a short-lived
      // glide that eases geometry/blend to their targets, then parks.
      let gliding = false;
      const kickGlide = () => {
        if (!reduced || gliding || cancelled) return;
        gliding = true;
        const tick = () => {
          if (cancelled) {
            gliding = false;
            return;
          }
          render();
          const settled =
            Math.abs(geo.x - tgt.x) +
              Math.abs(geo.y - tgt.y) +
              Math.abs(geo.w - tgt.w) +
              Math.abs(geo.h - tgt.h) <
              0.6 && Math.abs(blendRef.v - (docked ? 1 : 0)) < 0.01;
          if (settled) {
            gliding = false;
            return;
          }
          requestAnimationFrame(tick);
        };
        tick();
      };
      const onScroll = () => {
        if (cancelled) return;
        lastActive = performance.now();
        if (reduced) kickGlide();
      };
      const onResize = () => {
        if (cancelled) return;
        refreshPageMax();
        refreshRestBox();
        if (reduced) kickGlide();
      };
      const onPointerMove = (e: PointerEvent) => {
        if (!docked) return;
        const rect = canvas.getBoundingClientRect();
        const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        const ny = ((e.clientY - rect.top) / rect.height) * 2 - 1;
        if (dragging) {
          yawUser -= (e.clientX - lastX) * 0.008;
          pitchUser = Math.max(-0.6, Math.min(0.6, pitchUser + (e.clientY - lastY) * 0.005));
          lastX = e.clientX;
          lastY = e.clientY;
        } else {
          tiltY = nx * 0.22;
          tiltX = ny * 0.15;
        }
        if (reduced) render();
      };
      const onPointerDown = (e: PointerEvent) => {
        if (!docked) return;
        lastActive = performance.now();
        dragging = true;
        lastX = e.clientX;
        lastY = e.clientY;
        canvas.style.cursor = "grabbing";
        canvas.setPointerCapture(e.pointerId);
      };
      const onPointerUp = () => {
        dragging = false;
        canvas.style.cursor = "grab";
      };
      let camAspect = 0;
      // The glide resizes the stage every frame: keep the camera aspect
      // exact (cheap) but reallocate the GPU backing store only on
      // settled steps (expensive) — a few px of transient upscale is
      // invisible mid-flight and kills the resize stutter.
      let backingW = 0;
      let backingH = 0;
      const ro = new ResizeObserver(() => {
        const w = el.clientWidth;
        const h = el.clientHeight;
        if (!w || !h) return;
        const aspect = w / h;
        camera.aspect = aspect;
        camera.updateProjectionMatrix();
        if (
          Math.abs(w - backingW) >= 4 ||
          Math.abs(h - backingH) >= 4
        ) {
          backingW = w;
          backingH = h;
          renderer.setSize(w, h, false);
        }
        if (Math.abs(aspect - camAspect) > 0.05) {
          camAspect = aspect;
          baseScale = stage.fitScale(size, aspect, 0.7);
        }
      });
      ro.observe(el);

      // First frame: snap onto the travel rail with the pen already aboard.
      applyGeo(true);
      camAspect = el.clientWidth / Math.max(el.clientHeight, 1);
      camera.aspect = camAspect;
      camera.updateProjectionMatrix();
      canvas.addEventListener("pointermove", onPointerMove);
      canvas.addEventListener("pointerdown", onPointerDown);
      canvas.addEventListener("pointerup", onPointerUp);
      window.addEventListener("scroll", onScroll, { passive: true });
      window.addEventListener("resize", onResize);
      if (reduced) render();
      else loop();

      return () => {
        cancelAnimationFrame(raf);
        window.removeEventListener("scroll", onScroll);
        window.removeEventListener("resize", onResize);
        ro.disconnect();
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointerup", onPointerUp);
        object.traverse((child: any) => {
          if (child.isMesh) child.geometry?.dispose?.();
        });
        stage.dispose();
      };
    };

    // Short deferred start: one model only, so setup is fast — just let
    // first paint win the race before three.js downloads.
    const timer = window.setTimeout(() => {
      void start();
    }, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      cleanup?.();
    };
  }, [enabled, dockRef, onDock]);

  if (!enabled || gone) return null;
  return <div ref={hostRef} className="kv-companion" />;
}

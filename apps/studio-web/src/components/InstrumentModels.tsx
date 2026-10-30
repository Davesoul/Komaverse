import { useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "./Reveal";
import { PIXEL, createStage } from "../lib/models3d";

/* Instrument section lineup: the rest of the set (micron pen set + studio
 * pen) living permanently inside the dock panel. The traveling tablet pen
 * (ScrollCompanion) rests in the adjacent rest zone beside them — never on
 * top. One small stage, slow turntable, renders only while visible;
 * models load when the section nears the viewport so first paint costs
 * nothing. Drag to turn. Any failure removes it quietly. */
const LINEUP = [
  { src: "/models/pin.fbx", skin: "pin" },
  { src: "/models/stylo.fbx", skin: "studio" },
] as const;

export default function InstrumentModels() {
  const hostRef = useRef<HTMLDivElement>(null);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let cancelled = false;
    let cleanup: (() => void) | null = null;
    let started = false;

    const start = async () => {
      if (started || cancelled || !hostRef.current) return;
      started = true;
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
      const stage = createStage(THREE, RoomEnvironment, el, 1.25);
      const { renderer, scene, camera, canvas } = stage;
      canvas.style.cursor = "grab";
      canvas.style.touchAction = "pan-y";

      const manager = new THREE.LoadingManager();
      manager.setURLModifier((url: string) =>
        /normal\./i.test(String(url)) ? PIXEL : url,
      );
      const loader = new FBXLoader(manager);

      // A front fill so graphite-black bodies read against the dark dock.
      const fill = new THREE.DirectionalLight(0xffffff, 1.4);
      fill.position.set(0, 1, 6);
      scene.add(fill);

      // Load both pieces in parallel, normalize to a shared height, and
      // set them side by side so the pair reads as one lineup. Each piece
      // joins the scene the moment it is ready — the micron set never
      // waits on the 2MB studio pen.
      const group = new THREE.Group();
      scene.add(group);
      let placed = 0;
      // Side-by-side framing derived from the live aspect: the dock zone
      // is portrait, so offsets are a fraction of the visible half-width
      // and both pieces always sit fully inside the frame.
      const layout = () => {
        const halfH = Math.tan(THREE.MathUtils.degToRad(14)) * 7;
        const halfW = halfH * camera.aspect;
        const off = halfW * 0.42;
        const H = Math.min(2.7, halfH * 2 * 0.82);
        group.children.forEach((child: any, i: number) => {
          const h = child.userData.nativeH || 1;
          const s = H / h;
          child.scale.setScalar(s);
          child.position.set(
            (i === 0 ? -off : off) - child.userData.cx * s,
            -child.userData.cy * s,
            -child.userData.cz * s,
          );
        });
      };
      const place = async (spec: (typeof LINEUP)[number], i: number) => {
        const object = await loader.loadAsync(spec.src);
        if (cancelled) return;
        await stage.skin(spec.skin, object);
        if (cancelled) return;
        const box = new THREE.Box3().setFromObject(object);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        object.userData.nativeH = Math.max(size.x, size.y, size.z, 0.001);
        object.userData.cx = center.x;
        object.userData.cy = center.y;
        object.userData.cz = center.z;
        object.rotation.y = i === 0 ? 0.5 : -0.4;
        group.add(object);
        placed += 1;
        canvas.dataset.models = `${spec.src.split("/").pop()}:${Math.round(object.userData.nativeH * 100)}:${placed}`;
        if (cancelled) return;
        layout();
        // Reduced-motion has no frame loop: paint the arrival, otherwise
        // a model that lands after the first static frame never appears.
        if (reduced && visible) render(0);
      };
      void Promise.all(LINEUP.map((spec, i) => place(spec, i))).then(() => {
        if (!cancelled && placed === 0) setGone(true);
      });

      const reduced = prefersReducedMotion();
      let lastT = performance.now();
      let simT = 0;
      let raf = 0;
      let spin = 0;
      let yawUser = 0;
      let dragging = false;
      let lastX = 0;
      let visible = true;

      const render = (t: number) => {
        group.children.forEach((child: any, i: number) => {
          child.rotation.y = (i === 0 ? 0.5 : -0.4) + spin + yawUser;
          child.position.y = reduced ? 0 : Math.sin(t * 0.7 + i * 2.1) * 0.06;
        });
        renderer.render(scene, camera);
      };

      const loop = () => {
        const now = performance.now();
        if (!document.hidden && visible) {
          const dt = Math.min((now - lastT) / 1000, 0.05);
          if (!reduced) spin += dt * 0.25;
          simT += dt;
          render(simT);
        }
        lastT = now;
        raf = requestAnimationFrame(loop);
      };

      const ro = new ResizeObserver(() => {
        const w = el.clientWidth;
        const h = el.clientHeight;
        if (!w || !h) return;
        renderer.setSize(w, h, false);
        const aspect = w / h;
        camera.aspect = aspect;
        camera.updateProjectionMatrix();
        if (!cancelled) layout();
      });
      ro.observe(el);
      camera.aspect = el.clientWidth / Math.max(el.clientHeight, 1);
      camera.updateProjectionMatrix();

      // Run only while the section is on screen; preload just before.
      const io = new IntersectionObserver(
        (entries) => {
          visible = entries.some((e) => e.isIntersecting);
          if (visible && reduced) render(0);
        },
        { rootMargin: "800px 0px" },
      );
      io.observe(el);

      const onPointerDown = (e: PointerEvent) => {
        dragging = true;
        lastX = e.clientX;
        canvas.style.cursor = "grabbing";
        canvas.setPointerCapture(e.pointerId);
      };
      const onPointerMove = (e: PointerEvent) => {
        if (!dragging) return;
        yawUser -= (e.clientX - lastX) * 0.008;
        lastX = e.clientX;
        if (reduced && visible) render(0);
      };
      const onPointerUp = () => {
        dragging = false;
        canvas.style.cursor = "grab";
      };
      canvas.addEventListener("pointerdown", onPointerDown);
      canvas.addEventListener("pointermove", onPointerMove);
      canvas.addEventListener("pointerup", onPointerUp);

      if (reduced) render(0);
      else loop();

      return () => {
        cancelAnimationFrame(raf);
        ro.disconnect();
        io.disconnect();
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerup", onPointerUp);
        group.children.forEach((child: any) => {
          child.traverse((c: any) => {
            if (c.isMesh) c.geometry?.dispose?.();
          });
        });
        stage.dispose();
      };
    };

    // Preload when the instrument section nears the viewport — zero cost
    // at first paint, ready before the traveler docks.
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          io.disconnect();
          void start();
        }
      },
      { rootMargin: "900px 0px" },
    );
    io.observe(host);

    return () => {
      cancelled = true;
      io.disconnect();
      cleanup?.();
    };
  }, []);

  if (gone) return null;
  return <div ref={hostRef} className="lv-dock-models" aria-hidden="true" />;
}

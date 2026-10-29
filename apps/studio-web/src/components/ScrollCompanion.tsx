import { useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "./Reveal";

/* Scroll companion: one fixed 3D drawing tool that travels the whole
 * landing page and docks into the instrument panel when it is on screen.
 * Travel pose (drift, turns, model-per-third) follows page scroll; docked
 * pose is a slow turntable the visitor can drag. Authored PBR textures
 * throughout (color/roughness/metal; normals skipped by design), lit in
 * code (paper key + red rim + room environment). Frustum-fit scaling per
 * model and aspect so nothing ever clips a frame edge. Pointer-transparent
 * while traveling, interactive while docked; hidden on narrow screens.
 * Any failure removes it quietly. */
const MODELS = [
  { src: "/models/pin.fbx", name: "Ballpoint", skin: "pin", tilt: -0.12 },
  { src: "/models/wacom-pen.fbx", name: "Stylus", skin: "wacom", tilt: -0.3 },
  { src: "/models/stylo.fbx", name: "Studio pen", skin: "studio", tilt: -0.3 },
] as const;

// Normal maps are skipped deliberately (micro-relief invisible at this
// size, ~10MB saved): the loader is pointed at a pixel instead, and the
// resulting map is dropped during conversion — no 404s, no bad shading.
const PIXEL = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";

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
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
      renderer.setSize(el.clientWidth, el.clientHeight, false);
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      renderer.domElement.style.display = "block";
      renderer.domElement.style.cursor = "grab";
      renderer.domElement.style.touchAction = "pan-y";
      el.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 50);
      camera.position.set(0, 0.2, 7);

      const pmrem = new THREE.PMREMGenerator(renderer);
      scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

      const key = new THREE.DirectionalLight(0xf4efe4, 3.0);
      key.position.set(3, 4, 5);
      scene.add(key);
      const rim = new THREE.DirectionalLight(0xff6a70, 1.8);
      rim.position.set(-4, 2, -3);
      scene.add(rim);
      scene.add(new THREE.HemisphereLight(0x4a4a55, 0x060608, 1.0));

      const maxAniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
      const manager = new THREE.LoadingManager();
      manager.setURLModifier((url: string) =>
        /normal\./i.test(String(url)) ? PIXEL : url,
      );
      const loader = new FBXLoader(manager);
      const texLoader = new THREE.TextureLoader();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const owned: any[] = [];
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const loadTex = (url: string, srgb: boolean): Promise<any | null> =>
        texLoader
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          .loadAsync(url)
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          .then((t: any) => {
            if (srgb) t.colorSpace = THREE.SRGBColorSpace;
            t.anisotropy = maxAniso;
            owned.push(t);
            return t;
          })
          .catch(() => null);

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const toStandard = (m: any) => {
        const std = new THREE.MeshStandardMaterial({
          color: m.map
            ? new THREE.Color(0xffffff)
            : (m.color?.clone?.() ?? new THREE.Color(0xffffff)),
          map: m.map ?? null,
          roughnessMap: m.roughnessMap ?? null,
          metalnessMap: m.metalnessMap ?? null,
          roughness: m.roughnessMap ? 1 : 0.45,
          metalness: m.metalnessMap ? 1 : 0.08,
          transparent: !!m.transparent,
          opacity: m.opacity ?? 1,
          alphaTest: m.alphaTest ?? 0,
          side: m.side ?? THREE.FrontSide,
          envMapIntensity: 1.0,
        });
        owned.push(std);
        m.dispose?.();
        return std;
      };

      // Studio fallback (models with no source textures): graphite black
      // fineliner body, ink-metal details — reads as a real pen.
      const graphite = new THREE.MeshStandardMaterial({
        color: 0x17171c,
        metalness: 0.35,
        roughness: 0.5,
        envMapIntensity: 0.9,
      });
      const inkMetal = new THREE.MeshStandardMaterial({
        color: 0x1a1a20,
        metalness: 0.9,
        roughness: 0.32,
        envMapIntensity: 1.1,
      });
      owned.push(graphite, inkMetal);

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const skin = async (kind: string, object: any) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const meshes: any[] = [];
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        object.traverse((child: any) => {
          if (child.isMesh) meshes.push(child);
        });
        if (kind === "studio" || meshes.length === 0) {
          if (meshes.length > 1) {
            const volumes = meshes.map(
              (m) =>
                new THREE.Box3().setFromObject(m).getSize(new THREE.Vector3()).length(),
            );
            const biggest = volumes.indexOf(Math.max(...volumes));
            meshes.forEach((m, i) => {
              m.material = i === biggest ? graphite : inkMetal;
            });
          } else {
            meshes.forEach((m) => {
              m.material = inkMetal;
            });
          }
          return;
        }
        if (kind === "pin") {
          // The micron set resolves its color map itself; roughness and
          // metal arrive through FBX properties three.js ignores, so they
          // are bound explicitly — deterministic, no reliance on FBX quirks.
          for (const mesh of meshes) {
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
            const next = await Promise.all(
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              mats.map(async (m: any) => {
                if (!m.map) m.map = await loadTex("/models/canc_color.png", true);
                if (!m.roughnessMap)
                  m.roughnessMap = await loadTex("/models/canc_roughness.png", false);
                if (!m.metalnessMap)
                  m.metalnessMap = await loadTex("/models/canc_metal.png", false);
                return toStandard(m);
              }),
            );
            mesh.material = Array.isArray(mesh.material) ? next : next[0];
          }
          // The micron set ships as a scattered 3D arrangement, deep in Z
          // (hostile to turntables and near-plane clipping). Stand every
          // long piece upright, then stack the pieces in a vertical
          // lineup: narrow, rotation-safe, upright from the first frame.
          // Placement is always derived from measured centers, so any
          // swing from rotating about the mesh origin is absorbed.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const measure = () => {
            object.updateMatrixWorld(true);
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            return meshes.map((mesh: any) => {
              const b = new THREE.Box3().setFromObject(mesh);
              return {
                mesh,
                size: b.getSize(new THREE.Vector3()),
                center: b.getCenter(new THREE.Vector3()),
              };
            });
          };
          measure().forEach((it) => {
            const longest = Math.max(it.size.x, it.size.y, it.size.z);
            if (it.size.x >= longest * 0.9 && it.size.x > it.size.y * 1.2) {
              it.mesh.rotation.z -= Math.PI / 2;
            } else if (it.size.z >= longest * 0.9 && it.size.z > it.size.y * 1.2) {
              it.mesh.rotation.x += Math.PI / 2;
            }
          });
          const items = measure();
          const gap =
            Math.max(...items.map((it) => Math.max(it.size.x, it.size.y, it.size.z))) *
            0.1;
          const total =
            items.reduce((a, it) => a + it.size.y, 0) + gap * (items.length - 1);
          let cursor = total / 2;
          items.forEach((it) => {
            it.mesh.position.y += cursor - it.size.y / 2 - it.center.y;
            it.mesh.position.x += -it.center.x;
            it.mesh.position.z += -it.center.z;
            cursor -= it.size.y + gap;
          });
          object.updateMatrixWorld(true);
          return;
        }
        // "wacom": the FBX ships untextured plus a PBR set per part, matched
        // by the FBX material names (PenTop, PenUpperBody, …).
        for (const mesh of meshes) {
          const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
          const next = await Promise.all(
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            mats.map(async (m: any) => {
              const base = `/models/wacom/${m.name}_`;
              const [map, roughnessMap, metalnessMap] = await Promise.all([
                loadTex(`${base}BaseColor.png`, true),
                loadTex(`${base}Roughness.png`, false),
                loadTex(`${base}Metallic.png`, false),
              ]);
              if (map) m.map = map;
              if (roughnessMap) m.roughnessMap = roughnessMap;
              if (metalnessMap) m.metalnessMap = metalnessMap;
              return toStandard(m);
            }),
          );
          mesh.material = Array.isArray(mesh.material) ? next : next[0];
        }
      };

      const group = new THREE.Group();
      scene.add(group);

      const fitScale = (
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        size: any,
        aspect: number,
      ) => {
        const halfH = Math.tan(THREE.MathUtils.degToRad(14)) * 7;
        const halfW = halfH * aspect;
        return Math.min((2 * halfW * 0.72) / size.x, (2 * halfH * 0.72) / size.y);
      };

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const cache = new Map<number, { object: any; baseScale: number; size: any }>();
      const loadModel = async (i: number, aspect: number) => {
        const hit = cache.get(i);
        if (hit) return hit;
        const object = await loader.loadAsync(MODELS[i].src);
        await skin(MODELS[i].skin, object);
        // Stand the set upright for the portrait stage, then measure the
        // posed bounds so nothing overflows the frame edges.
        object.rotation.z = MODELS[i].tilt;
        const box = new THREE.Box3().setFromObject(object);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        object.position.sub(center);
        const entry = { object, baseScale: fitScale(size, aspect), size };
        cache.set(i, entry);
        return entry;
      };
      const refit = (aspect: number) => {
        cache.forEach((entry) => {
          entry.baseScale = fitScale(entry.size, aspect);
        });
      };

      const reduced = prefersReducedMotion();
      const clock = new THREE.Clock();
      let raf = 0;
      let current = -1;
      let spin = 0.5;
      let yawUser = 0;
      let pitchUser = 0;
      let tiltX = 0;
      let tiltY = 0;
      let dragging = false;
      let lastX = 0;
      let lastY = 0;
      let swapAt = -1;
      let pending = -1;
      let blend = 0;
      let docked = false;
      const geo = { x: 0, y: 0, w: 0, h: 0 };
      const canvas = renderer.domElement;
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

      // Dock panel geometry, document-relative so the per-frame viewport
      // position is pure arithmetic (scrollY is layout-free). Refreshed
      // on layout events, never in the hot loop.
      const dockBox = { top: -1, left: 0, w: 0, h: 0 };
      const refreshDockBox = () => {
        const d = dockRef.current;
        if (!d) {
          dockBox.top = -1;
          return;
        }
        const r = d.getBoundingClientRect();
        if (r.width <= 0) {
          dockBox.top = -1;
          return;
        }
        dockBox.top = r.top + window.scrollY;
        dockBox.left = r.left;
        dockBox.w = r.width;
        dockBox.h = r.height;
      };
      refreshDockBox();

      const pageProgress = () =>
        pageMax > 0 ? Math.min(1, Math.max(0, window.scrollY / pageMax)) : 0;

      // Thirds with hysteresis: hovering at a boundary never flickers
      // between models — a switch needs 0.02 of progress past the line.
      const thirdFor = (p: number, cur: number) => {
        const raw = Math.min(MODELS.length - 1, Math.floor(p * MODELS.length));
        if (raw === cur || cur < 0) return raw;
        const b = raw > cur ? raw / MODELS.length : (raw + 1) / MODELS.length;
        const past = raw > cur ? p - b : b - p;
        return past < 0.02 ? cur : raw;
      };

      const show = async (i: number) => {
        if (i === current) return;
        try {
          const entry = await loadModel(i, camera.aspect);
          if (cancelled) return;
          if (current < 0) {
            // First paint: place directly, no handoff from nothing.
            group.add(entry.object);
            group.scale.setScalar(entry.baseScale);
            current = i;
            if (reduced && !cancelled) kickGlide();
            return;
          }
          pending = i;
          swapAt = performance.now();
          if (reduced && !cancelled) kickGlide();
        } catch {
          // A model that never loads is skipped: release the transition
          // so the current model (if any) keeps traveling.
          if (pending === i) {
            pending = -1;
            swapAt = -1;
          }
        }
      };

      // Travel rect (right rail) vs dock rect (instrument panel). Returns
      // the target geometry and whether the panel owns the stage.
      const targetGeo = () => {
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        if (dockBox.top >= 0) {
          const top = dockBox.top - window.scrollY;
          const bottom = top + dockBox.h;
          if (top < vh * 0.7 && bottom > vh * 0.3) {
            return {
              docked: true,
              x: dockBox.left,
              y: top,
              w: dockBox.w,
              h: dockBox.h,
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
      const applyGeo = (instant: boolean) => {
        const t = targetGeo();
        tgt.x = t.x;
        tgt.y = t.y;
        tgt.w = t.w;
        tgt.h = t.h;
        if (t.docked !== docked) {
          docked = t.docked;
          el.style.pointerEvents = docked ? "auto" : "none";
          // Above the opaque dock panel while docked, behind page content
          // while traveling so text stays readable.
          el.style.zIndex = docked ? "2" : "-1";
          if (!cancelled) onDock(docked);
        }
        if (instant) {
          geo.x = t.x;
          geo.y = t.y;
          geo.w = t.w;
          geo.h = t.h;
        } else {
          // Slow glide so the flight between rail and dock reads as travel.
          const k = 0.06;
          geo.x += (t.x - geo.x) * k;
          geo.y += (t.y - geo.y) * k;
          geo.w += (t.w - geo.w) * k;
          geo.h += (t.h - geo.h) * k;
        }
        const sx = geo.x.toFixed(1);
        const sy = geo.y.toFixed(1);
        const sw = geo.w.toFixed(1);
        const sh = geo.h.toFixed(1);
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

      const poseTravel = (p: number, t: number) => ({
        ry: 0.5 + p * Math.PI * 4 + spin,
        rx: -0.1 + Math.sin(p * Math.PI * 2) * 0.18,
        x: Math.sin(p * Math.PI * 3) * 0.45,
        y: (0.5 - p) * 0.9 + (reduced ? 0 : Math.sin(t * 0.8) * 0.1),
      });
      const poseDock = () => ({
        ry: spin * 2 + yawUser + tiltY,
        rx: -0.1 + pitchUser + tiltX,
        x: 0,
        y: 0,
      });

      const render = () => {
        const dt = Math.min(clock.getDelta(), 0.05);
        const t = clock.elapsedTime;
        if (!reduced) spin += dt * 0.22;
        applyGeo(false);
        blend += ((docked ? 1 : 0) - blend) * (reduced ? 1 : Math.min(1, dt * 5));
        const p = pageProgress();
        const want = thirdFor(p, current);
        if (want !== current && pending < 0) void show(want);
        // Gentle handoff: breathe down to 65% with a soft extra turn and
        // back — never a vanish, never a whip.
        if (swapAt >= 0 && pending >= 0) {
          const k = Math.min((performance.now() - swapAt) / 650, 1);
          spin += reduced ? 0 : dt * 2.2 * Math.sin(k * Math.PI);
          if (k >= 0.5 && current !== pending && cache.has(pending)) {
            const entry = cache.get(pending)!;
            if (current >= 0) group.remove(group.children[0]);
            group.add(entry.object);
            current = pending;
          }
          const entry = cache.get(current >= 0 ? current : pending);
          const s = entry
            ? entry.baseScale * (1 - 0.35 * Math.sin(k * Math.PI))
            : 0.0001;
          group.scale.setScalar(Math.max(s, 0.0001));
          if (k >= 1 && cache.has(current)) {
            group.scale.setScalar(cache.get(current)!.baseScale);
            swapAt = -1;
            pending = -1;
          }
        } else if (current >= 0 && cache.has(current)) {
          group.scale.setScalar(cache.get(current)!.baseScale);
        }
        const a = poseTravel(p, t);
        const b = poseDock();
        group.rotation.y = a.ry + (b.ry - a.ry) * blend;
        group.rotation.x = a.rx + (b.rx - a.rx) * blend;
        group.position.x = a.x * (1 - blend);
        group.position.y = a.y * (1 - blend);
        renderer.render(scene, camera);
      };
      // Idle render-skip: after 4s of no scroll, drag, or handoff, park
      // the loop instead of burning frames on a settled scene.
      let lastActive = performance.now();
      let lastRefresh = 0;
      const loop = () => {
        if (!document.hidden) {
          const now = performance.now();
          const idle =
            now - lastActive > 4000 && swapAt < 0 && !dragging;
          if (idle) clock.getDelta();
          else {
            // Layout heartbeat while active: late fonts or resizes move
            // the page under us; re-measure at most every 5s, never in
            // the per-frame path itself.
            if (now - lastRefresh > 5000) {
              lastRefresh = now;
              refreshPageMax();
              refreshDockBox();
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
              0.6 &&
            Math.abs(blend - (docked ? 1 : 0)) < 0.01 &&
            pending < 0;
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
        refreshDockBox();
        if (reduced) kickGlide();
      };
      // Scroll-path preload: parse and texture the other models shortly
      // after first paint (staggered, off the scroll path), so crossing a
      // third mid-fling never runs FBX parsing on the main thread.
      const warm = () => {
        let i = 1;
        const step = () => {
          if (cancelled || i >= MODELS.length) return;
          loadModel(i, camera.aspect)
            .catch(() => {})
            .finally(() => {
              i += 1;
              window.setTimeout(step, 800);
            });
        };
        window.setTimeout(step, 2500);
      };
      const renderOnce = () => {
        applyGeo(true);
        blend = docked ? 1 : 0;
        const p = pageProgress();
        const want = thirdFor(p, current);
        if (want !== current) {
          void show(want);
          return;
        }
        const a = poseTravel(p, 0);
        const b = poseDock();
        group.rotation.y = a.ry + (b.ry - a.ry) * blend;
        group.rotation.x = a.rx + (b.rx - a.rx) * blend;
        group.position.x = a.x * (1 - blend);
        group.position.y = a.y * (1 - blend);
        if (current >= 0 && cache.has(current))
          group.scale.setScalar(cache.get(current)!.baseScale);
        renderer.render(scene, camera);
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
        if (reduced) renderOnce();
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
      const ro = new ResizeObserver(() => {
        const w = el.clientWidth;
        const h = el.clientHeight;
        if (!w || !h) return;
        renderer.setSize(w, h, false);
        const aspect = w / h;
        camera.aspect = aspect;
        camera.updateProjectionMatrix();
        if (Math.abs(aspect - camAspect) > 0.05) {
          camAspect = aspect;
          refit(aspect);
        }
        if (reduced) renderOnce();
      });
      ro.observe(el);

      // First frame: snap onto the travel rail, load the opening model.
      applyGeo(true);
      camAspect = el.clientWidth / Math.max(el.clientHeight, 1);
      camera.aspect = camAspect;
      camera.updateProjectionMatrix();
      canvas.addEventListener("pointermove", onPointerMove);
      canvas.addEventListener("pointerdown", onPointerDown);
      canvas.addEventListener("pointerup", onPointerUp);
      window.addEventListener("scroll", onScroll, { passive: true });
      window.addEventListener("resize", onResize);
      if (reduced) {
        void show(0).then(() => {
          if (!cancelled) renderOnce();
        });
        warm();
      } else {
        loop();
        warm();
      }

      return () => {
        cancelAnimationFrame(raf);
        window.removeEventListener("scroll", onScroll);
        window.removeEventListener("resize", onResize);
        ro.disconnect();
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointerup", onPointerUp);
        cache.forEach(({ object }) => {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          object.traverse((child: any) => {
            if (child.isMesh) child.geometry?.dispose?.();
          });
        });
        owned.forEach((o) => o.dispose?.());
        pmrem.dispose();
        renderer.dispose();
        canvas.remove();
      };
    };

    // Deferred start: let first paint and hero videos win the first
    // second instead of racing three.js and FBX parsing against them.
    const timer = window.setTimeout(() => {
      void start();
    }, 1200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      cleanup?.();
    };
  }, [enabled, dockRef, onDock]);

  if (!enabled || gone) return null;
  return <div ref={hostRef} className="kv-companion" />;
}

import { useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "./Reveal";
import { Skeleton } from "./ui";

/* Studio pen viewer: the stylo.fbx model lit in code (paper key + red rim),
 * floating with idle motion, drag-to-rotate, pointer parallax. Three.js and
 * the model load only when the viewer nears the viewport; reduced motion
 * renders one static frame. Any failure degrades to a quiet fallback. */
export default function PenViewer() {
  const hostRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");

  useEffect(() => {
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
        if (!cancelled) setState("ready");
        else cleanup();
      } catch {
        cleanup?.();
        if (!cancelled) setState("failed");
      }
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const build = async (THREE: any, FBXLoader: any, RoomEnvironment: any) => {
      const el = hostRef.current!;
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(el.clientWidth, el.clientHeight, false);
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      renderer.domElement.style.display = "block";
      renderer.domElement.style.cursor = "grab";
      renderer.domElement.style.touchAction = "pan-y";
      el.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(
        28,
        el.clientWidth / Math.max(el.clientHeight, 1),
        0.1,
        50,
      );
      camera.position.set(0, 0.4, 7);

      const pmrem = new THREE.PMREMGenerator(renderer);
      scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

      const key = new THREE.DirectionalLight(0xf4efe4, 2.4);
      key.position.set(3, 4, 5);
      scene.add(key);
      const rim = new THREE.DirectionalLight(0xff2a33, 3.0);
      rim.position.set(-4, 2, -3);
      scene.add(rim);
      scene.add(new THREE.HemisphereLight(0x35353f, 0x060608, 0.6));

      const object = await new FBXLoader().loadAsync("/models/stylo.fbx");
      if (cancelled) return () => {};

      // Studio materials: largest part in paper lacquer, the rest ink metal.
      const meshes: any[] = [];
      object.traverse((child: any) => {
        if (child.isMesh) meshes.push(child);
      });
      const paper = new THREE.MeshStandardMaterial({
        color: 0xf4efe4,
        metalness: 0.15,
        roughness: 0.3,
        envMapIntensity: 1.0,
      });
      const inkMetal = new THREE.MeshStandardMaterial({
        color: 0x1a1a20,
        metalness: 0.9,
        roughness: 0.32,
        envMapIntensity: 1.1,
      });
      if (meshes.length > 1) {
        const volumes = meshes.map(
          (m) => new THREE.Box3().setFromObject(m).getSize(new THREE.Vector3()).length(),
        );
        const biggest = volumes.indexOf(Math.max(...volumes));
        meshes.forEach((m, i) => {
          m.material = i === biggest ? paper : inkMetal;
        });
      } else {
        meshes.forEach((m) => {
          m.material = inkMetal;
        });
      }

      const group = new THREE.Group();
      group.add(object);
      const box = new THREE.Box3().setFromObject(group);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      object.position.sub(center);
      group.scale.setScalar(2.4 / Math.max(size.x, size.y, size.z));
      group.rotation.z = -0.42;
      group.rotation.x = -0.12;
      scene.add(group);

      const reduced = prefersReducedMotion();
      let raf = 0;
      let yaw = 0.6;
      let pitch = 0;
      let tiltX = 0;
      let tiltY = 0;
      let goalTiltX = 0;
      let goalTiltY = 0;
      let dragging = false;
      let lastX = 0;
      let lastY = 0;
      let idleTimer = 0;
      let auto = true;
      const clock = new THREE.Clock();
      const canvas = renderer.domElement;

      const render = () => {
        const dt = Math.min(clock.getDelta(), 0.05);
        const t = clock.elapsedTime;
        if (auto && !dragging) yaw += dt * 0.35;
        tiltX += (goalTiltX - tiltX) * Math.min(1, dt * 4);
        tiltY += (goalTiltY - tiltY) * Math.min(1, dt * 4);
        group.rotation.y = yaw + tiltY;
        group.rotation.x = -0.12 + pitch + tiltX;
        group.position.y = Math.sin(t * 0.9) * 0.07;
        renderer.render(scene, camera);
      };
      const loop = () => {
        render();
        raf = requestAnimationFrame(loop);
      };

      const onPointerMove = (e: PointerEvent) => {
        const rect = canvas.getBoundingClientRect();
        const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        const ny = ((e.clientY - rect.top) / rect.height) * 2 - 1;
        if (dragging) {
          yaw -= (e.clientX - lastX) * 0.008;
          pitch = Math.max(-0.6, Math.min(0.6, pitch + (e.clientY - lastY) * 0.005));
          lastX = e.clientX;
          lastY = e.clientY;
        } else {
          goalTiltY = nx * 0.22;
          goalTiltX = ny * 0.15;
        }
      };
      const onPointerDown = (e: PointerEvent) => {
        dragging = true;
        auto = false;
        lastX = e.clientX;
        lastY = e.clientY;
        canvas.style.cursor = "grabbing";
        canvas.setPointerCapture(e.pointerId);
        window.clearTimeout(idleTimer);
      };
      const onPointerUp = () => {
        dragging = false;
        canvas.style.cursor = "grab";
        window.clearTimeout(idleTimer);
        idleTimer = window.setTimeout(() => {
          auto = true;
        }, 2500);
      };
      const ro = new ResizeObserver(() => {
        const w = el.clientWidth;
        const h = el.clientHeight;
        if (!w || !h) return;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        if (reduced) renderer.render(scene, camera);
      });
      ro.observe(el);

      if (reduced) {
        renderer.render(scene, camera);
      } else {
        canvas.addEventListener("pointermove", onPointerMove);
        canvas.addEventListener("pointerdown", onPointerDown);
        canvas.addEventListener("pointerup", onPointerUp);
        loop();
      }

      return () => {
        cancelAnimationFrame(raf);
        window.clearTimeout(idleTimer);
        ro.disconnect();
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointerup", onPointerUp);
        group.traverse((child: any) => {
          if (child.isMesh) {
            child.geometry?.dispose?.();
          }
        });
        paper.dispose();
        inkMetal.dispose();
        pmrem.dispose();
        renderer.dispose();
        canvas.remove();
      };
    };

    const io = new IntersectionObserver(
      (entries) => {
        if (!entries[0].isIntersecting) return;
        io.disconnect();
        void start();
      },
      { rootMargin: "400px" },
    );
    io.observe(host);
    return () => {
      cancelled = true;
      io.disconnect();
      cleanup?.();
    };
  }, []);

  return (
    <div
      ref={hostRef}
      className="kv-panel-frame"
      role="img"
      aria-label="3D studio pen, drag to rotate"
      style={{
        position: "relative",
        aspectRatio: "16 / 9",
        minHeight: 300,
        background: "var(--kv-inset)",
        borderRadius: "var(--kv-radius)",
        overflow: "hidden",
      }}
    >
      {state === "loading" && (
        <div style={{ position: "absolute", inset: 16 }}>
          <Skeleton width="100%" height="100%" />
        </div>
      )}
      {state === "failed" && (
        <div
          className="kv-halftone"
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 13,
            color: "var(--kv-muted)",
          }}
        >
          3D unavailable on this device — the studio works the same.
        </div>
      )}
    </div>
  );
}

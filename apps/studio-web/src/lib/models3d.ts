/* Shared three.js stage for the landing 3D models: renderer/scene/camera
 * setup, authored PBR skins, and frustum-fit scaling so nothing clips.
 * Normal maps are skipped deliberately (micro-relief invisible at this
 * size, ~10MB saved): the loader is pointed at a pixel instead, and the
 * resulting map is dropped during conversion — no 404s, no bad shading.
 * Any failure is the caller's to handle quietly. */
/* eslint-disable @typescript-eslint/no-explicit-any */

export const PIXEL = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";

export type Stage = {
  renderer: any;
  scene: any;
  camera: any;
  canvas: HTMLCanvasElement;
  owned: any[];
  dispose: () => void;
  loadTex: (url: string, srgb: boolean) => Promise<any | null>;
  skin: (kind: string, object: any) => Promise<void>;
  fitScale: (size: any, aspect: number, fill?: number) => number;
};

export function createStage(
  THREE: any,
  RoomEnvironment: any,
  el: HTMLElement,
  pixelCap = 1.5,
): Stage {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, pixelCap));
  renderer.setSize(el.clientWidth, el.clientHeight, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.domElement.style.width = "100%";
  renderer.domElement.style.height = "100%";
  renderer.domElement.style.display = "block";
  el.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 50);
  camera.position.set(0, 0.2, 7);

  const owned: any[] = [];
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  owned.push(pmrem);

  const key = new THREE.DirectionalLight(0xf4efe4, 3.0);
  key.position.set(3, 4, 5);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xff6a70, 1.8);
  rim.position.set(-4, 2, -3);
  scene.add(rim);
  scene.add(new THREE.HemisphereLight(0x4a4a55, 0x060608, 1.0));

  const maxAniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  const texLoader = new THREE.TextureLoader();
  const loadTex = (url: string, srgb: boolean): Promise<any | null> =>
    texLoader
      .loadAsync(url)
      .then((t: any) => {
        if (srgb) t.colorSpace = THREE.SRGBColorSpace;
        t.anisotropy = maxAniso;
        owned.push(t);
        return t;
      })
      .catch(() => null);

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

  const skin = async (kind: string, object: any) => {
    const meshes: any[] = [];
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
      const measure = () => {
        object.updateMatrixWorld(true);
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
    if (kind === "holder") {
      // Manga dip-pen holder: only the G pen itself — the set's second
      // mesh is a glass vessel that is not part of the lineup. Wood is
      // dielectric, so the authored metalness map (white across the wood,
      // which chromed the shaft) is skipped: color + roughness carry the
      // lacquered look. The FBX references source-tree texture names that
      // don't exist on disk, so the maps bind unconditionally — a
      // conditional bind would keep the loader's broken slots. Then treat
      // the pen as one rigid piece: stand its longest axis upright and move
      // its center onto the local origin, so the turntable spins it in
      // place instead of orbiting it. (The caller still measures after.)
      const parts: any[] = [];
      object.traverse((child: any) => {
        if (child.isMesh) parts.push(child);
      });
      if (parts.length === 0) return;
      const isGlass = (mesh: any) => {
        const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        return mats.some((m: any) => /glass/i.test(String(m?.name ?? "")));
      };
      if (parts.some((mesh) => !isGlass(mesh))) {
        parts.filter(isGlass).forEach((mesh) => mesh.parent?.remove(mesh));
      }
      // The pen mesh also carries the set's ink vessel (container +
      // closure) as separate components, floating disconnected beside the
      // shaft — material filtering can't reach them. Keep the shaft and
      // whatever touches it (nib assembly); cut the disjoint vessel out
      // of the geometry.
      const cutVessel = (mesh: any) => {
        const g = mesh.geometry;
        const p = g?.attributes?.position;
        if (!p) return;
        const idx = g.index;
        const triCount = idx ? idx.count / 3 : p.count / 3;
        const findRoot = (parent: Map<number, number>, x: number) => {
          let r = x;
          while (parent.get(r) !== r) r = parent.get(r) ?? x;
          return r;
        };
        const keyOf = (vi: number) =>
          `${p.getX(vi).toFixed(2)},${p.getY(vi).toFixed(2)},${p.getZ(vi).toFixed(2)}`;
        const idOf = new Map<string, number>();
        const parent = new Map<number, number>();
        let next = 0;
        const compOfTri: number[] = [];
        for (let t = 0; t < triCount; t++) {
          const vs: number[] = [];
          for (let k = 0; k < 3; k++) {
            const vi = idx ? idx.getX(t * 3 + k) : t * 3 + k;
            const key = keyOf(vi);
            if (!idOf.has(key)) {
              idOf.set(key, next);
              parent.set(next, next);
              next++;
            }
            vs.push(idOf.get(key)!);
          }
          const r = findRoot(parent, vs[0]);
          for (const v of vs.slice(1)) parent.set(findRoot(parent, v), r);
          compOfTri.push(r);
        }
        for (let i = 0; i < compOfTri.length; i++) compOfTri[i] = findRoot(parent, compOfTri[i]);
        const boxes = new Map<number, any>();
        const v = new THREE.Vector3();
        compOfTri.forEach((r, t) => {
          if (!boxes.has(r)) boxes.set(r, new THREE.Box3());
          const bb = boxes.get(r);
          for (let k = 0; k < 3; k++) {
            const vi = idx ? idx.getX(t * 3 + k) : t * 3 + k;
            bb.expandByPoint(v.set(p.getX(vi), p.getY(vi), p.getZ(vi)));
          }
        });
        if (boxes.size < 2) return;
        let shaft = -1;
        let shaftLen = -1;
        boxes.forEach((bb, r) => {
          const len = bb.getSize(new THREE.Vector3()).length();
          if (len > shaftLen) {
            shaftLen = len;
            shaft = r;
          }
        });
        const shaftBox = boxes.get(shaft).clone();
        shaftBox.expandByScalar(Math.max(shaftLen * 0.01, 0.001));
        const keep = new Set<number>();
        boxes.forEach((bb, r) => {
          if (r === shaft || shaftBox.intersectsBox(bb)) keep.add(r);
        });
        if (keep.size === boxes.size) return;
        const keptTris: number[] = [];
        compOfTri.forEach((r, t) => {
          if (keep.has(r)) keptTris.push(t);
        });
        if (keptTris.length === 0) return;
        const src = g.index ? g.toNonIndexed() : g;
        const take = (name: string) => {
          const attr = src.attributes[name];
          if (!attr) return null;
          const itemSize = attr.itemSize;
          const out = new Float32Array(keptTris.length * 3 * itemSize);
          keptTris.forEach((t, i) => {
            for (let k = 0; k < 3; k++) {
              const s = (t * 3 + k) * itemSize;
              for (let j = 0; j < itemSize; j++) out[(i * 3 + k) * itemSize + j] = attr.array[s + j];
            }
          });
          return new THREE.BufferAttribute(out, itemSize);
        };
        const rebuilt = new THREE.BufferGeometry();
        const positions = take("position");
        if (!positions) return;
        rebuilt.setAttribute("position", positions);
        const normals = take("normal");
        if (normals) rebuilt.setAttribute("normal", normals);
        const uvs = take("uv");
        if (uvs) rebuilt.setAttribute("uv", uvs);
        if (src !== g) src.dispose();
        g.dispose();
        mesh.geometry = rebuilt;
      };
      parts.forEach((mesh) => {
        // Geometry surgery must never take the section down: on any
        // surprise, keep the mesh whole.
        if (!mesh.parent) return;
        try {
          cutVessel(mesh);
        } catch {
          /* keep uncut */
        }
      });
      const pen = parts.filter((mesh) => mesh.parent);
      const [map, roughnessMap] = await Promise.all([
        loadTex("/models/holder_color.png", true),
        loadTex("/models/holder_roughness.png", false),
      ]);
      for (const mesh of pen.length > 0 ? pen : parts) {
        const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        const next = mats.map((m: any) => {
          if (map) m.map = map;
          if (roughnessMap) m.roughnessMap = roughnessMap;
          return toStandard(m);
        });
        mesh.material = Array.isArray(mesh.material) ? next : next[0];
      }
      object.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(object);
      const size = box.getSize(new THREE.Vector3());
      const longest = Math.max(size.x, size.y, size.z);
      if (size.x >= longest * 0.9 && size.x > size.y * 1.2) {
        object.rotation.z -= Math.PI / 2;
      } else if (size.z >= longest * 0.9 && size.z > size.y * 1.2) {
        object.rotation.x += Math.PI / 2;
      }
      object.updateMatrixWorld(true);
      const center = new THREE.Box3().setFromObject(object).getCenter(new THREE.Vector3());
      object.position.sub(center);
      object.updateMatrixWorld(true);
      return;
    }
    // "wacom": the FBX ships untextured plus a PBR set per part, matched
    // by the FBX material names (PenTop, PenUpperBody, …).
    for (const mesh of meshes) {
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      const next = await Promise.all(
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

  const fitScale = (size: any, aspect: number, fill = 0.72) => {
    const halfH = Math.tan(THREE.MathUtils.degToRad(14)) * 7;
    const halfW = halfH * aspect;
    return Math.min((2 * halfW * fill) / size.x, (2 * halfH * fill) / size.y);
  };

  const dispose = () => {
    owned.forEach((o) => o.dispose?.());
    renderer.dispose();
    renderer.domElement.remove();
  };

  return {
    renderer,
    scene,
    camera,
    canvas: renderer.domElement,
    owned,
    dispose,
    loadTex,
    skin,
    fitScale,
  };
}

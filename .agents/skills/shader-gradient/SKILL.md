---
name: shader-gradient-design
description: >-
  Architectural guide for using ShaderGradient and procedural GLSL gradient shaders to create atmospheric, fluid background depth in IdeaForge. Covers color calibration with brand tokens (Ember Orange, Empirical Cyan, Obsidian), single-context WebGL integration with Three.js/R3F, performance throttling, and mobile optimization.
metadata:
  category: Shaders & Atmosphere
  framework: ShaderGradient / Three.js GLSL
---

# Shader Gradient Design

This skill guides the design, integration, and performance optimization of fluid WebGL gradient shaders and atmospheric backdrops for IdeaForge.

Primary Reference: [https://github.com/ruucm/shadergradient](https://github.com/ruucm/shadergradient)

---

## 1. Core Principle: Gradients as Atmosphere, Not Distraction

Shader gradients in IdeaForge serve one specific purpose: **creating deep spatial atmosphere**. They simulate the intellectual heat of the forge and the cool clarity of empirical truth.

> [!IMPORTANT]
> **Key Rules**:
> 1. Gradients must sit quietly in the background layer (`z-0`).
> 2. Avoid rapid color cycling, jarring wave animations, or high-frequency noise.
> 3. Coordinate all palettes directly with IdeaForge brand tokens.
> 4. Foreground typography and UI cards must remain 100% legible at all times.

---

## 2. IdeaForge Brand Color Space for Shaders

Shader gradients should use smooth interpolation between these three calibrated stops:

```typescript
export const IDEAFORGE_SHADER_PALETTE = {
  // Deep Void (Anchors the canvas)
  color1: '#04060a', 
  
  // Active Forge Spark (Simulates intellectual combustion)
  color2: '#FF6B00', 
  
  // Empirical Truth (Simulates cooled, verified evidence)
  color3: '#00C2D1', 
  
  // Ambient Obsidian Midtone
  color4: '#0a0f1d',
};
```

---

## 3. WebGL Context Architecture: Avoid Duplicate Renderers

ShaderGradient v2 integrates directly with Three.js and React Three Fiber.

> [!CAUTION]
> **Zero Duplicate Context Rule**:
> Do NOT mount a ShaderGradient `<Canvas>` directly behind an R3F 3D `<Canvas>`. Running two simultaneous WebGL contexts consumes double GPU memory and triggers context loss on mobile browsers.
>
> **Best Practice**:
> - **Option A (Unified Scene)**: Embed the gradient shader as a background plane or environment map inside the main Three.js / R3F scene.
> - **Option B (Section Separation)**: Use ShaderGradient on dedicated hero or section transitions, and 3D object lattices on interactive showcase components.
> - **Option C (CSS Hybrid)**: When a 3D canvas is already active, use hardware-accelerated CSS radial blur meshes (`blur-[140px]`) to achieve the identical visual aesthetic with zero additional WebGL draw calls.

---

## 4. Implementation Pattern

### A. Embedding within an R3F Scene
```tsx
import React, { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

const GradientPlaneShader = {
  uniforms: {
    uTime: { value: 0 },
    uColorA: { value: new THREE.Color('#FF6B00') },
    uColorB: { value: new THREE.Color('#00C2D1') },
    uColorBase: { value: new THREE.Color('#04060a') },
  },
  vertexShader: `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform float uTime;
    uniform vec3 uColorA;
    uniform vec3 uColorB;
    uniform vec3 uColorBase;
    varying vec2 vUv;

    void main() {
      float wave = sin(vUv.x * 3.0 + uTime * 0.4) * cos(vUv.y * 3.0 + uTime * 0.3) * 0.5 + 0.5;
      vec3 col = mix(uColorA, uColorB, vUv.x + sin(uTime * 0.2) * 0.2);
      col = mix(uColorBase, col, wave * 0.35);
      gl_FragColor = vec4(col, 1.0);
    }
  `,
};

export function AtmosphericGradientBackground() {
  const materialRef = useRef<THREE.ShaderMaterial>(null!);

  useFrame((_, delta) => {
    if (materialRef.current) {
      materialRef.current.uniforms.uTime.value += delta;
    }
  });

  return (
    <mesh position={[0, 0, -5]}>
      <planeGeometry args={[25, 20]} />
      <shaderMaterial ref={materialRef} args={[GradientPlaneShader]} depthWrite={false} />
    </mesh>
  );
}
```

---

## 5. Performance & Mobile Safeguards

1. **Slow Animation Frequency**:
   - Keep animation speed coefficients low (`uTime * 0.15` to `0.3`). Rapid movement causes visual fatigue and increases CPU uniform update overhead.
2. **Mobile Screen Resolution**:
   - On screens `< 768px`, downscale shader resolution or replace with high-efficiency radial CSS mesh gradients.
3. **prefers-reduced-motion**:
   - When active, clamp `uTime` to `0.0` or disable delta updates so the gradient renders as a static ambient backdrop.
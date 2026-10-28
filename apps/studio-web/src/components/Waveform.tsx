import { useEffect, useRef, useState } from "react";
import { decodePeaks } from "../lib/audio/peaks";

export function Waveform({
  src,
  width = 220,
  height = 40,
}: {
  src: string;
  width?: number;
  height?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setFailed(false);
    decodePeaks(src)
      .then((peaks) => {
        if (!live) return;
        const canvas = ref.current;
        if (!canvas) return;
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        const c = canvas.getContext("2d")!;
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        c.clearRect(0, 0, width, height);
        c.fillStyle =
          getComputedStyle(canvas).getPropertyValue("--kv-accent").trim() || "#c1121f";
        const n = peaks.maxs.length;
        const bw = width / n;
        for (let i = 0; i < n; i++) {
          const h = Math.max(1, ((peaks.maxs[i] - peaks.mins[i]) / 2) * height);
          c.fillRect(i * bw, (height - h) / 2, Math.max(1, bw - 0.5), h);
        }
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [src, width, height]);

  if (failed) {
    return (
      <span style={{ fontSize: 12, color: "var(--kv-muted)" }}>preview unavailable</span>
    );
  }
  return (
    <canvas ref={ref} style={{ width, height, display: "block" }} role="img" aria-label="Waveform" />
  );
}

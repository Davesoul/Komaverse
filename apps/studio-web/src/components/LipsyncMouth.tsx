/** Lip-sync mouth indicator: a small badge pinned to the preview corner.
 * The ellipse opens with the current viseme so dialogue reads as motion;
 * the label names the speaking cue (speaker or take) for caption pairing.
 */
export function LipsyncMouth({
  viseme,
  speaker,
  openness,
}: {
  /** closed | narrow | open | wide (backend VisemeFrame.viseme). */
  viseme: string;
  /** Speaking character, or null for untagged takes. */
  speaker: string | null;
  /** 0..1 mouth openness (backend VisemeFrame.openness). */
  openness: number;
}) {
  const open = Math.min(Math.max(Number(openness) || 0, 0), 1);
  // Mouth cavity height tracks openness; width stays fixed per viseme.
  const cavityH = 3 + open * 17;
  const cavityW = viseme === "narrow" ? 10 : viseme === "closed" ? 14 : 20;
  return (
    <span
      role="status"
      aria-label={speaker ? `${speaker} speaking (${viseme})` : `Speaking (${viseme})`}
      title={speaker ? `${speaker} · ${viseme}` : viseme}
      style={{
        display: "inline-flex",
        gap: 8,
        alignItems: "center",
        color: "#fff",
        background: "rgba(0,0,0,0.65)",
        borderRadius: 6,
        padding: "4px 10px",
        pointerEvents: "none",
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      <span
        aria-hidden
        style={{
          width: 26,
          height: 26,
          borderRadius: "50%",
          background: "rgba(255,255,255,0.12)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <span
          style={{
            display: "block",
            width: cavityW,
            height: cavityH,
            borderRadius: "50%",
            background: "#fff",
            transition: "height 90ms linear, width 90ms linear",
          }}
        />
      </span>
      <span className="kv-nums">
        {speaker ? `${speaker} · ${viseme}` : viseme}
      </span>
    </span>
  );
}

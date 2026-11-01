// Landing media catalog: backdrop reels and named showcase works.
export const VIDEOS = [
  "/videos/showcase_01.mp4",
  "/videos/showcase_02.mp4",
  "/videos/showcase_03.mp4",
  "/videos/showcase_04.mp4",
];

export type Reel = {
  v: number;
  title: string;
  tag: string;
  span: number;
  ratio: string;
  /* Manga-page break: "slash" slants both edges, "slash-l"/"slash-r" one
   * inner edge only — outer page edges always stay straight, so neighbours
   * sharing a gutter use the same lean and it reads as one channel.
   * "drop" lets a tall panel invade the tier below. Undefined = ruled. */
  cut?: "slash" | "slash-l" | "slash-r" | "drop";
};

export const REELS: Reel[] = [
  { v: 0, title: "Neon Crossing", tag: "★ Featured", span: 8, ratio: "16 / 10" },
  { v: 1, title: "Paper Storm", tag: "AI · 6s", span: 4, ratio: "4 / 5", cut: "drop" },
  { v: 2, title: "Ink Drift", tag: "AI · 4s", span: 4, ratio: "1 / 1", cut: "slash-l" },
  { v: 3, title: "Frame by Frame", tag: "Timeline · 8s", span: 3, ratio: "3 / 4", cut: "slash" },
  { v: 0, title: "Redline Run", tag: "AI · 5s", span: 5, ratio: "5 / 4", cut: "slash-r" },
  { v: 1, title: "Static Bloom", tag: "AI · 7s", span: 12, ratio: "21 / 9" },
];

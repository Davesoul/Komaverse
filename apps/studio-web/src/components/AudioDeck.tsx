import { useEffect, useRef, useState } from "react";
import { api, mediaUrl, type AudioTake, type Scene } from "../api";
import { probeDuration } from "../lib/audio/peaks";
import { Btn, IconBtn, SectionTitle, SelectInput, TextInput } from "./ui";
import { Waveform } from "./Waveform";

function pickMime(): string {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  if (typeof MediaRecorder === "undefined") return "";
  for (const m of candidates) {
    try {
      if (MediaRecorder.isTypeSupported(m)) return m;
    } catch {
      // Probe and move on.
    }
  }
  return "";
}

function extFor(mime: string): string {
  if (mime.includes("mp4")) return "m4a";
  if (mime.includes("ogg") || mime.includes("opus")) return "ogg";
  return "webm";
}

interface RecState {
  recorder: MediaRecorder;
  chunks: Blob[];
  stream: MediaStream;
  ctx: AudioContext;
  raf: number;
  startedAt: number;
}

export function AudioDeck({
  projectId,
  scene,
  takes,
  playhead,
  onTakes,
  onScene,
  onVoiceoverStart,
  notify,
}: {
  projectId: string;
  scene: Scene | null;
  takes: AudioTake[];
  /** Current preview position: quick-cues and voiceovers land here. */
  playhead: number;
  onTakes: () => void;
  onScene: () => void;
  /** The page seeks the preview here and presses play for a voiceover. */
  onVoiceoverStart: (atTime: number) => void;
  notify: (msg: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [voiceoverAt, setVoiceoverAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [level, setLevel] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [renaming, setRenaming] = useState("");
  const [cueTake, setCueTake] = useState("");
  const [cueStart, setCueStart] = useState("0");
  const [cueVolume, setCueVolume] = useState("1");
  const [genText, setGenText] = useState("");
  const [genBusy, setGenBusy] = useState(false);
  const [genCue, setGenCue] = useState(true);
  const recRef = useRef<RecState | null>(null);
  const voiceoverRef = useRef<number | null>(null);
  const playersRef = useRef(new Map<string, HTMLAudioElement>());
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(
    () => () => {
      // Stop any capture/playback when the deck unmounts.
      recRef.current?.stream.getTracks().forEach((t) => t.stop());
      recRef.current?.ctx.close().catch(() => undefined);
      playersRef.current.forEach((a) => a.pause());
    },
    [],
  );

  const stopPlayback = (except?: string) => {
    playersRef.current.forEach((a, id) => {
      if (id !== except) a.pause();
    });
    if (!except) setPlayingId(null);
  };

  const togglePlay = (take: AudioTake) => {
    if (playingId === take.id) {
      playersRef.current.get(take.id)?.pause();
      setPlayingId(null);
      return;
    }
    stopPlayback(take.id);
    let el = playersRef.current.get(take.id);
    if (!el) {
      el = new Audio(mediaUrl(take.url));
      el.crossOrigin = "use-credentials";
      el.onended = () => setPlayingId((cur) => (cur === take.id ? null : cur));
      playersRef.current.set(take.id, el);
    }
    void el.play().catch(() => notify("Couldn't play that take."));
    setPlayingId(take.id);
  };

  const saveBlob = async (blob: Blob, filename: string) => {
    setBusy(true);
    try {
      const duration = await probeDuration(blob);
      const take = await api.uploadTake(projectId, blob, filename, duration);
      onTakes();
      return { take, duration };
    } catch (e) {
      notify(`Couldn't save the take: ${(e as Error).message}`);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const startRecording = async (voiceover: boolean) => {
    if (!navigator.mediaDevices?.getUserMedia) {
      notify("Recording needs a secure context (localhost or HTTPS).");
      return;
    }
    const mime = pickMime();
    if (typeof MediaRecorder === "undefined") {
      notify("This browser can't record audio.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      const name = (e as Error).name;
      notify(
        name === "NotAllowedError"
          ? "Microphone blocked — allow access in the browser, then retry."
          : name === "NotFoundError"
            ? "No microphone found on this machine."
            : `Couldn't open the microphone: ${(e as Error).message}`,
      );
      return;
    }
    const chunks: Blob[] = [];
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    const AC = window.AudioContext;
    const ctx = new AC();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);
    const loop = () => {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3));
      const st = recRef.current;
      if (st) {
        setElapsed((Date.now() - st.startedAt) / 1000);
        st.raf = requestAnimationFrame(loop);
      }
    };
    recRef.current = { recorder, chunks, stream, ctx, raf: 0, startedAt: Date.now() };
    recorder.start();
    recRef.current.raf = requestAnimationFrame(loop);
    setElapsed(0);
    setRecording(true);
    if (voiceover) {
      voiceoverRef.current = playhead;
      setVoiceoverAt(playhead);
      onVoiceoverStart(playhead);
    }
  };

  const stopRecording = () => {
    const st = recRef.current;
    if (!st) return;
    recRef.current = null;
    cancelAnimationFrame(st.raf);
    setRecording(false);
    setLevel(0);
    const mime = st.recorder.mimeType || "audio/webm";
    st.recorder.onstop = () => {
      st.stream.getTracks().forEach((t) => t.stop());
      st.ctx.close().catch(() => undefined);
      const blob = new Blob(st.chunks, { type: mime.split(";")[0] });
      if (blob.size === 0) {
        notify("Nothing was captured — try again.");
        return;
      }
      const at = voiceoverRef.current;
      voiceoverRef.current = null;
      setVoiceoverAt(null);
      void (async () => {
        const saved = await saveBlob(blob, `take-${takes.length + 1}.${extFor(mime)}`);
        if (!saved) return;
        if (at !== null && scene) {
          try {
            await api.addCue(scene.id, { audio_id: saved.take.id, start_sec: at });
            onScene();
            scrollToLane();
            notify(`Voiceover placed at ${at.toFixed(1)}s — see the A1 lane.`);
          } catch (e) {
            notify(`Saved ${saved.take.name} but couldn't place it: ${(e as Error).message}`);
          }
        } else if (at !== null) {
          notify(`Saved ${saved.take.name} — pick a scene to cue it.`);
        } else {
          notify(`Saved ${saved.take.name} (${saved.duration.toFixed(1)}s).`);
        }
      })();
    };
    st.recorder.stop();
  };

  const scrollToLane = () => {
    document
      .querySelector('[aria-label="Audio track"]')
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  const quickCue = async (take: AudioTake) => {
    if (!scene) return;
    try {
      await api.addCue(scene.id, { audio_id: take.id, start_sec: Math.max(playhead, 0) });
      onScene();
      scrollToLane();
      notify(`“${take.name || "Take"}” cued at ${Math.max(playhead, 0).toFixed(1)}s — see the A1 lane.`);
    } catch (e) {
      notify(`Couldn't cue it: ${(e as Error).message}`);
    }
  };

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    await saveBlob(file, file.name);
    if (fileRef.current) fileRef.current.value = "";
  };

  const generateVoice = async () => {
    const text = genText.trim();
    if (!text || genBusy) return;
    setGenBusy(true);
    try {
      const take = await api.synthesizeVoice(projectId, {
        text,
        cue_scene_id: genCue && scene ? scene.id : undefined,
        cue_start_sec: Math.max(playhead, 0),
      });
      setGenText("");
      onTakes();
      if (genCue && scene) {
        onScene();
        scrollToLane();
        notify(`“${take.name}” generated and cued at ${Math.max(playhead, 0).toFixed(1)}s.`);
      } else {
        notify(`“${take.name}” generated into the library.`);
      }
    } catch (e) {
      notify(`Voice generation failed: ${(e as Error).message}`);
    } finally {
      setGenBusy(false);
    }
  };

  const removeTake = async (take: AudioTake) => {
    if (!window.confirm(`Delete take "${take.name || "Untitled"}"? This can't be undone.`)) return;
    try {
      await api.deleteTake(take.id);
      playersRef.current.delete(take.id);
      onTakes();
    } catch (e) {
      const msg = (e as Error).message;
      notify(
        msg.includes("409")
          ? "Take is on the timeline — remove its cues first."
          : `Delete failed: ${msg}`,
      );
    }
  };

  const addCue = async () => {
    if (!scene || !cueTake) return;
    try {
      await api.addCue(scene.id, {
        audio_id: cueTake,
        start_sec: Number(cueStart) || 0,
        volume: Number(cueVolume) || 0,
      });
      onScene();
      notify("Cue placed — it mixes into the next export.");
    } catch (e) {
      notify(`Couldn't place the cue: ${(e as Error).message}`);
    }
  };

  return (
    <div>
      <SectionTitle>Voice &amp; audio</SectionTitle>
      {!recording ? (
        <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
          <IconBtn onClick={() => void startRecording(false)} disabled={busy} label="Record a take into the library">
            ●
          </IconBtn>
          <IconBtn
            onClick={() => void startRecording(true)}
            disabled={busy || !scene}
            label={scene ? "Voiceover — play the preview and record over it; the take lands on the A1 lane where you started" : "Pick a scene first"}
          >
            ◉
          </IconBtn>
          <IconBtn onClick={() => fileRef.current?.click()} disabled={busy} label="Upload audio from disk">
            ⤒
          </IconBtn>
          <input
            ref={fileRef}
            type="file"
            accept="audio/*"
            style={{ display: "none" }}
            aria-label="Upload audio"
            onChange={(e) => void onFile(e.target.files?.[0])}
          />
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
          <IconBtn danger onClick={stopRecording} label="Stop recording">
            ■
          </IconBtn>
          <div
            style={{
              flex: 1,
              height: 10,
              borderRadius: 5,
              background: "var(--kv-inset)",
              border: "1px solid var(--kv-border)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${Math.round(level * 100)}%`,
                height: "100%",
                background: "var(--kv-accent)",
              }}
            />
          </div>
          <span className="kv-nums" style={{ fontSize: 13 }}>
            {elapsed.toFixed(1)}s
          </span>
          {voiceoverAt !== null && (
            <span style={{ fontSize: 12, color: "var(--kv-muted)" }} title="The take lands here when you stop">
              🎙 from {voiceoverAt.toFixed(1)}s
            </span>
          )}
        </div>
      )}

      <div style={{ marginBottom: 8, border: "1px solid var(--kv-border)", borderRadius: 6, padding: 8 }}>
        <div style={{ fontSize: 12, color: "var(--kv-muted)", marginBottom: 6 }}>
          Generate voice — engine and voice come from the Brain voice row
          (ElevenLabs cloud or local free voices, all in-app).
        </div>
        <textarea
          value={genText}
          onChange={(e) => setGenText(e.target.value)}
          placeholder="Type what the voice should say…"
          aria-label="Voiceover text to generate"
          rows={2}
          style={{ width: "100%", resize: "vertical", marginBottom: 6 }}
        />
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Btn small disabled={genBusy || !genText.trim()} onClick={() => void generateVoice()}>
            {genBusy ? "Generating…" : "Generate"}
          </Btn>
          <label style={{ display: "flex", gap: 4, alignItems: "center", fontSize: 12, color: "var(--kv-muted)" }}>
            <input
              type="checkbox"
              checked={genCue}
              disabled={!scene}
              onChange={(e) => setGenCue(e.target.checked)}
              aria-label="Cue the generated take at the playhead"
            />
            Cue at playhead{scene ? "" : " (pick a scene first)"}
          </label>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
        {takes.map((t, i) => (
          <div
            key={t.id}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.effectAllowed = "copy";
              e.dataTransfer.setData("text/kv-take", t.id);
            }}
            title="Drag onto the A1 lane to cue it there"
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              border: "1px solid var(--kv-border)",
              borderRadius: 6,
              padding: 6,
              cursor: "grab",
            }}
          >
            <IconBtn onClick={() => togglePlay(t)} label={playingId === t.id ? "Stop take" : "Play take"}>
              {playingId === t.id ? "■" : "▶"}
            </IconBtn>
            <IconBtn
              disabled={!scene}
              onClick={() => void quickCue(t)}
              label={scene ? `Cue take at the playhead (${Math.max(playhead, 0).toFixed(1)}s)` : "Pick a scene first"}
            >
              ＋
            </IconBtn>
            <div style={{ flex: 1, minWidth: 0 }}>
              {renaming === t.id ? (
                <TextInput
                  autoFocus
                  defaultValue={t.name}
                  style={{ width: "100%", marginBottom: 4 }}
                  aria-label="Take name"
                  onBlur={(e) => {
                    const v = e.target.value.trim();
                    setRenaming("");
                    if (v && v !== t.name) {
                      void api.patchTake(t.id, { name: v }).then(onTakes);
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                    if (e.key === "Escape") setRenaming("");
                  }}
                />
              ) : (
                <div
                  style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis" }}
                  title="Double-click to rename"
                  onDoubleClick={() => setRenaming(t.id)}
                >
                  <span className="kv-nums">TK {String(i + 1).padStart(2, "0")}</span>{" "}
                  {t.name || "Untitled"}{" "}
                  <span className="kv-nums" style={{ color: "var(--kv-muted)" }}>
                    {t.duration_sec.toFixed(1)}s
                  </span>
                </div>
              )}
              <Waveform src={mediaUrl(t.url)} width={200} height={32} />
            </div>
            <IconBtn danger onClick={() => void removeTake(t)} label="Delete take">
              ×
            </IconBtn>
          </div>
        ))}
        {takes.length === 0 && (
          <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
            No takes yet — record your voice or upload audio.
          </span>
        )}
      </div>

      {scene && takes.length > 0 && (
        <div>
          <SectionTitle>Cues on {scene.name}</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
            {scene.cues.map((c) => {
              const take = takes.find((t) => t.id === c.audio_id);
              return (
                <div key={c.id} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {take?.name || "Missing take"}
                  </span>
                  <span className="kv-nums" style={{ color: "var(--kv-muted)" }}>
                    @{c.start_sec.toFixed(1)}s ×{c.volume.toFixed(1)}
                  </span>
                  <IconBtn
                    onClick={() => {
                      void api.deleteCue(c.id).then(onScene);
                    }}
                    label="Remove cue from the timeline"
                  >
                    ×
                  </IconBtn>
                </div>
              );
            })}
            {scene.cues.length === 0 && (
              <span style={{ fontSize: 13, color: "var(--kv-muted)" }}>
                No cues — takes below only mix into exports once cued.
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <SelectInput
              value={cueTake}
              onChange={(e) => setCueTake(e.target.value)}
              aria-label="Take to cue"
              style={{ flex: 1, minWidth: 0 }}
            >
              <option value="">Take…</option>
              {takes.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name || t.id}
                </option>
              ))}
            </SelectInput>
            <TextInput
              value={cueStart}
              onChange={(e) => setCueStart(e.target.value)}
              aria-label="Cue start seconds"
              title="Start (seconds into the scene)"
              style={{ width: 56 }}
            />
            <TextInput
              value={cueVolume}
              onChange={(e) => setCueVolume(e.target.value)}
              aria-label="Cue volume"
              title="Volume (0–2)"
              style={{ width: 52 }}
            />
            <IconBtn disabled={!cueTake} onClick={() => void addCue()} label="Cue the selected take at the playhead">
              ＋
            </IconBtn>
          </div>
        </div>
      )}

      {recording && (
        <div
          role="status"
          aria-label={voiceoverAt !== null ? "Recording voiceover" : "Recording take"}
          style={{
            position: "fixed",
            bottom: 16,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 60,
            display: "flex",
            gap: 10,
            alignItems: "center",
            background: "var(--kv-surface)",
            border: "1px solid var(--kv-accent)",
            borderRadius: 999,
            padding: "8px 8px 8px 16px",
            boxShadow: "var(--kv-shadow-hard)",
          }}
        >
          <span className="kv-nums" style={{ fontSize: 13 }}>
            🎙 REC {elapsed.toFixed(1)}s
            {voiceoverAt !== null ? ` · lands at ${voiceoverAt.toFixed(1)}s` : ""}
          </span>
          <IconBtn danger onClick={stopRecording} label={voiceoverAt !== null ? "Stop and place the take on the timeline" : "Stop recording"}>
            ■
          </IconBtn>
        </div>
      )}
    </div>
  );
}

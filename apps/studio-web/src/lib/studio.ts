/* Shared studio session bits that belong to no single page.
 * Pages publish their selected project; the agent dock reads it at send time
 * so the agent always acts on the project you're looking at.
 * The published id (and the shell tab) survive reloads via localStorage, so
 * every tab reopens the latest project instead of asking again. Stored ids
 * are always validated against the server project list before use. */

const PROJECT_KEY = "komaverse.projectId";

let currentProject: string | null = null;

function readStored(): string | null {
  try {
    return localStorage.getItem(PROJECT_KEY);
  } catch {
    return null;
  }
}

export function setCurrentProject(id: string | null) {
  currentProject = id || null;
  try {
    if (currentProject) localStorage.setItem(PROJECT_KEY, currentProject);
    else localStorage.removeItem(PROJECT_KEY);
  } catch {
    // Private mode etc: in-memory only, pages fall back to manual select.
  }
}

export function getCurrentProject(): string | null {
  if (currentProject) return currentProject;
  currentProject = readStored();
  return currentProject;
}

/* Last-open draw frame, per project. Draw restores it on mount so the
 * canvas is never blank when you open the tab. */
const drawFrameKey = (projectId: string) => `komaverse.drawFrame.${projectId}`;

export function setDrawFrame(projectId: string, frameId: string | null) {
  try {
    if (frameId) localStorage.setItem(drawFrameKey(projectId), frameId);
    else localStorage.removeItem(drawFrameKey(projectId));
  } catch {
    // Private mode etc: the tab just opens the first frame instead.
  }
}

export function getDrawFrame(projectId: string): string | null {
  try {
    return localStorage.getItem(drawFrameKey(projectId));
  } catch {
    return null;
  }
}

/* Current animation sequence, per project. Draw and Media share it so
 * "Open in Draw" lands on the right sequence. */
const sequenceKey = (projectId: string) => `komaverse.sequence.${projectId}`;

/* Current timeline (scene), per project. Render publishes it; Media reads
 * it so "add to timeline" lands on the timeline you are cutting. */
const sceneKey = (projectId: string) => `komaverse.scene.${projectId}`;

export function setCurrentScene(projectId: string, sceneId: string | null) {
  try {
    if (sceneId) localStorage.setItem(sceneKey(projectId), sceneId);
    else localStorage.removeItem(sceneKey(projectId));
  } catch {
    // Private mode etc: Media falls back to the first timeline instead.
  }
}

export function getCurrentScene(projectId: string): string | null {
  try {
    return localStorage.getItem(sceneKey(projectId));
  } catch {
    return null;
  }
}

export function setCurrentSequence(projectId: string, sequenceId: string | null) {
  try {
    if (sequenceId) localStorage.setItem(sequenceKey(projectId), sequenceId);
    else localStorage.removeItem(sequenceKey(projectId));
  } catch {
    // Private mode etc: the tab just opens the first sequence instead.
  }
}

export function getCurrentSequence(projectId: string): string | null {
  try {
    return localStorage.getItem(sequenceKey(projectId));
  } catch {
    return null;
  }
}

/* Cross-tab jump: pages can't reach the shell's setTab, so they dispatch
 * this event (the project is already published via setCurrentProject, so
 * the target tab restores it on mount). */
export type StudioTab = "home" | "scan" | "draw" | "render" | "media";

export function gotoTab(tab: StudioTab) {
  window.dispatchEvent(new CustomEvent("komaverse:goto-tab", { detail: tab }));
}

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

/* Cross-tab jump: pages can't reach the shell's setTab, so they dispatch
 * this event (the project is already published via setCurrentProject, so
 * the target tab restores it on mount). */
export type StudioTab = "home" | "scan" | "draw" | "animate" | "media";

export function gotoTab(tab: StudioTab) {
  window.dispatchEvent(new CustomEvent("komaverse:goto-tab", { detail: tab }));
}

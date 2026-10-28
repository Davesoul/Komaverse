/* Shared studio session bits that belong to no single page.
 * Pages publish their selected project; the agent dock reads it at send time
 * so the agent always acts on the project you're looking at. */

let currentProject: string | null = null;

export function setCurrentProject(id: string | null) {
  currentProject = id || null;
}

export function getCurrentProject(): string | null {
  return currentProject;
}

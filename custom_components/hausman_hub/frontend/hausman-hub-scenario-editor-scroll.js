/* Preserve independent editor panes across the panel's bounded rerenders. */

const SELECTORS = [
  ".scenario-editor-steps",
  ".scenario-editor-workspace",
  ".scenario-editor-column-about",
  ".scenario-editor-column-rules",
  ".scenario-editor-column-actions",
];

export function captureScenarioEditorScroll(container) {
  if (!container || typeof container.querySelector !== "function") return null;
  return SELECTORS.map((selector) => {
    const node = container.querySelector(selector);
    return node ? [selector, Number(node.scrollTop) || 0, Number(node.scrollLeft) || 0] : null;
  }).filter(Boolean);
}

export function restoreScenarioEditorScroll(container, snapshot) {
  if (!container || !snapshot || typeof container.querySelector !== "function") return;
  snapshot.forEach(([selector, top, left]) => {
    const node = container.querySelector(selector);
    if (!node) return;
    node.scrollTop = top;
    node.scrollLeft = left;
  });
}

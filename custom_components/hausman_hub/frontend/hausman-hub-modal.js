/* Shared modal-window behaviour: Escape, Tab trap, initial focus, focus restore. */

const FOCUSABLE_SELECTOR = "button, input, select, textarea, summary, [href], [tabindex]";

function isFocusable(node) {
  if (!node || node.disabled || node.hidden) return false;
  if (node.tabIndex === -1) return false;
  if (typeof node.closest === "function" && node.closest("[hidden]")) return false;
  return true;
}

export function modalFocusableItems(container) {
  if (!container || typeof container.querySelectorAll !== "function") return [];
  return [...container.querySelectorAll(FOCUSABLE_SELECTOR)].filter(isFocusable);
}

export function activeElementWithin(node) {
  const root = node && typeof node.getRootNode === "function" ? node.getRootNode() : null;
  if (root && root.activeElement) return root.activeElement;
  return typeof document !== "undefined" ? document.activeElement : null;
}

export function focusInitialModalElement(sheet, preferred) {
  const target = preferred
    || (sheet.querySelector ? sheet.querySelector("[data-modal-initial]") : null)
    || modalFocusableItems(sheet)[0];
  if (target && typeof target.focus === "function") {
    try {
      target.focus({ preventScroll: true });
    } catch (error) {
      target.focus();
    }
  }
  return target || null;
}

export function trapModalTabKey(event, sheet) {
  if (!event || event.key !== "Tab") return false;
  const items = modalFocusableItems(sheet);
  if (typeof event.preventDefault === "function") event.preventDefault();
  if (!items.length) return true;
  const active = activeElementWithin(sheet);
  const first = items[0];
  const last = items[items.length - 1];
  const inside = items.includes(active);
  if (event.shiftKey) {
    const target = !inside || active === first ? last : null;
    if (target && typeof target.focus === "function") target.focus();
    return true;
  }
  const target = !inside || active === last ? first : null;
  if (target && typeof target.focus === "function") target.focus();
  return true;
}

function restoreFocusTo(element) {
  if (!element || typeof element.focus !== "function") return;
  if (element.isConnected === false) return;
  try {
    element.focus({ preventScroll: true });
  } catch (error) {
    try {
      element.focus();
    } catch (fallbackError) {
      /* the original card may already be gone after a re-render */
    }
  }
}

/* Modal appended on demand (backdrop + surface). Returns the wrapped closer. */
export function enhanceAppendedModal(backdrop, sheet, close, options = {}) {
  const restore = activeElementWithin(backdrop);
  let closed = false;
  const finish = () => {
    if (closed) return;
    closed = true;
    close();
    if (options.restoreFocus !== false) restoreFocusTo(restore);
  };
  sheet.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      finish();
      return;
    }
    trapModalTabKey(event, sheet);
  });
  if (options.initialFocus !== false) {
    Promise.resolve().then(() => {
      if (!closed) focusInitialModalElement(sheet, options.initialFocus || null);
    });
  }
  return finish;
}

/* Modal that lives inside a <details> element and appears when it opens. */
export function enhanceDetailsModal(details, sheet, close, options = {}) {
  let restore = null;
  details.addEventListener("toggle", () => {
    if (details.open) {
      restore = activeElementWithin(details);
      Promise.resolve().then(() => {
        if (details.open) focusInitialModalElement(sheet, options.initialFocus || null);
      });
      return;
    }
    const target = restore;
    restore = null;
    if (options.restoreFocus !== false) restoreFocusTo(target);
  });
  sheet.addEventListener("keydown", (event) => {
    if (!details.open) return;
    if (event.key === "Escape") {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
      close();
      return;
    }
    trapModalTabKey(event, sheet);
  });
}

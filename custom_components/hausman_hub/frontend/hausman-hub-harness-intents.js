export const HARNESS_INTENT_RULES = [
  [".sidebar-intercom", "navigation:intercom", "ui-only"],
  [".sidebar-collapse", "navigation:collapse", "ui-only"],
  [".kiosk-toggle", "navigation:kiosk", "ui-only"],

  [".overview-tablet-card-mode", "overview:utility-mode", "ui-only"],
  [".overview-tablet-bottom-card.is-energy .overview-tablet-bottom-actions > button", "overview:energy-settings", "ui-only"],
  [".overview-tablet-energy-source", "overview:energy-source", "ui-only"],
  [".overview-tablet-lighting-compact", "overview:lighting-compact", "ui-only"],

  [".lighting-section-link", "lighting:all-devices", "ui-only"],
  [".lighting-room-search input", "lighting:search", "ui-only"],
  [".lighting-room-chip", "lighting:filter", "ui-only"],
  [".lighting-room-card", "lighting:room", "ui-only"],
  [".lighting-room-power", "lighting:room-power", "blocked"],
  ["#hausman-lighting .inventory-device-summary", "lighting:device", "ui-only"],
  [".lighting-side-action", "lighting:side-action", "blocked"],
  [".lighting-side-link", "lighting:side-link", "ui-only"],

  [".climate-category-card", "climate:category", "ui-only"],
  [".hh-climate-room-search", "climate:search", "ui-only"],
  [".climate-mode-tab", "climate:mode-filter", "ui-only"],
  [".climate-side-equipment", "climate:equipment", "ui-only"],
  [".climate-side-link", "climate:side-link", "ui-only"],
  ["#hausman-climate-profiles .profile-room input", "climate-profile:input", "ui-only"],
  ["#hausman-climate-profiles .profile-room select", "climate-profile:select", "ui-only"],
  ["#hausman-climate-profiles > .actions > button", "climate-profile:save", "blocked"],

  [".rooms-canon-search", "rooms:search", "ui-only"],
  [".rooms-canon-filters > button", "rooms:filter", "ui-only"],
  [".rooms-canon-card", "rooms:room", "ui-only"],
  [".rooms-side-room", "rooms:side-room", "ui-only"],
  [".rooms-side-link", "rooms:side-link", "ui-only"],

  [".hh-media-search input", "media:search", "ui-only"],
  [".hh-media-chip", "media:filter", "ui-only"],
  [".hh-media-room-chip", "media:room", "ui-only"],
  ["#hausman-media .media-device-summary", "media:device", "ui-only"],
  [".hh-media-side-room", "media:side-room", "ui-only"],
  [".hh-media-side-link", "media:side-link", "ui-only"],

  [".security-quick-filter", "security:quick-filter", "ui-only"],
  [".security-canon-all", "security:all", "ui-only"],
  [".security-canon-type", "security:type", "ui-only"],
  ["#hausman-security .inventory-device-summary", "security:device", "ui-only"],
  [".security-canon-attention", "security:attention", "ui-only"],

  [".device-discovery-area-select", "devices:discovery-area", "ui-only"],
  [".devices-canon-reset", "devices:all", "ui-only"],
  [".devices-canon-category", "devices:category", "ui-only"],
  [".devices-canon-search", "devices:search", "ui-only"],
  ["#hausman-devices .inventory-device-summary", "devices:device", "ui-only"],

  [".energy-meter-reading-strip", "energy:meter", "ui-only"],
  [".energy-history-metrics > button", "energy:metric", "ui-only"],
  [".energy-history-ranges > button", "energy:range", "ui-only"],
  [".energy-device-controls > button", "energy:device-filter", "ui-only"],
  [".energy-device-search", "energy:search", "ui-only"],
  [".energy-device-card-open", "energy:device", "ui-only"],

  [".scenario-create-ai", "scenario:create-ai", "ui-only"],
  [".scenario-create", "scenario:create", "ui-only"],
  [".scenario-library-search", "scenario:search", "ui-only"],
  [".scenario-library-filters > button", "scenario:filter", "ui-only"],
  [".scenario-library-room-filters > button", "scenario:room-filter", "ui-only"],
  [".scenario-bulk-select", "scenario:bulk-select", "ui-only"],
  [".scenario-more-menu > button", "scenario:more-action", "ui-only"],
  [".scenario-edit", "scenario:edit", "ui-only"],
  [".scenario-editor-close", "scenario-editor:close", "ui-only"],
  [".scenario-editor-column-about .scenario-editor-field > input", "scenario-editor:about-input", "ui-only"],
  [".scenario-room-picker-controls > button", "scenario-editor:room", "ui-only"],
  [".scenario-room-picker-shortcuts > button", "scenario-editor:room-shortcut", "ui-only"],
  [".scenario-icon-grid > button", "scenario-editor:icon", "ui-only"],
  [".scenario-editor-column-about textarea", "scenario-editor:description", "ui-only"],
  [".scenario-editor-column-about > .scenario-editor-panel > .scenario-editor-field > select", "scenario-editor:mode", "ui-only"],
  [".scenario-backend-choice", "scenario-editor:backend", "ui-only"],
  [".scenario-editor-toggle", "scenario-editor:toggle", "ui-only"],
  [".scenario-rule-toggle", "scenario-editor:rule-toggle", "ui-only"],
  [".scenario-rule-remove", "scenario-editor:rule-remove", "ui-only"],
  [".scenario-rule-card > .scenario-editor-field > select", "scenario-editor:rule-select", "ui-only"],
  [".scenario-rule-card > .scenario-editor-field > input", "scenario-editor:rule-input", "ui-only"],
  [".scenario-rule-card > .scenario-editor-field > textarea", "scenario-editor:rule-textarea", "ui-only"],
  [".scenario-add-rule", "scenario-editor:add-rule", "ui-only"],
  [".scenario-editor-footer-actions > button.secondary", "scenario-editor:cancel", "ui-only"],

  [".settings-subnav > button", "settings:view", "ui-only"],
  [".settings-menu-card", "settings:card", "ui-only"],
  [".settings-room-summary > button", "settings:room-summary", "ui-only"],
  [".native-binding-callout > button", "settings:binding", "ui-only"],
  ["[data-testid^='manual-light-protection:field-']", "light-protection:field", "ui-only"],

  [".kiosk-panorama-exit", "kiosk:exit", "ui-only"],
  [".kiosk-panorama-hero-action", "kiosk:hero-details", "ui-only"],
  [".kiosk-panorama-hero-control", "kiosk:hero-control", "ui-only"],
  [".kiosk-panorama-hero-home", "kiosk:home", "ui-only"],
  ["button.kiosk-metric-card", "kiosk:metric", "ui-only"],
  [".kiosk-metric-card .kiosk-card-head > button", "kiosk:metric-action", "ui-only"],
  [".kiosk-panorama-section-head > button", "kiosk:scenarios-all", "ui-only"],
  [".kiosk-panorama-intercom", "kiosk:intercom", "ui-only"],
];

export function applyIntents(root) {
  if (!root) return;
  for (const [selector, key, intent] of HARNESS_INTENT_RULES) {
    root.querySelectorAll(selector).forEach((node) => {
      if (node.dataset.harnessKey) return;
      node.dataset.harnessKey = key;
      node.dataset.harnessIntent = intent;
    });
  }
  if (!root.__hausmanIntentObserver) {
    root.__hausmanIntentObserver = new MutationObserver(() => applyIntents(root));
    root.__hausmanIntentObserver.observe(root, { childList: true, subtree: true });
  }
}

import { roomHeroImage } from "./hausman-hub-room-icons.js?v=1.52.81";

/**
 * Canonical compact Hero for tablet-style library pages.
 *
 * The dashboard keeps its larger interactive Hero. Every other primary section
 * uses this component so typography, image treatment and facts cannot drift.
 */
export function createLibraryHero(panel, options, deps) {
  const { el } = deps;
  const dashboard = panel && panel._homeDashboard || {};
  const room = options.room || null;
  const hero = el("section", `hmh-library-hero${options.warning ? " has-warning" : ""}`);
  const media = el("div", "hmh-library-hero-media");
  media.style.backgroundImage = `url("${roomHeroImage(room, dashboard.summary || {}, dashboard.localIso || "")}")`;
  hero.appendChild(media);

  const overlay = el("div", "hmh-library-hero-overlay");
  const copy = el("div", "hmh-library-hero-copy");
  const heading = el("div", "hmh-library-hero-heading");
  const identity = el("div", "hmh-library-hero-identity");
  if (options.eyebrow) identity.appendChild(el("span", "hmh-library-hero-eyebrow", options.eyebrow));
  identity.appendChild(el("h2", null, options.title));
  identity.appendChild(el("p", null, options.subtitle));
  heading.appendChild(identity);
  if (options.status !== false) {
    const status = el("span", `hmh-library-hero-status${options.warning ? " is-warning" : ""}`);
    status.appendChild(el("span", "hmh-library-hero-status-dot"));
    status.appendChild(el("span", null, options.statusLabel || (options.warning ? "Требует внимания" : "Синхронизировано")));
    heading.appendChild(status);
  }
  copy.appendChild(heading);

  const facts = el("div", "hmh-library-hero-facts");
  (options.facts || []).slice(0, 4).forEach((item) => {
    const fact = el("span", `hmh-library-hero-fact${item.warning ? " has-warning" : ""}`);
    fact.appendChild(el("small", null, item.label));
    fact.appendChild(el("strong", null, String(item.value)));
    facts.appendChild(fact);
  });
  copy.appendChild(facts);
  overlay.appendChild(copy);
  hero.appendChild(overlay);
  return hero;
}

/* SB Modes — the card (custom:sb-modes-card) and the sidebar page (sb-modes-panel),
 * one module served by the integration and loaded on every page (add_extra_js_url), so
 * there is no Lovelace resource to keep in step. Same approach as SB Background.
 *
 * Everything is READ from the integration's entities, live, with no subscription:
 *   select.*        attribute sb_modes: true          the active mode, its options and names
 *   binary_sensor.* attribute sb_modes_problem: true  the table's errors and warnings
 *   sensor.*        attribute sb_modes_zone           one per zone: behaviour, status, entities
 * The only write is the mode itself (select.select_option). Editing the table comes later;
 * until then it is the sb_modes.save_config action.
 *
 * Rows use the shared SB row style (window.SBKit, from the SB UI Kit integration).
 */
const VERSION = "0.3.2";
const CARD = "sb-modes-card";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Times in Home Assistant's own time zone (its default for users), not the browser's.
const hhmm = (iso, tz) => {
  if (!iso) return "";
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: tz || undefined }); }
  catch (e) { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
};
const ago = (iso, now = Date.now()) => {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ${m % 60} min` : `${Math.floor(h / 24)} d ${h % 24} h`;
};

// ---- the shared SB row style: window.SBKit, from the SB UI Kit integration ----------
const kit = () => (window.SBKit && window.SBKit.api >= 1 ? window.SBKit : null);
let kitPromise = null;
const whenKit = () => kitPromise || (kitPromise = new Promise((resolve) => {
  if (kit()) { resolve(kit()); return; }
  const on = () => { if (kit()) { window.removeEventListener("sb-ui-kit-ready", on); resolve(kit()); } };
  window.addEventListener("sb-ui-kit-ready", on);
  setTimeout(() => { if (!kit()) import(`/sb_ui_kit_static/sb-ui-kit.js?v=retry-${Date.now()}`).then(on, () => {}); }, 3000);
  setTimeout(() => resolve(kit()), 15000);
}));
const KIT_MISSING = `<div style="padding: 16px; color: var(--error-color)">The <b>SB UI Kit</b> integration is not loaded — install “SB UI Kit” from HACS and restart Home Assistant.</div>`;

const MODE_ICONS = {
  morning: "mdi:weather-sunset-up", day: "mdi:white-balance-sunny", evening: "mdi:weather-sunset-down",
  overnight: "mdi:weather-night", night: "mdi:weather-night", away: "mdi:home-export-outline",
};
const modeIcon = (id) => MODE_ICONS[id] || "mdi:home-clock-outline";

const BEHAVIOUR_NAMES = {
  hands_off: "Hands off",
  static: "Static",
  nightlight_then_dark: "Nightlight, then dark",
  presence_lux: "Presence + lux",
};

/** A zone's live status → the row's icon, its tint and the words. */
const look = (behaviour, status) => {
  const s = String(status || "");
  if (behaviour === "hands_off") return { icon: "mdi:hand-back-left-off-outline", tint: "idle" };
  if (s.startsWith("dark")) return { icon: "mdi:weather-night", tint: "idle" };
  if (s === "nightlight") return { icon: "mdi:lightbulb-night-outline", tint: "run" };
  if (s === "occupied") return { icon: "mdi:account", tint: "run" };
  if (s.startsWith("vacant")) return { icon: "mdi:timer-sand", tint: "armed" };
  if (s.startsWith("waiting for the sensors")) return { icon: "mdi:motion-sensor-off", tint: "warn" };
  if (s.startsWith("waiting")) return { icon: "mdi:timer-sand-paused", tint: "idle" };
  if (s.startsWith("not built")) return { icon: "mdi:hammer-wrench", tint: "warn" };
  if (s === "applied") return { icon: "mdi:check", tint: "run" };
  return { icon: "mdi:texture-box", tint: "idle" };
};

const STYLE = `
:host { display: block; }
.modes { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 16px 14px; }
.mode { height: 36px; padding: 0 16px; border-radius: 18px; border: 1px solid var(--divider-color); background: none; cursor: pointer;
        color: var(--primary-text-color); font: inherit; font-size: 14px; font-weight: 500; }
.mode:hover { background: color-mix(in srgb, var(--primary-text-color) 8%, transparent); }
.mode.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.mode:disabled { opacity: .5; cursor: default; }
.mode ha-icon { --mdc-icon-size: 18px; margin: -2px 6px 0 -4px; vertical-align: middle; }
.now { display: flex; align-items: center; gap: 14px; padding: 16px 16px 10px; }
.sbk-ico.big { width: 56px; height: 56px; flex: 0 0 56px; border-radius: 28px; --mdc-icon-size: 30px; }
.cur-l { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--secondary-text-color); }
.cur { font-size: 26px; font-weight: 500; line-height: 1.15; }
.since { font-size: 13px; color: var(--secondary-text-color); }
.switch-l { padding: 4px 16px 6px; font-size: 13px; color: var(--secondary-text-color); }
.ask { margin: 0 12px 12px; padding: 10px 12px; border-radius: 8px; font-size: 14px; line-height: 1.45;
       background: color-mix(in srgb, var(--primary-color) 10%, transparent); display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.ask p { margin: 0; flex: 1 1 260px; }
.ask .go { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.sbk-head h2.sub { font-size: 16px; }
.problems { margin: 0 12px 12px; padding: 10px 12px; border-radius: 8px; font-size: 13px; line-height: 1.45;
            background: color-mix(in srgb, var(--error-color, #db4437) 10%, transparent); color: var(--error-color, #db4437); }
.problems.warn { background: color-mix(in srgb, var(--warning-color, #ffa600) 12%, transparent); color: var(--primary-text-color); }
.problems ul { margin: 4px 0 0; padding-left: 18px; }
.detail { padding: 2px 16px 12px 68px; font-size: 13px; color: var(--secondary-text-color); line-height: 1.6;
          background: color-mix(in srgb, var(--primary-text-color) 2.5%, transparent); }
.detail b { color: var(--primary-text-color); font-weight: 500; }
.ent { display: inline-flex; align-items: center; gap: 4px; margin-right: 10px; white-space: nowrap; }
.ent i { width: 8px; height: 8px; border-radius: 4px; display: inline-block; background: var(--disabled-color, #9e9e9e); }
.ent.on i { background: var(--warning-color, #ffa600); }
.ent.occ i { background: var(--success-color, #43a047); }
.ent.na { opacity: .55; }
`;

class SbModesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" }).innerHTML = `<style>${STYLE}</style><ha-card><div class="body"></div></ha-card>`;
    this._sig = "";
    this._open = new Set();      // zones whose details are shown
    this._busy = false;
  }

  static getStubConfig() { return { title: "Modes" }; }
  setConfig(config) { this._config = { title: "Modes", ...(config || {}) }; this._sig = ""; this._render(); }
  getCardSize() { return 3 + this._zones().length; }

  connectedCallback() { this._tick = setInterval(() => { this._sig = ""; this._render(); }, 30000); }
  disconnectedCallback() { clearInterval(this._tick); }

  set hass(h) {
    this._hass = h;
    const sel = this._select(), prob = this._problem();
    const zones = this._zones();
    // the zones' own lights and sensors too, so an open detail updates as they change
    const watched = zones.flatMap((z) => [...(z.attr.lights || []), ...(z.attr.occupancy || [])]);
    const sig = JSON.stringify([sel && [sel.state, sel.attributes], prob && [prob.state, prob.attributes],
      zones.map((z) => [z.id, z.state, z.attr]), watched.map((e) => [h.states[e]?.state, h.states[e]?.attributes?.brightness])]);
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  _select() { return Object.values(this._hass?.states || {}).find((s) => s.entity_id.startsWith("select.") && s.attributes.sb_modes); }
  _problem() { return Object.values(this._hass?.states || {}).find((s) => s.entity_id.startsWith("binary_sensor.") && s.attributes.sb_modes_problem); }
  _zones() {
    return Object.values(this._hass?.states || {})
      .filter((s) => s.entity_id.startsWith("sensor.") && s.attributes.sb_modes_zone)
      .map((s) => ({ entity: s.entity_id, id: s.attributes.sb_modes_zone, name: s.attributes.zone_name || s.attributes.sb_modes_zone,
                     order: s.attributes.order ?? 999, state: s.state, attr: s.attributes }))
      .sort((a, b) => a.order - b.order || String(a.name).localeCompare(String(b.name)));
  }

  _kitReady() {
    const K = kit();
    if (K) {
      if (K.sheet && !this.shadowRoot.adoptedStyleSheets.includes(K.sheet)) this.shadowRoot.adoptedStyleSheets = [...this.shadowRoot.adoptedStyleSheets, K.sheet];
      return true;
    }
    if (!this._kitWaiting) {
      this._kitWaiting = true;
      whenKit().then((k) => { this._kitMissing = !k; this._render(); });
      window.addEventListener("sb-ui-kit-ready", () => { this._kitMissing = false; this._render(); }, { once: true });
    }
    this.shadowRoot.querySelector(".body").innerHTML = this._kitMissing ? KIT_MISSING : `<div class="sbk-wait" style="padding: 16px; color: var(--secondary-text-color)">Loading…</div>`;
    return false;
  }

  _render() {
    if (!this._config || !this._hass) return;
    if (!this._kitReady()) return;
    const sel = this._select();
    const body = this.shadowRoot.querySelector(".body");
    if (!sel) {
      body.innerHTML = `<div class="sbk-head"><h2>${esc(this._config.title)}</h2></div><div class="sbk-empty">SB Modes is not set up — add it under Settings → Devices &amp; services.</div>`;
      return;
    }
    const names = sel.attributes.mode_names || {};
    const nm = (id) => names[id] || id;
    const since = sel.attributes.since || sel.last_changed;
    if (this._ask && (this._ask === sel.state || !(sel.attributes.options || []).includes(this._ask))) this._ask = null;
    const modes = (sel.attributes.options || []).filter((id) => id !== sel.state).map((id) => `<button class="mode ${id === this._ask ? "on" : ""}" data-m="${esc(id)}"
        ${this._busy ? "disabled" : ""} title="Switch to ${esc(nm(id))}"><ha-icon icon="${esc(modeIcon(id))}"></ha-icon>${esc(nm(id))}</button>`).join("");
    let ask = "";
    if (this._ask) {
      const took = (sel.attributes.takes_over || {})[this._ask] || [];
      const zl = took.map((t) => t.zone);
      const list = zl.length > 1 ? `${zl.slice(0, -1).join(", ")} and ${zl.at(-1)}` : zl[0];
      const dark = took.some((t) => t.behaviour === "nightlight_then_dark") ? " Zones that are already empty go dark straight away." : "";
      ask = `<div class="ask"><p>Switch to <b>${esc(nm(this._ask))}</b> now? ${zl.length
          ? `It takes over ${esc(list)} at once.${dark}` : "It controls no zones, so nothing changes in the house."}</p>
        <button class="sbk-btn no">Cancel</button><button class="sbk-btn go">Switch</button></div>`;
    }
    const prob = this._problem();
    const errs = prob?.attributes.errors || [], warns = prob?.attributes.warnings || [];
    const problems = errs.length
      ? `<div class="problems"><b>The mode table needs attention</b><ul>${errs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`
      : warns.length ? `<div class="problems warn"><b>Warnings</b><ul>${warns.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : "";
    const zones = this._zones();
    const rows = zones.map((z) => this._row(z, names[sel.state] || sel.state)).join("");
    body.innerHTML = `
      <div class="now"><span class="sbk-ico armed big"><ha-icon icon="${esc(modeIcon(sel.state))}"></ha-icon></span>
        <div><div class="cur-l">Current mode</div><div class="cur">${esc(nm(sel.state))}</div>
          <div class="since">since ${esc(hhmm(since, this._hass.config?.time_zone))} (${esc(ago(since))} ago) · fallback ${esc(nm(sel.attributes.fallback_mode))}</div></div></div>
      <div class="switch-l">Switch to</div>
      <div class="modes" role="group" aria-label="Switch to another mode">${modes}</div>
      ${ask}
      ${problems}
      <div class="sbk-head"><h2 class="sub">Zones</h2>
        <span class="sbk-count">${zones.length} zone${zones.length === 1 ? "" : "s"}</span></div>
      ${rows || `<div class="sbk-empty">No zones yet. Add them to the table with the <code>sb_modes.save_config</code> action.</div>`}
      ${this._msg ? `<div class="sbk-note">${esc(this._msg)}</div>` : ""}`;
    this._wire();
  }

  _row(z, modeName) {
    const a = z.attr, beh = z.state;
    const doing = beh === "hands_off" ? `Not controlled in ${modeName}` : a.status || "";
    const l = look(beh, a.status);
    const open = this._open.has(z.id);
    const src = a.source === "override" ? "zone's own" : "mode default";
    const ent = (eid, kind) => {
      const st = this._hass.states[eid];
      const name = st?.attributes.friendly_name || eid;
      const s = st?.state ?? "missing";
      const pct = s === "on" && st?.attributes.brightness != null ? ` ${Math.round(st.attributes.brightness * 100 / 255)}%` : "";
      const cls = s === "on" ? (kind === "occ" ? "occ" : "on") : ["unavailable", "unknown", "missing"].includes(s) ? "na" : "";
      return `<span class="ent ${cls}" title="${esc(eid)}"><i></i>${esc(name)}${esc(pct)}${cls === "na" ? ` (${esc(s)})` : ""}</span>`;
    };
    const nightlight = a.params?.nightlight ? Object.entries(a.params.nightlight).map(([e, t]) =>
      `${esc(this._hass.states[e]?.attributes.friendly_name || e)} ${t.state === "off" ? "off" : t.brightness_pct != null ? `${t.brightness_pct}%` : "on"}`).join(", ") : "";
    const detail = open ? `<div class="detail">
        <div><b>Areas</b> ${esc((a.areas || []).join(", "))}</div>
        ${nightlight ? `<div><b>Nightlight</b> ${nightlight}${a.params.delay ? ` · dark after ${esc(a.params.delay)} min vacant` : ""}</div>` : ""}
        ${(a.occupancy || []).length ? `<div><b>Occupancy</b> ${a.occupancy.map((e) => ent(e, "occ")).join("")}</div>` : ""}
        ${(a.lights || []).length ? `<div><b>Lights</b> ${a.lights.map((e) => ent(e, "light")).join("")}</div>` : ""}
      </div>` : "";
    return `<div class="sbk-row" data-z="${esc(z.id)}">
        <button class="sbk-open" aria-expanded="${open}" title="${open ? "Hide" : "Show"} what ${esc(z.name)} holds">
          <span class="sbk-ico ${l.tint}"><ha-icon icon="${esc(l.icon)}"></ha-icon></span>
          <span class="sbk-txt"><span class="sbk-t">${esc(z.name)}</span>
            <span class="sbk-s"><span class="sbk-chip" title="${esc(src)}">${esc(BEHAVIOUR_NAMES[beh] || beh)}</span>${esc(doing)}</span></span>
        </button>
        <span class="sbk-kb" aria-hidden="true"><ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></span>
      </div>${detail}`;
  }

  _wire() {
    const root = this.shadowRoot.querySelector(".body");
    // A mode acts on the house the moment it starts, so a tap only asks; Switch confirms.
    root.querySelectorAll("button.mode").forEach((b) => b.addEventListener("click", () => {
      this._ask = this._ask === b.dataset.m ? null : b.dataset.m; this._render();
    }));
    root.querySelector(".ask .no")?.addEventListener("click", () => { this._ask = null; this._render(); });
    root.querySelector(".ask .go")?.addEventListener("click", async () => {
      const sel = this._select(), target = this._ask;
      this._ask = null; this._busy = true; this._render();
      try { await this._hass.callService("select", "select_option", { entity_id: sel.entity_id, option: target }); this._msg = ""; }
      catch (e) { this._msg = `Could not change the mode: ${e?.message || e}`; }
      finally { this._busy = false; this._sig = ""; this._render(); }
    });
    root.querySelectorAll(".sbk-row .sbk-open").forEach((b) => b.addEventListener("click", () => {
      const id = b.closest("[data-z]").dataset.z;
      if (this._open.has(id)) this._open.delete(id); else this._open.add(id);
      this._render();
    }));
  }
}
if (!customElements.get(CARD)) customElements.define(CARD, SbModesCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD)) {
  window.customCards.push({ type: CARD, name: "SB Modes", description: "The active mode, a picker, and what each zone is doing." });
}

class SbModesPanel extends HTMLElement {
  set hass(h) { this._hass = h; this._build(); this._card.hass = h; this._menu.hass = h; }
  set narrow(v) { this._narrow = v; if (this._menu) this._menu.narrow = v; }
  set route(_v) {}
  set panel(_v) {}

  _build() {
    if (this._card) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color); }
      .bar { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 12px; box-sizing: border-box; position: sticky; top: 0; z-index: 2;
             background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); border-bottom: 1px solid var(--divider-color); }
      .title { font-size: 20px; flex: 1; margin-left: 4px; }
      .bar a { color: inherit; font-size: 14px; opacity: .85; text-decoration: none; padding: 6px 10px; border-radius: 14px; }
      .bar a:hover { background: rgba(127,127,127,.2); opacity: 1; }
      .page { padding: 16px; } .col { max-width: 860px; margin: 0 auto; }
      .intro { color: var(--secondary-text-color); font-size: 14px; margin: 0 4px 12px; line-height: 1.5; }
      .ver { color: var(--secondary-text-color); font-size: 12px; margin: 12px 4px 0; }
    </style>
    <div class="bar"><ha-menu-button></ha-menu-button><div class="title">SB Modes</div><a href="/config/integrations/integration/sb_modes">Integration page</a></div>
    <div class="page"><div class="col">
      <div class="intro">The house is in one mode at a time; each zone runs one behaviour for it. Switching asks first, because a mode acts at once. Tap a zone to see its lights and sensors.</div>
      <div class="slot"></div>
      <div class="ver">SB Modes ${VERSION}</div>
    </div></div>`;
    this._menu = root.querySelector("ha-menu-button");
    this._menu.narrow = this._narrow;
    const card = document.createElement(CARD);
    card.setConfig({ title: "Modes" });
    root.querySelector(".slot").appendChild(card);
    this._card = card;
  }
}
if (!customElements.get("sb-modes-panel")) customElements.define("sb-modes-panel", SbModesPanel);
console.info(`%c SB-MODES %c v${VERSION} `, "background:#37474f;color:#fff", "background:#90a4ae;color:#000");

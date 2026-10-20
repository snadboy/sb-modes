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
const VERSION = "0.15.0";
const CARD = "sb-modes-card";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Times in Home Assistant's own time zone (its default for users), not the browser's.
// Times follow the user's Home Assistant setting (Profile → Time format: 12 / 24 / language),
// in Home Assistant's time zone. setTimeLocale() is called with each new hass.
let TIME = { h12: false, tz: undefined };
const setTimeLocale = (hass) => {
  const f = hass?.locale?.time_format;
  let h12 = f === "12" ? true : f === "24" ? false : null;
  if (h12 === null) {
    try { h12 = !!new Intl.DateTimeFormat(hass?.locale?.language || undefined, { hour: "numeric" }).resolvedOptions().hour12; }
    catch (e) { h12 = false; }
  }
  TIME = { h12, tz: hass?.config?.time_zone || undefined };
};
const clock = (h, m) => (TIME.h12 ? `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`
                                  : `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
const hhmm = (iso) => {
  if (!iso) return "";
  try {
    const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TIME.tz }).formatToParts(new Date(iso));
    return clock(Number(parts.find((p) => p.type === "hour").value), Number(parts.find((p) => p.type === "minute").value));
  } catch (e) { return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
};
/** Clock times SB Modes writes into statuses ("goes dark at 23:14", "at 22:10 switches to 'empty'") in the user's format. */
const fmtTimes = (text) => String(text || "").replace(/\b([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\b/g, (_, h, m) => clock(Number(h), Number(m)));
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
// Collapsed or not, per card (type + title), per browser — the same keys and button as the
// other SB cards. Storage can be blocked (private window): then it is simply not remembered.
const foldKey = (card, title) => `sb-card-collapsed:${card}:${title || ""}`;
const foldGet = (k) => { try { return localStorage.getItem(k) === "1"; } catch (e) { return false; } };
const foldSet = (k, v) => { try { if (v) localStorage.setItem(k, "1"); else localStorage.removeItem(k); } catch (e) { /* not remembered */ } };
const foldBtn = (folded) => `<button class="sbk-kb fold" title="${folded ? "Show the entries" : "Hide the entries"}" aria-label="${folded ? "Show the entries" : "Hide the entries"}" aria-expanded="${folded ? "false" : "true"}"><ha-icon icon="${folded ? "mdi:chevron-right" : "mdi:chevron-down"}"></ha-icon></button>`;
const fire = (node, type, detail) => node.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));

// HA lazy-loads ha-form with the card editors; force it in before the card editor shows.
const loadHaForm = async () => {
  if (customElements.get("ha-form")) return true;
  try {
    if (!window.loadCardHelpers) {
      await customElements.whenDefined("partial-panel-resolver");
      const ppr = document.createElement("partial-panel-resolver");
      ppr.hass = { panels: [{ url_path: "tmp", component_name: "lovelace" }] };
      ppr._updateRoutes();
      await ppr.routerOptions.routes.tmp.load();
    }
    const helpers = await window.loadCardHelpers();
    helpers.createCardElement({ type: "entities", entities: [] });
    await Promise.race([customElements.whenDefined("hui-entities-card"), new Promise((r) => setTimeout(r, 5000))]);
    const C = customElements.get("hui-entities-card");
    if (C?.getConfigElement) await C.getConfigElement();
  } catch (e) { /* the editor simply does not show */ }
  return !!customElements.get("ha-form");
};

const KIT_MISSING = `<div style="padding: 16px; color: var(--error-color)">The <b>SB UI Kit</b> integration is not loaded — install “SB UI Kit” from HACS and restart Home Assistant.</div>`;

const DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LABEL = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
/** ["mon","tue","wed","thu","fri"] → "Mon–Fri"; ["sun","mon"] → "Sun–Mon"; all → "Every day". */
const daysText = (days) => {
  const idx = [...new Set(days || [])].map((d) => DAY_NAMES.indexOf(d)).filter((i) => i >= 0).sort((a, b) => a - b);
  if (idx.length === 7) return "Every day";
  // runs on a ring, so Sun–Thu stays one run
  const set = new Set(idx), runs = [];
  const starts = idx.filter((i) => !set.has((i + 6) % 7));
  for (const st of (starts.length ? starts : [idx[0]])) {
    let e = st; while (set.has((e + 1) % 7) && (e + 1) % 7 !== st) e = (e + 1) % 7;
    const len = ((e - st + 7) % 7) + 1;
    runs.push(len >= 3 ? `${DAY_LABEL[DAY_NAMES[st]]}–${DAY_LABEL[DAY_NAMES[e]]}` : len === 2 ? `${DAY_LABEL[DAY_NAMES[st]]}, ${DAY_LABEL[DAY_NAMES[e]]}` : DAY_LABEL[DAY_NAMES[st]]);
  }
  return runs.join(", ");
};
const pretty = (id) => String(id || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
/** A rule's days in words: weekdays, or a day set ("Workday", "Night before a workday", "Not a workday"). */
const ruleDays = (r) => {
  if (!r.day_set) return daysText(r.days);
  const ds = pretty(r.day_set).toLowerCase();
  if (r.eve && r.not) return `Night before a non-${ds}`;
  if (r.eve) return `Night before a ${ds}`;
  if (r.not) return `Not a ${ds}`;
  return pretty(r.day_set);
};
/** "06:00" | "sunset-01:00" → "06:00" | "Sunset −1:00" */
const ruleAt = (at) => {
  const c = /^(\d\d):(\d\d)$/.exec(String(at));
  if (c) return clock(Number(c[1]), Number(c[2]));
  const m = /^(sunrise|sunset)(?:([+-])(\d\d):(\d\d))?$/.exec(String(at));
  if (!m) return at;
  return `${pretty(m[1])}${m[2] ? ` ${m[2] === "-" ? "−" : "+"}${Number(m[3])}:${m[4]}` : ""}`;
};
const dayOf = (iso, tz) => { try { return new Date(iso).toLocaleDateString([], { weekday: "short", timeZone: tz || undefined }); } catch (e) { return ""; } };
const HOLDS = [["next", null], ["15", "15 min"], ["30", "30 min"], ["60", "1 hour"], ["paused", "Until I resume"]];

const MODE_ICONS = {
  morning: "mdi:weather-sunset-up", day: "mdi:white-balance-sunny", evening: "mdi:weather-sunset-down",
  overnight: "mdi:weather-night", night: "mdi:weather-night", away: "mdi:home-export-outline",
};
const modeIcon = (id) => MODE_ICONS[id] || "mdi:home-clock-outline";

const BEHAVIOUR_NAMES = {
  hands_off: "Hands off",
  static: "Static",
  nightlight_then_dark: "Nightlight, then dark",
  vacancy_off: "Off when empty",
  occupied_vacant: "Occupied / empty",
  wake_sequence: "Wake-up",
  presence_lux: "Presence + light level",
};

/** A zone's live status → the row's icon, its tint and the words. */
const PRESS = { multi_press_1: "press", multi_press_2: "double press", multi_press_3: "triple press", long_press: "hold", long_release: "hold (on release)" };

const look = (behaviour, status) => {
  const s = String(status || "");
  if (s.startsWith("held")) return { icon: "mdi:hand-back-right-outline", tint: "warn" };
  if (s.startsWith("overlay:")) return { icon: "mdi:television-play", tint: "run" };
  if (behaviour === "hands_off") return { icon: "mdi:hand-back-left-off-outline", tint: "idle" };
  if (s.startsWith("dark")) return { icon: "mdi:weather-night", tint: "idle" };
  if (s === "nightlight") return { icon: "mdi:lightbulb-night-outline", tint: "run" };
  if (s === "occupied" || s.startsWith("occupied;")) return { icon: "mdi:account", tint: "run" };
  if (s === "on" || s.startsWith("on:")) return { icon: "mdi:lightbulb-on-outline", tint: "run" };
  if (s.startsWith("vacant; 'empty, light' since")) return { icon: "mdi:lightbulb-off-outline", tint: "idle" };
  if (s.startsWith("vacant; 'empty' since")) return { icon: "mdi:lightbulb-night-outline", tint: "idle" };
  if (s.startsWith("next step")) return { icon: "mdi:weather-sunset-up", tint: "armed" };
  if (behaviour === "wake_sequence") return { icon: "mdi:alarm", tint: "idle" };
  if (s === "vacant; lights off") return { icon: "mdi:lightbulb-off-outline", tint: "idle" };
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
.now { display: flex; align-items: center; gap: 14px; padding: 4px 16px 10px; border-top: 1px solid var(--divider-color); padding-top: 14px; }
.sbk-ico.big { width: 56px; height: 56px; flex: 0 0 56px; border-radius: 28px; --mdc-icon-size: 30px; }
.cur-l { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--secondary-text-color); }
.cur { font-size: 26px; font-weight: 500; line-height: 1.15; }
.since { font-size: 13px; color: var(--secondary-text-color); }
.switch-l { padding: 4px 16px 6px; font-size: 13px; color: var(--secondary-text-color); }
.ask { margin: 0 12px 12px; padding: 10px 12px; border-radius: 8px; font-size: 14px; line-height: 1.45;
       background: color-mix(in srgb, var(--primary-color) 10%, transparent); display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.ask p { margin: 0; flex: 1 1 260px; }
.ask .go { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.hold { margin: 0 12px 10px; padding: 8px 12px; border-radius: 8px; font-size: 13px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
        background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
.hold span { flex: 1 1 200px; }
.hold.paused { background: color-mix(in srgb, var(--warning-color, #ffa600) 20%, transparent); font-weight: 500; }
.hold.quiet { background: none; color: var(--secondary-text-color); padding-top: 0; }
.holds { flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 13px; color: var(--secondary-text-color); }
.holds .mode { height: 30px; padding: 0 12px; font-size: 13px; }
button.zt { display: flex; align-items: center; gap: 6px; width: 100%; border: none; border-top: 1px solid var(--divider-color); background: none; cursor: pointer;
            text-align: left; font-family: inherit; padding-top: 10px; }
button.zt .cnt { text-transform: none; letter-spacing: 0; font-weight: 400; }
button.zt ha-icon { margin-left: auto; --mdc-icon-size: 20px; }
.rules { padding: 0 16px 10px; }
.rule { display: grid; grid-template-columns: 104px 22px 100px 1fr; align-items: center; gap: 6px; font-size: 14px; padding: 4px 0; --mdc-icon-size: 18px; }
.rule .t { font-variant-numeric: tabular-nums; font-weight: 500; }
.rule .d { color: var(--secondary-text-color); }
.zh { padding: 6px 16px 8px; font-size: 13px; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .05em; }
.pvtag { margin-left: 6px; padding: 1px 7px; border-radius: 9px; font-size: 11px; letter-spacing: .04em;
         background: color-mix(in srgb, var(--warning-color, #ffa600) 18%, transparent); color: var(--primary-text-color); }
.curtag { margin-left: 8px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; opacity: .75; }
.mode.on .curtag { opacity: .9; }
.ask .make { border-color: var(--primary-color); color: var(--primary-color); }
.ask.sure { background: color-mix(in srgb, var(--warning-color, #ffa600) 14%, transparent); }
.sbk-row.pv .sbk-ico { opacity: .8; }
.detail .src { opacity: .75; }
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
  static getConfigElement() { return document.createElement(`${CARD}-editor`); }
  setConfig(config) { this._config = { title: "Modes", ...(config || {}) }; this._sig = ""; this._render(); }
  getCardSize() { return 3 + this._zones().length; }

  connectedCallback() { this._tick = setInterval(() => { this._sig = ""; this._render(); }, 30000); }
  disconnectedCallback() { clearInterval(this._tick); this._unMenu?.(); }

  set hass(h) {
    this._hass = h;
    setTimeLocale(h);
    const sel = this._select(), prob = this._problem();
    const zones = this._zones();
    // the zones' own lights and sensors too, so an open detail updates as they change
    const watched = zones.flatMap((z) => [...(z.attr.lights || []), ...(z.attr.occupancy || [])]);
    const dev = this._deviations();
    const sig = JSON.stringify([sel && [sel.state, sel.attributes], prob && [prob.state, prob.attributes], dev && dev.state,
      zones.map((z) => [z.id, z.state, z.attr]), watched.map((e) => [h.states[e]?.state, h.states[e]?.attributes?.brightness])]);
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  _select() { return Object.values(this._hass?.states || {}).find((s) => s.entity_id.startsWith("select.") && s.attributes.sb_modes); }
  _deviations() { return Object.values(this._hass?.states || {}).find((s) => s.entity_id.startsWith("sensor.") && s.attributes.sb_modes_deviations); }
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
    this._modeName = nm;
    const options = sel.attributes.options || [];
    const since = sel.attributes.since || sel.last_changed;
    // Looking at a mode and switching to it are separate: a tap on a mode only VIEWS it.
    if (this._view && (this._view === sel.state || !options.includes(this._view))) { this._view = null; this._ask = false; }
    const viewed = this._view || sel.state;
    const previewing = viewed !== sel.state;
    const chips = options.map((id) => `<button class="mode ${id === viewed ? "on" : ""}" data-m="${esc(id)}" ${this._busy ? "disabled" : ""}
        title="${id === sel.state ? "The current mode" : `See what ${esc(nm(id))} does (does not switch)`}"><ha-icon icon="${esc(modeIcon(id))}"></ha-icon>${esc(nm(id))}${id === sel.state ? `<span class="curtag">current</span>` : ""}</button>`).join("");
    const tz = this._hass.config?.time_zone;
    const man = sel.attributes.manual, nxt = sel.attributes.next_change;
    const at = (iso) => `${hhmm(iso)}${dayOf(iso, tz) !== dayOf(new Date().toISOString(), tz) ? ` ${dayOf(iso, tz)}` : ""}`;
    const nextTxt = nxt ? `${nm(nxt.mode)} at ${at(nxt.at)}` : "";
    let status = "";
    if (man?.hold === "paused") {
      status = `<div class="hold paused"><span>Schedule paused since ${esc(at(man.since))}${nextTxt ? ` · skipping ${esc(nextTxt)}` : ""}</span><button class="sbk-btn resume">Resume schedule</button></div>`;
    } else if (man?.hold === "timed") {
      status = `<div class="hold"><span>Set by hand · back to ${esc(nm(sel.attributes.scheduled_mode) || "the schedule")} at ${esc(at(man.until))}</span><button class="sbk-btn resume">Resume schedule</button></div>`;
    } else if (man?.hold === "next") {
      status = `<div class="hold"><span>Set by hand · until the next scheduled change${nextTxt ? ` (${esc(nextTxt)})` : ""}</span><button class="sbk-btn resume">Resume schedule</button></div>`;
    } else if (nextTxt) {
      status = `<div class="hold quiet"><span>On schedule · next: ${esc(nextTxt)}</span></div>`;
    }
    let banner = "";
    if (previewing && !this._ask) {
      banner = `<div class="ask"><p>Showing <b>${esc(nm(viewed))}</b>. Nothing has changed: the house is still in <b>${esc(nm(sel.state))}</b>.</p>
        <button class="sbk-btn back">Back to ${esc(nm(sel.state))}</button><button class="sbk-btn make">Make ${esc(nm(viewed))} the current mode…</button></div>`;
    } else if (previewing) {
      const took = (sel.attributes.takes_over || {})[viewed] || [];
      const zl = took.map((t) => t.zone);
      const list = zl.length > 1 ? `${zl.slice(0, -1).join(", ")} and ${zl.at(-1)}` : zl[0];
      const dark = took.some((t) => t.behaviour === "nightlight_then_dark") ? " Zones that are already empty go dark straight away." : "";
      const hold = this._hold || "next";
      const holds = HOLDS.map(([v, label]) => `<button class="mode hold-opt ${v === hold ? "on" : ""}" data-h="${v}">${esc(
        label || (nextTxt ? `Until ${nextTxt}` : "Until the next scheduled change"))}</button>`).join("");
      banner = `<div class="ask sure"><p>Switch the house to <b>${esc(nm(viewed))}</b> now? ${zl.length
          ? `It takes over ${esc(list)} at once.${dark}` : "It controls no zones, so nothing changes in the house."}</p>
        <div class="holds"><span>For how long:</span>${holds}</div>
        <button class="sbk-btn no">Cancel</button><button class="sbk-btn go">Switch to ${esc(nm(viewed))}</button></div>`;
    }
    const prob = this._problem();
    const errs = prob?.attributes.errors || [], warns = prob?.attributes.warnings || [];
    const problems = errs.length
      ? `<div class="problems"><b>The mode table needs attention</b><ul>${errs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`
      : warns.length ? `<div class="problems warn"><b>Warnings</b><ul>${warns.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : "";
    const zones = this._zones();
    const rows = zones.map((z) => this._row(z, viewed, nm(viewed), previewing)).join("");
    const cfg = this._config;
    const folded = !cfg.panel && foldGet(foldKey(CARD, cfg.title));
    const attention = errs.length ? 1 + zones.filter((z) => look(z.state, z.attr.status).tint === "warn").length
      : zones.filter((z) => look(z.state, z.attr.status).tint === "warn").length;
    const devCount = Number(this._deviations()?.state || 0);
    const headMenu = !cfg.panel && this._menu === "__head" ? kit().menu("Card actions", [{ act: "page", icon: "mdi:open-in-app", label: "Open the SB Modes page" }]) : "";
    const head = `<div class="sbk-head sbk-hwrap"><h2>${esc(cfg.title || "")}</h2>
        <span class="sbk-count">${esc(nm(sel.state))}${sel.attributes.manual?.hold === "paused" ? ` <span style="color: var(--warning-color, #ffa600)">(paused)</span>` : sel.attributes.manual ? " (by hand)" : ""} · ${options.length} mode${options.length === 1 ? "" : "s"} · ${zones.length} zone${zones.length === 1 ? "" : "s"}${
          devCount ? ` · <span style="color: var(--warning-color, #ffa600)" title="Held or changed by hand — see sensor.sb_modes_deviations">${devCount} by hand</span>` : ""}${
          folded && attention ? ` · <span style="color: var(--error-color)">${attention} need${attention === 1 ? "s" : ""} attention</span>` : ""}</span>
        ${cfg.panel ? "" : `<button class="sbk-kb hkb ${headMenu ? "on" : ""}" aria-label="More" title="More: open the SB Modes page" aria-expanded="${headMenu ? "true" : "false"}">${kit().kbIcon}</button>${headMenu}${foldBtn(folded)}`}
      </div>`;
    if (folded) { body.innerHTML = head; this._wire(); return; }
    body.innerHTML = `${head}
      <div class="now"><span class="sbk-ico armed big"><ha-icon icon="${esc(modeIcon(sel.state))}"></ha-icon></span>
        <div><div class="cur-l">Current mode</div><div class="cur">${esc(nm(sel.state))}</div>
          <div class="since">since ${esc(hhmm(since))} (${esc(ago(since))} ago) · fallback ${esc(nm(sel.attributes.fallback_mode))}</div></div></div>
      ${status}
      <div class="switch-l">Modes · tap one to see what it does</div>
      <div class="modes" role="group" aria-label="See a mode">${chips}</div>
      ${banner}
      ${problems}
      <div class="zh">Zones${previewing ? ` in ${esc(nm(viewed))} <span class="pvtag">preview</span>` : ""}</div>
      ${rows || `<div class="sbk-empty">No zones yet. Add them to the table with the <code>sb_modes.save_config</code> action.</div>`}
      ${this._scheduleHtml(sel, nm)}
      ${this._msg ? `<div class="sbk-note">${esc(this._msg)}</div>` : ""}`;
    this._wire();
  }

  _scheduleHtml(sel, nm) {
    const key = (r) => (/^\d\d:\d\d$/.test(r.at) ? r.at : /^sunrise/.test(r.at) ? "06:59" : "17:59");   // sun rules sort near when they fall
    const rules = [...(sel.attributes.schedule || [])].sort((a, b) => key(a).localeCompare(key(b)));
    const open = this._open.has("__schedule");
    const body = !rules.length ? `<div class="sbk-empty">No schedule — the mode changes only by hand.</div>`
      : open ? rules.map((r) => `<div class="rule"><span class="t">${esc(ruleAt(r.at))}</span><ha-icon icon="${esc(modeIcon(r.mode))}"></ha-icon><span class="m">${esc(nm(r.mode))}</span><span class="d">${esc(ruleDays(r))}</span></div>`).join("") : "";
    return `<button class="zh zt" data-x="__schedule" aria-expanded="${open}">Schedule <span class="cnt">${rules.length} rule${rules.length === 1 ? "" : "s"}</span>
        <ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button>${rules.length && !open ? "" : `<div class="rules">${body}</div>`}`;
  }

  _name(e) { return this._hass.states[e]?.attributes.friendly_name || e; }

  _buttonAct(act) {
    const flash = act.flash ? `flash${act.flash > 1 ? ` ×${act.flash}` : ""}, ` : "";
    if (act === "release" || act.release) return `${flash}release`;
    const hold = act.hold === "paused" ? "hold until released" : act.hold === "next" ? "hold until the next mode" : `hold ${act.hold} min`;
    return `${flash}${hold}${act.targets ? ` at ${this._targets(act.targets)}` : ""}`;
  }

  _targets(targets) {
    return Object.entries(targets || {}).map(([e, t]) => `${this._name(e)} ${
      t.value != null ? t.value : t.state === "off" ? "off" : t.brightness_pct != null ? `${t.brightness_pct}%` : "on"}${t.effect ? ` (${t.effect})` : ""}`).join(", ");
  }

  /** What a behaviour does with these parameters, in words. */
  _describe(beh, p = {}) {
    if (beh === "hands_off") return "Not controlled";
    if (beh === "static") return `Sets ${this._targets(p.targets)}`;
    if (beh === "nightlight_then_dark") {
      const nl = this._targets(p.nightlight);
      return `${nl ? `Nightlight (${nl}) when someone comes in · ` : ""}dark after ${p.delay ?? 10} min empty`;
    }
    if (beh === "vacancy_off") {
      const secs = p.off_after_seconds ?? 60;
      const after = secs >= 120 && secs % 60 === 0 ? `${secs / 60} min` : `${secs} s`;
      return `${(p.lights || []).map((e) => this._name(e)).join(", ")} off ${after} after empty${
        p.max_on_minutes ? ` · or once on ${p.max_on_minutes} min while empty` : ""}${p.on_when_occupied ? " · on when someone comes in" : ""}`;
    }
    if (beh === "occupied_vacant") {
      const after = p.vacant_after_minutes ? ` (after ${p.vacant_after_minutes} min)` : "";
      return `Occupied: ${this._targets(p.occupied)} · Empty${after}: ${this._targets(p.vacant)}`;
    }
    if (beh === "wake_sequence") {
      const steps = (p.steps || []).map((st) => `+${st.after_minutes} min: ${this._targets(st.targets)}`).join(" · ");
      return `After ${this._modeName?.(p.after_mode) || p.after_mode} ends: ${steps}`;
    }
    if (beh === "presence_lux") {
      const vac = p.vacant && Object.keys(p.vacant).length ? this._targets(p.vacant) : "lights off";
      const bright = p.vacant_bright && Object.keys(p.vacant_bright).length ? this._targets(p.vacant_bright) : null;
      return `Occupied and below ${p.target_lux} lx: ${this._targets(p.occupied)} · Empty ${p.vacant_after_minutes ?? 15} min: ${
        bright ? `dark → ${vac}; light → ${bright}` : vac}`;
    }
    return beh;
  }

  _row(z, viewed, modeName, previewing) {
    const a = z.attr;
    const pick = (a.by_mode || {})[viewed] || { behaviour: z.state, params: a.params, source: a.source };
    const beh = pick.behaviour;
    const enter = (a.on_enter || {})[viewed];
    const what = this._describe(beh, pick.params) + (enter ? ` · when ${modeName} begins: ${this._targets(enter)}` : "");
    const held = !previewing && String(a.status || "").startsWith("held");
    // live: "Vacant since 12:12 PM · lights off" — occupancy first, then what the behaviour is doing
    const occ = a.occupancy_state === "occupied" ? `Occupied since ${hhmm(a.occupancy_since)}`
      : a.occupancy_state === "vacant" ? `Vacant since ${hhmm(a.occupancy_since)}`
      : a.occupancy_state === "unknown" ? "Occupancy unknown (sensor unavailable)" : "";
    const rest = held ? a.status : beh === "hands_off" ? `not controlled in ${modeName}`
      : String(a.status || "").replace(/^(vacant|occupied)(;\s*|$)/, "").replace(/^overlay:\s*/, "");
    const doing = previewing ? what : [occ, fmtTimes(rest)].filter(Boolean).join(" · ");
    const l = previewing ? { icon: look(beh, "").icon, tint: "idle" } : look(beh, a.status);
    const open = this._open.has(z.id);
    const src = pick.source === "override" ? "this zone's own setting for this mode" : pick.source === "zone" ? "this zone's setting for all modes" : "the mode's default";
    const ent = (eid, kind) => {
      const st = this._hass.states[eid];
      const name = st?.attributes.friendly_name || eid;
      const s = st?.state ?? "missing";
      const pct = s === "on" && st?.attributes.brightness != null ? ` ${Math.round(st.attributes.brightness * 100 / 255)}%` : "";
      const cls = s === "on" ? (kind === "occ" ? "occ" : "on") : ["unavailable", "unknown", "missing"].includes(s) ? "na" : "";
      return `<span class="ent ${cls}" title="${esc(eid)}"><i></i>${esc(name)}${esc(pct)}${cls === "na" ? ` (${esc(s)})` : ""}</span>`;
    };
    const detail = open ? `<div class="detail">
        <div><b>In ${esc(modeName)}</b> ${esc(what)} <span class="src">(${esc(src)})</span></div>
        <div><b>Areas</b> ${esc((a.areas || []).join(", "))}</div>
        ${a.follow ? `<div><b>Follows ${esc(this._name(a.follow.leader))}</b> (every mode) ${[...a.follow.bands].sort((x, y) => (y.min_pct || 0) - (x.min_pct || 0))
            .map((b) => `${b.min_pct ? `≥${b.min_pct}%` : "off / low"} → ${esc(this._targets(b.targets))}`).join(" · ")}</div>` : ""}
        ${a.overlay_config ? `<div><b>${esc(this._name(a.overlay_config.entity))}</b> (every mode, once a state lasts ${esc(a.overlay_config.debounce_seconds ?? 5)} s) ${Object.entries(a.overlay_config.states || {})
            .map(([st, c]) => `${esc(st)} → ${esc(this._targets(c.targets || {}) || "nothing")}${c.holds ? " (holds the zone)" : ""}`).join(" · ")}</div>` : ""}
        ${a.buttons ? `<div><b>Buttons</b> ${Object.entries(a.buttons).map(([ent, acts]) => `${esc(this._name(ent))}: ${Object.entries(acts)
            .map(([t, act]) => `${esc(PRESS[t] || t)} → ${esc(this._buttonAct(act))}`).join(", ")}`).join(" · ")}</div>` : ""}
        ${(a.occupancy || []).length ? `<div><b>Occupancy</b> ${a.occupancy.map((e) => ent(e, "occ")).join("")}</div>` : ""}
        ${(a.lights || []).length ? `<div><b>Lights</b> ${a.lights.map((e) => ent(e, "light")).join("")}</div>` : ""}
      </div>` : "";
    // live view only: lights changed by hand, and the hold / release menu
    const ov = previewing ? {} : (a.overrides || {});
    const ovNames = Object.keys(ov).map((e) => this._hass.states[e]?.attributes.friendly_name || e);
    // the same delay SB Modes uses (bookkeeping.release_delay): delay / vacant_after / off-when-empty (capped)
    const pp = pick.params || {};
    let rel = pp.delay ?? pp.vacant_after_minutes;
    if (rel == null && pp.off_after_seconds != null) rel = Math.min(pp.off_after_seconds / 60, pp.max_on_minutes || Infinity);
    rel = rel ?? 10;
    const relTxt = rel < 1 ? `${Math.round(rel * 60)} s` : `${Math.round(rel * 10) / 10} min`;
    const third = ovNames.length ? `<span class="sbk-m warn">Changed by hand: ${esc(ovNames.join(", "))} · left alone until ${esc(z.name)} has been empty ${esc(relTxt)} or the mode changes</span>` : "";
    const key = `z:${z.id}`;
    const menu = !previewing && this._menu === key ? kit().menu(`${z.name} actions`, [
      { act: "hold:15", icon: "mdi:hand-back-right-outline", label: "Hold 15 min" },
      { act: "hold:30", icon: "mdi:hand-back-right-outline", label: "Hold 30 min" },
      { act: "hold:60", icon: "mdi:hand-back-right-outline", label: "Hold 1 hour" },
      { act: "hold:next", icon: "mdi:hand-back-right-outline", label: "Hold until the next mode change" },
      { act: "hold:paused", icon: "mdi:hand-back-right-off-outline", label: "Hold until released" },
      "-",
      { act: "release", icon: "mdi:play-outline", label: `Release — back to ${modeName}`, disabled: !a.hold && !ovNames.length },
    ]) : "";
    return `<div class="sbk-row ${previewing ? "pv" : ""}" data-z="${esc(z.id)}">
        <button class="sbk-open" aria-expanded="${open}" title="${open ? "Hide" : "Show"} what ${esc(z.name)} holds">
          <span class="sbk-ico ${l.tint}"><ha-icon icon="${esc(l.icon)}"></ha-icon></span>
          <span class="sbk-txt"><span class="sbk-t">${esc(z.name)}</span>
            <span class="sbk-s"><span class="sbk-chip" title="${esc(src)}">${esc(BEHAVIOUR_NAMES[beh] || beh)}</span>${esc(doing)}</span>${third}</span>
        </button>
        ${previewing ? "" : `<button class="sbk-kb zkb ${menu ? "on" : ""}" aria-label="Hold or release ${esc(z.name)}" title="Hold or release ${esc(z.name)}" aria-expanded="${menu ? "true" : "false"}">${kit().kbIcon}</button>${menu}`}
      </div>${detail}`;
  }

  _setMenu(id) {
    this._menu = id;
    // one outside click / Escape closes it; installed only while a menu is open
    this._unMenu?.();
    if (id) { const off = kit().away(() => this._setMenu(null)); this._unMenu = () => { off(); this._unMenu = null; }; }
    this._render();
  }

  _wire() {
    const root = this.shadowRoot.querySelector(".body");
    // A tap on a mode only shows it. Switching takes two deliberate steps: "Make … the
    // current mode…", then "Switch to …" in the confirmation that names what it takes over.
    root.querySelectorAll("button.mode").forEach((b) => b.addEventListener("click", () => {
      const sel = this._select();
      this._view = b.dataset.m === sel?.state ? null : b.dataset.m; this._ask = false; this._render();
    }));
    root.querySelector(".ask .back")?.addEventListener("click", () => { this._view = null; this._ask = false; this._render(); });
    root.querySelector(".ask .make")?.addEventListener("click", () => { this._ask = true; this._render(); });
    root.querySelector(".ask .no")?.addEventListener("click", () => { this._ask = false; this._render(); });
    root.querySelectorAll(".ask .hold-opt").forEach((b) => b.addEventListener("click", () => { this._hold = b.dataset.h; this._render(); }));
    root.querySelector(".hold .resume")?.addEventListener("click", async () => {
      try { await this._hass.callService("sb_modes", "resume_schedule", {}); this._msg = ""; }
      catch (e) { this._msg = `Could not resume the schedule: ${e?.message || e}`; }
      this._sig = ""; this._render();
    });
    root.querySelector("button.zt")?.addEventListener("click", (e) => {
      const k = e.currentTarget.dataset.x; if (this._open.has(k)) this._open.delete(k); else this._open.add(k); this._render();
    });
    root.querySelector(".ask .go")?.addEventListener("click", async () => {
      const target = this._view, h = this._hold || "next";
      this._ask = false; this._busy = true; this._render();
      try { await this._hass.callService("sb_modes", "set_mode", { mode: target, hold: /^\d+$/.test(h) ? Number(h) : h }); this._msg = ""; this._view = null; this._hold = "next"; }
      catch (e) { this._msg = `Could not change the mode: ${e?.message || e}`; }
      finally { this._busy = false; this._sig = ""; this._render(); }
    });
    root.querySelector("button.hkb")?.addEventListener("click", () => this._setMenu(this._menu === "__head" ? null : "__head"));
    root.querySelector("button.fold")?.addEventListener("click", () => {
      const k = foldKey(CARD, this._config.title); foldSet(k, !foldGet(k)); this._render();
    });
    root.querySelectorAll(".sbk-row .zkb").forEach((b) => b.addEventListener("click", () => {
      const k = `z:${b.closest("[data-z]").dataset.z}`; this._setMenu(this._menu === k ? null : k);
    }));
    root.querySelectorAll(".sbk-menu button").forEach((b) => b.addEventListener("click", async () => {
      const act = b.dataset.act, zone = b.closest("[data-z]")?.dataset.z;
      this._setMenu(null);
      if (act === "page") { history.pushState(null, "", "/sb-modes"); window.dispatchEvent(new CustomEvent("location-changed")); return; }
      try {
        if (act.startsWith("hold:")) {
          const h = act.slice(5);
          await this._hass.callService("sb_modes", "hold_zone", { zone, hold: /^\d+$/.test(h) ? Number(h) : h });
        } else if (act === "release") {
          await this._hass.callService("sb_modes", "release_zone", { zone });
        }
        this._msg = "";
      } catch (e) { this._msg = `Could not change ${zone}: ${e?.message || e}`; }
      this._sig = ""; this._render();
    }));
    root.querySelectorAll(".sbk-row .sbk-open").forEach((b) => b.addEventListener("click", () => {
      const id = b.closest("[data-z]").dataset.z;
      if (this._open.has(id)) this._open.delete(id); else this._open.add(id);
      this._render();
    }));
  }
}
class SbModesCardEditor extends HTMLElement {
  setConfig(config) { this._config = { ...config }; this._render(); }
  set hass(hass) { this._hass = hass; if (this._form) this._form.hass = hass; }
  async _render() {
    if (!(await loadHaForm())) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.hass = this._hass;
      this._form.schema = [{ name: "title", selector: { text: {} } }];
      this._form.computeLabel = (sch) => ({ title: "Title" }[sch.name]);
      this._form.addEventListener("value-changed", (e) => { e.stopPropagation(); this._config = { ...this._config, ...e.detail.value }; fire(this, "config-changed", { config: this._config }); });
      this.appendChild(this._form);
    }
    this._form.data = { title: "Modes", ...this._config };
  }
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD)) {
  window.customCards.push({ type: CARD, name: "SB Modes Card", description: "The current mode, what each mode does per zone, and a deliberate, confirmed switch.",
                            preview: true, documentationURL: "https://github.com/snadboy/sb-modes" });
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
      <div class="intro">The house is in one mode at a time; each zone runs one behaviour for it. Tap a mode to see what it does; switching is a separate, confirmed step because a mode acts at once. Tap a zone for its lights and sensors.</div>
      <div class="slot"></div>
      <div class="ver">SB Modes ${VERSION}</div>
    </div></div>`;
    this._menu = root.querySelector("ha-menu-button");
    this._menu.narrow = this._narrow;
    const card = document.createElement(CARD);
    card.setConfig({ title: "Modes", panel: true });
    root.querySelector(".slot").appendChild(card);
    this._card = card;
  }
}
// Register the elements — and register them AGAIN if they go missing. Since HA 2026.10 the
// frontend replaces window.customElements with a scoped-registry polyfill on dashboard pages,
// after integration modules (add_extra_js_url) have already run, so a definition made at load
// is lost and the card shows "Configuration error" (seen 2026-10-18). A fresh subclass is
// defined each time: a constructor cannot be registered twice. HA's error card rebuilds itself
// once the tag is defined in the current registry.
const DEFS = [[CARD, SbModesCard], [`${CARD}-editor`, SbModesCardEditor], ["sb-modes-panel", SbModesPanel]];
const ensureDefined = () => {
  for (const [tag, C] of DEFS) {
    if (customElements.get(tag)) continue;
    try { customElements.define(tag, class extends C {}); } catch (e) { /* defined meanwhile */ }
  }
};
// Not only at load: HA can swap the registry again later (when a part of the frontend that
// needs scoped registries is first loaded), and a dashboard redrawn after that — e.g. after
// HA restarts — shows "Configuration error" again (2026-10-19, a long-open tab). So check
// for good, cheaply: on every navigation, and every 2 s (a few customElements.get calls).
ensureDefined();
setInterval(ensureDefined, 2000);
window.addEventListener("location-changed", ensureDefined);
console.info(`%c SB-MODES %c v${VERSION} `, "background:#37474f;color:#fff", "background:#90a4ae;color:#000");

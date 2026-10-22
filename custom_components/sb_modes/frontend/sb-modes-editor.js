/* SB Modes — the editor (sidebar page /sb-modes/edit). Loaded by the page only when opened.
 *
 * Draft → check → review → save, against the integration's websocket commands (editor.py):
 * every edit changes a working copy of the table and, after a short pause, is stored as the
 * shared draft (sb_modes/draft/set), which answers with its errors (with field paths) and the
 * changes in words. Save turns the draft into the table (a new version); Discard drops it.
 *
 * Three views (the structure agreed from the mock-ups, 2026-10-21):
 *   House — rooms × modes: what each room does in each mode, "Usual" or its own setting, and
 *           what is set when a mode begins; mode headings open the mode (name, icon, order).
 *   Times — the day types (Vacation, Workdays, Days off…) in priority order, each with its
 *           mode changes on a 24-hour band; other rules; the coming week.
 *   Room  — one room top to bottom: Usually · By mode (behaviour + when it begins) · Add-ons
 *           (follow, TV overlay, hold button, wake-up, auto-off: in every mode) · Devices.
 *
 * Light settings ("targets") live in several places; a LOC says which:
 *   {k:"choice", owner, key, step?}  a behaviour's param (owner: {kind:"zone", zone, mode} or
 *                                    {kind:"mode", mode} for a mode's default)
 *   {k:"step"|"band"|"overlay"|"button", zone, addon, …}   an add-on's step / follow band / TV state / press
 *   {k:"band", zone, band}           a follow band
 *   {k:"overlay", zone, state}       a TV state
 *   {k:"button", zone, ent, etype}   a button press that holds
 *
 * Typing never redraws (it would lose focus): field edits change the model and the draft
 * bar; only structural changes redraw.
 */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));
const ALL = "*";
const DEFAULT_ICONS = {
  morning: "mdi:weather-sunset-up", day: "mdi:white-balance-sunny", evening: "mdi:weather-sunset-down",
  overnight: "mdi:weather-night", night: "mdi:weather-night", away: "mdi:home-export-outline",
};
const DAY_NAMES = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
const MODE_FILL = ["#3d2b5a", "#1f5a4f", "#5a2b44", "#4a4a2a"];          // for modes beyond the usual four
const KNOWN_FILL = { overnight: "#2b3a5a", night: "#2b3a5a", morning: "#6a4f1f", day: "#56541b", evening: "#6b3a26", away: "#3a3a3a" };
const OVERLAY_STATES = ["playing", "paused", "idle", "off"];

const ADDON_LEGACY = ["overlay", "buttons", "follow", "auto_off", "wake_up", "on_enter"];
/** An older table's per-room keys as add-on instances (model.zone_addons in Python). */
function toAddons(z) {
  if (Array.isArray(z.addons) && !ADDON_LEGACY.some((k) => k in z)) return z;
  const out = Array.isArray(z.addons) ? [...z.addons] : [];
  if (!Array.isArray(z.addons)) {
    if (z.overlay) out.push({ id: "tv", name: "TV lighting", type: "while", ...z.overlay });
    const bt = Object.entries(z.buttons || {});
    bt.forEach(([entity, presses], n) => out.push({ id: `button-${n + 1}`, name: bt.length === 1 ? "Hold button" : `Button ${n + 1}`, type: "button", entity, presses }));
    if (z.follow) out.push({ id: "follow", name: "Follow", type: "follow", ...z.follow });
    (z.auto_off || []).forEach((r, n) => out.push({ id: `auto-off-${n + 1}`, name: "Auto-off", type: "auto_off", ...r }));
    if (z.wake_up) out.push({ id: "wake-up", name: "Wake-up", type: "lights", trigger: { mode_ends: [z.wake_up.after_mode] }, steps: z.wake_up.steps || [] });
    const groups = [];
    for (const [m, t] of Object.entries(z.on_enter || {})) {
      const g = groups.find((x) => JSON.stringify(x[0]) === JSON.stringify(t));
      if (g) g[1].push(m); else groups.push([t, [m]]);
    }
    groups.forEach(([targets, modes], n) => out.push({ id: `begins-${n + 1}`, name: "When the mode begins", type: "lights",
      trigger: { mode_begins: modes }, steps: [{ after_minutes: 0, targets }] }));
  }
  const y = { ...z };
  ADDON_LEGACY.forEach((k) => delete y[k]);
  if (out.length) y.addons = out; else delete y.addons;
  return y;
}
const ADDON_NEW = [
  ["time", "Lights at a time of day"], ["begins", "Lights when a mode begins"], ["ends", "Lights when a mode ends"],
  ["state", "When a device changes (lights or a script)"], ["while", "TV lighting (while a device is …)"],
  ["button", "Hold / release button"], ["follow", "Follow a leader light"], ["auto_off", "Auto-off (lights turned on by hand)"],
];
const KIND_LABEL = { lights: "Lights", while: "While", button: "Button", follow: "Follow", auto_off: "Auto-off" };
const PRESS = { multi_press_1: "Press", multi_press_2: "Double press", multi_press_3: "Triple press", long_press: "Hold", long_release: "Hold (on release)" };
const HOLD_OPTS = [["m15", "hold 15 min"], ["m30", "hold 30 min"], ["m60", "hold 60 min"], ["next", "hold until the next mode"], ["paused", "hold until released"]];
const holdKey = (a) => !a ? "" : a === "release" || a.release ? "release" : a.hold === "next" ? "next" : a.hold === "paused" ? "paused" : a.hold != null ? `m${a.hold}` : "";
const holdVal = (k) => k === "next" || k === "paused" ? k : Number(k.slice(1));
const newId = (list, base) => { let n = 1; while (list.some((a) => a.id === `${base}-${n}`)) n += 1; return `${base}-${n}`; };
const slug = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "x_$1").slice(0, 32) || "item";
const uniqueId = (name, taken) => { let id = slug(name); for (let n = 2; taken.has(id); n++) id = `${slug(name)}_${n}`; return id; };

/** HA's form elements are lazy-loaded by the frontend; pull them in through a card editor. */
let formsReady = null;
const loadForms = () => {
  if (formsReady) return formsReady;
  formsReady = (async () => {
    if (customElements.get("ha-form") && customElements.get("ha-selector")) return;
    try {
      const helpers = await window.loadCardHelpers?.();
      const card = await helpers?.createCardElement({ type: "entities", entities: [] });
      await card?.constructor?.getConfigElement?.();
    } catch (e) { /* the forms simply won't render; the error shows */ }
    await Promise.race([customElements.whenDefined("ha-form"), new Promise((r) => setTimeout(r, 5000))]);
  })();
  return formsReady;
};

/** A schedule time ("06:00", "sunset", "sunset-01:00") ↔ its parts. */
const parseAt = (at) => {
  const m = /^(sunrise|sunset)(?:([+-])(\d{1,2}):(\d{2}))?$/.exec(String(at || ""));
  if (m) return { kind: m[1], sign: m[2] || "-", off: m[2] ? `${m[3].padStart(2, "0")}:${m[4]}` : "00:00" };
  return { kind: "clock", time: /^\d{1,2}:\d{2}$/.test(String(at || "")) ? String(at).padStart(5, "0") : "" };
};
const joinAt = (p) => (p.kind === "clock" ? p.time : p.off && p.off !== "00:00" ? `${p.kind}${p.sign}${p.off}` : p.kind);
const offInput = (v) => {
  const m = /^(\d{1,2})(?::(\d{1,2}))?$/.exec(String(v || "").trim() || "0");
  return m ? `${m[1].padStart(2, "0")}:${(m[2] || "0").padStart(2, "0")}` : String(v).trim();
};

const STYLE = `
:host { display: block; }
.bar { position: sticky; top: var(--header-height, 56px); z-index: 2; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
       padding: 10px 14px; margin-bottom: 12px; border-radius: 12px; background: var(--card-background-color); border: 1px solid var(--divider-color); }
.bar .st { flex: 1; font-size: 14px; min-width: 160px; }
.bar .st b { font-weight: 500; }
.err { color: var(--error-color); }
a.lk, button.lk { color: var(--primary-color); background: none; border: none; padding: 0; font: inherit; cursor: pointer; text-decoration: none; }
button.b { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 18px; padding: 6px 14px; font: inherit; font-size: 14px; cursor: pointer; }
button.b.pri { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
button.b:disabled { opacity: .45; cursor: default; }
button.b.sm { padding: 3px 10px; font-size: 13px; }
button.b.danger { color: var(--error-color); border-color: var(--error-color); }
button.b.add { border-style: dashed; border-radius: 10px; padding: 8px 14px; }
button.ic { border: none; background: none; color: var(--secondary-text-color); cursor: pointer; font-size: 18px; padding: 2px 6px; line-height: 1; font-family: inherit; }
button.ic:disabled { opacity: .3; cursor: default; }
.tabs { display: flex; gap: 6px; margin-bottom: 12px; }
.tabs button { flex: 1; border: 1px solid var(--divider-color); background: var(--card-background-color); color: inherit; font: inherit; font-size: 15px; border-radius: 10px; padding: 8px 0; cursor: pointer; }
.tabs button.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 500; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--error-color); margin-left: 6px; vertical-align: middle; }
.card { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 12px; margin-bottom: 12px; }
.sec { padding: 14px 16px; }
.sec + .sec { border-top: 1px solid var(--divider-color); }
h3 { margin: 0 0 8px; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); }
h3 .sub { text-transform: none; letter-spacing: 0; font-weight: 400; margin-left: 8px; }
.help { color: var(--secondary-text-color); font-size: 13px; margin: 2px 0 8px; line-height: 1.45; }
.errs { color: var(--error-color); font-size: 13px; margin: 4px 0 8px; padding-left: 18px; }
.warns { color: var(--warning-color, #ffa600); font-size: 13px; margin: 4px 0 8px; padding-left: 18px; }
.empty { color: var(--secondary-text-color); font-size: 14px; padding: 4px 0; }
.load { padding: 24px; color: var(--secondary-text-color); }
.note { font-size: 13px; padding: 8px 12px; border-radius: 8px; background: var(--secondary-background-color, rgba(127,127,127,.1)); margin: 0 0 12px; }
.review ul { margin: 6px 0; padding-left: 20px; font-size: 14px; line-height: 1.6; }
select, input.in { font: inherit; font-size: 14px; color: var(--primary-text-color); background: var(--secondary-background-color, transparent);
       border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 6px; }
.tools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
.tools .add { min-width: 220px; flex: 1; }
/* House grid */
.grid { overflow-x: auto; }
.grid table { width: 100%; border-collapse: collapse; min-width: 900px; font-size: 14px; }
.grid th, .grid td { border-bottom: 1px solid var(--divider-color); text-align: left; vertical-align: middle; padding: 10px 12px; }
.grid th + th, .grid td + td { border-left: 1px solid var(--divider-color); }
.grid th { font-weight: 500; background: var(--secondary-background-color, rgba(127,127,127,.06)); }
.grid th.lbl { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); vertical-align: bottom; }
.grid th button.mh { all: unset; cursor: pointer; display: flex; flex-direction: column; gap: 3px; }
.grid th button.mh:focus-visible { outline: 2px solid var(--primary-color); }
.grid th .mn { display: flex; align-items: center; gap: 8px; font-size: 15px; }
.grid th .mt { font-size: 12px; color: var(--secondary-text-color); font-weight: 400; }
.grid th ha-icon { --mdc-icon-size: 20px; }
.grid td.room { width: 230px; }
.grid td.room button { all: unset; cursor: pointer; font-weight: 500; font-size: 15px; color: var(--primary-color); }
.grid td.room .us { display: block; font-size: 12.5px; color: var(--secondary-text-color); line-height: 1.35; margin-top: 2px; }
.grid td.cell { cursor: pointer; }
.grid td.cell:hover { background: rgba(127,127,127,.06); }
.grid .usual { color: var(--secondary-text-color); font-size: 13px; }
.grid .own { display: inline-block; padding: 4px 9px; border-radius: 8px; background: rgba(3,169,244,.12); border: 1px solid rgba(3,169,244,.45); font-size: 13px; line-height: 1.35; }
.grid .beg { display: block; margin-top: 4px; font-size: 12.5px; color: var(--primary-color); line-height: 1.35; }
.grid td.alw { font-size: 12.5px; color: var(--secondary-text-color); width: 130px; }
.legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12.5px; color: var(--secondary-text-color); padding: 2px 4px 12px; }
/* Room */
.crumb { display: flex; align-items: baseline; gap: 14px; margin: 0 0 12px; }
.crumb h2 { margin: 0; font-size: 24px; font-weight: 500; flex: 1; }
.crumb .live { font-size: 13px; color: var(--secondary-text-color); }
.mrow { border-top: 1px solid var(--divider-color); padding: 10px 0; }
.mrow:first-of-type { border-top: none; }
.mhead { display: grid; grid-template-columns: 150px minmax(0, 1fr) minmax(0, 1fr) 28px; gap: 10px; align-items: center; width: 100%;
         background: none; border: none; color: inherit; font: inherit; cursor: pointer; text-align: left; padding: 0; }
.mhead .mn { font-weight: 500; font-size: 15px; display: flex; flex-direction: column; }
.mhead .mn small { font-weight: 400; font-size: 12px; color: var(--secondary-text-color); }
.mhead .what { color: var(--secondary-text-color); font-size: 14px; }
.mhead .what.own { color: var(--primary-text-color); }
.mhead .beg { font-size: 13px; color: var(--primary-color); }
.mhead ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
.mbody { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; padding: 10px 0 4px 160px; }
.mbody .col h4 { margin: 0 0 6px; font-size: 13px; font-weight: 500; color: var(--secondary-text-color); }
.box { background: var(--secondary-background-color, rgba(127,127,127,.06)); border: 1px solid var(--divider-color); border-radius: 10px; padding: 12px; margin-bottom: 10px; }
.box .bh { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.box .bh b { flex: 1; font-weight: 500; }
.tlabel { font-weight: 500; font-size: 14px; margin: 12px 0 2px; }
table.tg { width: 100%; border-collapse: collapse; font-size: 14px; }
table.tg td { padding: 4px 4px 4px 0; vertical-align: middle; }
table.tg td.n { width: 40%; }
table.tg input[type=number] { width: 64px; }
.step { border-left: 3px solid var(--divider-color); padding-left: 10px; margin: 10px 0; }
.step .sh { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.step input { width: 64px; }
.devices summary { cursor: pointer; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); list-style: none; display: flex; gap: 10px; align-items: baseline; }
.devices summary .sub { text-transform: none; letter-spacing: 0; font-weight: 400; color: var(--primary-text-color); font-size: 13px; }
.brow { display: grid; grid-template-columns: 120px 1fr; gap: 10px; align-items: start; padding: 8px 0; border-top: 1px solid var(--divider-color); }
.brow:first-child { border-top: none; }
/* Times */
.dtrow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 8px 0; border-top: 1px solid var(--divider-color); }
.dtrow:first-of-type { border-top: none; }
.dtrow .pri { width: 22px; color: var(--secondary-text-color); font-size: 13px; text-align: right; }
.dtrow input.in { width: 160px; }
.axis { display: flex; justify-content: space-between; font-size: 11.5px; color: var(--secondary-text-color); margin: 8px 0 4px 120px; }
.band { display: flex; align-items: stretch; gap: 10px; margin: 12px 0 6px; }
.band .bn { width: 110px; flex: none; display: flex; flex-direction: column; justify-content: center; }
.band .bn b { font-weight: 500; font-size: 15px; }
.band .bn small { font-size: 12px; color: var(--secondary-text-color); }
.band .strip { flex: 1; display: flex; height: 40px; border-radius: 8px; overflow: hidden; border: 1px solid var(--divider-color); }
.band .strip div { display: flex; align-items: center; color: #f2f2f2; font-size: 12.5px; min-width: 0; border-right: 1px solid rgba(0,0,0,.5); }
.band .strip span { padding: 0 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stops { margin-left: 120px; display: flex; flex-direction: column; gap: 6px; }
.stop { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 14px; }
.stop label { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
.rule { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 0; border-top: 1px solid var(--divider-color); font-size: 14px; }
.rule:first-of-type { border-top: none; }
.rule .days { display: flex; gap: 3px; flex-wrap: wrap; }
.rule .days button { border: 1px solid var(--divider-color); background: none; color: inherit; font: inherit; font-size: 12px; border-radius: 12px; padding: 3px 7px; cursor: pointer; }
.rule .days button.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.rule label { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; }
.rule .grow, .stop .grow { flex: 1; }
.week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 8px; min-width: 820px; }
.week .wd { background: var(--secondary-background-color, rgba(127,127,127,.06)); border: 1px solid var(--divider-color); border-radius: 10px; padding: 8px 10px; }
.week .wd b { font-weight: 500; display: block; }
.week .wd small { font-size: 11.5px; color: var(--secondary-text-color); display: block; margin-bottom: 4px; }
.week .ch { display: flex; gap: 6px; font-size: 13px; padding: 1px 0; }
.week .ch .t { width: 66px; flex: none; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; white-space: nowrap; }
.modepanel { border: 1px solid var(--primary-color); }
@media (max-width: 700px) {
  .mhead { grid-template-columns: 1fr 28px; }
  .mhead .what, .mhead .beg { grid-column: 1; }
  .mbody { grid-template-columns: 1fr; padding-left: 0; }
  .band .bn { width: 80px; } .stops, .axis { margin-left: 0; }
}
.acard { border: 1px solid var(--divider-color); border-radius: 10px; margin-bottom: 8px; background: var(--secondary-background-color, rgba(127,127,127,.06)); }
.acard.open { border-color: var(--primary-color); }
.acard.off .amain { opacity: .55; }
.ahead { display: grid; grid-template-columns: 40px minmax(0, 1fr) 150px auto; gap: 10px; align-items: center; padding: 10px 12px; }
.ahead .an { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ahead .an b { font-weight: 500; font-size: 15px; }
.kind { font-size: 11.5px; padding: 1px 7px; border-radius: 8px; background: rgba(127,127,127,.18); color: var(--secondary-text-color); }
.pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; font-size: 12.5px; }
.pill { padding: 2px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); max-width: 100%; box-sizing: border-box; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pill i { font-style: normal; color: var(--secondary-text-color); margin-right: 4px; }
.anext { font-size: 12.5px; color: var(--secondary-text-color); }
.actl { display: flex; gap: 4px; align-items: center; }
.abody { padding: 4px 14px 12px; border-top: 1px solid var(--divider-color); }
.abody h4 { margin: 12px 0 4px; font-size: 12px; font-weight: 500; letter-spacing: .06em; text-transform: uppercase; color: var(--secondary-text-color); }
.abody .nm { display: flex; gap: 10px; align-items: center; margin-top: 8px; }
.abody .nm input { flex: 1; }
@media (max-width: 700px) { .ahead { grid-template-columns: 36px minmax(0, 1fr) auto; } .ahead .anext { grid-column: 2; } }
`;

export class SbModesEditor extends HTMLElement {
  constructor() {
    super();
    this._open = new Set();
    this._timer = null;
    this._check = { errors: [], warnings: [] };
    this._changes = [];
    this._view = "house";
    this._unlinked = new Set();
  }

  set hass(h) {
    const first = !this._hass;
    this._hass = h;
    if (first) this._init();
    this.shadowRoot?.querySelectorAll("ha-form, ha-selector").forEach((f) => { f.hass = h; });
  }

  set zone(z) { if (z) { this._room = z; this._view = "room"; } }

  async _init() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="load">Loading the editor…</div>`;
    await loadForms();
    try {
      this._schema = await this._ws({ type: "sb_modes/schema" });
      await this._load();
    } catch (e) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="note">Could not open the editor: ${esc(e?.message || e)}</div>`;
    }
  }

  _ws(msg) { return this._hass.callWS(msg); }

  async _load() {
    const r = await this._ws({ type: "sb_modes/draft/get" });
    this._saved = r.saved;
    this._version = r.version;
    this._draft = clone(r.draft || r.saved);
    this._draft.zones = (this._draft.zones || []).map(toAddons);   // older per-room keys → add-on instances
    this._hasDraft = !!r.draft;
    this._stale = !!r.stale;
    this._changes = r.changes || [];
    this._check = { errors: r.errors || [], warnings: r.warnings || [] };
    if (this._room && !this._zoneObj(this._room)) { this._room = null; this._view = "house"; }
    this._preview = null;
    this._render();
  }

  // ── model helpers ──────────────────────────────────────────────────────
  _modes() { return this._draft.modes || []; }
  _mode(id) { return this._modes().find((m) => m.id === id); }
  _modeName(id) { return id === ALL ? "Usually" : (this._mode(id)?.name || id); }
  _modeIcon(id) { return this._mode(id)?.icon || DEFAULT_ICONS[id] || "mdi:home-clock-outline"; }
  _zones() { return this._draft.zones || []; }
  _zoneObj(id) { return this._zones().find((z) => z.id === id); }
  _zoneIndex(id) { return this._zones().findIndex((z) => z.id === id); }
  _modeIndex(id) { return this._modes().findIndex((m) => m.id === id); }
  _types() { return this._draft.day_types || []; }
  _beh(name) { return this._schema.behaviours[name] || { label: name || "—", params: {} }; }
  _name(eid) { return this._hass.states[eid]?.attributes.friendly_name || eid; }
  _short(eid, zoneId) {
    const n = this._name(eid), z = this._zoneObj(zoneId)?.name;
    return z && n.toLowerCase().startsWith(`${z.toLowerCase()} `) && n.length > z.length + 1 ? n.slice(z.length + 1) : n;
  }
  _clock(at) {
    const p = parseAt(at);
    if (p.kind === "clock") {
      const [h, m] = p.time.split(":").map(Number);
      if (Number.isNaN(h)) return at;
      const h12 = this._hass.locale?.time_format === "24" ? false : this._hass.locale?.time_format === "12" ? true
        : new Intl.DateTimeFormat(this._hass.locale?.language, { hour: "numeric" }).resolvedOptions().hour12;
      return h12 ? `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}` : p.time;
    }
    const [oh, om] = p.off.split(":");
    return p.off && p.off !== "00:00" ? `${p.kind} ${p.sign === "-" ? "−" : "+"} ${Number(oh)}:${om}` : p.kind;
  }

  /** A behaviour in words: its name and its numbers ("Presence + light level · dark below 50 lx · empty after 3 min"). */
  _sayChoice(ch, zoneId, withTargets = false) {
    if (!ch?.behaviour) return "Not controlled";
    const b = this._beh(ch.behaviour), p = ch.params || {}, bits = [b.label];
    for (const [k, s] of Object.entries(b.params || {})) {
      const v = p[k] ?? s.default;
      if (v === undefined || v === null || v === "") continue;
      if (s.type === "number") {
        if (k === "max_on_minutes" && !v) continue;
        const val = s.unit === "s" && v >= 120 && v % 60 === 0 ? `${v / 60} min` : `${v}${s.unit ? ` ${s.unit}` : ""}`;
        bits.push(`${(s.label || k).toLowerCase()} ${val}`);
      } else if (s.type === "bool" && v) bits.push((s.label || k).toLowerCase());
      else if (s.type === "mode") bits.push(`${(s.label || k).toLowerCase()} ${this._modeName(v)}`);
      else if (s.type === "targets" && withTargets && Object.keys(v).length) bits.push(this._sayTargets(v, zoneId));
      else if (s.type === "steps" && withTargets) bits.push(v.map((st) => `+${st.after_minutes} min ${this._sayTargets(st.targets, zoneId)}`).join(", "));
    }
    return bits.join(" · ");
  }
  _sayTarget(t) {
    if (!t) return "";
    if ("value" in t) return String(t.value);
    if (t.state === "off") return "off";
    if (t.min_brightness_pct != null) return `at least ${t.min_brightness_pct}%`;
    return t.brightness_pct != null ? `${t.brightness_pct}%${t.color_temp_kelvin ? ` ${t.color_temp_kelvin} K` : ""}` : "on";
  }
  /** "Aux Recessed, Aux Sink at least 10% · Aux Cabinet on" — lights with the same setting together. */
  _sayTargets(targets, zoneId) {
    const groups = new Map();
    for (const [e, t] of Object.entries(targets || {})) {
      const k = this._sayTarget(t);
      groups.set(k, [...(groups.get(k) || []), this._short(e, zoneId)]);
    }
    return [...groups].map(([k, names]) => `${names.join(", ")} ${k}`).join(" · ");
  }
  /** "Workdays 6:00 AM · Days off 8:00 AM" — when a mode begins, from the day types. */
  _modeTimes(mid) {
    const times = this._draft.times || {}, per = [];
    for (const t of this._types()) {
      const at = (times[t.id] || []).filter((e) => e.mode === mid).map((e) => this._clock(e.at) + (e.eve ? " (night before)" : ""));
      if (at.length) per.push([t.name || t.id, at.join(", ")]);
    }
    if (!per.length) return (this._draft.schedule || []).some((r) => r.mode === mid) ? "by other rules" : "not scheduled";
    if (per.length === this._types().length && per.every((x) => x[1] === per[0][1])) return `Every day ${per[0][1]}`;
    return per.map(([n, a]) => `${n} ${a}`).join(" · ");
  }

  // owners and locs
  _choice(d, owner) {
    if (owner.kind === "mode") return d.modes.find((m) => m.id === owner.mode)?.default;
    return (d.zones.find((z) => z.id === owner.zone)?.overrides || {})[owner.mode];
  }
  _setChoice(d, owner, value) {
    if (owner.kind === "mode") { d.modes.find((m) => m.id === owner.mode).default = value; return; }
    const z = d.zones.find((q) => q.id === owner.zone);
    z.overrides = z.overrides || {};
    if (value) z.overrides[owner.mode] = value; else delete z.overrides[owner.mode];
    if (!Object.keys(z.overrides).length) delete z.overrides;
  }
  _ownerPath(owner) {
    return owner.kind === "mode" ? ["modes", this._modeIndex(owner.mode), "default"] : ["zones", this._zoneIndex(owner.zone), "overrides", owner.mode];
  }
  _zoneOf(loc) { return loc.k === "choice" ? (loc.owner.kind === "zone" ? loc.owner.zone : null) : loc.zone; }
  _targetsRef(d, loc) {
    const z = this._zoneOf(loc) ? d.zones.find((q) => q.id === this._zoneOf(loc)) : null;
    if (loc.k === "choice") {
      const ch = this._choice(d, loc.owner);
      ch.params = ch.params || {};
      if (loc.step != null) { const st = ch.params[loc.key][loc.step]; return (st.targets ||= {}); }
      return (ch.params[loc.key] ||= {});
    }
    const a = (z?.addons || []).find((x) => x.id === loc.addon);
    if (!a) return {};
    if (loc.k === "band") return (a.bands[loc.band].targets ||= {});
    if (loc.k === "overlay") return (a.states[loc.state].targets ||= {});
    if (loc.k === "button") return (a.presses[loc.etype].targets ||= {});
    if (loc.k === "step") return (a.steps[loc.step].targets ||= {});
    return {};
  }
  _tidy() {}
  _lookTargets(loc) { try { return this._targetsRef(clone(this._draft), loc); } catch (e) { return {}; } }
  _locPath(loc) {
    const zi = this._zoneIndex(this._zoneOf(loc));
    if (loc.k === "choice") {
      const p = [...this._ownerPath(loc.owner), "params", loc.key];
      return loc.step != null ? [...p, loc.step, "targets"] : p;
    }
    const ai = (this._zoneObj(this._zoneOf(loc))?.addons || []).findIndex((x) => x.id === loc.addon);
    const base = ["zones", zi, "addons", ai];
    if (loc.k === "band") return [...base, "bands", loc.band];
    if (loc.k === "overlay") return [...base, "states", loc.state];
    if (loc.k === "button") return [...base, "presses", loc.etype];
    if (loc.k === "step") return [...base, "steps", loc.step];
    return [];
  }

  // ── draft ──────────────────────────────────────────────────────────────
  _edit(fn, redraw = false) {
    fn(this._draft);
    this._dirty = true;
    if (redraw) this._render(); else this._renderBar();
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._sync(), 500);
  }

  async _sync() {
    try {
      const r = await this._ws({ type: "sb_modes/draft/set", config: this._draft });
      this._hasDraft = !r.same;                          // the same as the table: no draft is kept
      if (r.same) this._stale = false;
      this._dirty = false;
      this._changes = r.changes || [];
      this._check = { errors: r.errors || [], warnings: r.warnings || [] };
      this._msg = "";
    } catch (e) { this._msg = `The draft was not stored: ${e?.message || e}`; }
    this._renderBar();
    this._renderErrors();
    if (this._view === "times") this._loadPreview();
  }

  _errorsAt(prefix, list = this._check.errors) { return list.filter((e) => prefix.every((p, i) => e.path[i] === p)); }

  // ── rendering ──────────────────────────────────────────────────────────
  _render() {
    const root = this.shadowRoot;
    const tab = (k, l) => `<button role="tab" data-view="${k}" class="${(this._view === k || (k === "house" && this._view === "room")) ? "on" : ""}">${l}<span class="tdot" data-tdot="${k}"></span></button>`;
    const body = this._view === "times" ? this._timesHtml() : this._view === "room" ? this._roomHtml() : this._houseHtml();
    root.innerHTML = `<style>${STYLE}</style><div class="bar"></div>${this._review ? this._reviewHtml() : ""}
      <div class="tabs" role="tablist" aria-label="Edit">${tab("house", "House")}${tab("times", "Times")}</div>${body}`;
    this._renderBar();
    this._mountForms();
    this._wire();
    this._renderErrors();
    if (this._view === "times") { if (this._preview) this._renderPreview(); else this._loadPreview(); }
  }

  _renderBar() {
    const bar = this.shadowRoot.querySelector(".bar");
    if (!bar) return;
    const n = this._changes.length, ne = this._check.errors.length;
    bar.innerHTML = `<span class="st">${this._hasDraft || this._dirty
        ? `<b>Draft</b> · ${n} change${n === 1 ? "" : "s"}${ne ? ` · <span class="err">${ne} error${ne === 1 ? "" : "s"}</span>` : ""}${this._dirty ? " · storing…" : ""}${
          this._stale ? ` · <span class="err">the table was saved elsewhere since</span>` : ""}`
        : `No changes${this._version ? ` · version ${esc(this._version)}` : " to the saved table"}`}${this._msg ? ` · <span class="err">${esc(this._msg)}</span>` : ""}${
        this._toast ? ` · <span>${esc(this._toast)}</span>` : ""}</span>
      <button class="b" data-act="review" ${n ? "" : "disabled"}>Review</button>
      <button class="b" data-act="discard" ${this._hasDraft || this._dirty ? "" : "disabled"}>Discard</button>
      <button class="b pri" data-act="save" ${n && !ne && !this._dirty ? "" : "disabled"}>Save</button>`;
    bar.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => this._act(b.dataset.act)));
  }

  _reviewHtml() {
    return `<div class="card review"><div class="sec"><h3>Review</h3>
      ${this._stale ? `<div class="note">The table was saved elsewhere after this draft began. Saving replaces those changes with this draft.</div>` : ""}
      ${this._changes.length ? `<ul>${this._changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : `<div class="empty">No changes.</div>`}
      <div class="tools"><button class="b" data-act="review-close">Back to editing</button>
        <button class="b pri" data-act="save" ${this._changes.length && !this._check.errors.length ? "" : "disabled"}>Save ${this._changes.length} change${this._changes.length === 1 ? "" : "s"}</button></div>
    </div></div>`;
  }

  // ── House ──────────────────────────────────────────────────────────────
  _houseHtml() {
    const modes = this._modes();
    const head = modes.map((m) => `<th><button class="mh" data-mode-open="${esc(m.id)}" aria-label="Edit mode ${esc(m.name || m.id)}">
        <span class="mn"><ha-icon icon="${esc(this._modeIcon(m.id))}"></ha-icon>${esc(m.name || m.id)}${this._errorsAt(["modes", this._modeIndex(m.id)]).length ? `<span class="dot"></span>` : ""}</span>
        <span class="mt">${esc(this._modeTimes(m.id))}</span></button></th>`).join("");
    const rows = this._zones().map((z) => {
      const ov = z.overrides || {}, usual = ov[ALL];
      const cells = modes.map((m) => {
        const own = ov[m.id];
        const fallback = !own && !usual ? this._mode(m.id)?.default : null;
        const what = own ? `<span class="own">${esc(this._sayChoice(own, z.id, true))}</span>`
          : fallback && fallback.behaviour !== "hands_off" ? `<span class="usual">Mode default: ${esc(this._beh(fallback.behaviour).label)}</span>`
          : usual ? `<span class="usual">Usual</span>` : `<span class="usual">Not controlled</span>`;
        const begs = (z.addons || []).filter((a) => a.type === "lights" && a.enabled !== false && (a.trigger?.mode_begins || []).includes(m.id));
        return `<td class="cell" data-cell="${esc(z.id)}|${esc(m.id)}">${what}${begs.map((a) => `<span class="beg">▸ ${esc(a.name || a.id)}</span>`).join("")}</td>`;
      }).join("");
      const addons = (z.addons || []).map((a) => [a.id, (a.name || a.id) + ("at" in (a.trigger || {}) ? ` · ${this._clock(a.trigger.at)}` : "") + (a.enabled === false ? " (off)" : "")]);
      const nerr = this._errorsAt(["zones", this._zoneIndex(z.id)]).length;
      return `<tr><td class="room"><button data-room="${esc(z.id)}">${esc(z.name || z.id)} ›</button>${nerr ? `<span class="dot" title="Has errors"></span>` : ""}
          <span class="us">${esc(usual ? this._sayChoice(usual, z.id) : "No usual setting")}</span></td>${cells}<td class="alw">${addons.map(([k, t]) => `<button class="lk" data-addon="${esc(z.id)}|${k}">${esc(t)}</button>`).join("<br>")}</td></tr>`;
    }).join("");
    return `${this._modePanelHtml()}
      <div class="card"><div class="grid"><table>
        <thead><tr><th class="lbl">Room · room modes</th>${head}<th class="lbl">Add-ons</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="${modes.length + 2}" class="empty">No rooms yet.</td></tr>`}</tbody></table></div></div>
      <div class="legend"><span>Usual = the room's rule in every mode (its Room modes, left)</span><span><span class="own" style="padding:1px 7px">Own</span> = a mode exception</span>
        <span style="color:var(--primary-color)">▸ an add-on that runs when the mode begins</span><span>Tap a room or a cell to edit · tap a mode for its name, icon and order</span></div>
      <div class="tools" style="margin-bottom:12px"><button class="b add" data-act="add-zone">+ Room</button><button class="b add" data-act="add-mode">+ Mode</button>
        <span class="grow" style="flex:1"></span><span class="form" data-form="fallback" style="min-width:260px"></span></div>`;
  }

  _modePanelHtml() {
    const m = this._modePanel && this._mode(this._modePanel);
    if (!m) return "";
    const i = this._modeIndex(m.id), n = this._modes().length;
    return `<div class="card modepanel"><div class="sec">
      <h3>Mode · ${esc(m.name || m.id)}<span class="sub">${esc(m.id)}</span></h3>
      <div class="errs-slot" data-path='${esc(JSON.stringify(["modes", i]))}'></div>
      <div class="form" data-form="mode" data-mode="${esc(m.id)}"></div>
      <div class="help">When it begins: ${esc(this._modeTimes(m.id))} — change that under Times.</div>
      <details style="margin:8px 0"><summary class="help" style="cursor:pointer">Rooms with no setting of their own (advanced)</summary>
        ${this._choiceEditorHtml({ kind: "mode", mode: m.id }, m.default)}</details>
      <div class="tools"><button class="b sm" data-move-mode="${i}" data-dir="-1" ${i ? "" : "disabled"}>← Earlier</button>
        <button class="b sm" data-move-mode="${i}" data-dir="1" ${i < n - 1 ? "" : "disabled"}>Later →</button>
        <button class="b sm danger" data-del-mode="${esc(m.id)}">Delete mode</button><span style="flex:1"></span>
        <button class="b sm" data-act="mode-close">Done</button></div></div></div>`;
  }

  // ── Room ───────────────────────────────────────────────────────────────
  _roomHtml() {
    const z = this._zoneObj(this._room);
    if (!z) return `<div class="empty">That room is gone.</div>`;
    const zi = this._zoneIndex(z.id);
    const zs = Object.values(this._hass.states).find((s) => s.attributes.sb_modes_zone === z.id);
    const usual = (z.overrides || {})[ALL];
    const modeRows = this._modes().map((m) => this._roomModeRowHtml(z, m)).join("");
    return `<div class="crumb"><button class="lk" data-view="house">‹ House</button><h2>${esc(z.name || z.id)}</h2>
        <span class="live">${esc(zs ? zs.attributes.status || "" : "new — saved rooms show their live state here")}</span></div>
      <div class="errs-slot" data-path='${esc(JSON.stringify(["zones", zi]))}'></div>
      <div class="card"><div class="sec"><h3>1 · Room modes<span class="sub">what ${esc(z.name || z.id)} does in every mode, unless an exception below</span></h3>
        ${this._choiceEditorHtml({ kind: "zone", zone: z.id, mode: ALL }, usual)}</div></div>
      <div class="card"><div class="sec"><h3>2 · Mode exceptions<span class="sub">a different rule in a mode</span></h3>${modeRows}</div></div>
      <div class="card"><div class="sec"><h3>3 · Add-ons<span class="sub">each runs when its trigger fires and its When matches — in any mode unless it names some</span></h3>${this._addonsHtml(z)}</div></div>
      <div class="card"><div class="sec"><details class="devices" ${this._open.has(`dev|${z.id}`) ? "open" : ""} data-dev="${esc(z.id)}"><summary>4 · Devices
          <span class="sub">${esc(this._sayDevices(z, zs))}</span></summary><div class="form" data-form="basics" style="margin-top:10px"></div></details>
        <div class="tools"><button class="b sm danger" data-act="del-zone">Delete room</button></div></div></div>`;
  }

  _sayDevices(z, zs) {
    const a = zs?.attributes || {};
    const lights = z.lights || a.lights || [], occ = z.occupancy || a.occupancy || [], lux = z.illuminance || a.illuminance || [];
    return [`areas ${(z.areas || []).join(", ") || "—"}`, `${lights.length} light${lights.length === 1 ? "" : "s"}`,
      `presence: ${occ.map((e) => this._short(e, z.id)).join(", ") || "—"}`, lux.length ? `light level: ${lux.map((e) => this._short(e, z.id)).join(", ")}` : ""].filter(Boolean).join(" · ");
  }

  _roomModeRowHtml(z, m) {
    const ov = (z.overrides || {})[m.id], key = `${z.id}|${m.id}`, open = this._open.has(key);
    const begs = (z.addons || []).filter((a) => a.type === "lights" && (a.trigger?.mode_begins || []).includes(m.id));
    const what = ov ? this._sayChoice(ov, z.id, true) : (z.overrides || {})[ALL] ? "Same as the room modes" : `Mode default: ${this._beh(m.default?.behaviour).label}`;
    const nerr = this._errorsAt(["zones", this._zoneIndex(z.id), "overrides", m.id]).length;
    return `<div class="mrow"><button class="mhead" data-row="${esc(key)}" aria-expanded="${open}">
        <span class="mn">${esc(m.name || m.id)}<small>${esc(this._modeTimes(m.id))}</small></span>
        <span class="what ${ov ? "own" : ""}">${esc(what)}${nerr ? ` <span class="err">· ${nerr} error${nerr === 1 ? "" : "s"}</span>` : ""}</span>
        <span class="beg">${begs.map((a) => `▸ ${esc(a.name || a.id)}`).join(" · ")}</span>
        <ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button>
      ${open ? `<div class="mbody"><div class="col"><h4>Behaviour</h4>${this._choiceEditorHtml({ kind: "zone", zone: z.id, mode: m.id }, ov)}</div></div>` : ""}</div>`;
  }

  // ── add-ons ────────────────────────────────────────────────────────────
  _dayName(id) {
    return this._types().find((t) => t.id === id)?.name || (this._schema.day_sets || []).find((s) => s.id === id)?.name || id;
  }
  _sayStep(st, zid) {
    const lead = st.after_minutes ? `+${st.after_minutes} min ` : "";
    if (st.script) return `${lead}run ${this._name(st.script)}`;
    if (st.release) return `${lead}release${st.flash ? `, flash ×${st.flash}` : ""}`;
    if ("hold" in st) return `${lead}${HOLD_OPTS.find(([k]) => k === holdKey(st))?.[1] || `hold ${st.hold} min`}${st.targets && Object.keys(st.targets).length ? `, ${this._sayTargets(st.targets, zid)}` : ""}`;
    return lead + (this._sayTargets(st.targets, zid) || "no lights yet");
  }
  /** {kind, trigger, when, then} — the words on an add-on's card. */
  _addonParts(a, zid) {
    const t = a.trigger || {}, w = a.when || {};
    const when = [w.modes?.length ? `in ${w.modes.map((m) => this._modeName(m)).join(", ")}` : "",
      w.days?.length ? `only on ${w.days.map((d) => this._dayName(d)).join(", ")}` : "",
      w.not_days?.length ? `never on ${w.not_days.map((d) => this._dayName(d)).join(", ")}` : "",
      w.between?.length === 2 ? `between ${this._clock(w.between[0])} and ${this._clock(w.between[1])}` : ""].filter(Boolean).join(" · ") || "any time";
    if (a.type === "lights") {
      const trigger = "at" in t ? this._clock(t.at) : t.mode_begins ? `${t.mode_begins.map((m) => this._modeName(m)).join(" or ")} begins`
        : t.mode_ends ? `${t.mode_ends.map((m) => this._modeName(m)).join(" or ")} ends` : t.entity ? `${this._short(t.entity, zid)} becomes ${t.to || "?"}` : "not set";
      const script = (a.steps || []).some((s) => s.script);
      return { kind: script ? "Script" : "Lights", trigger, when, then: (a.steps || []).map((s) => this._sayStep(s, zid)).join(" · ") || "nothing yet" };
    }
    if (a.type === "while") {
      return { kind: "While", trigger: `while ${a.entity ? this._short(a.entity, zid) : "?"} is …`, when,
        then: Object.entries(a.states || {}).map(([s, c]) => `${s}${c.holds ? " (holds)" : ""}: ${this._sayTargets(c.targets, zid) || "—"}`).join(" · ") };
    }
    if (a.type === "button") {
      return { kind: "Button", trigger: a.entity ? this._short(a.entity, zid) : "no button yet", when,
        then: Object.entries(a.presses || {}).map(([et, p]) => `${PRESS[et] || et}: ${this._sayStep(p === "release" ? { release: true } : p, zid)}`).join(" · ") || "nothing yet" };
    }
    if (a.type === "follow") {
      return { kind: "Follow", trigger: `${a.leader ? this._short(a.leader, zid) : "?"} changes`, when, then: `${(a.bands || []).length} brightness bands` };
    }
    return { kind: "Auto-off", trigger: "a light turned on by hand", when,
      then: `${(a.lights || []).map((e) => this._short(e, zid)).join(", ") || "?"} off${a.with_empty ? " with the empty setting" : ""}${a.on_minutes ? `${a.with_empty ? ", or" : ""} once on ${a.on_minutes} min with the room empty` : ""}` };
  }
  _addonOpen(zid, aid) { return this._openAddon === `${zid}|${aid}`; }

  _addonsHtml(z) {
    const zi = this._zoneIndex(z.id), list = z.addons || [];
    const zs = Object.values(this._hass.states).find((s) => s.attributes.sb_modes_zone === z.id);
    const live = zs?.attributes.addon_status || {};
    const cards = list.map((a, ai) => {
      const p = this._addonParts(a, z.id), open = this._addonOpen(z.id, a.id);
      const nerr = this._errorsAt(["zones", zi, "addons", ai]).length;
      const pill = (label, text) => `<span class="pill" title="${esc(text)}"><i>${label}</i>${esc(text)}</span>`;
      return `<div class="acard ${open ? "open" : ""} ${a.enabled === false ? "off" : ""}" data-box="${esc(a.id)}">
        <div class="ahead"><ha-switch data-ad-on="${esc(a.id)}" ${a.enabled === false ? "" : "checked"} title="On / off"></ha-switch>
          <div class="amain"><div class="an"><b>${esc(a.name || a.id)}</b><span class="kind">${esc(p.kind)}</span>${nerr ? `<span class="err">${nerr} error${nerr === 1 ? "" : "s"}</span>` : ""}</div>
            <div class="pills">${pill("Trigger", p.trigger)}${pill("When", p.when)}${pill("Then", p.then)}</div></div>
          <div class="anext">${esc(live[a.id] || "")}</div>
          <div class="actl"><button class="ic" data-ad-move="${ai}" data-dir="-1" ${ai ? "" : "disabled"} title="Move up" aria-label="Move up">▲</button>
            <button class="ic" data-ad-move="${ai}" data-dir="1" ${ai < list.length - 1 ? "" : "disabled"} title="Move down" aria-label="Move down">▼</button>
            <button class="lk" data-ad-open="${esc(a.id)}">${open ? "Done" : "Edit"}</button></div></div>
        ${open ? `<div class="abody">${this._addonBodyHtml(z, a, ai)}</div>` : ""}</div>`;
    }).join("");
    return `${cards || `<div class="help">No add-ons yet.</div>`}
      <div class="tools"><select class="b add" data-add-addon aria-label="Add an add-on"><option value="">+ Add-on…</option>
        ${ADDON_NEW.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select>
        <span class="help" style="margin:0">Order matters only when two add-ons set the same light at the same moment: the lower one wins. A hold or a hand change still wins over any add-on.</span></div>`;
  }

  _addonBodyHtml(z, a, ai) {
    const zi = this._zoneIndex(z.id), base = ["zones", zi, "addons", ai];
    const slot = (p) => `<div class="errs-slot" data-path='${esc(JSON.stringify([...base, ...p]))}'></div>`;
    let trig = "", then = "";
    if (a.type === "lights") {
      trig = `<div class="form" data-form="ad-trigger"></div>${slot(["trigger"])}`;
      then = `${(a.steps || []).map((st, i) => this._stepHtml(z, a, st, i)).join("")}
        <div class="tools"><button class="b sm" data-st-add="1">+ Step</button></div>${slot(["steps"])}`;
    } else if (a.type === "while") {
      const st = a.states || {};
      trig = `<div class="form" data-form="ad-while"></div>`;
      then = OVERLAY_STATES.map((s) => `<div class="brow"><div><label><input type="checkbox" data-ov-on="${s}" ${st[s] ? "checked" : ""}> <b style="font-weight:500">${s}</b></label>
          ${st[s] ? `<label class="help" style="display:block;margin-top:4px"><input type="checkbox" data-ov-holds="${s}" ${st[s].holds ? "checked" : ""}> holds the room</label>` : ""}</div>
        <div>${st[s] ? this._targetsHtml({ k: "overlay", zone: z.id, addon: a.id, state: s }, st[s].targets || {}, { label: "" }) : `<span class="help">Not used.</span>`}</div></div>`).join("") + slot(["states"]);
    } else if (a.type === "button") {
      trig = `<div class="form" data-form="ad-button"></div>`;
      const types = (a.entity && this._hass.states[a.entity]?.attributes.event_types) || Object.keys(a.presses || {});
      then = types.length ? types.map((et) => this._pressHtml(z, a, et)).join("") : `<div class="help">Choose the button first.</div>`;
      then += slot(["presses"]);
    } else if (a.type === "follow") {
      trig = `<div class="form" data-form="ad-follow"></div>`;
      const bands = (a.bands || []).map((b, i) => ({ b, i })).sort((x, y) => (y.b.min_pct || 0) - (x.b.min_pct || 0));
      then = bands.map(({ b, i }) => `<div class="brow"><div><label class="help" style="display:block">Leader at or above</label>
            <input class="in" type="number" min="0" max="100" data-band-pct="${i}" value="${esc(b.min_pct ?? 0)}" style="width:64px"> %
            <button class="ic" data-band-rm="${i}" title="Remove band" aria-label="Remove band">×</button></div>
          <div>${this._targetsHtml({ k: "band", zone: z.id, addon: a.id, band: i }, b.targets || {}, { label: "" })}</div></div>`).join("")
        + `<div class="tools"><button class="b sm" data-band-add="1">+ Band</button></div>`;
    } else {
      trig = `<div class="help">A light someone turns on by hand. SB Modes only ever turns these off.</div>`;
      then = `<div class="form" data-form="ad-autooff"></div>`;
    }
    return `<div class="nm"><input class="in" data-ad-name value="${esc(a.name || "")}" aria-label="Name" placeholder="Name">
        <button class="b sm danger" data-ad-rm="${esc(a.id)}">Remove add-on</button></div>
      ${slot([])}
      <h4>Trigger</h4>${trig}
      <h4>When</h4><div class="form" data-form="ad-when"></div>${slot(["when"])}
      <h4>Then</h4>${then}`;
  }

  _stepHtml(z, a, st, i) {
    const act = st.script !== undefined ? "script" : st.release ? "release" : "hold" in st ? "hold" : "lights";
    const flash = (n) => `<label class="help">flash <select data-st-flash="${i}">${[0, 1, 2, 3].map((k) => `<option value="${k}" ${(n || 0) === k ? "selected" : ""}>${k ? `×${k}` : "none"}</option>`).join("")}</select></label>`;
    let body = "";
    if (act === "lights") body = this._targetsHtml({ k: "step", zone: z.id, addon: a.id, step: i }, st.targets || {}, { label: "" });
    else if (act === "hold") {
      const k = holdKey(st);
      body = `<div class="stop"><select data-st-hold="${i}">${[...HOLD_OPTS, ...(/^m\d+$/.test(k) && !HOLD_OPTS.some(([x]) => x === k) ? [[k, `hold ${k.slice(1)} min`]] : [])]
        .map(([v, l]) => `<option value="${v}" ${v === k ? "selected" : ""}>${l}</option>`).join("")}</select>${flash(st.flash)}</div>
        ${this._targetsHtml({ k: "step", zone: z.id, addon: a.id, step: i }, st.targets || {}, { label: "", help: "Lights to set as it holds (leave empty to keep them as they are)." })}`;
    } else if (act === "release") body = `<div class="stop">${flash(st.flash)}<span class="help">ends the hold; the room's own rule (or a TV add-on) takes over</span></div>`;
    else body = `<div class="form" data-form="ad-script" data-i="${i}"></div><div class="help">The script gets: zone, zone_name, mode, occupied, held, changed_by_hand, addon.</div>`;
    return `<div class="brow"><div><label class="help" style="display:block">${i ? "Then after" : "At the trigger, after"}</label>
        <input class="in" type="number" min="0" max="1440" data-st-min="${i}" value="${esc(st.after_minutes ?? 0)}" style="width:64px"> min
        <button class="ic" data-st-rm="${i}" title="Remove step" aria-label="Remove step" ${(a.steps || []).length > 1 ? "" : "disabled"}>×</button>
        <select data-st-act="${i}" style="display:block;margin-top:6px">${[["lights", "Set lights"], ["hold", "Hold the room"], ["release", "Release the room"], ["script", "Run a script"]]
          .map(([v, l]) => `<option value="${v}" ${v === act ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div>${body}</div></div>`;
  }

  _pressHtml(z, a, et) {
    const p = (a.presses || {})[et], k = holdKey(p);
    return `<div class="brow"><div><b style="font-weight:500">${esc(PRESS[et] || et)}</b></div><div>
      <div class="stop"><select data-pr-act="${esc(et)}"><option value="" ${k ? "" : "selected"}>nothing</option>
        ${[...HOLD_OPTS, ["release", "release"], ...(/^m\d+$/.test(k) && !HOLD_OPTS.some(([x]) => x === k) ? [[k, `hold ${k.slice(1)} min`]] : [])]
          .map(([v, l]) => `<option value="${v}" ${v === k ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${k ? `<label class="help">flash <select data-pr-flash="${esc(et)}">${[0, 1, 2, 3].map((n) => `<option value="${n}" ${((p && p.flash) || 0) === n ? "selected" : ""}>${n ? `×${n}` : "none"}</option>`).join("")}</select></label>` : ""}</div>
      ${k && k !== "release" ? this._targetsHtml({ k: "button", zone: z.id, addon: a.id, etype: et }, p.targets || {}, { label: "", help: "Lights to set when it holds (leave empty to keep them as they are)." }) : ""}</div></div>`;
  }

  // ── the behaviour editor ───────────────────────────────────────────────
  _choiceEditorHtml(owner, ch) {
    const o = esc(JSON.stringify(owner));
    const pick = `<div class="form" data-form="pick" data-owner='${o}'></div>`;
    if (!ch) return pick;
    const b = this._beh(ch.behaviour), params = b.params || {};
    const scalar = Object.keys(params).some((k) => !["targets", "steps"].includes(params[k].type));
    const big = Object.entries(params).filter(([, s]) => ["targets", "steps"].includes(s.type));
    return `${pick}<div class="help">${esc(b.help || "")}</div>
      <div class="errs-slot" data-path='${esc(JSON.stringify(this._ownerPath(owner)))}'></div>
      ${scalar ? `<div class="form" data-form="params" data-owner='${o}'></div>` : ""}
      ${big.map(([k, s]) => s.type === "targets" ? this._targetsHtml({ k: "choice", owner, key: k }, (ch.params || {})[k] || {}, s)
        : this._stepsHtml(owner, k, (ch.params || {})[k] || [], s)).join("")}`;
  }

  _targetsHtml(loc, targets, spec) {
    const id = esc(JSON.stringify(loc)), zoneId = this._zoneOf(loc);
    const rows = Object.entries(targets).map(([eid, t]) => {
      const mode = t.state === "off" ? "off" : t.min_brightness_pct != null ? "min" : "on";
      const pct = mode === "min" ? t.min_brightness_pct : t.brightness_pct;
      const isLight = eid.startsWith("light.");
      return `<tr data-eid="${esc(eid)}"><td class="n" title="${esc(this._name(eid))} (${esc(eid)})">${esc(zoneId ? this._short(eid, zoneId) : this._name(eid))}</td>
        <td><select data-f="mode" aria-label="Setting">${[["on", "On"], ["off", "Off"], ...(isLight ? [["min", "At least"]] : [])].map(([v, l]) => `<option value="${v}" ${v === mode ? "selected" : ""}>${l}</option>`).join("")}</select></td>
        <td>${isLight && mode !== "off" ? `<input class="in" type="number" min="0" max="100" data-f="pct" value="${esc(pct ?? "")}" placeholder="—" aria-label="Brightness %"> %` : ""}</td>
        <td>${isLight && mode === "on" ? `<input class="in" type="number" min="1500" max="9000" step="100" data-f="k" value="${esc(t.color_temp_kelvin ?? "")}" placeholder="—" aria-label="Colour temperature K"> K` : ""}</td>
        <td><button class="ic" data-f="rm" title="Remove" aria-label="Remove">×</button></td></tr>`;
    }).join("");
    return `<div class="tg-wrap" data-loc='${id}'>
      ${spec.label ? `<div class="tlabel">${esc(spec.label)}</div>` : ""}${spec.help ? `<div class="help">${esc(spec.help)}</div>` : ""}
      ${rows ? `<table class="tg"><tbody>${rows}</tbody></table>` : `<div class="empty">No lights set.</div>`}
      <div class="tools"><div class="add" data-picker='${id}'></div>
        <button class="b sm" data-tg="capture" title="Fill from how the lights are right now">Use the lights as they are now</button>
        ${zoneId ? `<button class="b sm" data-tg="try" ${rows ? "" : "disabled"} title="Set these now and hold the zone 15 min">Try it</button>` : ""}</div>
      <div class="errs-slot" data-path='${esc(JSON.stringify(this._locPath(loc)))}'></div></div>`;
  }

  _stepsHtml(owner, key, steps, spec) {
    const o = esc(JSON.stringify(owner));
    return `<div class="tlabel">${esc(spec.label || key)}</div>
      ${steps.map((st, i) => `<div class="step"><div class="sh">+ <input class="in" type="number" min="0" max="720" data-step="${i}" data-owner='${o}' data-key="${esc(key)}" value="${esc(st.after_minutes ?? 0)}" aria-label="Minutes after"> min
          <button class="ic" data-step-rm="${i}" data-owner='${o}' data-key="${esc(key)}" title="Remove step">×</button></div>
        ${this._targetsHtml({ k: "choice", owner, key, step: i }, st.targets || {}, { label: `Step ${i + 1}` })}</div>`).join("")}
      <div class="tools"><button class="b sm" data-step-add="1" data-owner='${o}' data-key="${esc(key)}">+ Step</button></div>`;
  }

  // ── Times ──────────────────────────────────────────────────────────────
  _timesHtml() {
    const types = this._types(), sets = this._schema.day_sets || [];
    const typeRows = types.map((t, i) => {
      const last = i === types.length - 1;
      return `<div class="dtrow" data-dt="${i}"><span class="pri">${i + 1}</span>
        <input class="in" data-dt-f="name" value="${esc(t.name || "")}" aria-label="Day type name">
        ${last ? `<span class="help" style="margin:0">every other day</span>`
          : `<select data-dt-f="set" aria-label="Calendar">${sets.some((s) => s.id === t.day_set) ? "" : `<option value="${esc(t.day_set || "")}" selected>${esc(t.day_set || "choose a calendar…")}</option>`}${
              sets.map((s) => `<option value="${esc(s.id)}" ${s.id === t.day_set ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>`}
        <span class="grow" style="flex:1"></span>
        <button class="ic" data-dt-move="${i}" data-dir="-1" ${i && !last ? "" : "disabled"} title="Higher priority" aria-label="Higher priority">↑</button>
        <button class="ic" data-dt-move="${i}" data-dir="1" ${i < types.length - 2 ? "" : "disabled"} title="Lower priority" aria-label="Lower priority">↓</button>
        <button class="ic" data-dt-rm="${i}" ${last || types.length < 2 ? "disabled" : ""} title="Remove day type" aria-label="Remove day type">×</button>
        <div class="errs-slot" style="flex-basis:100%" data-path='${esc(JSON.stringify(["day_types", i]))}'></div></div>`;
    }).join("");
    const bands = types.map((t) => this._bandHtml(t)).join("");
    return `<div class="card"><div class="sec"><h3>Day types<span class="sub">a day takes the first type whose calendar holds it; the last takes every other day</span></h3>
        <div class="errs-slot" data-path='["day_types"]'></div>
        ${typeRows || `<div class="empty">No day types — the house follows only the other rules below.</div>`}
        <div class="tools">${types.length ? `<button class="b sm" data-act="add-type">+ Day type</button>` : `<button class="b sm" data-act="start-types">Use day types</button>`}</div></div>
      ${types.length ? `<div class="sec"><div class="axis"><span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span><span>12 AM</span></div>${bands}</div>` : ""}</div>
      <div class="card"><div class="sec"><h3>Other rules<span class="sub">one weekday, another calendar — they add to the day types' times</span></h3>${this._rulesHtml()}</div></div>
      <div class="card"><div class="sec"><h3>The coming week</h3><div class="pvbody" style="overflow-x:auto"><div class="empty">Working it out…</div></div></div></div>`;
  }

  /** One day type: a 24-hour strip from its times (sun times at today's), then its editable times. */
  _bandHtml(t) {
    const entries = (this._draft.times || {})[t.id] || [];
    const sun = this._hass.states["sun.sun"]?.attributes || {};
    const hourOf = (at) => {
      const p = parseAt(at);
      if (p.kind === "clock") { const [h, m] = p.time.split(":").map(Number); return h + m / 60; }
      const base = new Date(p.kind === "sunset" ? sun.next_setting : sun.next_rising);
      if (Number.isNaN(base.getTime())) return p.kind === "sunset" ? 18 : 7;
      const local = new Date(base.toLocaleString("en-US", { timeZone: this._hass.config?.time_zone }));
      const [oh, om] = p.off.split(":").map(Number);
      return local.getHours() + local.getMinutes() / 60 + (p.sign === "-" ? -1 : 1) * (oh + om / 60);
    };
    const own = entries.filter((e) => !e.eve).map((e) => ({ h: hourOf(e.at), mode: e.mode })).sort((a, b) => a.h - b.h);
    const eve = entries.filter((e) => e.eve).map((e) => ({ h: hourOf(e.at), mode: e.mode })).sort((a, b) => a.h - b.h);
    const stops = [...own, ...eve].sort((a, b) => a.h - b.h);
    const colour = (mid) => KNOWN_FILL[mid] || MODE_FILL[Math.max(0, this._modeIndex(mid)) % MODE_FILL.length];
    const segs = [];
    // before the first change the house is still in the night before's mode: an 'eve' entry of this
    // type if it has one, else (roughly) its own last change of the day
    let at = 0, cur = (eve.at(-1) || own.at(-1))?.mode || null;
    for (const s of stops) { if (s.h > at) segs.push({ from: at, to: s.h, mode: cur }); at = Math.max(at, s.h); cur = s.mode; }
    segs.push({ from: at, to: 24, mode: cur });
    const strip = segs.filter((s) => s.to > s.from).map((s) => `<div style="flex:${(s.to - s.from).toFixed(3)} 0 0;background:${s.mode ? colour(s.mode) : "transparent"}" title="${esc(s.mode ? this._modeName(s.mode) : "the day before's mode")}"><span>${esc(s.mode ? this._modeName(s.mode) : "")}</span></div>`).join("");
    const others = this._types().filter((x) => x.id !== t.id);
    const rows = entries.map((e, j) => {
      const p = parseAt(e.at), key = `${t.id}|${j}`;
      const twins = others.filter((x) => ((this._draft.times || {})[x.id] || []).some((f) => f.mode === e.mode && f.at === e.at && !!f.eve === !!e.eve));
      const linked = twins.length && !this._unlinked.has(key);
      return `<div class="stop" data-stop="${esc(t.id)}|${j}">
        <select data-s="mode" aria-label="Mode">${this._modes().map((m) => `<option value="${esc(m.id)}" ${m.id === e.mode ? "selected" : ""}>${esc(m.name || m.id)}</option>`).join("")}</select>
        <span>at</span>
        <select data-s="kind" aria-label="Time kind">${[["clock", "a time"], ["sunrise", "sunrise"], ["sunset", "sunset"]].map(([v, l]) => `<option value="${v}" ${v === p.kind ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${p.kind === "clock" ? `<input class="in" type="time" data-s="time" value="${esc(p.time)}" aria-label="Time">`
          : `<select data-s="sign" aria-label="Before or after">${[["-", "−"], ["+", "+"]].map(([v, l]) => `<option value="${v}" ${v === p.sign ? "selected" : ""}>${l}</option>`).join("")}</select>
             <input class="in" type="text" inputmode="numeric" data-s="off" value="${esc(p.off.replace(/^0/, ""))}" size="5" placeholder="0:00" aria-label="Offset, hours:minutes"> h`}
        <label><input type="checkbox" data-s="eve" ${e.eve ? "checked" : ""}> the night before</label>
        ${twins.length ? `<label title="Also change it in ${esc(twins.map((x) => x.name).join(", "))}"><input type="checkbox" data-s="link" ${linked ? "checked" : ""}> same in ${esc(twins.map((x) => x.name).join(", "))}</label>` : ""}
        <span class="grow"></span><button class="ic" data-s="rm" title="Remove" aria-label="Remove">×</button>
        <div class="errs-slot" style="flex-basis:100%" data-path='${esc(JSON.stringify(["times", t.id, j]))}'></div></div>`;
    }).join("");
    return `<div class="band"><div class="bn"><b>${esc(t.name || t.id)}</b><small>${esc(t.day_set ? (this._schema.day_sets || []).find((s) => s.id === t.day_set)?.name || t.day_set : "every other day")}</small></div>
        <div class="strip">${strip}</div></div>
      <div class="stops">${rows || `<div class="empty">No mode changes on these days.</div>`}
        <div class="errs-slot" data-path='${esc(JSON.stringify(["times", t.id]))}'></div>
        <div class="tools"><button class="b sm" data-add-stop="${esc(t.id)}">+ Mode change</button></div></div>`;
  }

  _rulesHtml() {
    const rules = this._draft.schedule || [], sets = this._schema.day_sets || [];
    const rows = rules.map((r, i) => {
      const at = parseAt(r.at), byDays = r.day_set == null, days = Array.isArray(r.days) ? r.days : [];
      return `<div class="rule" data-rule="${i}">
        <select data-r="mode" aria-label="Mode">${this._modes().map((m) => `<option value="${esc(m.id)}" ${m.id === r.mode ? "selected" : ""}>${esc(m.name || m.id)}</option>`).join("")}</select>
        <span>at</span>
        <select data-r="atkind" aria-label="Time kind">${[["clock", "a time"], ["sunrise", "sunrise"], ["sunset", "sunset"]].map(([v, l]) => `<option value="${v}" ${v === at.kind ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${at.kind === "clock" ? `<input class="in" type="time" data-r="time" value="${esc(at.time)}" aria-label="Time">`
          : `<select data-r="sign" aria-label="Before or after">${[["-", "−"], ["+", "+"]].map(([v, l]) => `<option value="${v}" ${v === at.sign ? "selected" : ""}>${l}</option>`).join("")}</select>
             <input class="in" type="text" inputmode="numeric" data-r="off" value="${esc(at.off.replace(/^0/, ""))}" size="5" placeholder="0:00" aria-label="Offset, hours:minutes"> h`}
        <span>on</span>
        <select data-r="by" aria-label="Days or calendar"><option value="days" ${byDays ? "selected" : ""}>these days</option><option value="set" ${byDays ? "" : "selected"}>a calendar</option></select>
        ${byDays ? `<span class="days">${(this._schema.days || Object.keys(DAY_NAMES)).map((dd) => `<button data-day="${dd}" class="${days.includes(dd) ? "on" : ""}" aria-pressed="${days.includes(dd)}">${DAY_NAMES[dd] || dd}</button>`).join("")}</span>`
          : `<select data-r="set" aria-label="Calendar">${sets.some((s) => s.id === r.day_set) ? "" : `<option value="${esc(r.day_set || "")}" selected>${esc(r.day_set || "choose…")}</option>`}${sets.map((s) => `<option value="${esc(s.id)}" ${s.id === r.day_set ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
             <select data-r="which" aria-label="Which days">${[["", "those days"], ["not", "days not in it"], ["eve", "the evening before"], ["eve-not", "the evening before days not in it"]].map(([v, l]) =>
               `<option value="${v}" ${v === `${r.eve ? "eve" : ""}${r.eve && r.not ? "-" : ""}${r.not ? "not" : ""}` ? "selected" : ""}>${l}</option>`).join("")}</select>`}
        <span class="grow"></span><button class="ic" data-r="rm" title="Remove rule" aria-label="Remove rule">×</button>
        <div class="errs-slot" style="flex-basis:100%" data-path='${esc(JSON.stringify(["schedule", i]))}'></div></div>`;
    }).join("");
    return `${rows || `<div class="empty">None.</div>`}<div class="tools"><button class="b sm" data-act="add-rule">+ Rule</button></div>`;
  }

  async _loadPreview() {
    try { this._preview = await this._ws({ type: "sb_modes/preview", config: this._draft, days: 7 }); }
    catch (e) { this._preview = { error: e?.message || String(e) }; }
    this._renderPreview();
  }

  _renderPreview() {
    const el = this.shadowRoot.querySelector(".pvbody");
    if (!el || !this._preview) return;
    const p = this._preview;
    if (p.error) { el.innerHTML = `<div class="err">${esc(p.error)}</div>`; return; }
    const tz = this._hass.config?.time_zone, lang = this._hass.locale?.language;
    const dayKey = (d) => new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(d);
    const fmtDay = (d) => new Intl.DateTimeFormat(lang, { weekday: "short", day: "numeric", timeZone: tz }).format(d);
    const fmtTime = (d) => new Intl.DateTimeFormat(lang, { hour: "numeric", minute: "2-digit", timeZone: tz }).format(d);
    const typeName = (id) => this._types().find((t) => t.id === id)?.name || (id ? id : "—");
    const days = Object.keys(p.day_types || {}).slice(0, 7);
    const byDay = {};
    for (const c of p.changes) (byDay[dayKey(new Date(c.at))] ||= []).push(c);
    const today = dayKey(new Date());
    el.innerHTML = `<div class="help">Now: ${esc(p.now ? this._modeName(p.now) : "no scheduled mode")}</div>${
      p.missing_day_sets?.length ? `<div class="warns">Calendar not found: ${esc(p.missing_day_sets.join(", "))} — its days are skipped.</div>` : ""}
      <div class="week">${days.map((k) => {
        const d = new Date(`${k}T12:00:00`);
        return `<div class="wd"><b>${esc(fmtDay(d))}</b><small>${esc(typeName(p.day_types[k]))}${k === today ? " · today" : ""}</small>${
          (byDay[k] || []).map((c) => `<div class="ch"><span class="t">${esc(fmtTime(new Date(c.at)))}</span><span>${esc(this._modeName(c.mode))}</span></div>`).join("") || `<div class="ch help">no change</div>`}</div>`;
      }).join("")}</div>`;
  }

  // ── HA form elements ───────────────────────────────────────────────────
  _form(schema, data, onChange, labels = {}, helpers = {}) {
    const f = document.createElement("ha-form");
    f.hass = this._hass;
    f.schema = schema;
    f.data = data;
    f.computeLabel = (s) => labels[s.name] || s.name;
    f.computeHelper = (s) => helpers[s.name];
    f.addEventListener("value-changed", (e) => { e.stopPropagation(); onChange(e.detail.value); });
    return f;
  }

  _picker(slot, selector, label, onPick) {
    const sel = document.createElement("ha-selector");
    sel.hass = this._hass;
    sel.selector = selector;
    sel.label = label;
    sel.required = false;
    sel.value = "";
    sel.addEventListener("value-changed", (e) => { if (e.detail.value) onPick(e.detail.value); });
    slot.appendChild(sel);
  }

  _mountForms() {
    const root = this.shadowRoot, modeOpts = () => this._modes().map((m) => ({ value: m.id, label: m.name || m.id }));
    const fb = root.querySelector('[data-form="fallback"]');
    if (fb) fb.appendChild(this._form([{ name: "fallback_mode", selector: { select: { mode: "dropdown", options: modeOpts() } } }],
      { fallback_mode: this._draft.fallback_mode || "" }, (v) => this._edit((d) => { d.fallback_mode = v.fallback_mode; }), { fallback_mode: "Mode at start-up when the schedule gives none" }));
    root.querySelectorAll('[data-form="mode"]').forEach((slot) => {
      const m = this._mode(slot.dataset.mode);
      slot.appendChild(this._form([{ name: "name", selector: { text: {} } }, { name: "icon", selector: { icon: { placeholder: DEFAULT_ICONS[m.id] || "mdi:home-clock-outline" } } }],
        { name: m.name || "", icon: m.icon || "" },
        (v) => this._edit((d) => { const x = d.modes.find((q) => q.id === m.id); x.name = v.name; if (v.icon) x.icon = v.icon; else delete x.icon; }), { name: "Name", icon: "Icon" }));
    });
    const z = this._view === "room" ? this._zoneObj(this._room) : null;
    const basics = root.querySelector('[data-form="basics"]');
    if (basics && z) {
      basics.appendChild(this._form([
        { name: "name", selector: { text: {} } }, { name: "areas", selector: { area: { multiple: true } } },
        { name: "lights", selector: { entity: { multiple: true, filter: { domain: ["light", "switch"] } } } },
        { name: "occupancy", selector: { entity: { multiple: true, filter: { domain: "binary_sensor", device_class: ["occupancy", "presence"] } } } },
        { name: "illuminance", selector: { entity: { multiple: true, filter: { domain: "sensor", device_class: "illuminance" } } } },
      ], { name: z.name || "", areas: z.areas || [], lights: z.lights || [], occupancy: z.occupancy || [], illuminance: z.illuminance || [] },
      (v) => this._edit((d) => {
        const x = d.zones.find((q) => q.id === z.id);
        x.name = v.name; x.areas = v.areas || [];
        for (const k of ["lights", "occupancy", "illuminance"]) { if (v[k]?.length) x[k] = v[k]; else delete x[k]; }
      }), { name: "Name", areas: "Areas", lights: "Own lights", occupancy: "Own presence sensors", illuminance: "Own light sensors" },
      { lights: "Leave empty to use every light in the areas.", occupancy: "Leave empty to use the areas' presence sensors.", illuminance: "Leave empty to use (the average of) the areas' light sensors." }));
    }
    const ad = z && this._openAddon?.startsWith(`${z.id}|`) ? (z.addons || []).find((x) => `${z.id}|${x.id}` === this._openAddon) : null;
    const adEdit = (fn, redraw = false) => this._edit((d) => { const x = d.zones.find((q) => q.id === z.id).addons.find((y) => y.id === ad.id); fn(x); }, redraw);
    const when = root.querySelector('[data-form="ad-when"]');
    if (when && ad) {
      const w = ad.when || {};
      const dayOpts = [...this._types().map((t) => ({ value: t.id, label: `${t.name || t.id} (day type)` })),
        ...(this._schema.day_sets || []).filter((s) => !this._types().some((t) => t.id === s.id)).map((s) => ({ value: s.id, label: s.name || s.id }))];
      when.appendChild(this._form([
        { name: "modes", selector: { select: { multiple: true, mode: "dropdown", options: modeOpts() } } },
        { name: "days", selector: { select: { multiple: true, mode: "dropdown", options: dayOpts } } },
        { name: "not_days", selector: { select: { multiple: true, mode: "dropdown", options: dayOpts } } },
        { name: "from", selector: { time: {} } }, { name: "to", selector: { time: {} } },
      ], { modes: w.modes || [], days: w.days || [], not_days: w.not_days || [], from: w.between?.[0] || "", to: w.between?.[1] || "" },
      (v) => adEdit((x) => {
        const n = {};
        if (v.modes?.length) n.modes = v.modes;
        if (v.days?.length) n.days = v.days;
        if (v.not_days?.length) n.not_days = v.not_days;
        if (v.from && v.to) n.between = [v.from.slice(0, 5), v.to.slice(0, 5)];
        if (Object.keys(n).length) x.when = n; else delete x.when;
      }), { modes: "Only in these modes", days: "Only on", not_days: "Never on", from: "Only between", to: "and" },
      { modes: "Empty = any mode.", days: "Day types (Workdays, Days off, Vacation) or SB Scheduler calendars. Empty = every day.", from: "Leave both empty for any time." }));
    }
    const trig = root.querySelector('[data-form="ad-trigger"]');
    if (trig && ad) {
      const t = ad.trigger || {}, kind = "at" in t ? "time" : t.mode_begins ? "begins" : t.mode_ends ? "ends" : t.entity !== undefined ? "state" : "time";
      const schema = [{ name: "kind", selector: { select: { mode: "dropdown", options: [{ value: "time", label: "A time of day" },
        { value: "begins", label: "A mode begins" }, { value: "ends", label: "A mode ends" }, { value: "state", label: "A device reaches a state" }] } } }];
      if (kind === "time") schema.push({ name: "at", selector: { text: {} } });
      if (kind === "begins" || kind === "ends") schema.push({ name: "modes", selector: { select: { multiple: true, mode: "dropdown", options: modeOpts() } } });
      if (kind === "state") schema.push({ name: "entity", selector: { entity: {} } }, { name: "to", selector: { text: {} } });
      trig.appendChild(this._form(schema, { kind, at: t.at || "", modes: t.mode_begins || t.mode_ends || [], entity: t.entity || "", to: t.to || "" },
        (v) => adEdit((x) => {
          if (v.kind !== kind) {
            x.trigger = v.kind === "time" ? { at: "06:30" } : v.kind === "begins" ? { mode_begins: [] } : v.kind === "ends" ? { mode_ends: [] } : { entity: "", to: "on" };
            return;
          }
          if (kind === "time") x.trigger = { at: v.at || "" };
          else if (kind === "begins") x.trigger = { mode_begins: v.modes || [] };
          else if (kind === "ends") x.trigger = { mode_ends: v.modes || [] };
          else x.trigger = { entity: v.entity || "", to: v.to || "" };
        }, v.kind !== kind), { kind: "Trigger", at: "At", modes: "Mode", entity: "Device", to: "Reaches the state" },
        { at: "HH:MM (06:30), or sunrise / sunset ± HH:MM (sunset-01:00).", to: "As HA shows it, e.g. playing, on, open." }));
    }
    root.querySelectorAll('[data-form="ad-script"]').forEach((slot) => {
      const i = Number(slot.dataset.i);
      slot.appendChild(this._form([{ name: "script", selector: { entity: { filter: { domain: "script" } } } }], { script: ad.steps[i].script || "" },
        (v) => adEdit((x) => { x.steps[i].script = v.script || ""; }), { script: "Script" }));
    });
    const wh = root.querySelector('[data-form="ad-while"]');
    if (wh && ad) wh.appendChild(this._form([
      { name: "entity", selector: { entity: { filter: { domain: "media_player" } } } },
      { name: "debounce_seconds", selector: { number: { min: 0, max: 3600, mode: "box", unit_of_measurement: "s" } } },
      { name: "unavailable_cooldown_seconds", selector: { number: { min: 0, max: 3600, mode: "box", unit_of_measurement: "s" } } },
      { name: "presence", selector: { boolean: {} } },
    ], { entity: ad.entity || "", debounce_seconds: ad.debounce_seconds ?? 5, unavailable_cooldown_seconds: ad.unavailable_cooldown_seconds ?? 60, presence: ad.presence ?? true },
    (v) => adEdit((x) => { Object.assign(x, v); if (x.presence === true) delete x.presence; }),
    { entity: "TV", debounce_seconds: "A state counts after", unavailable_cooldown_seconds: "Ignore changes after a drop-out for", presence: "Watching counts as someone in the room" }));
    const bt = root.querySelector('[data-form="ad-button"]');
    if (bt && ad) bt.appendChild(this._form([{ name: "entity", selector: { entity: { filter: { domain: "event" } } } }], { entity: ad.entity || "" },
      (v) => adEdit((x) => { x.entity = v.entity || ""; }, true), { entity: "Button" }));
    const fo = root.querySelector('[data-form="ad-follow"]');
    if (fo && ad) fo.appendChild(this._form([{ name: "leader", selector: { entity: { filter: { domain: ["light", "switch"] } } } }], { leader: ad.leader || "" },
      (v) => adEdit((x) => { x.leader = v.leader || ""; }), { leader: "Leader light" }));
    const ao = root.querySelector('[data-form="ad-autooff"]');
    if (ao && ad) ao.appendChild(this._form([
      { name: "lights", selector: { entity: { multiple: true, filter: { domain: ["light", "switch"] } } } },
      { name: "with_empty", selector: { boolean: {} } },
      { name: "on_minutes", selector: { number: { min: 0, max: 1440, mode: "box", unit_of_measurement: "min" } } },
    ], { lights: ad.lights || [], with_empty: !!ad.with_empty, on_minutes: ad.on_minutes || 0 },
    (v) => adEdit((x) => {
      x.lights = v.lights || [];
      if (v.with_empty) x.with_empty = true; else delete x.with_empty;
      if (v.on_minutes) x.on_minutes = v.on_minutes; else delete x.on_minutes;
    }), { lights: "Lights", with_empty: "Off with the room's empty setting (when it turns other lights off)", on_minutes: "Or once on this long while the room is empty" },
    { on_minutes: "0 = only with the empty setting. “Empty” means empty for the room's usual delay, so a sensor's blink doesn't count." }));
    root.querySelectorAll('[data-form="pick"]').forEach((slot) => {
      const owner = JSON.parse(slot.dataset.owner), ch = this._choice(this._draft, owner);
      const NONE = "__none__";
      const opts = [...(owner.kind === "zone" ? [{ value: NONE, label: owner.mode === ALL ? "Not set — each mode's default" : "Same as usual" }] : []),
        ...Object.entries(this._schema.behaviours).filter(([k]) => k !== "wake_sequence" || ch?.behaviour === k)   // now the Wake-up add-on
          .map(([k, b]) => ({ value: k, label: b.label }))];
      slot.appendChild(this._form([{ name: "behaviour", selector: { select: { mode: "dropdown", options: opts } } }], { behaviour: ch?.behaviour || NONE },
        (v) => this._edit((d) => {
          const cur = this._choice(d, owner);
          if (!v.behaviour || v.behaviour === NONE) { if (owner.kind === "zone") this._setChoice(d, owner, null); }
          else if (cur?.behaviour !== v.behaviour) this._setChoice(d, owner, { behaviour: v.behaviour, params: this._defaults(v.behaviour) });
        }, true), { behaviour: owner.kind === "mode" ? "Behaviour" : owner.mode === ALL ? "Behaviour" : `In ${this._modeName(owner.mode)}` }));
    });
    root.querySelectorAll('[data-form="params"]').forEach((slot) => {
      const owner = JSON.parse(slot.dataset.owner), ch = this._choice(this._draft, owner), params = this._beh(ch.behaviour).params;
      const schema = [], labels = {}, helps = {};
      for (const [k, s] of Object.entries(params)) {
        if (["targets", "steps"].includes(s.type)) continue;
        labels[k] = s.label || k; helps[k] = s.help;
        if (s.type === "number") schema.push({ name: k, required: !!s.required, selector: { number: { min: s.min, max: s.max, mode: "box", unit_of_measurement: s.unit } } });
        else if (s.type === "bool") schema.push({ name: k, selector: { boolean: {} } });
        else if (s.type === "entities") schema.push({ name: k, required: !!s.required, selector: { entity: { multiple: true, filter: { domain: s.domains } } } });
        else if (s.type === "entity") schema.push({ name: k, required: !!s.required, selector: { entity: { filter: { domain: s.domains } } } });
        else if (s.type === "mode") schema.push({ name: k, required: !!s.required, selector: { select: { mode: "dropdown", options: modeOpts() } } });
      }
      const data = {};
      for (const k of Object.keys(labels)) data[k] = (ch.params || {})[k] ?? params[k].default;
      const form = this._form(schema, data, (v) => this._edit((d) => {
        const p = (this._choice(d, owner).params ||= {});
        for (const k of Object.keys(labels)) { if (v[k] === undefined || v[k] === "" || v[k] === null) delete p[k]; else p[k] = v[k]; }
      }), labels, helps);
      form.dataset.owner = slot.dataset.owner;
      form.classList.add("params");
      slot.appendChild(form);
    });
    root.querySelectorAll("[data-picker]").forEach((slot) => {
      const loc = JSON.parse(slot.dataset.picker);
      this._picker(slot, { entity: { filter: { domain: ["light", "switch", "number"] } } }, "Add a light",
        (eid) => this._edit((d) => { this._targetsRef(d, loc)[eid] = eid.startsWith("number.") ? { value: 0 } : { brightness_pct: 100 }; }, true));
    });
  }

  _defaults(behaviour) {
    const p = {};
    for (const [k, s] of Object.entries(this._beh(behaviour).params || {})) {
      if (s.default !== undefined && !["targets", "steps"].includes(s.type)) p[k] = s.default;
      if (s.type === "steps" && s.required) p[k] = [{ after_minutes: 0, targets: {} }];
      if (s.type === "mode" && s.required) p[k] = this._modes()[0]?.id;
    }
    return p;
  }

  /** Errors and warnings into their slots (the deepest whose path starts theirs), scalar param
   *  errors onto their ha-form field, and dots on the tabs. */
  _renderErrors() {
    const root = this.shadowRoot;
    const slots = [...root.querySelectorAll(".errs-slot")].map((el) => ({ el, path: JSON.parse(el.dataset.path), errs: [], warns: [] }));
    const forms = [...root.querySelectorAll("ha-form.params")].map((f) => {
      const owner = JSON.parse(f.dataset.owner), ch = this._choice(this._draft, owner);
      return { f, path: this._ownerPath(owner), params: this._beh(ch?.behaviour).params || {}, out: {} };
    });
    const place = (e, kind) => {
      const p = e.path;
      for (const fm of forms) {
        if (p.length === fm.path.length + 2 && fm.path.every((x, i) => p[i] === x) && p[fm.path.length] === "params") {
          const spec = fm.params[p[fm.path.length + 1]];
          if (spec && !["targets", "steps"].includes(spec.type)) {
            if (kind === "errs") fm.out[p[fm.path.length + 1]] = e.message.replace(/^.*:\s+(?=[^:]+$)/, "");
            return;
          }
        }
      }
      let best = null;
      for (const s of slots) if (s.path.every((x, i) => p[i] === x) && (!best || s.path.length > best.path.length)) best = s;
      best?.[kind].push(e.message);
    };
    this._check.errors.forEach((e) => place(e, "errs"));
    this._check.warnings.forEach((e) => place(e, "warns"));
    for (const s of slots) {
      s.el.innerHTML = `${s.errs.length ? `<ul class="errs">${s.errs.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}${
        s.warns.length ? `<ul class="warns">${s.warns.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}`;
    }
    for (const fm of forms) fm.f.error = Object.keys(fm.out).length ? fm.out : undefined;
    const house = this._check.errors.filter((e) => ["zones", "modes", "fallback_mode"].includes(e.path[0])).length;
    const times = this._check.errors.filter((e) => ["day_types", "times", "schedule"].includes(e.path[0])).length;
    root.querySelectorAll("[data-tdot]").forEach((el) => { el.innerHTML = (el.dataset.tdot === "house" ? house : times) ? `<span class="dot"></span>` : ""; });
  }

  // ── events ─────────────────────────────────────────────────────────────
  _wire() {
    const root = this.shadowRoot, q = (sel, fn) => root.querySelectorAll(sel).forEach(fn);
    const room = this._view === "room" ? this._room : null;
    const zone = (d) => d.zones.find((x) => x.id === room);
    q("[data-view]", (b) => b.addEventListener("click", () => { this._view = b.dataset.view; this._modePanel = null; this._render(); window.scrollTo({ top: 0 }); }));
    q("[data-room]", (b) => b.addEventListener("click", () => this._goRoom(b.dataset.room)));
    q("[data-addon]", (b) => b.addEventListener("click", (e) => { e.stopPropagation(); const [z, k] = b.dataset.addon.split("|"); this._goRoom(z, k); }));
    q("[data-cell]", (td) => td.addEventListener("click", () => { const [z, m] = td.dataset.cell.split("|"); this._open.add(`${z}|${m}`); this._goRoom(z); }));
    q("[data-mode-open]", (b) => b.addEventListener("click", () => { this._modePanel = this._modePanel === b.dataset.modeOpen ? null : b.dataset.modeOpen; this._render(); }));
    q("[data-row]", (b) => b.addEventListener("click", () => this._toggle(b.dataset.row)));
    q("[data-dev]", (det) => det.addEventListener("toggle", () => { const k = `dev|${det.dataset.dev}`; if (det.open) this._open.add(k); else this._open.delete(k); }));
    q("[data-act]", (b) => { if (!b.closest(".bar")) b.addEventListener("click", () => this._act(b.dataset.act)); });
    q("[data-move-mode]", (b) => b.addEventListener("click", () => this._edit((d) => {
      const i = Number(b.dataset.moveMode), j = i + Number(b.dataset.dir); [d.modes[i], d.modes[j]] = [d.modes[j], d.modes[i]];
    }, true)));
    q("[data-del-mode]", (b) => b.addEventListener("click", () => this._deleteMode(b.dataset.delMode)));
    q(".tg-wrap", (w) => this._wireTargets(w));
    q("[data-step]", (inp) => inp.addEventListener("input", () => this._edit((d) => {
      this._choice(d, JSON.parse(inp.dataset.owner)).params[inp.dataset.key][Number(inp.dataset.step)].after_minutes = Number(inp.value || 0);
    })));
    q("[data-step-rm]", (b) => b.addEventListener("click", () => this._edit((d) => {
      this._choice(d, JSON.parse(b.dataset.owner)).params[b.dataset.key].splice(Number(b.dataset.stepRm), 1);
    }, true)));
    q("[data-step-add]", (b) => b.addEventListener("click", () => this._edit((d) => {
      const list = ((this._choice(d, JSON.parse(b.dataset.owner)).params ||= {})[b.dataset.key] ||= []);
      list.push({ after_minutes: (list.at(-1)?.after_minutes ?? 0) + 10, targets: {} });
    }, true)));
    // Add-ons
    const openId = () => (this._openAddon || "").split("|").slice(1).join("|");
    const inst = (d) => (zone(d).addons || []).find((a) => a.id === openId());
    q("[data-add-addon]", (sel) => sel.addEventListener("change", () => {
      const k = sel.value;
      if (!k) return;
      let id;
      this._edit((d) => {
        const x = zone(d), list = (x.addons ||= []);
        const base = { time: "wake", begins: "begins", ends: "ends", state: "device", while: "tv", button: "button", follow: "follow", auto_off: "auto-off" }[k];
        id = newId(list, base);
        const name = { time: "At a time", begins: "When a mode begins", ends: "When a mode ends", state: "When a device changes", while: "TV lighting",
          button: "Hold button", follow: "Follow", auto_off: "Auto-off" }[k];
        const lights = (trigger) => ({ id, name, type: "lights", trigger, steps: [{ after_minutes: 0, targets: {} }] });
        list.push(k === "time" ? lights({ at: "06:30" }) : k === "begins" ? lights({ mode_begins: [] }) : k === "ends" ? lights({ mode_ends: [] })
          : k === "state" ? lights({ entity: "", to: "on" })
          : k === "while" ? { id, name, type: "while", entity: "", states: { playing: { holds: true, targets: {} }, paused: { holds: true, targets: {} }, off: { targets: {} } } }
          : k === "button" ? { id, name, type: "button", entity: "", presses: {} }
          : k === "follow" ? { id, name, type: "follow", leader: "", bands: [{ min_pct: 50, targets: {} }, { min_pct: 0, targets: {} }] }
          : { id, name, type: "auto_off", lights: [], with_empty: true, on_minutes: 15 });
        this._openAddon = `${x.id}|${id}`;
      }, true);
    }));
    q("[data-ad-open]", (b) => b.addEventListener("click", () => {
      const key = `${room}|${b.dataset.adOpen}`;
      this._openAddon = this._openAddon === key ? null : key;
      this._render();
    }));
    q("[data-ad-on]", (sw) => sw.addEventListener("change", () => this._edit((d) => {
      const a = (zone(d).addons || []).find((x) => x.id === sw.dataset.adOn);
      if (sw.checked) delete a.enabled; else a.enabled = false;
    }, true)));
    q("[data-ad-move]", (b) => b.addEventListener("click", () => this._edit((d) => {
      const list = zone(d).addons, i = Number(b.dataset.adMove), j = i + Number(b.dataset.dir);
      [list[i], list[j]] = [list[j], list[i]];
    }, true)));
    q("[data-ad-rm]", (b) => b.addEventListener("click", () => {
      if (!confirm("Remove this add-on (in the draft)?")) return;
      this._openAddon = null;
      this._edit((d) => { const x = zone(d); x.addons = x.addons.filter((a) => a.id !== b.dataset.adRm); if (!x.addons.length) delete x.addons; }, true);
    }));
    q("[data-ad-name]", (inp) => inp.addEventListener("input", () => this._edit((d) => { inst(d).name = inp.value; })));
    q("[data-st-add]", (b) => b.addEventListener("click", () => this._edit((d) => {
      const list = (inst(d).steps ||= []); list.push({ after_minutes: (list.at(-1)?.after_minutes ?? 0) + 10, targets: {} });
    }, true)));
    q("[data-st-rm]", (b) => b.addEventListener("click", () => this._edit((d) => { inst(d).steps.splice(Number(b.dataset.stRm), 1); }, true)));
    q("[data-st-min]", (inp) => inp.addEventListener("input", () => this._edit((d) => { inst(d).steps[Number(inp.dataset.stMin)].after_minutes = Number(inp.value || 0); })));
    q("[data-st-act]", (sel) => sel.addEventListener("change", () => this._edit((d) => {
      const steps = inst(d).steps, i = Number(sel.dataset.stAct), after = steps[i].after_minutes || 0;
      steps[i] = sel.value === "lights" ? { after_minutes: after, targets: {} } : sel.value === "hold" ? { after_minutes: after, hold: 30 }
        : sel.value === "release" ? { after_minutes: after, release: true } : { after_minutes: after, script: "" };
    }, true)));
    q("[data-st-hold]", (sel) => sel.addEventListener("change", () => this._edit((d) => { inst(d).steps[Number(sel.dataset.stHold)].hold = holdVal(sel.value); }, true)));
    q("[data-st-flash]", (sel) => sel.addEventListener("change", () => this._edit((d) => {
      const s = inst(d).steps[Number(sel.dataset.stFlash)], n = Number(sel.value); if (n) s.flash = n; else delete s.flash;
    })));
    q("[data-pr-act]", (sel) => sel.addEventListener("change", () => this._edit((d) => {
      const presses = (inst(d).presses ||= {}), et = sel.dataset.prAct, cur = presses[et], v = sel.value;
      if (!v) { delete presses[et]; return; }
      const keep = cur && typeof cur === "object" ? { ...(cur.flash ? { flash: cur.flash } : {}), ...(cur.targets && v !== "release" ? { targets: cur.targets } : {}) } : {};
      presses[et] = v === "release" ? { release: true, ...keep } : { hold: holdVal(v), ...keep };
    }, true)));
    q("[data-pr-flash]", (sel) => sel.addEventListener("change", () => this._edit((d) => {
      const p = inst(d).presses[sel.dataset.prFlash], n = Number(sel.value); if (n) p.flash = n; else delete p.flash;
    })));
    q("[data-band-pct]", (inp) => inp.addEventListener("input", () => this._edit((d) => { inst(d).bands[Number(inp.dataset.bandPct)].min_pct = Number(inp.value || 0); })));
    q("[data-band-rm]", (b) => b.addEventListener("click", () => this._edit((d) => { inst(d).bands.splice(Number(b.dataset.bandRm), 1); }, true)));
    q("[data-band-add]", (b) => b.addEventListener("click", () => this._edit((d) => { inst(d).bands.push({ min_pct: 0, targets: {} }); }, true)));
    q("[data-ov-on]", (cb) => cb.addEventListener("change", () => this._edit((d) => {
      const st = (inst(d).states ||= {});
      if (cb.checked) st[cb.dataset.ovOn] = { targets: {} }; else delete st[cb.dataset.ovOn];
    }, true)));
    q("[data-ov-holds]", (cb) => cb.addEventListener("change", () => this._edit((d) => {
      const s = inst(d).states[cb.dataset.ovHolds]; if (cb.checked) s.holds = true; else delete s.holds;
    })));
    // Times
    q("[data-dt]", (row) => {
      const i = Number(row.dataset.dt);
      row.querySelector('[data-dt-f="name"]')?.addEventListener("input", (e) => this._edit((d) => { d.day_types[i].name = e.target.value; }));
      row.querySelector('[data-dt-f="set"]')?.addEventListener("change", (e) => this._edit((d) => { d.day_types[i].day_set = e.target.value; }, true));
    });
    q("[data-dt-move]", (b) => b.addEventListener("click", () => this._edit((d) => {
      const i = Number(b.dataset.dtMove), j = i + Number(b.dataset.dir); [d.day_types[i], d.day_types[j]] = [d.day_types[j], d.day_types[i]];
    }, true)));
    q("[data-dt-rm]", (b) => b.addEventListener("click", () => {
      const t = this._types()[Number(b.dataset.dtRm)];
      if (!confirm(`Remove the day type “${t.name}” and its times (in the draft)?`)) return;
      this._edit((d) => { d.day_types.splice(Number(b.dataset.dtRm), 1); if (d.times) delete d.times[t.id]; }, true);
    }));
    q("[data-add-stop]", (b) => b.addEventListener("click", () => this._edit((d) => {
      ((d.times ||= {})[b.dataset.addStop] ||= []).push({ mode: this._modes()[0]?.id, at: "12:00" });
    }, true)));
    q("[data-stop]", (row) => this._wireStop(row));
    q(".rule", (row) => this._wireRule(row));
  }

  _goRoom(id, box = null) {
    this._room = id; this._view = "room"; this._modePanel = null;
    if (box) this._openAddon = `${id}|${box}`;
    this._render();
    const el = box && this.shadowRoot.querySelector(`[data-box="${box}"]`);
    if (el) el.scrollIntoView({ block: "start", behavior: "smooth" }); else window.scrollTo({ top: 0 });
  }
  _toggle(k) { if (this._open.has(k)) this._open.delete(k); else this._open.add(k); this._render(); }

  _wireTargets(w) {
    const loc = JSON.parse(w.dataset.loc);
    const edit = (fn, redraw) => this._edit((d) => { fn(d); this._tidy(d, loc); }, redraw);
    w.querySelectorAll("tr[data-eid]").forEach((tr) => {
      const eid = tr.dataset.eid;
      tr.querySelector('[data-f="mode"]')?.addEventListener("change", (e) => edit((d) => {
        const t = this._targetsRef(d, loc), cur = t[eid] || {}, v = e.target.value, pct = cur.min_brightness_pct ?? cur.brightness_pct;
        t[eid] = v === "off" ? { state: "off" } : v === "min" ? { min_brightness_pct: pct ?? 1 }
          : eid.startsWith("light.") ? { brightness_pct: pct ?? 100, ...(cur.color_temp_kelvin ? { color_temp_kelvin: cur.color_temp_kelvin } : {}) } : { state: "on" };
      }, true));
      tr.querySelector('[data-f="pct"]')?.addEventListener("input", (e) => edit((d) => {
        const t = this._targetsRef(d, loc)[eid], v = e.target.value === "" ? null : Number(e.target.value);
        const key = t.min_brightness_pct != null ? "min_brightness_pct" : "brightness_pct";
        if (v == null) { delete t[key]; if (key === "brightness_pct" && !Object.keys(t).length) t.state = "on"; } else { t[key] = v; delete t.state; }
      }));
      tr.querySelector('[data-f="k"]')?.addEventListener("input", (e) => edit((d) => {
        const t = this._targetsRef(d, loc)[eid]; if (e.target.value === "") delete t.color_temp_kelvin; else t.color_temp_kelvin = Number(e.target.value);
      }));
      tr.querySelector('[data-f="rm"]')?.addEventListener("click", () => edit((d) => { delete this._targetsRef(d, loc)[eid]; }, true));
    });
    w.querySelector('[data-tg="capture"]')?.addEventListener("click", () => this._capture(loc));
    w.querySelector('[data-tg="try"]')?.addEventListener("click", () => this._try(loc));
  }

  /** A day type's time entry; "same in …" changes the identical entries in the other day types too. */
  _wireStop(row) {
    const [tid, js] = row.dataset.stop.split("|"), j = Number(js), key = `${tid}|${j}`;
    const linked = () => !!row.querySelector('[data-s="link"]')?.checked;
    const change = (fn, redraw = false) => this._edit((d) => {
      const e = d.times[tid][j], before = { mode: e.mode, at: e.at, eve: !!e.eve };
      const twins = linked() ? Object.entries(d.times).filter(([k]) => k !== tid)
        .flatMap(([, list]) => list.filter((f) => f.mode === before.mode && f.at === before.at && !!f.eve === before.eve)) : [];
      for (const x of [e, ...twins]) fn(x);
    }, redraw);
    const on = (sel, ev, fn, redraw) => row.querySelector(sel)?.addEventListener(ev, (e) => change((x) => fn(x, e.target), redraw));
    on('[data-s="mode"]', "change", (x, t) => { x.mode = t.value; }, true);
    on('[data-s="kind"]', "change", (x, t) => { const p = parseAt(x.at); x.at = t.value === "clock" ? (p.kind === "clock" && p.time ? p.time : "07:00") : joinAt({ kind: t.value, sign: p.sign || "-", off: p.off || "00:00" }); }, true);
    on('[data-s="time"]', "change", (x, t) => { if (t.value) x.at = t.value.slice(0, 5); }, true);
    on('[data-s="sign"]', "change", (x, t) => { const p = parseAt(x.at); p.sign = t.value; x.at = joinAt(p); }, true);
    on('[data-s="off"]', "change", (x, t) => { const p = parseAt(x.at); p.off = offInput(t.value); x.at = joinAt(p); }, true);
    on('[data-s="eve"]', "change", (x, t) => { if (t.checked) x.eve = true; else delete x.eve; }, true);
    row.querySelector('[data-s="link"]')?.addEventListener("change", (e) => { if (e.target.checked) this._unlinked.delete(key); else this._unlinked.add(key); });
    row.querySelector('[data-s="rm"]')?.addEventListener("click", () => change((x) => { x.__rm = true; }, false) || this._edit((d) => {
      for (const k of Object.keys(d.times)) d.times[k] = d.times[k].filter((x) => !x.__rm);
    }, true));
  }

  _wireRule(row) {
    const i = Number(row.dataset.rule), rule = (d) => d.schedule[i];
    const on = (sel, ev, fn, redraw = false) => row.querySelector(sel)?.addEventListener(ev, (e) => this._edit((d) => fn(rule(d), e.target), redraw));
    on('[data-r="mode"]', "change", (r, t) => { r.mode = t.value; }, true);
    on('[data-r="atkind"]', "change", (r, t) => { const p = parseAt(r.at); r.at = t.value === "clock" ? (p.kind === "clock" && p.time ? p.time : "07:00") : joinAt({ kind: t.value, sign: p.sign || "-", off: p.off || "00:00" }); }, true);
    on('[data-r="time"]', "change", (r, t) => { if (t.value) r.at = t.value.slice(0, 5); });
    on('[data-r="sign"]', "change", (r, t) => { const p = parseAt(r.at); p.sign = t.value; r.at = joinAt(p); });
    on('[data-r="off"]', "change", (r, t) => { const p = parseAt(r.at); p.off = offInput(t.value); r.at = joinAt(p); });
    on('[data-r="by"]', "change", (r, t) => {
      if (t.value === "set") { delete r.days; r.day_set = (this._schema.day_sets || [])[0]?.id || ""; }
      else { delete r.day_set; delete r.eve; delete r.not; r.days = ["mon", "tue", "wed", "thu", "fri"]; }
    }, true);
    on('[data-r="set"]', "change", (r, t) => { r.day_set = t.value; });
    on('[data-r="which"]', "change", (r, t) => { delete r.eve; delete r.not; if (t.value.includes("eve")) r.eve = true; if (t.value.includes("not")) r.not = true; });
    row.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => this._edit((d) => {
      const r = rule(d), set = new Set(r.days || []);
      if (set.has(b.dataset.day)) set.delete(b.dataset.day); else set.add(b.dataset.day);
      r.days = (this._schema.days || Object.keys(DAY_NAMES)).filter((x) => set.has(x));
    }, true)));
    row.querySelector('[data-r="rm"]')?.addEventListener("click", () => this._edit((d) => { d.schedule.splice(i, 1); if (!d.schedule.length) delete d.schedule; }, true));
  }

  _capture(loc) {
    const zoneId = this._zoneOf(loc);
    const zs = zoneId && Object.values(this._hass.states).find((s) => s.attributes.sb_modes_zone === zoneId);
    const listed = Object.keys(this._lookTargets(loc));
    const lights = listed.length ? listed : (zs?.attributes.lights || []);
    if (!lights.length) { this._msg = "No lights to read — add one first."; this._renderBar(); return; }
    this._edit((d) => {
      const t = this._targetsRef(d, loc);
      for (const eid of lights) {
        const st = this._hass.states[eid];
        if (!st || ["unavailable", "unknown"].includes(st.state)) continue;
        if (eid.startsWith("number.")) { t[eid] = { value: Number(st.state) }; continue; }
        if (st.state !== "on") { t[eid] = { state: "off" }; continue; }
        const x = {};
        if (st.attributes.brightness != null) x.brightness_pct = Math.round(st.attributes.brightness * 100 / 255);
        if (st.attributes.color_mode === "color_temp" && st.attributes.color_temp_kelvin) x.color_temp_kelvin = Math.round(st.attributes.color_temp_kelvin / 50) * 50;
        t[eid] = Object.keys(x).length ? x : { state: "on" };
      }
    }, true);
  }

  async _try(loc) {
    const zoneId = this._zoneOf(loc);
    try {
      await this._ws({ type: "sb_modes/try", zone: zoneId, targets: this._lookTargets(loc) });
      this._msg = "";
      this._toast = `${this._zoneObj(zoneId)?.name || zoneId} set and held 15 min — release it on the Status page to go back`;
    } catch (e) { this._msg = `Try it failed: ${e?.message || e}`; }
    this._renderBar();
  }

  _deleteMode(id) {
    const d = this._draft, uses = [], blocks = [];
    for (const z of d.zones || []) {
      if ((z.overrides || {})[id]) uses.push(`${z.name || z.id}: its own setting for this mode`);
      for (const a of z.addons || []) {
        const t = a.trigger || {};
        if ((t.mode_begins || []).includes(id) || (t.mode_ends || []).includes(id) || (a.when?.modes || []).includes(id))
          blocks.push(`${z.name || z.id}: the add-on “${a.name || a.id}” names this mode`);
      }
      for (const [k, c] of Object.entries(z.overrides || {})) if (c?.params?.after_mode === id) blocks.push(`${z.name || z.id} (${this._modeName(k)}): a wake-up after this mode`);
    }
    for (const m of d.modes || []) if (m.id !== id && m.default?.params?.after_mode === id) blocks.push(`${m.name || m.id}: a wake-up after this mode`);
    for (const [t, list] of Object.entries(d.times || {})) if ((list || []).some((e) => e.mode === id)) uses.push(`${this._types().find((x) => x.id === t)?.name || t}: its times`);
    (d.schedule || []).forEach((r, i) => { if (r.mode === id) uses.push(`other rule ${i + 1}`); });
    if (d.fallback_mode === id) blocks.push("it is the start-up mode");
    if (blocks.length) { alert(`“${this._modeName(id)}” can't be deleted yet:\n• ${blocks.join("\n• ")}\nChange those first.`); return; }
    if (this._modes().length <= 1) { alert("The table needs at least one mode."); return; }
    if (!confirm(`Delete the mode “${this._modeName(id)}” from the draft?${uses.length ? `\nThese go with it:\n• ${uses.join("\n• ")}` : ""}\nNothing changes until you save.`)) return;
    this._modePanel = null;
    this._edit((dd) => {
      dd.modes = dd.modes.filter((m) => m.id !== id);
      for (const z of dd.zones || []) {
        if (z.overrides) { delete z.overrides[id]; if (!Object.keys(z.overrides).length) delete z.overrides; }
      }
      for (const k of Object.keys(dd.times || {})) dd.times[k] = dd.times[k].filter((e) => e.mode !== id);
      if (dd.schedule) { dd.schedule = dd.schedule.filter((r) => r.mode !== id); if (!dd.schedule.length) delete dd.schedule; }
    }, true);
  }

  async _act(act) {
    if (act === "review") { this._review = true; this._render(); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    if (act === "review-close") { this._review = false; this._render(); return; }
    if (act === "mode-close") { this._modePanel = null; this._render(); return; }
    if (act === "discard") {
      if (!confirm("Discard the draft? The table stays as saved.")) return;
      clearTimeout(this._timer);
      await this._ws({ type: "sb_modes/draft/discard" });
      this._review = false; this._dirty = false; this._toast = ""; this._unlinked.clear();
      await this._load();
      return;
    }
    if (act === "save") {
      clearTimeout(this._timer);
      if (this._dirty) await this._sync();
      try {
        const r = await this._ws({ type: "sb_modes/save", force: this._stale });
        this._review = false;
        await this._load();
        this._toast = `Saved as version ${r.version}`;
        this._renderBar();
      } catch (e) { this._msg = `Not saved: ${e?.message || e}`; this._renderBar(); }
      return;
    }
    if (act === "add-zone") {
      const name = prompt("Name of the new room:");
      if (!name) return;
      const id = uniqueId(name, new Set(this._zones().map((z) => z.id)));
      this._open.add(`dev|${id}`);
      this._room = id; this._view = "room";
      this._edit((d) => { (d.zones ||= []).push({ id, name, areas: [], overrides: { [ALL]: { behaviour: "hands_off", params: {} } } }); }, true);
      return;
    }
    if (act === "del-zone") {
      const z = this._zoneObj(this._room);
      if (!z || !confirm(`Delete the room “${z.name || z.id}” from the draft? Nothing changes until you save.`)) return;
      this._view = "house"; this._room = null;
      this._edit((d) => { d.zones = d.zones.filter((x) => x.id !== z.id); }, true);
      return;
    }
    if (act === "add-mode") {
      const name = prompt("Name of the new mode:");
      if (!name) return;
      const id = uniqueId(name, new Set(this._modes().map((m) => m.id)));
      this._modePanel = id;
      this._edit((d) => { (d.modes ||= []).push({ id, name, default: { behaviour: "hands_off", params: {} } }); }, true);
      return;
    }
    if (act === "start-types") {
      this._edit((d) => { d.day_types = [{ id: "every_day", name: "Every day" }]; d.times = { every_day: [] }; }, true);
      return;
    }
    if (act === "add-type") {
      const name = prompt("Name of the new day type (for example Vacation):");
      if (!name) return;
      const id = uniqueId(name, new Set(this._types().map((t) => t.id)));
      const sets = this._schema.day_sets || [], used = new Set(this._types().map((t) => t.day_set));
      const guess = sets.find((s) => !used.has(s.id) && (s.id === id || s.name.toLowerCase().includes(name.toLowerCase())));
      this._edit((d) => {
        const last = d.day_types.length - 1, copyFrom = d.day_types[last]?.id;
        d.day_types.splice(0, 0, { id, name, day_set: guess?.id || "" });           // new types go first: they are the exceptions
        (d.times ||= {})[id] = clone((d.times || {})[copyFrom] || []);
      }, true);
      return;
    }
    if (act === "add-rule") {
      this._edit((d) => { (d.schedule ||= []).push({ days: ["mon", "tue", "wed", "thu", "fri"], at: "07:00", mode: this._modes()[0]?.id }); }, true);
    }
  }
}

export const defineEditor = () => {
  if (!customElements.get("sb-modes-editor")) {
    try { customElements.define("sb-modes-editor", class extends SbModesEditor {}); } catch (e) { /* defined meanwhile */ }
  }
};

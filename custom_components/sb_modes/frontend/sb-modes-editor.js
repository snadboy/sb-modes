/* SB Modes — the editor (sidebar page /sb-modes/edit). Loaded by the page only when opened.
 *
 * Draft → check → review → save, against the integration's websocket commands (editor.py):
 * every edit changes a working copy of the table and, after a short pause, is stored as the
 * shared draft (sb_modes/draft/set), which answers with its errors (with field paths) and the
 * changes in words. Save turns the draft into the table (a new version); Discard drops it.
 *
 * Tabs: Zones (basics, each mode's behaviour), Modes (name, icon, default behaviour, order,
 * fallback) and Schedule (rules, with the coming week previewed from the draft). The zone
 * extras (follow, overlay, buttons, when a mode begins) are kept as they are until step 4.
 *
 * A behaviour setting lives in one of two places, an OWNER: a zone's setting for a mode
 * ({kind: "zone", zone, mode}) or a mode's default ({kind: "mode", mode}); the same editor
 * (behaviour picker, schema-built form, light-settings tables, wake steps) serves both.
 *
 * Typing never redraws the form (it would lose focus): field edits change the model and the
 * draft bar; only structural changes (another tab or zone, a row opened, a behaviour picked, a
 * light or rule added or removed) redraw.
 */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));
const ALL = "*";
/** The card's built-in icons for the usual mode ids (a mode's own `icon` wins). */
const DEFAULT_ICONS = {
  morning: "mdi:weather-sunset-up", day: "mdi:white-balance-sunny", evening: "mdi:weather-sunset-down",
  overnight: "mdi:weather-night", night: "mdi:weather-night", away: "mdi:home-export-outline",
};
const DAY_NAMES = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
const slug = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "x_$1").slice(0, 32) || "item";

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

const STYLE = `
:host { display: block; }
.bar { position: sticky; top: var(--header-height, 56px); z-index: 1; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
       padding: 10px 14px; margin-bottom: 12px; border-radius: 12px; background: var(--card-background-color); border: 1px solid var(--divider-color); }
.bar .st { flex: 1; font-size: 14px; min-width: 160px; }
.bar .st b { font-weight: 500; }
.err { color: var(--error-color); }
button.b { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 18px; padding: 6px 14px;
           font: inherit; font-size: 14px; cursor: pointer; }
button.b.pri { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
button.b:disabled { opacity: .45; cursor: default; }
button.b.sm { padding: 3px 10px; font-size: 13px; }
button.b.danger { color: var(--error-color); border-color: var(--error-color); }
button.ic { border: none; background: none; color: var(--secondary-text-color); cursor: pointer; font-size: 18px; padding: 2px 6px; line-height: 1; }
button.ic:disabled { opacity: .3; cursor: default; }
.tabs { display: flex; gap: 6px; margin-bottom: 12px; }
.tabs button { flex: 1; border: 1px solid var(--divider-color); background: var(--card-background-color); color: inherit; font: inherit; font-size: 15px;
               border-radius: 10px; padding: 8px 0; cursor: pointer; }
.tabs button.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 500; }
.tabs .dot, .chips .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--error-color); margin-left: 6px; vertical-align: middle; }
.card { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 12px; margin-bottom: 12px; }
.sec { padding: 12px 16px; }
.sec + .sec { border-top: 1px solid var(--divider-color); }
h3 { margin: 0 0 8px; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 16px; }
.chips button { border: 1px solid var(--divider-color); background: none; color: inherit; font: inherit; font-size: 14px; border-radius: 16px; padding: 5px 12px; cursor: pointer; }
.chips button.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.mrow { border-top: 1px solid var(--divider-color); }
.mrow:first-of-type { border-top: none; }
.mhead { display: flex; align-items: center; gap: 10px; width: 100%; padding: 10px 0; background: none; border: none; color: inherit; font: inherit; cursor: pointer; text-align: left; }
.mhead .mn { width: 110px; flex: none; font-weight: 500; }
.mhead .what { flex: 1; color: var(--secondary-text-color); font-size: 14px; }
.mhead .what.own { color: var(--primary-text-color); }
.mhead ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
.mbody { padding: 0 0 14px; }
.help { color: var(--secondary-text-color); font-size: 13px; margin: 2px 0 8px; line-height: 1.45; }
.errs { color: var(--error-color); font-size: 13px; margin: 4px 0 8px; padding-left: 18px; }
.warns { color: var(--warning-color, #ffa600); font-size: 13px; margin: 4px 0 8px; padding-left: 18px; }
.tlabel { font-weight: 500; font-size: 14px; margin: 12px 0 2px; }
table.tg { width: 100%; border-collapse: collapse; font-size: 14px; }
table.tg td { padding: 4px 4px 4px 0; vertical-align: middle; }
table.tg td.n { width: 40%; }
select, input.in { font: inherit; font-size: 14px; color: var(--primary-text-color); background: var(--secondary-background-color, transparent);
       border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 6px; }
table.tg input[type=number] { width: 64px; }
.tools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
.tools .add { min-width: 220px; flex: 1; }
.step { border-left: 3px solid var(--divider-color); padding-left: 10px; margin: 10px 0; }
.step .sh { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.step input { width: 64px; }
.note { font-size: 13px; padding: 8px 12px; border-radius: 8px; background: var(--secondary-background-color, rgba(127,127,127,.1)); margin: 0 0 12px; }
.review ul { margin: 6px 0; padding-left: 20px; font-size: 14px; line-height: 1.6; }
.extra { font-size: 14px; color: var(--secondary-text-color); line-height: 1.6; }
.empty { color: var(--secondary-text-color); font-size: 14px; padding: 4px 0; }
.load { padding: 24px; color: var(--secondary-text-color); }
.mtitle { display: flex; align-items: center; gap: 8px; }
.mtitle .mn { flex: 1; font-weight: 500; font-size: 16px; cursor: pointer; display: flex; align-items: center; gap: 8px; }
.mtitle .mid { color: var(--secondary-text-color); font-size: 12px; font-weight: 400; }
.mtitle ha-icon { --mdc-icon-size: 20px; }
.rule { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 0; border-top: 1px solid var(--divider-color); font-size: 14px; }
.rule:first-of-type { border-top: none; }
.rule .days { display: flex; gap: 3px; flex-wrap: wrap; }
.rule .days button { border: 1px solid var(--divider-color); background: none; color: inherit; font: inherit; font-size: 12px; border-radius: 12px; padding: 3px 7px; cursor: pointer; }
.rule .days button.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.rule label { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; }
.rule .grow { flex: 1; }
.rule .errs { flex-basis: 100%; margin: 0; }
.pv .day { font-size: 12px; font-weight: 500; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); margin: 10px 0 2px; }
.pv .day:first-child { margin-top: 0; }
.pv .ch { display: flex; gap: 10px; align-items: center; font-size: 14px; padding: 2px 0; }
.pv .ch .t { width: 74px; font-variant-numeric: tabular-nums; color: var(--secondary-text-color); }
.pv .ch ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
`;

export class SbModesEditor extends HTMLElement {
  constructor() {
    super();
    this._open = new Set();
    this._timer = null;
    this._check = { errors: [], warnings: [] };
    this._changes = [];
    this._tab = "zones";
  }

  set hass(h) {
    const first = !this._hass;
    this._hass = h;
    if (first) this._init();
    this.shadowRoot?.querySelectorAll("ha-form, ha-selector").forEach((f) => { f.hass = h; });
  }

  set zone(z) { this._wantZone = z; }

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

  /** The saved table and the draft (if any) from HA. */
  async _load() {
    const r = await this._ws({ type: "sb_modes/draft/get" });
    this._saved = r.saved;
    this._version = r.version;
    this._draft = clone(r.draft || r.saved);
    this._hasDraft = !!r.draft;
    this._stale = !!r.stale;
    this._changes = r.changes || [];
    this._check = { errors: r.errors || [], warnings: r.warnings || [] };
    const zones = this._draft.zones || [];
    if (!this._zone || !zones.some((z) => z.id === this._zone)) {
      this._zone = (this._wantZone && zones.some((z) => z.id === this._wantZone)) ? this._wantZone : zones[0]?.id;
    }
    this._preview = null;
    this._render();
  }

  // ── model ──────────────────────────────────────────────────────────────
  _modes() { return this._draft.modes || []; }
  _modeName(id) { return id === ALL ? "All modes" : (this._modes().find((m) => m.id === id)?.name || id); }
  _modeIcon(id) { return this._modes().find((m) => m.id === id)?.icon || DEFAULT_ICONS[id] || "mdi:home-clock-outline"; }
  _zoneObj(id = this._zone) { return (this._draft.zones || []).find((z) => z.id === id); }
  _zoneIndex(id = this._zone) { return (this._draft.zones || []).findIndex((z) => z.id === id); }
  _modeIndex(id) { return this._modes().findIndex((m) => m.id === id); }
  _beh(name) { return this._schema.behaviours[name] || { label: name || "—", params: {} }; }
  _name(eid) { return this._hass.states[eid]?.attributes.friendly_name || eid; }
  /** A light's name without its zone's name in front ("Kitchen Aux Sink" in Kitchen → "Aux Sink"). */
  _short(eid, zoneId = this._zone) {
    const n = this._name(eid), z = this._zoneObj(zoneId)?.name;
    return z && n.toLowerCase().startsWith(`${z.toLowerCase()} `) && n.length > z.length + 1 ? n.slice(z.length + 1) : n;
  }

  /** The {behaviour, params} an owner holds in table d (undefined when it has none). */
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
    return owner.kind === "mode" ? ["modes", this._modeIndex(owner.mode), "default"]
      : ["zones", this._zoneIndex(owner.zone), "overrides", owner.mode];
  }
  _targetsRef(d, loc) {
    const ch = this._choice(d, loc.owner);
    ch.params = ch.params || {};
    if (loc.step != null) {
      const st = ch.params[loc.key][loc.step];
      st.targets = st.targets || {};
      return st.targets;
    }
    ch.params[loc.key] = ch.params[loc.key] || {};
    return ch.params[loc.key];
  }
  _lookTargets(loc) { try { return this._targetsRef(clone(this._draft), loc); } catch (e) { return {}; } }
  _locPath(loc) {
    const p = [...this._ownerPath(loc.owner), "params", loc.key];
    return loc.step != null ? [...p, loc.step, "targets"] : p;
  }

  /** A field edit: change the model, store the draft after a pause, refresh the bar. */
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
      this._hasDraft = true;
      this._dirty = false;
      this._changes = r.changes || [];
      this._check = { errors: r.errors || [], warnings: r.warnings || [] };
      this._msg = "";
    } catch (e) { this._msg = `The draft was not stored: ${e?.message || e}`; }
    this._renderBar();
    this._renderErrors();
    if (this._tab === "schedule") this._loadPreview();
  }

  _errorsAt(prefix, list = this._check.errors) {
    return list.filter((e) => prefix.every((p, i) => e.path[i] === p));
  }

  // ── rendering ──────────────────────────────────────────────────────────
  _render() {
    const root = this.shadowRoot;
    const tabErr = (k) => this._errorsAt([k === "schedule" ? "schedule" : k]).length + (k === "modes" ? this._errorsAt(["fallback_mode"]).length : 0);
    const tabs = [["zones", "Zones"], ["modes", "Modes"], ["schedule", "Schedule"]].map(([k, l]) =>
      `<button role="tab" data-tab="${k}" class="${this._tab === k ? "on" : ""}" aria-selected="${this._tab === k}">${l}${tabErr(k) ? `<span class="dot"></span>` : ""}</button>`).join("");
    const body = this._tab === "modes" ? this._modesHtml() : this._tab === "schedule" ? this._scheduleHtml() : this._zonesHtml();
    root.innerHTML = `<style>${STYLE}</style>
      <div class="bar"></div>
      ${this._review ? this._reviewHtml() : ""}
      <div class="tabs" role="tablist" aria-label="Edit">${tabs}</div>
      ${body}`;
    this._renderBar();
    this._mountForms();
    this._wire();
    this._renderErrors();
    if (this._tab === "schedule" && !this._preview) this._loadPreview();
  }

  _renderBar() {
    const bar = this.shadowRoot.querySelector(".bar");
    if (!bar) return;
    const n = this._changes.length, ne = this._check.errors.length;
    const pending = this._dirty ? " · storing the draft…" : "";
    bar.innerHTML = `<span class="st">${this._hasDraft || this._dirty
        ? `<b>Draft</b> · ${n} change${n === 1 ? "" : "s"}${ne ? ` · <span class="err">${ne} error${ne === 1 ? "" : "s"}</span>` : ""}${pending}${
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

  // ── Zones tab ──────────────────────────────────────────────────────────
  _zonesHtml() {
    const z = this._zoneObj();
    return `<div class="card"><div class="chips" role="tablist" aria-label="Zones">${(this._draft.zones || []).map((x, i) =>
        `<button role="tab" data-zone="${esc(x.id)}" class="${x.id === this._zone ? "on" : ""}" aria-selected="${x.id === this._zone}">${esc(x.name || x.id)}${
          this._errorsAt(["zones", i]).length ? `<span class="dot" title="Has errors"></span>` : ""}</button>`).join("")}
        <button data-act="add-zone" title="Add a zone">+ Zone</button></div></div>
      ${z ? this._zoneHtml(z) : `<div class="card"><div class="sec empty">No zones yet — add one.</div></div>`}`;
  }

  _zoneHtml(z) {
    const zi = this._zoneIndex();
    const rows = [ALL, ...this._modes().map((m) => m.id)].map((mid) => this._zoneModeRowHtml(z, zi, mid)).join("");
    return `<div class="card">
      <div class="sec"><h3>${esc(z.name || z.id)}</h3><div class="errs-slot" data-path='${esc(JSON.stringify(["zones", zi]))}'></div>
        <div class="form" data-form="basics"></div>
        <div class="tools"><button class="b sm danger" data-act="del-zone">Delete zone</button></div></div>
      <div class="sec"><h3>Behaviour by mode</h3>
        <div class="help">“All modes” applies wherever a mode has no setting of its own; a mode with neither uses its own default.</div>
        ${rows}</div>
      <div class="sec"><h3>More</h3><div class="extra">${this._extrasHtml(z)}</div></div>
    </div>`;
  }

  /** One row per mode: what the zone does there, and its editor when open. */
  _zoneModeRowHtml(z, zi, mid) {
    const ov = (z.overrides || {})[mid];
    const key = `${z.id}|${mid}`;
    const open = this._open.has(key);
    let what;
    if (ov) what = this._beh(ov.behaviour).label;
    else if (mid === ALL) what = "Not set — each mode's default";
    else if ((z.overrides || {})[ALL]) what = `From All modes: ${this._beh(z.overrides[ALL].behaviour).label}`;
    else what = `Mode default: ${this._beh(this._modes().find((m) => m.id === mid)?.default?.behaviour).label}`;
    const nerr = this._errorsAt(["zones", zi, "overrides", mid]).length;
    return `<div class="mrow"><button class="mhead" data-row="${esc(key)}" aria-expanded="${open}">
        <span class="mn">${esc(this._modeName(mid))}</span><span class="what ${ov ? "own" : ""}">${esc(what)}${nerr ? ` <span class="err">· ${nerr} error${nerr === 1 ? "" : "s"}</span>` : ""}</span>
        <ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button>
      ${open ? `<div class="mbody">${this._choiceEditorHtml({ kind: "zone", zone: z.id, mode: mid }, ov)}</div>` : ""}</div>`;
  }

  _extrasHtml(z) {
    const bits = [];
    if (z.on_enter) bits.push(`When a mode begins: ${Object.keys(z.on_enter).map((m) => esc(this._modeName(m))).join(", ")}`);
    if (z.follow) bits.push(`Follows ${esc(this._name(z.follow.leader))}`);
    if (z.overlay) bits.push(`TV overlay: ${esc(this._name(z.overlay.entity))}`);
    if (z.buttons) bits.push(`Buttons: ${Object.keys(z.buttons).map((e) => esc(this._name(e))).join(", ")}`);
    return (bits.length ? bits.join("<br>") : "None.") + `<br><span class="help">These are edited in a later step; they are kept as they are.</span>`;
  }

  // ── the behaviour editor (a zone's setting for a mode, or a mode's default) ──
  _choiceEditorHtml(owner, ch) {
    const o = esc(JSON.stringify(owner));
    const pick = `<div class="form" data-form="pick" data-owner='${o}'></div>`;
    if (!ch) return `${pick}<div class="help">${owner.mode === ALL ? "Pick a behaviour to use in every mode without its own setting." : "Pick a behaviour to give this mode its own setting."}</div>`;
    const b = this._beh(ch.behaviour);
    const params = b.params || {};
    const scalar = Object.keys(params).some((k) => !["targets", "steps"].includes(params[k].type));
    const big = Object.entries(params).filter(([, s]) => ["targets", "steps"].includes(s.type));
    return `${pick}<div class="help">${esc(b.help || "")}</div>
      <div class="errs-slot" data-path='${esc(JSON.stringify(this._ownerPath(owner)))}'></div>
      ${scalar ? `<div class="form" data-form="params" data-owner='${o}'></div>` : ""}
      ${big.map(([k, s]) => s.type === "targets"
        ? this._targetsHtml({ owner, key: k }, (ch.params || {})[k] || {}, s)
        : this._stepsHtml(owner, k, (ch.params || {})[k] || [], s)).join("")}`;
  }

  /** The light-settings table for one targets parameter. */
  _targetsHtml(loc, targets, spec) {
    const id = esc(JSON.stringify(loc));
    const zoneId = loc.owner.kind === "zone" ? loc.owner.zone : null;
    const rows = Object.entries(targets).map(([eid, t]) => {
      const mode = t.state === "off" ? "off" : t.min_brightness_pct != null ? "min" : "on";
      const pct = mode === "min" ? t.min_brightness_pct : t.brightness_pct;
      const isLight = eid.startsWith("light.");
      return `<tr data-eid="${esc(eid)}"><td class="n" title="${esc(this._name(eid))} (${esc(eid)})">${esc(zoneId ? this._short(eid, zoneId) : this._name(eid))}</td>
        <td><select data-f="mode" aria-label="Setting">${[["on", "On"], ["off", "Off"], ...(isLight ? [["min", "At least"]] : [])]
          .map(([v, l]) => `<option value="${v}" ${v === mode ? "selected" : ""}>${l}</option>`).join("")}</select></td>
        <td>${isLight && mode !== "off" ? `<input class="in" type="number" min="0" max="100" data-f="pct" value="${esc(pct ?? "")}" placeholder="—" aria-label="Brightness %"> %` : ""}</td>
        <td>${isLight && mode === "on" ? `<input class="in" type="number" min="1500" max="9000" step="100" data-f="k" value="${esc(t.color_temp_kelvin ?? "")}" placeholder="—" aria-label="Colour temperature K"> K` : ""}</td>
        <td><button class="ic" data-f="rm" title="Remove" aria-label="Remove">×</button></td></tr>`;
    }).join("");
    return `<div class="tg-wrap" data-loc='${id}'>
      <div class="tlabel">${esc(spec.label || loc.key)}</div>${spec.help ? `<div class="help">${esc(spec.help)}</div>` : ""}
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
        ${this._targetsHtml({ owner, key, step: i }, st.targets || {}, { label: `Step ${i + 1}` })}</div>`).join("")}
      <div class="tools"><button class="b sm" data-step-add="1" data-owner='${o}' data-key="${esc(key)}">+ Step</button></div>`;
  }

  // ── Modes tab ──────────────────────────────────────────────────────────
  _modesHtml() {
    const modes = this._modes();
    const list = modes.map((m, i) => {
      const open = this._open.has(`mode|${m.id}`);
      const saved = (this._saved.modes || []).some((x) => x.id === m.id);
      const nerr = this._errorsAt(["modes", i]).length;
      return `<div class="sec"><div class="mtitle">
          <span class="mn" data-mode-row="${esc(m.id)}" role="button" aria-expanded="${open}"><ha-icon icon="${esc(this._modeIcon(m.id))}"></ha-icon>${esc(m.name || m.id)}
            <span class="mid">${esc(m.id)}${saved ? "" : " · new"}</span>${nerr ? ` <span class="err" style="font-size:13px">· ${nerr} error${nerr === 1 ? "" : "s"}</span>` : ""}</span>
          <button class="ic" data-move="${i}" data-dir="-1" ${i ? "" : "disabled"} title="Move up" aria-label="Move up">↑</button>
          <button class="ic" data-move="${i}" data-dir="1" ${i < modes.length - 1 ? "" : "disabled"} title="Move down" aria-label="Move down">↓</button>
          <button class="ic" data-mode-row="${esc(m.id)}" aria-label="${open ? "Close" : "Open"}"><ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button></div>
        ${open ? `<div class="errs-slot" data-path='${esc(JSON.stringify(["modes", i]))}'></div>
          <div class="form" data-form="mode" data-mode="${esc(m.id)}"></div>
          <div class="tlabel">Default behaviour</div><div class="help">What every zone does in ${esc(m.name || m.id)} unless it has its own setting (or one for All modes).</div>
          ${this._choiceEditorHtml({ kind: "mode", mode: m.id }, m.default)}
          <div class="tools"><button class="b sm danger" data-del-mode="${esc(m.id)}">Delete mode</button></div>` : ""}</div>`;
    }).join("");
    return `<div class="card"><div class="sec"><h3>Fallback</h3><div class="errs-slot" data-path='["fallback_mode"]'></div>
        <div class="form" data-form="fallback"></div>
        <div class="help">The mode at start-up when the schedule gives none, and when the active mode is deleted.</div></div></div>
      <div class="card">${list || `<div class="sec empty">No modes.</div>`}
        <div class="sec"><button class="b sm" data-act="add-mode">+ Mode</button></div></div>`;
  }

  /** Where a mode is used, in words — before it is deleted. */
  _modeUses(id) {
    const d = this._draft, uses = [], blocks = [];
    for (const z of d.zones || []) {
      if ((z.overrides || {})[id]) uses.push(`${z.name || z.id}: its own setting for this mode`);
      if ((z.on_enter || {})[id]) uses.push(`${z.name || z.id}: settings when this mode begins`);
      for (const [k, c] of Object.entries(z.overrides || {})) if (c?.params?.after_mode === id) blocks.push(`${z.name || z.id} (${this._modeName(k)}): wake-up after this mode`);
    }
    for (const m of d.modes || []) if (m.id !== id && m.default?.params?.after_mode === id) blocks.push(`${m.name || m.id}: its default wakes up after this mode`);
    (d.schedule || []).forEach((r, i) => { if (r.mode === id) uses.push(`schedule rule ${i + 1}`); });
    if (d.fallback_mode === id) blocks.push("it is the fallback mode");
    return { uses, blocks };
  }

  // ── Schedule tab ───────────────────────────────────────────────────────
  _scheduleHtml() {
    const rules = this._draft.schedule || [];
    const sets = this._schema.day_sets || [];
    const rows = rules.map((r, i) => {
      const at = parseAt(r.at);
      const byDays = r.day_set == null;
      const days = Array.isArray(r.days) ? r.days : [];
      return `<div class="rule" data-rule="${i}">
        <select data-r="mode" aria-label="Mode">${this._modes().map((m) => `<option value="${esc(m.id)}" ${m.id === r.mode ? "selected" : ""}>${esc(m.name || m.id)}</option>`).join("")}</select>
        <span>at</span>
        <select data-r="atkind" aria-label="Time kind">${[["clock", "a time"], ["sunrise", "sunrise"], ["sunset", "sunset"]].map(([v, l]) => `<option value="${v}" ${v === at.kind ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${at.kind === "clock" ? `<input class="in" type="time" data-r="time" value="${esc(at.time)}" aria-label="Time">`
          : `<select data-r="sign" aria-label="Before or after">${[["-", "−"], ["+", "+"]].map(([v, l]) => `<option value="${v}" ${v === at.sign ? "selected" : ""}>${l}</option>`).join("")}</select>
             <input class="in" type="text" inputmode="numeric" data-r="off" value="${esc(at.off.replace(/^0/, ""))}" size="5" placeholder="0:00" aria-label="Offset, hours:minutes" title="Offset, hours:minutes (1:00 = an hour)"> h`}
        <span>on</span>
        <select data-r="by" aria-label="Days or calendar"><option value="days" ${byDays ? "selected" : ""}>these days</option><option value="set" ${byDays ? "" : "selected"}>a calendar</option></select>
        ${byDays ? `<span class="days">${(this._schema.days || Object.keys(DAY_NAMES)).map((dd) =>
            `<button data-day="${dd}" class="${days.includes(dd) ? "on" : ""}" aria-pressed="${days.includes(dd)}">${DAY_NAMES[dd] || dd}</button>`).join("")}</span>`
          : `<select data-r="set" aria-label="Calendar">${sets.some((s) => s.id === r.day_set) ? "" : `<option value="${esc(r.day_set || "")}" selected>${esc(r.day_set || "choose…")}</option>`}${
              sets.map((s) => `<option value="${esc(s.id)}" ${s.id === r.day_set ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
             <label><input type="checkbox" data-r="eve" ${r.eve ? "checked" : ""}> the evening before</label>
             <label><input type="checkbox" data-r="not" ${r.not ? "checked" : ""}> not on those days</label>`}
        <span class="grow"></span><button class="ic" data-r="rm" title="Remove rule" aria-label="Remove rule">×</button>
        <div class="errs-slot" data-path='${esc(JSON.stringify(["schedule", i]))}'></div></div>`;
    }).join("");
    return `<div class="card"><div class="sec"><h3>Rules</h3>
        <div class="help">Each rule switches the house to a mode at a time on certain days. Sunrise and sunset take an offset (sunset − 1:00 = an hour before). Calendars are SB Scheduler's day sets (Workday, Weekend…); "the evening before" moves a rule to the day before each calendar day.</div>
        ${rows || `<div class="empty">No rules — the mode changes only by hand.</div>`}
        <div class="tools"><button class="b sm" data-act="add-rule">+ Rule</button></div></div></div>
      <div class="card"><div class="sec pv"><h3>The coming week</h3><div class="pvbody"><div class="empty">Working it out…</div></div></div></div>`;
  }

  async _loadPreview() {
    try {
      this._preview = await this._ws({ type: "sb_modes/preview", config: this._draft, days: 7 });
    } catch (e) { this._preview = { error: e?.message || String(e) }; }
    this._renderPreview();
  }

  _renderPreview() {
    const el = this.shadowRoot.querySelector(".pvbody");
    if (!el || !this._preview) return;
    const p = this._preview;
    if (p.error) { el.innerHTML = `<div class="err">${esc(p.error)}</div>`; return; }
    const tz = this._hass.config?.time_zone;
    const fmtDay = (d) => new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric", timeZone: tz }).format(d);
    const fmtTime = (d) => new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: tz }).format(d);
    let day = null;
    const rows = p.changes.map((c) => {
      const d = new Date(c.at), k = fmtDay(d), head = k !== day ? `<div class="day">${esc(k)}</div>` : "";
      day = k;
      return `${head}<div class="ch"><span class="t">${esc(fmtTime(d))}</span><ha-icon icon="${esc(this._modeIcon(c.mode))}"></ha-icon>${esc(this._modeName(c.mode))}</div>`;
    }).join("");
    el.innerHTML = `<div class="help">Now: ${esc(p.now ? this._modeName(p.now) : "no scheduled mode")}</div>${
      p.missing_day_sets?.length ? `<div class="warns">Calendar not found: ${esc(p.missing_day_sets.join(", "))} — rules on it are skipped.</div>` : ""}${
      rows || `<div class="empty">No mode changes in the next 7 days.</div>`}`;
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

  _mountForms() {
    const root = this.shadowRoot;
    const modeOpts = () => this._modes().map((m) => ({ value: m.id, label: m.name || m.id }));
    const z = this._zoneObj();
    const basics = root.querySelector('[data-form="basics"]');
    if (basics && z) {
      basics.appendChild(this._form([
        { name: "name", selector: { text: {} } },
        { name: "areas", selector: { area: { multiple: true } } },
        { name: "lights", selector: { entity: { multiple: true, filter: { domain: ["light", "switch"] } } } },
        { name: "occupancy", selector: { entity: { multiple: true, filter: { domain: "binary_sensor", device_class: ["occupancy", "presence"] } } } },
        { name: "illuminance", selector: { entity: { multiple: true, filter: { domain: "sensor", device_class: "illuminance" } } } },
      ], { name: z.name || "", areas: z.areas || [], lights: z.lights || [], occupancy: z.occupancy || [], illuminance: z.illuminance || [] },
      (v) => this._edit((d) => {
        const x = d.zones.find((q) => q.id === z.id);
        x.name = v.name;
        x.areas = v.areas || [];
        for (const k of ["lights", "occupancy", "illuminance"]) { if (v[k]?.length) x[k] = v[k]; else delete x[k]; }
      }), { name: "Name", areas: "Areas", lights: "Own lights", occupancy: "Own occupancy sensors", illuminance: "Own light sensors" },
      { lights: "Leave empty to use every light in the areas.", occupancy: "Leave empty to use the areas' occupancy sensors.",
        illuminance: "Leave empty to use (the average of) the areas' light sensors." }));
    }
    const fb = root.querySelector('[data-form="fallback"]');
    if (fb) {
      fb.appendChild(this._form([{ name: "fallback_mode", selector: { select: { mode: "dropdown", options: modeOpts() } } }],
        { fallback_mode: this._draft.fallback_mode || "" }, (v) => this._edit((d) => { d.fallback_mode = v.fallback_mode; }), { fallback_mode: "Fallback mode" }));
    }
    root.querySelectorAll('[data-form="mode"]').forEach((slot) => {
      const m = this._modes().find((x) => x.id === slot.dataset.mode);
      slot.appendChild(this._form([{ name: "name", selector: { text: {} } },
        { name: "icon", selector: { icon: { placeholder: DEFAULT_ICONS[m.id] || "mdi:home-clock-outline" } } }],
        { name: m.name || "", icon: m.icon || "" },
        (v) => this._edit((d) => { const x = d.modes.find((q) => q.id === m.id); x.name = v.name; if (v.icon) x.icon = v.icon; else delete x.icon; }),
        { name: "Name", icon: "Icon" }));
    });
    root.querySelectorAll('[data-form="pick"]').forEach((slot) => {
      const owner = JSON.parse(slot.dataset.owner);
      const ch = this._choice(this._draft, owner);
      const opts = [
        ...(owner.kind === "zone" ? [{ value: "", label: owner.mode === ALL ? "Not set — each mode's default" : "Not set — use All modes or the mode's default" }] : []),
        ...Object.entries(this._schema.behaviours).map(([k, b]) => ({ value: k, label: b.label })),
      ];
      const label = owner.kind === "mode" ? "Behaviour" : `Setting for ${this._modeName(owner.mode)}`;
      slot.appendChild(this._form([{ name: "behaviour", selector: { select: { mode: "dropdown", options: opts } } }],
        { behaviour: ch?.behaviour || "" },
        (v) => this._edit((d) => {
          const cur = this._choice(d, owner);
          if (!v.behaviour) { if (owner.kind === "zone") this._setChoice(d, owner, null); }
          else if (cur?.behaviour !== v.behaviour) this._setChoice(d, owner, { behaviour: v.behaviour, params: this._defaults(v.behaviour) });
        }, true), { behaviour: label }));
    });
    root.querySelectorAll('[data-form="params"]').forEach((slot) => {
      const owner = JSON.parse(slot.dataset.owner);
      const ch = this._choice(this._draft, owner);
      const params = this._beh(ch.behaviour).params;
      const schema = [], labels = {}, helps = {};
      for (const [k, s] of Object.entries(params)) {
        if (["targets", "steps"].includes(s.type)) continue;
        labels[k] = s.label || k;
        helps[k] = s.help;
        if (s.type === "number") schema.push({ name: k, required: !!s.required, selector: { number: { min: s.min, max: s.max, mode: "box", unit_of_measurement: s.unit } } });
        else if (s.type === "bool") schema.push({ name: k, selector: { boolean: {} } });
        else if (s.type === "entities") schema.push({ name: k, required: !!s.required, selector: { entity: { multiple: true, filter: { domain: s.domains } } } });
        else if (s.type === "entity") schema.push({ name: k, required: !!s.required, selector: { entity: { filter: { domain: s.domains } } } });
        else if (s.type === "mode") schema.push({ name: k, required: !!s.required, selector: { select: { mode: "dropdown", options: modeOpts() } } });
      }
      const data = {};
      for (const k of Object.keys(labels)) data[k] = (ch.params || {})[k] ?? params[k].default;
      const form = this._form(schema, data, (v) => this._edit((d) => {
        const c = this._choice(d, owner);
        const p = (c.params ||= {});
        for (const k of Object.keys(labels)) { if (v[k] === undefined || v[k] === "" || v[k] === null) delete p[k]; else p[k] = v[k]; }
      }), labels, helps);
      form.dataset.owner = slot.dataset.owner;
      form.classList.add("params");
      slot.appendChild(form);
    });
    root.querySelectorAll("[data-picker]").forEach((slot) => {
      const loc = JSON.parse(slot.dataset.picker);
      const sel = document.createElement("ha-selector");
      sel.hass = this._hass;
      sel.selector = { entity: { filter: { domain: ["light", "switch", "number"] } } };
      sel.label = "Add a light";
      sel.required = false;
      sel.value = "";
      sel.addEventListener("value-changed", (e) => {
        const eid = e.detail.value;
        if (!eid) return;
        this._edit((d) => { this._targetsRef(d, loc)[eid] = eid.startsWith("number.") ? { value: 0 } : { brightness_pct: 100 }; }, true);
      });
      slot.appendChild(sel);
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

  /** Errors and warnings into their slots, and onto the params forms by field. Each message
   *  goes to the deepest slot whose path starts its own; a message about one form field goes on
   *  that field instead (ha-form's own error line). */
  _renderErrors() {
    const root = this.shadowRoot;
    const slots = [...root.querySelectorAll(".errs-slot")].map((el) => ({ el, path: JSON.parse(el.dataset.path), errs: [], warns: [] }));
    const forms = [...root.querySelectorAll("ha-form.params")].map((f) => {
      const owner = JSON.parse(f.dataset.owner);
      const ch = this._choice(this._draft, owner);
      return { f, path: this._ownerPath(owner), params: this._beh(ch?.behaviour).params || {}, out: {} };
    });
    const place = (e, kind) => {
      const p = e.path;
      for (const fm of forms) {
        if (p.length === fm.path.length + 2 && fm.path.every((x, i) => p[i] === x) && p[fm.path.length] === "params") {
          const spec = fm.params[p[fm.path.length + 1]];
          if (spec && !["targets", "steps"].includes(spec.type)) {
            // the field already says where: keep the last part ("5000 is outside 1–2000.")
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
    root.querySelectorAll(".chips [data-zone]").forEach((b) => this._dot(b, this._errorsAt(["zones", this._zoneIndex(b.dataset.zone)]).length));
    root.querySelectorAll(".tabs [data-tab]").forEach((b) => {
      const k = b.dataset.tab;
      this._dot(b, this._errorsAt([k]).length + (k === "modes" ? this._errorsAt(["fallback_mode"]).length : 0));
    });
  }

  _dot(el, on) {
    const dot = el.querySelector(".dot");
    if (on && !dot) el.insertAdjacentHTML("beforeend", `<span class="dot" title="Has errors"></span>`);
    if (!on && dot) dot.remove();
  }

  // ── events ─────────────────────────────────────────────────────────────
  _wire() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { this._tab = b.dataset.tab; this._render(); }));
    root.querySelectorAll("[data-zone]").forEach((b) => b.addEventListener("click", () => { this._zone = b.dataset.zone; this._render(); }));
    root.querySelectorAll("[data-row]").forEach((b) => b.addEventListener("click", () => this._toggle(b.dataset.row)));
    root.querySelectorAll("[data-mode-row]").forEach((b) => b.addEventListener("click", () => this._toggle(`mode|${b.dataset.modeRow}`)));
    root.querySelectorAll("[data-act]").forEach((b) => { if (!b.closest(".bar")) b.addEventListener("click", () => this._act(b.dataset.act)); });
    root.querySelectorAll("[data-move]").forEach((b) => b.addEventListener("click", () => this._edit((d) => {
      const i = Number(b.dataset.move), j = i + Number(b.dataset.dir);
      [d.modes[i], d.modes[j]] = [d.modes[j], d.modes[i]];
    }, true)));
    root.querySelectorAll("[data-del-mode]").forEach((b) => b.addEventListener("click", () => this._deleteMode(b.dataset.delMode)));
    root.querySelectorAll(".tg-wrap").forEach((w) => this._wireTargets(w));
    root.querySelectorAll("[data-step]").forEach((inp) => inp.addEventListener("input", () => this._edit((d) => {
      this._choice(d, JSON.parse(inp.dataset.owner)).params[inp.dataset.key][Number(inp.dataset.step)].after_minutes = Number(inp.value || 0);
    })));
    root.querySelectorAll("[data-step-rm]").forEach((b) => b.addEventListener("click", () => this._edit((d) => {
      this._choice(d, JSON.parse(b.dataset.owner)).params[b.dataset.key].splice(Number(b.dataset.stepRm), 1);
    }, true)));
    root.querySelectorAll("[data-step-add]").forEach((b) => b.addEventListener("click", () => this._edit((d) => {
      const c = this._choice(d, JSON.parse(b.dataset.owner));
      const list = (c.params ||= {})[b.dataset.key] ||= [];
      list.push({ after_minutes: (list.at(-1)?.after_minutes ?? 0) + 10, targets: {} });
    }, true)));
    root.querySelectorAll(".rule").forEach((row) => this._wireRule(row));
  }

  _toggle(k) { if (this._open.has(k)) this._open.delete(k); else this._open.add(k); this._render(); }

  _wireTargets(w) {
    const loc = JSON.parse(w.dataset.loc);
    w.querySelectorAll("tr[data-eid]").forEach((tr) => {
      const eid = tr.dataset.eid;
      tr.querySelector('[data-f="mode"]')?.addEventListener("change", (e) => this._edit((d) => {
        const t = this._targetsRef(d, loc), cur = t[eid] || {}, v = e.target.value;
        const pct = cur.min_brightness_pct ?? cur.brightness_pct;
        t[eid] = v === "off" ? { state: "off" } : v === "min" ? { min_brightness_pct: pct ?? 1 }
          : eid.startsWith("light.") ? { brightness_pct: pct ?? 100, ...(cur.color_temp_kelvin ? { color_temp_kelvin: cur.color_temp_kelvin } : {}) } : { state: "on" };
      }, true));
      tr.querySelector('[data-f="pct"]')?.addEventListener("input", (e) => this._edit((d) => {
        const t = this._targetsRef(d, loc)[eid], v = e.target.value === "" ? null : Number(e.target.value);
        const key = t.min_brightness_pct != null ? "min_brightness_pct" : "brightness_pct";
        if (v == null) { delete t[key]; if (key === "brightness_pct" && !Object.keys(t).length) t.state = "on"; } else { t[key] = v; delete t.state; }
      }));
      tr.querySelector('[data-f="k"]')?.addEventListener("input", (e) => this._edit((d) => {
        const t = this._targetsRef(d, loc)[eid];
        if (e.target.value === "") delete t.color_temp_kelvin; else t.color_temp_kelvin = Number(e.target.value);
      }));
      tr.querySelector('[data-f="rm"]')?.addEventListener("click", () => this._edit((d) => { delete this._targetsRef(d, loc)[eid]; }, true));
    });
    w.querySelector('[data-tg="capture"]')?.addEventListener("click", () => this._capture(loc));
    w.querySelector('[data-tg="try"]')?.addEventListener("click", () => this._try(loc));
  }

  _wireRule(row) {
    const i = Number(row.dataset.rule);
    const rule = (d) => d.schedule[i];
    const on = (sel, ev, fn, redraw = false) => row.querySelector(sel)?.addEventListener(ev, (e) => this._edit((d) => fn(rule(d), e.target), redraw));
    on('[data-r="mode"]', "change", (r, t) => { r.mode = t.value; }, true);
    on('[data-r="atkind"]', "change", (r, t) => {
      const p = parseAt(r.at);
      r.at = t.value === "clock" ? (p.kind === "clock" && p.time ? p.time : "07:00") : joinAt({ kind: t.value, sign: p.sign || "-", off: p.off || "00:00" });
    }, true);
    on('[data-r="time"]', "change", (r, t) => { if (t.value) r.at = t.value.slice(0, 5); });
    on('[data-r="sign"]', "change", (r, t) => { const p = parseAt(r.at); p.sign = t.value; r.at = joinAt(p); });
    on('[data-r="off"]', "change", (r, t) => {
      const m = /^(\d{1,2})(?::(\d{1,2}))?$/.exec(t.value.trim() || "0");
      const p = parseAt(r.at);
      // "1" → 1:00, "1:5" → 1:05; anything else is passed through for the check to flag
      p.off = m ? `${m[1].padStart(2, "0")}:${(m[2] || "0").padStart(2, "0")}` : t.value.trim();
      r.at = joinAt(p);
    });
    on('[data-r="by"]', "change", (r, t) => {
      if (t.value === "set") { delete r.days; r.day_set = (this._schema.day_sets || [])[0]?.id || ""; }
      else { delete r.day_set; delete r.eve; delete r.not; r.days = ["mon", "tue", "wed", "thu", "fri"]; }
    }, true);
    on('[data-r="set"]', "change", (r, t) => { r.day_set = t.value; });
    on('[data-r="eve"]', "change", (r, t) => { if (t.checked) r.eve = true; else delete r.eve; });
    on('[data-r="not"]', "change", (r, t) => { if (t.checked) r.not = true; else delete r.not; });
    row.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => this._edit((d) => {
      const r = rule(d), dd = b.dataset.day, set = new Set(r.days || []);
      if (set.has(dd)) set.delete(dd); else set.add(dd);
      r.days = (this._schema.days || Object.keys(DAY_NAMES)).filter((x) => set.has(x));
    }, true)));
    row.querySelector('[data-r="rm"]')?.addEventListener("click", () => this._edit((d) => {
      d.schedule.splice(i, 1);
      if (!d.schedule.length) delete d.schedule;
    }, true));
  }

  /** "Use the lights as they are now": the listed lights (or the zone's own) as they stand. */
  _capture(loc) {
    const zoneId = loc.owner.kind === "zone" ? loc.owner.zone : null;
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
    try {
      await this._ws({ type: "sb_modes/try", zone: loc.owner.zone, targets: this._lookTargets(loc) });
      this._msg = "";
      this._toast = `${this._zoneObj(loc.owner.zone)?.name || loc.owner.zone} set and held 15 min — release it on the Status page to go back`;
    } catch (e) { this._msg = `Try it failed: ${e?.message || e}`; }
    this._renderBar();
  }

  _deleteMode(id) {
    const { uses, blocks } = this._modeUses(id);
    if (blocks.length) { alert(`“${this._modeName(id)}” can't be deleted yet:\n• ${blocks.join("\n• ")}\nChange those first.`); return; }
    if ((this._modes().length) <= 1) { alert("The table needs at least one mode."); return; }
    if (!confirm(`Delete the mode “${this._modeName(id)}” from the draft?${uses.length ? `\nThese go with it:\n• ${uses.join("\n• ")}` : ""}\nNothing changes until you save.`)) return;
    this._edit((d) => {
      d.modes = d.modes.filter((m) => m.id !== id);
      for (const z of d.zones || []) {
        if (z.overrides) { delete z.overrides[id]; if (!Object.keys(z.overrides).length) delete z.overrides; }
        if (z.on_enter) { delete z.on_enter[id]; if (!Object.keys(z.on_enter).length) delete z.on_enter; }
      }
      if (d.schedule) { d.schedule = d.schedule.filter((r) => r.mode !== id); if (!d.schedule.length) delete d.schedule; }
    }, true);
  }

  async _act(act) {
    if (act === "review") { this._review = true; this._render(); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    if (act === "review-close") { this._review = false; this._render(); return; }
    if (act === "discard") {
      if (!confirm("Discard the draft? The table stays as saved.")) return;
      clearTimeout(this._timer);
      await this._ws({ type: "sb_modes/draft/discard" });
      this._review = false; this._dirty = false; this._toast = "";
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
      const name = prompt("Name of the new zone:");
      if (!name) return;
      const ids = new Set((this._draft.zones || []).map((z) => z.id));
      let id = slug(name);
      for (let n = 2; ids.has(id); n++) id = `${slug(name)}_${n}`;
      this._zone = id;
      this._edit((d) => { (d.zones ||= []).push({ id, name, areas: [] }); }, true);
      return;
    }
    if (act === "del-zone") {
      const z = this._zoneObj();
      if (!z || !confirm(`Delete the zone “${z.name || z.id}” from the draft? Nothing changes until you save.`)) return;
      this._edit((d) => { d.zones = d.zones.filter((x) => x.id !== z.id); }, false);
      this._zone = this._draft.zones[0]?.id;
      this._render();
      return;
    }
    if (act === "add-mode") {
      const name = prompt("Name of the new mode:");
      if (!name) return;
      const ids = new Set(this._modes().map((m) => m.id));
      let id = slug(name);
      for (let n = 2; ids.has(id); n++) id = `${slug(name)}_${n}`;
      this._open.add(`mode|${id}`);
      this._edit((d) => { (d.modes ||= []).push({ id, name, default: { behaviour: "hands_off", params: {} } }); }, true);
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

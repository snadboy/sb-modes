/* SB Modes — the editor (sidebar page /sb-modes/edit). Loaded by the page only when opened.
 *
 * Draft → check → review → save, against the integration's websocket commands (editor.py):
 * every edit changes a working copy of the table and, after a short pause, is stored as the
 * shared draft (sb_modes/draft/set), which answers with its errors (with field paths) and the
 * changes in words. Save turns the draft into the table (a new version); Discard drops it.
 *
 * Step 2 of the build: the Zones tab — basics, each mode's behaviour with forms built from the
 * behaviour schema, a light-settings table with "use the lights as they are now" and Try it.
 * Modes, Schedule and the zone extras (follow, overlay, buttons, when a mode begins) follow.
 *
 * Typing never redraws the form (it would lose focus): field edits change the model and the
 * draft bar; only structural changes (another zone, a row opened, a behaviour picked, a light
 * added or removed) redraw.
 */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));
const ALL = "*";

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

const STYLE = `
:host { display: block; }
.bar { position: sticky; top: var(--header-height, 56px); z-index: 1; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
       padding: 10px 14px; margin-bottom: 12px; border-radius: 12px; background: var(--card-background-color); border: 1px solid var(--divider-color); }
.bar .st { flex: 1; font-size: 14px; min-width: 160px; }
.bar .st b { font-weight: 500; }
.bar .err { color: var(--error-color); }
button.b { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 18px; padding: 6px 14px;
           font: inherit; font-size: 14px; cursor: pointer; }
button.b.pri { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
button.b:disabled { opacity: .45; cursor: default; }
button.b.sm { padding: 3px 10px; font-size: 13px; }
button.b.danger { color: var(--error-color); border-color: var(--error-color); }
.card { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 12px; margin-bottom: 12px; }
.sec { padding: 12px 16px; }
.sec + .sec { border-top: 1px solid var(--divider-color); }
h3 { margin: 0 0 8px; font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); }
.zones { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 16px; }
.zones button { border: 1px solid var(--divider-color); background: none; color: inherit; font: inherit; font-size: 14px; border-radius: 16px; padding: 5px 12px; cursor: pointer; }
.zones button.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.zones button .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--error-color); margin-left: 6px; vertical-align: middle; }
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
table.tg select, table.tg input { font: inherit; font-size: 14px; color: var(--primary-text-color); background: var(--secondary-background-color, transparent);
       border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 6px; }
table.tg input[type=number] { width: 64px; }
table.tg .x { border: none; background: none; color: var(--secondary-text-color); cursor: pointer; font-size: 18px; padding: 0 4px; }
.tools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
.tools .add { min-width: 220px; flex: 1; }
.step { border-left: 3px solid var(--divider-color); padding-left: 10px; margin: 10px 0; }
.step .sh { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.step input { width: 64px; font: inherit; }
.note { font-size: 13px; padding: 8px 12px; border-radius: 8px; background: var(--secondary-background-color, rgba(127,127,127,.1)); margin: 0 0 12px; }
.review ul { margin: 6px 0; padding-left: 20px; font-size: 14px; line-height: 1.6; }
.extra { font-size: 14px; color: var(--secondary-text-color); line-height: 1.6; }
.empty { color: var(--secondary-text-color); font-size: 14px; padding: 4px 0; }
.load { padding: 24px; color: var(--secondary-text-color); }
`;

export class SbModesEditor extends HTMLElement {
  constructor() {
    super();
    this._open = new Set();
    this._timer = null;
    this._check = { errors: [], warnings: [] };
    this._changes = [];
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
    this._render();
  }

  // ── model ──────────────────────────────────────────────────────────────
  _modes() { return this._draft.modes || []; }
  _modeName(id) { return id === ALL ? "All modes" : (this._modes().find((m) => m.id === id)?.name || id); }
  _zoneObj() { return (this._draft.zones || []).find((z) => z.id === this._zone); }
  _zoneIndex() { return (this._draft.zones || []).findIndex((z) => z.id === this._zone); }
  _beh(name) { return this._schema.behaviours[name] || { label: name, params: {} }; }
  _name(eid) { return this._hass.states[eid]?.attributes.friendly_name || eid; }
  /** A light's name without its zone's name in front ("Kitchen Aux Sink" in Kitchen → "Aux Sink"). */
  _short(eid) {
    const n = this._name(eid), z = this._zoneObj()?.name;
    return z && n.toLowerCase().startsWith(`${z.toLowerCase()} `) && n.length > z.length + 1 ? n.slice(z.length + 1) : n;
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
  }

  _errorsAt(prefix, list = this._check.errors) {
    return list.filter((e) => prefix.every((p, i) => e.path[i] === p));
  }

  // ── rendering ──────────────────────────────────────────────────────────
  _render() {
    const root = this.shadowRoot;
    const z = this._zoneObj();
    root.innerHTML = `<style>${STYLE}</style>
      <div class="bar"></div>
      ${this._review ? this._reviewHtml() : ""}
      <div class="card"><div class="zones" role="tablist" aria-label="Zones">${(this._draft.zones || []).map((x) =>
        `<button role="tab" data-zone="${esc(x.id)}" class="${x.id === this._zone ? "on" : ""}" aria-selected="${x.id === this._zone}">${esc(x.name || x.id)}${
          this._errorsAt(["zones", (this._draft.zones || []).indexOf(x)]).length ? `<span class="dot" title="Has errors"></span>` : ""}</button>`).join("")}
        <button data-act="add-zone" title="Add a zone">+ Zone</button></div></div>
      ${z ? this._zoneHtml(z) : `<div class="card"><div class="sec empty">No zones yet — add one.</div></div>`}`;
    this._renderBar();
    this._mountForms();
    this._wire();
    this._renderErrors();
  }

  _renderBar() {
    const bar = this.shadowRoot.querySelector(".bar");
    if (!bar) return;
    const n = this._changes.length, ne = this._check.errors.length;
    const pending = this._dirty ? " · saving the draft…" : "";
    bar.innerHTML = `<span class="st">${this._hasDraft || this._dirty
        ? `<b>Draft</b> · ${n} change${n === 1 ? "" : "s"}${ne ? ` · <span class="err">${ne} error${ne === 1 ? "" : "s"}</span>` : ""}${pending}${
          this._stale ? ` · <span class="err">the table was saved elsewhere since</span>` : ""}`
        : `No changes${this._version ? ` · version ${esc(this._version)}` : " to the saved table"}`}${this._msg ? ` · <span class="err">${esc(this._msg)}</span>` : ""}</span>
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

  _zoneHtml(z) {
    const zi = this._zoneIndex();
    const rows = [ALL, ...this._modes().map((m) => m.id)].map((mid) => this._modeRowHtml(z, zi, mid)).join("");
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
  _modeRowHtml(z, zi, mid) {
    const ov = (z.overrides || {})[mid];
    const key = `${z.id}|${mid}`;
    const open = this._open.has(key);
    let what, own = !!ov;
    if (ov) what = this._beh(ov.behaviour).label;
    else if (mid === ALL) what = "Not set — each mode's default";
    else if ((z.overrides || {})[ALL]) what = `From All modes: ${this._beh(z.overrides[ALL].behaviour).label}`;
    else what = `Mode default: ${this._beh(this._modes().find((m) => m.id === mid)?.default?.behaviour).label}`;
    const nerr = this._errorsAt(["zones", zi, "overrides", mid]).length;
    return `<div class="mrow"><button class="mhead" data-row="${esc(key)}" aria-expanded="${open}">
        <span class="mn">${esc(this._modeName(mid))}</span><span class="what ${own ? "own" : ""}">${esc(what)}${nerr ? ` <span class="err" style="color:var(--error-color)">· ${nerr} error${nerr === 1 ? "" : "s"}</span>` : ""}</span>
        <ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button>
      ${open ? `<div class="mbody">${this._behaviourEditorHtml(z, zi, mid, ov)}</div>` : ""}</div>`;
  }

  _behaviourEditorHtml(z, zi, mid, ov) {
    const base = ["zones", zi, "overrides", mid];
    const pick = `<div class="form" data-form="pick" data-mode="${esc(mid)}"></div>`;
    if (!ov) return `${pick}<div class="help">${mid === ALL ? "Pick a behaviour to use in every mode without its own setting." : "Pick a behaviour to give this mode its own setting."}</div>`;
    const b = this._beh(ov.behaviour);
    const params = b.params || {};
    const scalar = Object.keys(params).some((k) => !["targets", "steps"].includes(params[k].type));
    const big = Object.entries(params).filter(([, s]) => ["targets", "steps"].includes(s.type));
    return `${pick}<div class="help">${esc(b.help || "")}</div>
      <div class="errs-slot" data-path='${esc(JSON.stringify(base))}'></div>
      ${scalar ? `<div class="form" data-form="params" data-mode="${esc(mid)}"></div>` : ""}
      ${big.map(([k, s]) => s.type === "targets"
        ? this._targetsHtml({ zone: z.id, mode: mid, key: k }, (ov.params || {})[k] || {}, s)
        : this._stepsHtml(z, mid, k, (ov.params || {})[k] || [], s)).join("")}`;
  }

  /** The light-settings table for one targets parameter (loc says where it lives). */
  _targetsHtml(loc, targets, spec, title = null) {
    const id = esc(JSON.stringify(loc));
    const rows = Object.entries(targets).map(([eid, t]) => {
      const mode = t.state === "off" ? "off" : t.min_brightness_pct != null ? "min" : "on";
      const pct = mode === "min" ? t.min_brightness_pct : t.brightness_pct;
      const isLight = eid.startsWith("light.");
      return `<tr data-eid="${esc(eid)}"><td class="n" title="${esc(this._name(eid))} (${esc(eid)})">${esc(this._short(eid))}</td>
        <td><select data-f="mode" aria-label="Setting">${[["on", "On"], ["off", "Off"], ...(isLight ? [["min", "At least"]] : [])]
          .map(([v, l]) => `<option value="${v}" ${v === mode ? "selected" : ""}>${l}</option>`).join("")}</select></td>
        <td>${isLight && mode !== "off" ? `<input type="number" min="0" max="100" data-f="pct" value="${esc(pct ?? "")}" placeholder="—" aria-label="Brightness %"> %` : ""}</td>
        <td>${isLight && mode === "on" ? `<input type="number" min="1500" max="9000" step="100" data-f="k" value="${esc(t.color_temp_kelvin ?? "")}" placeholder="—" aria-label="Colour temperature K"> K` : ""}</td>
        <td><button class="x" data-f="rm" title="Remove" aria-label="Remove ${esc(this._short(eid))}">×</button></td></tr>`;
    }).join("");
    return `<div class="tg-wrap" data-loc='${id}'>
      <div class="tlabel">${esc(title || spec.label || loc.key)}</div>${spec.help ? `<div class="help">${esc(spec.help)}</div>` : ""}
      ${rows ? `<table class="tg"><tbody>${rows}</tbody></table>` : `<div class="empty">No lights set.</div>`}
      <div class="tools"><div class="add" data-picker='${id}'></div>
        <button class="b sm" data-tg="capture" title="Fill from how the lights are right now">Use the lights as they are now</button>
        <button class="b sm" data-tg="try" ${rows ? "" : "disabled"} title="Set these now and hold the zone 15 min">Try it</button></div>
      <div class="errs-slot" data-path='${esc(JSON.stringify(this._locPath(loc)))}'></div></div>`;
  }

  _stepsHtml(z, mid, key, steps, spec) {
    return `<div class="tlabel">${esc(spec.label || key)}</div>
      ${steps.map((st, i) => `<div class="step"><div class="sh">+ <input type="number" min="0" max="720" data-step="${i}" data-mode="${esc(mid)}" data-key="${esc(key)}" value="${esc(st.after_minutes ?? 0)}" aria-label="Minutes after"> min
          <button class="x" data-step-rm="${i}" data-mode="${esc(mid)}" data-key="${esc(key)}" title="Remove step" style="border:none;background:none;color:var(--secondary-text-color);cursor:pointer;font-size:18px">×</button></div>
        ${this._targetsHtml({ zone: z.id, mode: mid, key, step: i }, st.targets || {}, { label: `Step ${i + 1}` })}</div>`).join("")}
      <div class="tools"><button class="b sm" data-step-add="1" data-mode="${esc(mid)}" data-key="${esc(key)}">+ Step</button></div>`;
  }

  _extrasHtml(z) {
    const bits = [];
    if (z.on_enter) bits.push(`When a mode begins: ${Object.keys(z.on_enter).map((m) => esc(this._modeName(m))).join(", ")}`);
    if (z.follow) bits.push(`Follows ${esc(this._name(z.follow.leader))}`);
    if (z.overlay) bits.push(`TV overlay: ${esc(this._name(z.overlay.entity))}`);
    if (z.buttons) bits.push(`Buttons: ${Object.keys(z.buttons).map((e) => esc(this._name(e))).join(", ")}`);
    return (bits.length ? bits.join("<br>") : "None.") + `<br><span class="help">These are edited in a later step; they are kept as they are.</span>`;
  }

  // ── where things live in the table ─────────────────────────────────────
  _locPath(loc) {
    const zi = (this._draft.zones || []).findIndex((x) => x.id === loc.zone);
    const p = ["zones", zi, "overrides", loc.mode, "params", loc.key];
    return loc.step != null ? [...p, loc.step, "targets"] : p;
  }

  _targetsRef(d, loc) {
    const z = d.zones.find((x) => x.id === loc.zone);
    const ov = z.overrides[loc.mode];
    ov.params = ov.params || {};
    if (loc.step != null) {
      const st = ov.params[loc.key][loc.step];
      st.targets = st.targets || {};
      return st.targets;
    }
    ov.params[loc.key] = ov.params[loc.key] || {};
    return ov.params[loc.key];
  }

  _lookTargets(loc) {
    try { return this._targetsRef(clone(this._draft), loc); } catch (e) { return {}; }
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
    const z = this._zoneObj();
    if (!z) return;
    const basics = root.querySelector('[data-form="basics"]');
    if (basics) {
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
    const behOptions = (mid) => [
      { value: "", label: mid === ALL ? "Not set — each mode's default" : "Not set — use All modes or the mode's default" },
      ...Object.entries(this._schema.behaviours).map(([k, b]) => ({ value: k, label: b.label })),
    ];
    root.querySelectorAll('[data-form="pick"]').forEach((slot) => {
      const mid = slot.dataset.mode;
      const ov = (z.overrides || {})[mid];
      slot.appendChild(this._form([{ name: "behaviour", selector: { select: { mode: "dropdown", options: behOptions(mid) } } }],
        { behaviour: ov?.behaviour || "" },
        (v) => this._edit((d) => {
          const x = d.zones.find((q) => q.id === z.id);
          x.overrides = x.overrides || {};
          if (!v.behaviour) delete x.overrides[mid];
          else if (x.overrides[mid]?.behaviour !== v.behaviour) x.overrides[mid] = { behaviour: v.behaviour, params: this._defaults(v.behaviour) };
          if (!Object.keys(x.overrides).length) delete x.overrides;
        }, true), { behaviour: `Setting for ${this._modeName(mid)}` }));
    });
    root.querySelectorAll('[data-form="params"]').forEach((slot) => {
      const mid = slot.dataset.mode;
      const ov = z.overrides[mid];
      const params = this._beh(ov.behaviour).params;
      const schema = [], labels = {}, helps = {};
      for (const [k, s] of Object.entries(params)) {
        if (["targets", "steps"].includes(s.type)) continue;
        labels[k] = s.label || k;
        helps[k] = s.help;
        if (s.type === "number") schema.push({ name: k, required: !!s.required, selector: { number: { min: s.min, max: s.max, mode: "box", unit_of_measurement: s.unit } } });
        else if (s.type === "bool") schema.push({ name: k, selector: { boolean: {} } });
        else if (s.type === "entities") schema.push({ name: k, required: !!s.required, selector: { entity: { multiple: true, filter: { domain: s.domains } } } });
        else if (s.type === "entity") schema.push({ name: k, required: !!s.required, selector: { entity: { filter: { domain: s.domains } } } });
        else if (s.type === "mode") schema.push({ name: k, required: !!s.required, selector: { select: { mode: "dropdown", options: this._modes().map((m) => ({ value: m.id, label: m.name || m.id })) } } });
      }
      const data = {};
      for (const k of Object.keys(labels)) data[k] = (ov.params || {})[k] ?? params[k].default;
      const form = this._form(schema, data, (v) => this._edit((d) => {
        const p = (d.zones.find((q) => q.id === z.id).overrides[mid].params ||= {});
        for (const k of Object.keys(labels)) { if (v[k] === undefined || v[k] === "" || v[k] === null) delete p[k]; else p[k] = v[k]; }
      }), labels, helps);
      form.dataset.paramsFor = mid;
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
    const onForm = {};                                   // mode → {param: message}
    const zi = this._zoneIndex();
    const formModes = new Set([...root.querySelectorAll("ha-form[data-params-for]")].map((f) => f.dataset.paramsFor));
    const place = (e, kind) => {
      const p = e.path;
      if (p[0] === "zones" && p[1] === zi && p[2] === "overrides" && p[4] === "params" && p.length === 6 && formModes.has(p[3])) {
        const spec = this._beh(this._zoneObj()?.overrides?.[p[3]]?.behaviour).params?.[p[5]];
        if (spec && !["targets", "steps"].includes(spec.type)) {
          // the field already says where: keep the last part ("5000 is outside 1–2000.")
          if (kind === "errs") (onForm[p[3]] ||= {})[p[5]] = e.message.replace(/^.*:\s+(?=[^:]+$)/, "");
          return;
        }
      }
      let best = null;
      for (const s of slots) {
        if (s.path.every((x, i) => p[i] === x) && (!best || s.path.length > best.path.length)) best = s;
      }
      best?.[kind].push(e.message);
    };
    this._check.errors.forEach((e) => place(e, "errs"));
    this._check.warnings.forEach((e) => place(e, "warns"));
    for (const s of slots) {
      s.el.innerHTML = `${s.errs.length ? `<ul class="errs">${s.errs.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}${
        s.warns.length ? `<ul class="warns">${s.warns.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}`;
    }
    root.querySelectorAll("ha-form[data-params-for]").forEach((form) => { form.error = onForm[form.dataset.paramsFor]; });
    root.querySelectorAll(".zones [data-zone]").forEach((b) => {
      const i = (this._draft.zones || []).findIndex((x) => x.id === b.dataset.zone);
      const has = this._errorsAt(["zones", i]).length > 0;
      const dot = b.querySelector(".dot");
      if (has && !dot) b.insertAdjacentHTML("beforeend", `<span class="dot" title="Has errors"></span>`);
      if (!has && dot) dot.remove();
    });
  }

  // ── events ─────────────────────────────────────────────────────────────
  _wire() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-zone]").forEach((b) => b.addEventListener("click", () => { this._zone = b.dataset.zone; this._render(); }));
    root.querySelectorAll("[data-row]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.row; if (this._open.has(k)) this._open.delete(k); else this._open.add(k); this._render();
    }));
    root.querySelectorAll("[data-act]").forEach((b) => { if (!b.closest(".bar")) b.addEventListener("click", () => this._act(b.dataset.act)); });
    root.querySelectorAll(".tg-wrap").forEach((w) => {
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
    });
    root.querySelectorAll("[data-step]").forEach((inp) => inp.addEventListener("input", () => this._edit((d) => {
      const ov = d.zones.find((q) => q.id === this._zone).overrides[inp.dataset.mode];
      ov.params[inp.dataset.key][Number(inp.dataset.step)].after_minutes = Number(inp.value || 0);
    })));
    root.querySelectorAll("[data-step-rm]").forEach((b) => b.addEventListener("click", () => this._edit((d) => {
      d.zones.find((q) => q.id === this._zone).overrides[b.dataset.mode].params[b.dataset.key].splice(Number(b.dataset.stepRm), 1);
    }, true)));
    root.querySelectorAll("[data-step-add]").forEach((b) => b.addEventListener("click", () => this._edit((d) => {
      const ov = d.zones.find((q) => q.id === this._zone).overrides[b.dataset.mode];
      const list = (ov.params ||= {})[b.dataset.key] ||= [];
      list.push({ after_minutes: (list.at(-1)?.after_minutes ?? 0) + 10, targets: {} });
    }, true)));
  }

  /** "Use the lights as they are now": the listed lights (or the zone's own) as they stand. */
  _capture(loc) {
    const zs = Object.values(this._hass.states).find((s) => s.attributes.sb_modes_zone === loc.zone);
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
    const targets = this._lookTargets(loc);
    try {
      await this._ws({ type: "sb_modes/try", zone: loc.zone, targets });
      this._msg = "";
      this._note(`${this._zoneObj()?.name || loc.zone}: set and held for 15 minutes. Release it from the status page (zone ⋮ → Release) to go back.`);
    } catch (e) { this._msg = `Try it failed: ${e?.message || e}`; this._renderBar(); }
  }

  _note(text) {
    this._msg = "";
    this._renderBar();
    const bar = this.shadowRoot.querySelector(".bar .st");
    if (bar) bar.insertAdjacentHTML("beforeend", ` · <span>${esc(text)}</span>`);
  }

  async _act(act) {
    if (act === "review") { this._review = true; this._render(); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    if (act === "review-close") { this._review = false; this._render(); return; }
    if (act === "discard") {
      if (!confirm("Discard the draft? The table stays as saved.")) return;
      clearTimeout(this._timer);
      await this._ws({ type: "sb_modes/draft/discard" });
      this._review = false; this._dirty = false;
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
        this._note(`Saved as version ${r.version}.`);
      } catch (e) { this._msg = `Not saved: ${e?.message || e}`; this._renderBar(); }
      return;
    }
    if (act === "add-zone") {
      const name = prompt("Name of the new zone:");
      if (!name) return;
      let id = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "z_$1").slice(0, 32) || "zone";
      const ids = new Set((this._draft.zones || []).map((z) => z.id));
      for (let n = 2; ids.has(id); n++) id = `${id.replace(/_\d+$/, "")}_${n}`;
      this._edit((d) => { (d.zones ||= []).push({ id, name, areas: [] }); }, false);
      this._zone = id;
      this._render();
      return;
    }
    if (act === "del-zone") {
      const z = this._zoneObj();
      if (!z || !confirm(`Delete the zone “${z.name || z.id}” from the draft? Nothing changes until you save.`)) return;
      this._edit((d) => { d.zones = d.zones.filter((x) => x.id !== z.id); }, false);
      this._zone = this._draft.zones[0]?.id;
      this._render();
    }
  }
}

export const defineEditor = () => {
  if (!customElements.get("sb-modes-editor")) {
    try { customElements.define("sb-modes-editor", class extends SbModesEditor {}); } catch (e) { /* defined meanwhile */ }
  }
};

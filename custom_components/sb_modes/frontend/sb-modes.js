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
const VERSION = "0.45.1";
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
const dayKey = (d) => { try { return new Intl.DateTimeFormat("en-CA", { timeZone: TIME.tz }).format(d); } catch (e) { return d.toDateString(); } };
/** A Logbook time (epoch seconds) as the user's clock time; the day goes in a heading (actDay). */
const actTime = (when) => hhmm(new Date(when * 1000).toISOString());
const actDay = (when) => {
  const d = new Date(when * 1000), k = dayKey(d);
  if (k === dayKey(new Date())) return "Today";
  if (k === dayKey(new Date(Date.now() - 864e5))) return "Yesterday";
  try { return new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric", timeZone: TIME.tz }).format(d); }
  catch (e) { return d.toDateString(); }
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
const HOLD_MINUTES = [15, 30, 45, 60, 90, 120];
const foldKey = (card, title) => `sb-card-collapsed:${card}:${title || ""}`;
const foldGet = (k) => { try { return localStorage.getItem(k) === "1"; } catch (e) { return false; } };
const foldSet = (k, v) => { try { if (v) localStorage.setItem(k, "1"); else localStorage.removeItem(k); } catch (e) { /* not remembered */ } };
const foldBtn = (folded) => `<button class="sbk-kb fold" title="${folded ? "Show the entries" : "Hide the entries"}" aria-label="${folded ? "Show the entries" : "Hide the entries"}" aria-expanded="${folded ? "false" : "true"}"><ha-icon icon="${folded ? "mdi:chevron-right" : "mdi:chevron-down"}"></ha-icon></button>`;
/** In-app navigation, as HA's own links do. */
const navigate = (path) => { history.pushState(null, "", path); window.dispatchEvent(new CustomEvent("location-changed")); };
/** Which sections are open on the SB Modes page: every section starts closed, and stays as the
 *  user leaves it for the rest of the browser session (sessionStorage), as agreed 2026-11-01. */
const SESS = "sb-modes-open";
const sessGet = (k) => { try { return (JSON.parse(sessionStorage.getItem(SESS) || "[]")).includes(k); } catch (e) { return false; } };
const sessSet = (k, on) => {
  try {
    const all = new Set(JSON.parse(sessionStorage.getItem(SESS) || "[]"));
    if (on) all.add(k); else all.delete(k);
    sessionStorage.setItem(SESS, JSON.stringify([...all]));
  } catch (e) { /* not remembered */ }
};
/** A to Z, ignoring case and accents. */
const byName = (a, b) => String(a).localeCompare(String(b), undefined, { sensitivity: "base", numeric: true });
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
let ICON_OVERRIDES = {};              // the table's own mode icons (select attribute mode_icons)
const modeIcon = (id) => ICON_OVERRIDES[id] || MODE_ICONS[id] || "mdi:home-clock-outline";

const BEHAVIOUR_NAMES = {
  hands_off: "Hands off",
  static: "Static",
  nightlight_then_dark: "Nightlight, then dark",
  vacancy_off: "Off when empty",
  timed_off: "Off after a while",
  occupied_vacant: "Occupied / empty",
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
  if (s.startsWith("On until")) return { icon: "mdi:timer-sand", tint: "armed" };
  if (s === "Lights off" || s.startsWith("off:")) return { icon: "mdi:lightbulb-off-outline", tint: "idle" };
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
button.zt.zones { border-top: none; }
button.zt ha-icon { margin-left: auto; --mdc-icon-size: 20px; }
.rules { padding: 0 16px 10px; }
.dtype + .dtype { margin-top: 8px; }
.dtn { font-size: 13px; font-weight: 500; padding: 4px 0 2px; display: flex; gap: 8px; align-items: baseline; }
.dtn .d { color: var(--secondary-text-color); font-weight: 400; font-size: 12px; }
.actf { display: flex; flex-wrap: wrap; gap: 6px; padding: 2px 0 8px; }
.actf .sbk-chip { cursor: pointer; border: 1px solid var(--divider-color); background: none; color: inherit; font: inherit; font-size: 12px; }
.actf .sbk-chip.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
.act { display: grid; grid-template-columns: 74px 20px 1fr; align-items: start; gap: 6px; font-size: 13px; padding: 3px 0; --mdc-icon-size: 16px; line-height: 1.4; }
.act .t { font-variant-numeric: tabular-nums; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.actday { font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); padding: 8px 0 2px; }
.actday:first-child { padding-top: 0; }
.act ha-icon { color: var(--secondary-text-color); margin-top: 1px; }
.act b { font-weight: 500; }
.detail .act { grid-template-columns: 74px 20px 1fr; }
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
.ent { display: inline-flex; align-items: center; gap: 4px; margin-right: 10px; white-space: nowrap;
       font: inherit; color: inherit; background: none; border: 0; padding: 2px 0; cursor: pointer; }
.ent:hover { color: var(--primary-text-color); text-decoration: underline; }
.lvl { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; margin: 4px 0; }
.lvl input { flex: 1 1 160px; max-width: 320px; accent-color: var(--primary-color); }
.lvl .lv { min-width: 3.2em; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
.lvl .lh { flex-basis: 100%; font-size: 12px; }
.lvl b { min-width: 5.5em; }
.lvl .sw { font: inherit; padding: 2px 12px; border: 1px solid var(--divider-color); border-radius: 12px; background: none; color: var(--primary-text-color); cursor: pointer; }
.lvl .sw:hover, .dlg .sw:hover { border-color: var(--primary-color); }
dialog.lights { padding: 0; border: 0; border-radius: var(--ha-card-border-radius, 12px); width: min(420px, calc(100vw - 32px));
                background: var(--card-background-color, #fff); color: var(--primary-text-color); box-shadow: 0 8px 32px rgba(0,0,0,.35); }
dialog.adet { width: min(640px, calc(100vw - 32px)); }
.adw { overflow: auto; max-height: calc(100vh - 200px); }
.adw:focus, .adw:focus-visible { outline: none; }
.adw .mt th:first-child, .adw .mt td:first-child { padding-left: 8px; }
.adw th { position: sticky; top: 0; background: var(--card-background-color, #1c1c1c); }
.adk { all: unset; cursor: pointer; color: var(--primary-color); font-size: 13px; font-weight: 500; white-space: nowrap; }
.adk:hover { text-decoration: underline; }
dialog.lights::backdrop { background: rgba(0,0,0,.45); }
.dlg { padding: 16px 20px; font-size: 14px; }
.dlg h3 { margin: 0 0 12px; font-size: 18px; font-weight: 500; }
.dlg .lvl { margin: 10px 0; }
.dlg .lvl input { max-width: none; }
.dlg .lh { color: var(--secondary-text-color); font-size: 12px; }
.dlg .err { color: var(--error-color, #db4437); font-size: 13px; }
.dlg .sw { font: inherit; padding: 6px 16px; border: 1px solid var(--divider-color); border-radius: 16px; background: none; color: var(--primary-text-color); cursor: pointer; }
.sbk-head .sbk-count { min-width: 0; white-space: normal; }
.sbk-head > .sbk-kb { flex: none; }
.dlg .sect { margin: 14px 0 6px; }
.dlg .opts { display: flex; flex-direction: column; gap: 8px; margin: 4px 0 8px; }
.dlg .opt { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.dlg .opt input[type=radio] { accent-color: var(--primary-color); width: 18px; height: 18px; margin: 0; }
.dlg select { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, #fff); border: 1px solid var(--divider-color); border-radius: 6px; padding: 2px 4px; }
.dlg .lvl.off { opacity: .45; }
.dlg-foot { gap: 8px; }
.dlg .sect:first-of-type { margin-top: 0; }
.dlg .chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0; }
.dlg .sw.sel { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.dlg .sw:disabled { opacity: .45; cursor: default; }
.dlg-foot { display: flex; justify-content: flex-end; margin-top: 8px; }
/* the SB Modes page (panel: true): sections as cards, rooms as a table */
:host([panel]) ha-card { background: none; border: 0; box-shadow: none; }
.pc { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 12px; margin-bottom: 12px; overflow: hidden; }
.pc .now { border-top: 0; padding-top: 16px; }
.pc > .zh.zt { border-top: 0; padding: 14px 16px; min-height: 48px; box-sizing: border-box; }
.pc .rules { padding: 0 16px 14px; }
.zh .lead { margin-left: 0 !important; --mdc-icon-size: 18px; color: var(--warning-color, #ffa600); }
.att-h.bad .lead, .sub.err { color: var(--error-color); }
.held { margin: 0 12px 10px; padding: 8px 12px 6px; border-radius: 8px; font-size: 13.5px;
        background: color-mix(in srgb, var(--warning-color, #ffa600) 14%, transparent); }
.held .hh { display: flex; align-items: center; gap: 8px; }
.held .hh ha-icon { --mdc-icon-size: 18px; color: var(--warning-color, #ffa600); }
.held .hh b { font-weight: 500; }
.held ul { list-style: none; margin: 4px 0 0; padding: 0 0 0 26px; }
.held li { display: grid; grid-template-columns: minmax(90px, 200px) minmax(0, 1fr) auto; align-items: center; gap: 12px; padding: 3px 0;
           border-top: 1px solid color-mix(in srgb, var(--warning-color, #ffa600) 20%, transparent); }
.held li:first-child { border-top: 0; }
.held .hn { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.held .rel { height: 28px; padding: 0 10px; font-size: 12.5px; }
.pc .held { margin: 4px 12px 10px; }
.att-h.quiet .lead { color: var(--secondary-text-color); }
.zh .cnt { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-left: 6px; }
ul.att { margin: 4px 0 10px; padding-left: 18px; font-size: 13px; color: var(--secondary-text-color); line-height: 1.5; }
ul.att.err { color: var(--error-color); }
.att-d { padding: 6px 0 0; font-size: 14px; }
.sh { display: flex; align-items: baseline; gap: 10px; padding: 14px 16px 6px; font-size: 15px; font-weight: 500; }
.sh .cnt { font-size: 13px; font-weight: 400; color: var(--secondary-text-color); }
.rt { font-size: 14px; }
.rt-h, .rt-o { display: grid; grid-template-columns: 28px 170px 190px minmax(0, 1fr) 150px; align-items: center; column-gap: 10px; }
.rt-h { grid-template-columns: 28px 170px 190px minmax(0, 1fr) 150px 44px; padding: 4px 8px 6px 12px; border-bottom: 1px solid var(--divider-color); }
.rt-r { display: grid; grid-template-columns: minmax(0, 1fr) 44px; align-items: center; padding: 0 8px 0 12px; border-bottom: 1px solid var(--divider-color); }
.rt-r.open { background: color-mix(in srgb, var(--primary-text-color) 4%, transparent); }
.rt-o { min-height: 52px; border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; padding: 6px 0; }
.rt-o > span { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rt-c { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
.rt-n { font-weight: 500; }
.rt-r.off .rt-n, .rt-r.off .rt-d { opacity: .55; }
.rt-d { color: var(--secondary-text-color); font-size: 13px; }
.rt-r.hand { border-bottom: 0; }
.rd-hand { color: var(--warning-color, #ffa600); padding-top: 0; }
.rt-x { padding: 0 16px 10px 50px; font-size: 12.5px; color: var(--warning-color, #ffa600); border-bottom: 1px solid var(--divider-color); }
.sbk-chip.warn { background: color-mix(in srgb, var(--warning-color, #ffa600) 18%, transparent); color: var(--warning-color, #ffa600); }
.sbk-chip.dim { background: rgba(127,127,127,.15); color: var(--secondary-text-color); }
button.th { all: unset; cursor: pointer; font-size: 11.5px; font-weight: 500; letter-spacing: .05em; text-transform: uppercase; color: var(--secondary-text-color); }
button.th:hover, button.th.on { color: var(--primary-text-color); }
button.th:focus-visible { outline: 2px solid var(--primary-color); }
.rt-h > span, .mt th { font-size: 11.5px; font-weight: 500; letter-spacing: .05em; text-transform: uppercase; color: var(--secondary-text-color); }
.rd { padding: 4px 16px 16px 50px; font-size: 13.5px; border-bottom: 1px solid var(--divider-color); background: color-mix(in srgb, var(--primary-text-color) 2.5%, transparent); }
.rd-in { padding: 8px 0 4px; color: var(--secondary-text-color); line-height: 1.5; }
.rd-in b { color: var(--primary-text-color); font-weight: 500; }
.rd-two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.rd .sub { font-size: 11.5px; font-weight: 500; letter-spacing: .06em; text-transform: uppercase; color: var(--secondary-text-color); margin: 14px 0 4px; display: flex; gap: 10px; align-items: baseline; }
.rd .sub .lk { all: unset; cursor: pointer; text-transform: none; letter-spacing: 0; color: var(--primary-color); font-weight: 400; }
table.mt { width: 100%; border-collapse: collapse; table-layout: fixed; }
.mt th { text-align: left; padding: 4px 8px 4px 0; border-bottom: 1px solid var(--divider-color); }
.mt td { padding: 6px 8px 6px 0; border-bottom: 1px solid color-mix(in srgb, var(--divider-color) 60%, transparent); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; vertical-align: top; }
.mt td.wrap { white-space: normal; }
.mt tr.dimrow td { opacity: .55; }
.mt .ent { margin: 0; padding: 0; max-width: 100%; overflow: hidden; text-overflow: ellipsis; display: block; text-align: left; color: var(--primary-text-color); }
.dot { width: 8px; height: 8px; border-radius: 4px; display: inline-block; background: var(--disabled-color, #9e9e9e); margin-right: 6px; vertical-align: middle; }
.dot.on { background: var(--warning-color, #ffa600); } .dot.occ { background: var(--success-color, #43a047); } .dot.na { background: transparent; border: 1px solid var(--secondary-text-color); box-sizing: border-box; }
.mut { color: var(--secondary-text-color); }
@media (max-width: 700px) {
  .rt-h { display: none; }
  .rt-o { display: flex; flex-wrap: wrap; column-gap: 8px; row-gap: 2px; }
  .rt-c { order: 0; } .rt-n { flex: 1 1 60%; }
  .rt-hd { order: 1; }
  .rt-rl { order: 2; margin-left: 26px; }
  .rt-d { order: 3; flex: 1 1 0; }
  .rt-hd:has(.mut) { display: none; }
  .rt-x { padding-left: 36px; }
  .rd { padding: 4px 12px 14px 12px; }
  .rd-two { grid-template-columns: minmax(0, 1fr); gap: 0; }
  table.mt.ad thead { display: none; }
  table.mt.ad tr { display: block; padding: 6px 0; border-bottom: 1px solid color-mix(in srgb, var(--divider-color) 60%, transparent); }
  table.mt.ad td { display: block; border: 0; padding: 1px 0; white-space: normal; }
  table.mt.ad td:first-child { font-weight: 500; }
  table.mt.ad td:nth-child(2), table.mt.ad td:nth-child(4) { color: var(--secondary-text-color); font-size: 12.5px; }
  table.mt.ad td:empty { display: none; }
  .pc .now { padding-top: 12px; }
}
.ent i { width: 8px; height: 8px; border-radius: 4px; display: inline-block; background: var(--disabled-color, #9e9e9e); }
.ent.on i { background: var(--warning-color, #ffa600); }
.ent.occ i { background: var(--success-color, #43a047); }
.ent.na { opacity: .55; }
`;

class SbModesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" }).innerHTML = `<style>${STYLE}</style><ha-card><div class="body"></div></ha-card><dialog class="lights"></dialog><dialog class="lights adet"></dialog>`;
    this.shadowRoot.addEventListener("click", (e) => {
      const b = e.target.closest?.("[data-adet]");
      if (b) { const [zid, xid] = b.dataset.adet.split("|"); this._openAddonDet(zid, xid); }
    });
    this._sig = "";
    this._open = new Set();      // zones whose details are shown
    this._busy = false;
  }

  static getStubConfig() { return { title: "Modes" }; }
  static getConfigElement() { return document.createElement(`${CARD}-editor`); }
  setConfig(config) { this._config = { title: "Modes", ...(config || {}) }; this.toggleAttribute("panel", !!this._config.panel); this._sig = ""; this._render(); }
  getCardSize() { return 3 + this._zones().length; }

  connectedCallback() { this._tick = setInterval(() => { this._sig = ""; this._render(); }, 30000); }
  disconnectedCallback() { clearInterval(this._tick); this._unMenu?.(); }

  set hass(h) {
    this._hass = h;
    if (this._sliding) return;                           // no re-render under a finger on the room slider
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
      .sort((a, b) => byName(a.name, b.name));                       // A to Z everywhere (2026-11-01)
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
    ICON_OVERRIDES = sel.attributes.mode_icons || {};
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
    const headMenu = !cfg.panel && this._menu === "__head" ? kit().menu("Card actions", [{ act: "page", icon: "mdi:open-in-app", label: "Open the SB Modes page" },
      ...(this._hass.user?.is_admin ? [{ act: "edit", icon: "mdi:pencil", label: "Edit modes & zones" }] : [])]) : "";
    const head = `<div class="sbk-head sbk-hwrap"><h2>${esc(cfg.title || "")}</h2>
        <span class="sbk-count">${esc(nm(sel.state))}${sel.attributes.manual?.hold === "paused" ? ` <span style="color: var(--warning-color, #ffa600)">(paused)</span>` : sel.attributes.manual ? " (by hand)" : ""} · ${options.length} mode${options.length === 1 ? "" : "s"} · ${zones.length} zone${zones.length === 1 ? "" : "s"}${
          devCount ? ` · <span style="color: var(--warning-color, #ffa600)" title="Held or changed by hand — see sensor.sb_modes_deviations">${devCount} by hand</span>` : ""}${
          folded && attention ? ` · <span style="color: var(--error-color)">${attention} need${attention === 1 ? "s" : ""} attention</span>` : ""}</span>
        ${cfg.panel ? "" : `<button class="sbk-kb hkb ${headMenu ? "on" : ""}" aria-label="More" title="More: open the SB Modes page" aria-expanded="${headMenu ? "true" : "false"}">${kit().kbIcon}</button>${headMenu}${foldBtn(folded)}`}
      </div>`;
    if (folded) { body.innerHTML = head; this._wire(); return; }
    if (cfg.panel) {
      body.innerHTML = `<section class="pc">
          <div class="now"><span class="sbk-ico armed big"><ha-icon icon="${esc(modeIcon(sel.state))}"></ha-icon></span>
            <div style="min-width:0"><div class="cur">${esc(nm(sel.state))}</div>
              <div class="since">since ${esc(hhmm(since))} (${esc(ago(since))} ago) · fallback ${esc(nm(sel.attributes.fallback_mode))}</div></div></div>
          ${status}
          <div class="switch-l">Modes · tap one to see what it does</div>
          <div class="modes" role="group" aria-label="See a mode">${chips}</div>
          ${banner}</section>
        ${this._attentionHtml(errs, warns, prob?.attributes.out_of_service || [])}
        <section class="pc">${this._roomsTableHtml(zones, viewed, nm(viewed), previewing)}</section>
        <section class="pc">${this._scheduleHtml(sel, nm)}</section>
        <section class="pc">${this._activityHtml()}</section>
        ${this._msg ? `<div class="sbk-note">${esc(this._msg)}</div>` : ""}`;
      this._wire();
      if (this._isOpen("__activity") || zones.some((z) => this._isOpen(z.id))) this._fetchActivity();
      return;
    }
    body.innerHTML = `${head}
      <div class="now"><span class="sbk-ico armed big"><ha-icon icon="${esc(modeIcon(sel.state))}"></ha-icon></span>
        <div><div class="cur-l">Current mode</div><div class="cur">${esc(nm(sel.state))}</div>
          <div class="since">since ${esc(hhmm(since))} (${esc(ago(since))} ago) · fallback ${esc(nm(sel.attributes.fallback_mode))}</div></div></div>
      ${status}
      ${this._heldHtml(zones)}
      <div class="switch-l">Modes · tap one to see what it does</div>
      <div class="modes" role="group" aria-label="See a mode">${chips}</div>
      ${banner}
      ${problems}
      ${this._zonesHead(zones, previewing, nm(viewed))}
      ${!this._zonesOpen() ? "" : rows || `<div class="sbk-empty">No zones yet. Add them to the table with the <code>sb_modes.save_config</code> action.</div>`}
      ${this._scheduleHtml(sel, nm)}
      ${this._activityHtml()}
      ${this._msg ? `<div class="sbk-note">${esc(this._msg)}</div>` : ""}`;
    this._wire();
    if (this._isOpen("__activity") || zones.some((z) => this._isOpen(z.id))) this._fetchActivity();
  }

  /** Open or closed: on the SB Modes page every section starts closed and is remembered for the
   *  browser session; on a dashboard card, for as long as the card is shown. */
  _isOpen(k) { return this._config?.panel ? sessGet(`status:${k}`) : this._open.has(k); }
  _toggleOpen(k) {
    const on = !this._isOpen(k);
    if (this._config?.panel) sessSet(`status:${k}`, on); else if (on) this._open.add(k); else this._open.delete(k);
  }
  /** A table's sort: {key, dir}, A to Z by name unless the user picked a heading (this session). */
  _sortOf(table, def = "name") {
    let v = null;
    try { v = JSON.parse(sessionStorage.getItem(`sb-modes-sort:${table}`) || "null"); } catch (e) { /* none */ }
    return v && v.key ? v : { key: def, dir: 1 };
  }
  _setSort(table, key) {
    const cur = this._sortOf(table), v = { key, dir: cur.key === key ? -cur.dir : 1 };
    try { sessionStorage.setItem(`sb-modes-sort:${table}`, JSON.stringify(v)); } catch (e) { /* not remembered */ }
  }
  _th(table, key, label, def = "name") {
    const s = this._sortOf(table, def), on = s.key === key;
    return `<button class="th ${on ? "on" : ""}" data-sort="${table}|${key}" aria-label="Sort by ${esc(label)}">${esc(label)}${on ? (s.dir > 0 ? " ▲" : " ▼") : ""}</button>`;
  }

  /** Zones are open unless folded; the choice is remembered per card (like the card's own fold). */
  _zonesKey() { return foldKey(CARD, `${this._config.title || ""}:zones`); }
  _zonesOpen() { return !foldGet(this._zonesKey()); }

  _zonesHead(zones, previewing, viewedName) {
    const open = this._zonesOpen();
    const held = zones.filter((z) => z.attr.control && z.attr.control !== "auto").length;
    const warn = zones.filter((z) => look(z.state, z.attr.status).tint === "warn").length;
    const extra = open ? "" : [held ? `${held} held or by hand` : "", warn ? `${warn} need${warn === 1 ? "s" : ""} a look` : ""].filter(Boolean).join(" · ");
    return `<button class="zh zt zones" data-x="__zones" aria-expanded="${open}">Zones${previewing ? ` in ${esc(viewedName)} <span class="pvtag">preview</span>` : ""}
        <span class="cnt">${zones.length} zone${zones.length === 1 ? "" : "s"}${extra ? ` · <span style="color: var(--warning-color, #ffa600)">${esc(extra)}</span>` : ""}</span>
        <ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button>`;
  }

  /** The last 24 h of SB Modes' Logbook entries (activity.py), refreshed every 30 s while shown. */
  async _fetchActivity(force = false) {
    if (this._actBusy || !this._hass) return;
    if (!force && this._act && Date.now() - this._act.at < 30000) return;
    this._actBusy = true;
    const ids = [...this._zones().map((z) => z.entity), this._select()?.entity_id].filter(Boolean);
    const end = new Date(), start = new Date(end.getTime() - 24 * 3600e3);
    try {
      const res = await this._hass.callWS({ type: "logbook/get_events", start_time: start.toISOString(), end_time: end.toISOString(), entity_ids: ids });
      const entries = (res || []).filter((e) => e.domain === "sb_modes" && e.message).sort((a, b) => b.when - a.when);
      this._act = { at: Date.now(), entries, err: null };
    } catch (e) {
      this._act = { at: Date.now(), entries: this._act?.entries || [], err: e?.message || String(e) };
    }
    this._actBusy = false;
    this._render();
  }

  /** Rows, newest first, with a heading wherever the day changes. */
  _actRows(list, zoneName) {
    let day = null;
    return list.map((e) => {
      const d = actDay(e.when), head = d !== day ? `<div class="actday">${esc(d)}</div>` : "";
      day = d;
      return head + this._actRow(e, zoneName(e));
    }).join("");
  }

  _actRow(e, zoneName) {
    return `<div class="act"><span class="t">${esc(actTime(e.when))}</span><ha-icon icon="${esc(e.icon || "mdi:texture-box")}"></ha-icon>
      <span class="m">${zoneName ? `<b>${esc(zoneName)}</b> · ` : ""}${esc(fmtTimes(e.message))}</span></div>`;
  }

  _recentHtml(z) {
    if (!this._act) return `<div><b>Recent</b> loading…</div>`;
    const mine = this._act.entries.filter((e) => e.entity_id === z.entity);
    if (!mine.length) return `<div><b>Recent</b> nothing in the last 24 h</div>`;
    return `<div><b>Recent</b>${mine.length > 5 ? ` <span class="src">(last 5 of ${mine.length} — all in Activity below)</span>` : ""}</div>${
      this._actRows(mine.slice(0, 5), () => null)}`;
  }

  _activityHtml() {
    const open = this._isOpen("__activity");
    const all = this._act?.entries || [];
    const zones = this._zones();
    const zoneOf = Object.fromEntries(zones.map((z) => [z.entity, z]));
    const f = this._actZone || "all";
    const pick = (e) => f === "all" || (f === "__mode" ? !zoneOf[e.entity_id] : zoneOf[e.entity_id]?.id === f);
    const list = all.filter(pick).slice(0, 150);
    const filters = [["all", "All"], ...(all.some((e) => !zoneOf[e.entity_id]) ? [["__mode", "Mode"]] : []),
      ...zones.filter((z) => all.some((e) => e.entity_id === z.entity)).map((z) => [z.id, z.name])];
    const chips = `<div class="actf" role="group" aria-label="Show activity for">${filters.map(([k, n]) =>
      `<button class="sbk-chip actc ${k === f ? "on" : ""}" data-f="${esc(k)}" aria-pressed="${k === f}">${esc(n)}</button>`).join("")}</div>`;
    const rows = this._actRows(list, (e) => (f === "all" ? (zoneOf[e.entity_id]?.name || "Mode") : null));
    const cnt = this._act ? `${all.length} in the last 24 h` : "last 24 h";
    const inner = !this._act ? `<div class="sbk-empty">Loading…</div>`
      : `${this._act.err ? `<div class="sbk-note">Could not read the Logbook: ${esc(this._act.err)}</div>` : ""}${all.length ? chips : ""}${
        rows || `<div class="sbk-empty">Nothing in the last 24 hours.</div>`}`;
    return `<button class="zh zt" data-x="__activity" aria-expanded="${open}">Activity <span class="cnt">${esc(cnt)}</span>
        <ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button>${open ? `<div class="rules">${inner}</div>` : ""}`;
  }

  _scheduleHtml(sel, nm) {
    const key = (r) => (/^\d\d:\d\d$/.test(r.at) ? r.at : /^sunrise/.test(r.at) ? "06:59" : "17:59");   // sun times sort near when they fall
    const ekey = (e) => (e.eve ? "00:00" : "") + key(e);                                                  // "the night before" first
    const rules = [...(sel.attributes.schedule || [])].sort((a, b) => key(a).localeCompare(key(b)));
    const types = sel.attributes.day_types || [], times = sel.attributes.times || {};
    const open = this._isOpen("__schedule");
    const typeRows = types.map((t) => {
      const list = [...(times[t.id] || [])].sort((a, b) => ekey(a).localeCompare(ekey(b)));
      return `<div class="dtype"><div class="dtn">${esc(t.name || t.id)}<span class="d">${esc(t.day_set ? pretty(t.day_set) : "every other day")}</span></div>${
        list.map((e) => `<div class="rule"><span class="t">${esc(ruleAt(e.at))}</span><ha-icon icon="${esc(modeIcon(e.mode))}"></ha-icon><span class="m">${esc(nm(e.mode))}</span><span class="d">${e.eve ? "the night before" : ""}</span></div>`).join("")
        || `<div class="sbk-empty">No mode changes.</div>`}</div>`;
    }).join("");
    const ruleRows = rules.map((r) => `<div class="rule"><span class="t">${esc(ruleAt(r.at))}</span><ha-icon icon="${esc(modeIcon(r.mode))}"></ha-icon><span class="m">${esc(nm(r.mode))}</span><span class="d">${esc(ruleDays(r))}</span></div>`).join("");
    const count = types.length ? `${types.length} day type${types.length === 1 ? "" : "s"}${rules.length ? ` · ${rules.length} other rule${rules.length === 1 ? "" : "s"}` : ""}`
      : `${rules.length} rule${rules.length === 1 ? "" : "s"}`;
    const any = types.length || rules.length;
    const body = !any ? `<div class="sbk-empty">No schedule — the mode changes only by hand.</div>`
      : open ? `${typeRows}${ruleRows ? `${types.length ? `<div class="dtype"><div class="dtn">Other rules</div>` : "<div>"}${ruleRows}</div>` : ""}` : "";
    return `<button class="zh zt" data-x="__schedule" aria-expanded="${open}">Schedule <span class="cnt">${esc(count)}</span>
        <ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button>${any && !open ? "" : `<div class="rules">${body}</div>`}`;
  }

  /** An entity's name; inside a room's details, without the room's own name in front
   *  ("Floor Lamp Plants", not "Living Room Floor Lamp Plants"). */
  _name(e) {
    const n = this._hass.states[e]?.attributes.friendly_name || e, r = this._inRoom;
    return r && n.toLowerCase().startsWith(`${r.toLowerCase()} `) && n.length > r.length + 1 ? n.slice(r.length + 1) : n;
  }

  _buttonAct(act) {
    const flash = act.flash ? `flash${act.flash > 1 ? ` ×${act.flash}` : ""}, ` : "";
    if (act === "release" || act.release) return `${flash}release`;
    const hold = act.hold === "paused" ? "hold until released" : act.hold === "next" ? "hold until the next mode" : `hold ${act.hold} min`;
    return `${flash}${hold}${act.targets ? ` at ${this._targets(act.targets)}` : ""}`;
  }

  /** Up to 3 devices by name; more as a count ("34 devices off") — the add-on's Details › lists them. */
  _targetsBrief(targets) {
    const ent = Object.entries(targets || {});
    if (ent.length <= 3) return this._targets(targets);
    const say = (t) => t.value != null ? `at ${t.value}` : t.state === "off" ? "off" : t.min_brightness_pct != null ? `≥${t.min_brightness_pct}%` : t.brightness_pct != null ? `${t.brightness_pct}%` : "on";
    const kinds = new Set(ent.map(([, t]) => say(t)));
    return `${ent.length} devices ${kinds.size === 1 ? [...kinds][0] : "set"}`;
  }
  /** An add-on's device settings as columns (a step, a state, a band): [[heading, targets], …]. */
  _addonCols(x) {
    const mn = (m) => this._modeName?.(m) || m;
    if (x.type === "lights") return (x.steps || []).filter((st) => st.targets && Object.keys(st.targets).length)
      .map((st, k, all) => [st.after_minutes ? `+${st.after_minutes} min` : all.length > 1 ? "First" : "Sets", st.targets]);
    if (x.type === "while") return Object.entries(x.states || {}).filter(([, c]) => Object.keys(c.targets || {}).length).map(([st, c]) => [`while ${st}`, c.targets]);
    if (x.type === "follow") return [...(x.bands || [])].sort((p, q) => (q.min_pct || 0) - (p.min_pct || 0)).map((b) => [b.min_pct ? `≥${b.min_pct}%` : "off / low", b.targets || {}]);
    if (x.type === "button") return Object.entries(x.presses || {}).filter(([, a]) => a.targets).map(([k, a]) => [PRESS[k] || k, a.targets]);
    return [];
  }
  /** The add-on popup: a device per row, a column per step / state / band. */
  _addonDetHtml(z, x) {
    const cols = this._addonCols(x);
    const eids = [...new Set(cols.flatMap(([, t]) => Object.keys(t)))].sort((a, b) => byName(this._name(a), this._name(b)));
    const say = (t) => !t ? `<span class="mut">—</span>` : esc(t.value != null ? String(t.value) : t.state === "off" ? "off"
      : [t.brightness_pct != null ? `${t.brightness_pct}%` : t.min_brightness_pct != null ? `at least ${t.min_brightness_pct}%` : "on",
         t.color_temp_kelvin ? `${t.color_temp_kelvin} K` : "", t.effect ? `effect ${t.effect}` : ""].filter(Boolean).join(" · "));
    const f = this._addonInfo(x);
    return `<div class="dlg"><h3>${esc(x.name || x.id)}</h3><div class="lh" style="margin:-6px 0 10px">${esc(z.name)} · ${esc(f.when)}</div>
      <div class="adw"><table class="mt"><thead><tr><th>Device</th>${cols.map(([h]) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${eids.map((e) => `<tr><td title="${esc(e)}">${esc(this._name(e))}</td>${cols.map(([, t]) => `<td>${say(t[e])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="dlg-foot"><button class="sw" data-adet-close>Close</button></div></div>`;
  }
  _openAddonDet(zid, xid) {
    const z = this._zones().find((q) => q.id === zid), x = (z?.attr.addons || []).find((q) => q.id === xid);
    const dlg = this.shadowRoot.querySelector("dialog.adet");
    if (!x || !dlg) return;
    this._inRoom = z.name;
    try { dlg.innerHTML = this._addonDetHtml(z, x); } finally { this._inRoom = null; }
    if (!dlg._wired) {
      dlg._wired = true;
      dlg.addEventListener("click", (e) => { if (e.target === dlg || e.target.closest("[data-adet-close]")) dlg.close(); });
    }
    if (!dlg.open) dlg.showModal();
  }
  /** An add-on as When (its trigger and where it runs) and Does, for the page's add-on table. */
  _addonInfo(x) {
    const t = x.trigger || {}, w = x.when || {}, mn = (m) => this._modeName?.(m) || m;
    const where = [w.modes?.length ? `in ${w.modes.map(mn).join(", ")}` : "", w.days?.length ? `only on ${w.days.join(", ")}` : "",
      w.not_days?.length ? `never on ${w.not_days.join(", ")}` : "", w.between?.length === 2 ? `${fmtTimes(w.between[0])}–${fmtTimes(w.between[1])}` : ""].filter(Boolean).join(", ");
    let when = "", does = "";
    if (x.type === "lights") {
      when = "at" in t ? `at ${ruleAt(t.at)}` : t.mode_begins ? `${t.mode_begins.map(mn).join(" or ")} begins` : t.mode_ends ? `${t.mode_ends.map(mn).join(" or ")} ends`
        : t.calendar ? `${t.offset_minutes ? `${Math.abs(t.offset_minutes)} min ${t.offset_minutes < 0 ? "before" : "after"}` : "when"} ${t.match ? `“${t.match}”` : "an event"} ${t.edge === "end" ? "ends" : "starts"}`
        : t.entity ? `${this._name(t.entity)} becomes ${t.to}` : "";
      does = (x.steps || []).map((st) => `${st.after_minutes ? `+${st.after_minutes} min ` : ""}${
        st.set_mode ? `house → ${mn(st.set_mode)}` : st.resume_schedule ? "back on schedule"
        : st.script ? `run ${this._name(st.script)}` : st.release || "hold" in st ? this._buttonAct(st) : this._targetsBrief(st.targets)}`).join(" · ");
    } else if (x.type === "while") {
      when = `while ${(x.entities || [x.entity]).filter(Boolean).map((e) => this._name(e)).join(" / ")} is …`;
      does = Object.entries(x.states || {}).map(([s, c]) => `${s} → ${this._targetsBrief(c.targets || {}) || "nothing"}${c.holds ? " (holds)" : ""}`).join(" · ");
    } else if (x.type === "button") {
      when = `${this._name(x.entity)} pressed`;
      does = Object.entries(x.presses || {}).map(([k, act]) => `${PRESS[k] || k} → ${this._buttonAct(act)}`).join(", ");
    } else if (x.type === "follow") {
      when = `${this._name(x.leader)} changes`;
      does = [...(x.bands || [])].sort((p, q) => (q.min_pct || 0) - (p.min_pct || 0)).map((b) => `${b.min_pct ? `≥${b.min_pct}%` : "off / low"} → ${this._targetsBrief(b.targets)}`).join(" · ");
    } else if (x.type === "exhaust") {
      when = `someone comes in${x.humidity_rate ? " or humidity rises" : ""}`;
      does = `${this._name(x.fan)} on; off ${x.empty_minutes ?? 5} min after empty + ${x.drying_minutes ?? 10} min drying`;
    } else if (x.type === "replay") {
      when = "throughout";
      does = `${(x.lights || []).length} light${(x.lights || []).length === 1 ? "" : "s"} repeat what they did ${x.days_back || 7} days ago`;
    } else {
      when = "a light turned on by hand";
      does = `${(x.lights || []).map((e) => this._name(e)).join(", ")} off${x.with_empty ? " with the empty setting" : ""}${x.on_minutes ? `${x.with_empty ? ", or" : ""} once on ${x.on_minutes} min with the room empty` : ""}`;
    }
    const big = this._addonCols(x).some(([, t]) => Object.keys(t).length > 3);
    return { when: [when, where].filter(Boolean).join(" · "), does, big };
  }

  /** One add-on in the room's details: name, what it does, and its live status. */
  _addonLine(x, status) {
    const t = x.trigger || {}, w = x.when || {}, mn = (m) => this._modeName?.(m) || m;
    let what;
    if (x.type === "lights") {
      const trig = "at" in t ? `at ${fmtTimes(t.at)}` : t.mode_begins ? `when ${t.mode_begins.map(mn).join(" or ")} begins`
        : t.mode_ends ? `when ${t.mode_ends.map(mn).join(" or ")} ends`
        : t.calendar ? `${t.offset_minutes ? `${Math.abs(t.offset_minutes)} min ${t.offset_minutes < 0 ? "before" : "after"}` : "when"} ${t.match ? `“${t.match}”` : "an event"} on ${this._name(t.calendar)} ${t.edge === "end" ? "ends" : "starts"}`
        : t.entity ? `when ${this._name(t.entity)} becomes ${t.to}` : "";
      what = `${trig}: ` + (x.steps || []).map((st) => `${st.after_minutes ? `+${st.after_minutes} min ` : ""}${
        st.set_mode ? `house → ${mn(st.set_mode)}` : st.resume_schedule ? "back on schedule"
        : st.script ? `run ${this._name(st.script)}` : st.release || "hold" in st ? this._buttonAct(st) : this._targets(st.targets)}`).join(" · ");
    } else if (x.type === "while") {
      what = `while ${(x.entities || [x.entity]).filter(Boolean).map((e) => this._name(e)).join(" / ")} is ` + Object.entries(x.states || {})
        .map(([st, c]) => `${st} → ${this._targets(c.targets || {}) || "nothing"}${c.holds ? " (holds the room)" : ""}`).join(" · ");
    } else if (x.type === "button") {
      what = `${this._name(x.entity)}: ` + Object.entries(x.presses || {}).map(([k, act]) => `${PRESS[k] || k} → ${this._buttonAct(act)}`).join(", ");
    } else if (x.type === "follow") {
      what = `follows ${this._name(x.leader)}: ` + [...(x.bands || [])].sort((p, q) => (q.min_pct || 0) - (p.min_pct || 0))
        .map((b) => `${b.min_pct ? `≥${b.min_pct}%` : "off / low"} → ${this._targets(b.targets)}`).join(" · ");
    } else if (x.type === "exhaust") {
      what = `${this._name(x.fan)} on when someone comes in${x.humidity_rate ? ` or humidity rises > ${x.on_rate ?? 2} %/min` : ""}; off ${x.empty_minutes ?? 5} min after empty + ${x.drying_minutes ?? 10} min drying${x.max_on_minutes ?? 20 ? `, at most ${x.max_on_minutes ?? 20} min unless occupied` : ""}`;
    } else if (x.type === "replay") {
      what = `${(x.lights || []).map((e) => this._name(e)).join(", ")} repeat what they did ${x.days_back || 7} days ago`;
    } else {
      what = `${(x.lights || []).map((e) => this._name(e)).join(", ")} off${x.with_empty ? " with the empty setting" : ""}${x.on_minutes ? `${x.with_empty ? ", or" : ""} once on ${x.on_minutes} min with the room empty` : ""}`;
    }
    const when = [w.modes?.length ? `in ${w.modes.map(mn).join(", ")}` : "", w.days?.length ? `only on ${w.days.join(", ")}` : "",
      w.not_days?.length ? `never on ${w.not_days.join(", ")}` : "", w.between?.length === 2 ? `${w.between[0]}–${w.between[1]}` : ""].filter(Boolean).join(", ");
    return `<div${x.enabled === false ? ' style="opacity:.55"' : ""}><b>${esc(x.name || x.id)}</b>${when ? ` (${esc(when)})` : ""} ${esc(what)}${
      x.enabled === false ? " · off" : status ? ` · <i>${esc(fmtTimes(status))}</i>` : ""}</div>`;
  }

  _targets(targets) {
    return Object.entries(targets || {}).map(([e, t]) => `${this._name(e)} ${
      t.value != null ? t.value : t.state === "off" ? "off" : t.min_brightness_pct != null ? `≥${t.min_brightness_pct}%` : t.brightness_pct != null ? `${t.brightness_pct}%` : "on"}${t.effect ? ` (${t.effect})` : ""}`).join(", ");
  }

  /** A behaviour by name and timings only, no devices ("Occupied / empty · empty after 5 min"):
   *  for the "In <mode>" line, where the lights table below already shows the devices. */
  _brief(beh, p = {}) {
    const bits = [BEHAVIOUR_NAMES[beh] || beh];
    if (beh === "nightlight_then_dark") bits.push(`dark after ${p.delay ?? 10} min empty`);
    if (beh === "vacancy_off") {
      const secs = p.off_after_seconds ?? 60;
      bits.push(`off ${secs >= 120 && secs % 60 === 0 ? `${secs / 60} min` : `${secs} s`} after empty`);
      if (p.max_on_minutes) bits.push(`or once on ${p.max_on_minutes} min while empty`);
      if (p.on_when_occupied) bits.push("on when someone comes in");
    }
    if (beh === "timed_off") bits.push(`off ${p.off_after_minutes ?? 5} min after on`);
    if (beh === "occupied_vacant" && p.vacant_after_minutes) bits.push(`empty after ${p.vacant_after_minutes} min`);
    if (beh === "presence_lux") bits.push(`dark below ${p.target_lux} lx`, `empty after ${p.vacant_after_minutes ?? 15} min`);
    return bits.join(" · ");
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
    if (beh === "timed_off") return `${(p.lights || []).map((e) => this._name(e)).join(", ")} off ${p.off_after_minutes ?? 5} min after on`;
    if (beh === "occupied_vacant") {
      const after = p.vacant_after_minutes ? ` (after ${p.vacant_after_minutes} min)` : "";
      return `Occupied: ${this._targets(p.occupied)} · Empty${after}: ${this._targets(p.vacant)}`;
    }
    if (beh === "presence_lux") {
      const vac = p.vacant && Object.keys(p.vacant).length ? this._targets(p.vacant) : "lights off";
      const bright = p.vacant_bright && Object.keys(p.vacant_bright).length ? this._targets(p.vacant_bright) : null;
      return `Occupied and below ${p.target_lux} lx: ${this._targets(p.occupied)} · Empty ${p.vacant_after_minutes ?? 15} min: ${
        bright ? `dark → ${vac}; light → ${bright}` : vac}`;
    }
    return beh;
  }

  /** What a room is doing, for the card's row and the page's table. */
  _roomInfo(z, viewed, modeName, previewing) {
    this._inRoom = z.name;
    try { return this._roomInfoIn(z, viewed, modeName, previewing); } finally { this._inRoom = null; }
  }
  _roomInfoIn(z, viewed, modeName, previewing) {
    const a = z.attr;
    const pick = (a.by_mode || {})[viewed] || { behaviour: z.state, params: a.params, source: a.source };
    const beh = pick.behaviour, off = pick.source === "disabled";
    const begins = (a.addons || []).filter((x) => x.type === "lights" && x.enabled !== false && (x.trigger?.mode_begins || []).includes(viewed));
    const what = off ? "Off — SB Modes leaves this room alone" : this._describe(beh, pick.params) + begins.map((x) => ` · when ${modeName} begins: ${x.name}`).join("");
    const held = !previewing && String(a.status || "").startsWith("held");
    const occ = a.occupancy_state === "occupied" ? `Occupied since ${hhmm(a.occupancy_since)}`
      : a.occupancy_state === "vacant" ? `Vacant since ${hhmm(a.occupancy_since)}`
      : a.occupancy_state === "unknown" ? "Occupancy unknown (sensor unavailable)" : "";
    const rest = off ? "room switched off" : held ? "" : beh === "hands_off" ? `not controlled in ${modeName}`
      : String(a.status || "").replace(/^(vacant|occupied)(;\s*|$)/, "").replace(/^overlay:\s*/, "");
    const doing = previewing ? what : [occ, fmtTimes(rest)].filter(Boolean).join(" · ");
    const h = previewing ? null : a.hold;
    const hand = previewing ? 0 : Object.keys(a.overrides || {}).length;
    const hold = !h ? (hand ? "by hand" : "") : h.hold === "timed" ? `until ${hhmm(h.until)}` : h.hold === "next" ? "until next mode" : "until released";
    const src = off ? "switched off in the editor" : pick.source === "override" ? "this room's own setting for this mode" : pick.source === "zone" ? "this room's default rule" : "no rule";
    const brief = off ? "Off — SB Modes leaves this room alone" : this._brief(beh, pick.params) + begins.map((x) => ` · when ${modeName} begins: ${x.name}`).join("");
    return { a, pick, beh, off, what, brief, doing, hold, held: !!h, hand, src, rule: off ? "Switched off" : BEHAVIOUR_NAMES[beh] || beh,
             look: previewing ? { icon: look(beh, "").icon, tint: "idle" } : look(beh, a.status) };
  }

  /** "Held: Office (until next mode) · Laundry Room (until 3:15 PM)", each with Release — only
   *  while a room is held; on the card under the current mode, on the page above the rooms. */
  _heldHtml(zones) {
    // held rooms, and rooms with lights changed by hand (release_zone ends both)
    const held = zones.filter((z) => z.attr.hold || Object.keys(z.attr.overrides || {}).length);
    if (!held.length) return "";
    const hand = (z) => {
      const ov = z.attr.overrides || {}, nm = (e) => { const r = this._inRoom; this._inRoom = z.name; const n = this._name(e); this._inRoom = r; return n; };
      const on = Object.keys(ov).filter((e) => !ov[e].off).map(nm), off = Object.keys(ov).filter((e) => ov[e].off).map(nm);
      const parts = [];
      if (on.length || !off.length) parts.push("changed by hand" + (on.length ? `: ${on.join(", ")}` : ""));
      if (off.length) parts.push(`off by hand: ${off.join(", ")}`);
      return parts.join(" · ");
    };
    const until = (z) => { const h = z.attr.hold; return !h ? hand(z) : h.hold === "timed" ? `until ${hhmm(h.until)}` : h.hold === "next" ? "until next mode" : "until released"; };
    const anyHand = held.some((z) => !z.attr.hold);
    return `<div class="held" role="group" aria-label="Held rooms"><div class="hh"><ha-icon icon="mdi:hand-back-right-outline"></ha-icon><b>${anyHand ? "Held or by hand" : "Held"}</b>
        <span class="mut">${held.length} room${held.length === 1 ? "" : "s"}</span></div>
      <ul>${held.map((z) => `<li><span class="hn">${esc(z.name)}</span><span class="mut">${esc(until(z))}</span>
        <button class="sbk-btn rel" data-release-zone="${esc(z.id)}" aria-label="Release ${esc(z.name)}">Release</button></li>`).join("")}</ul></div>`;
  }

  /** The page's rooms: a table (Room · Rule now · Doing · Hold), each row opening its details. */
  _roomsTableHtml(zones, viewed, modeName, previewing) {
    const sort = this._sortOf("rooms");
    const rows = zones.map((z) => ({ z, i: this._roomInfo(z, viewed, modeName, previewing) }));
    const key = { name: (r) => r.z.name, rule: (r) => r.i.rule, hold: (r) => (r.i.hold ? `0${r.i.hold}` : "1"), doing: (r) => r.i.doing }[sort.key] || ((r) => r.z.name);
    rows.sort((x, y) => byName(key(x), key(y)) * sort.dir || byName(x.z.name, y.z.name));
    const head = `<div class="rt-h"><span></span>${this._th("rooms", "name", "Room")}${this._th("rooms", "rule", "Rule now")}${this._th("rooms", "doing", "Doing")}${this._th("rooms", "hold", "Hold")}<span></span></div>`;
    const body = rows.map(({ z, i }) => {
      const open = this._isOpen(z.id);
      const ov = previewing ? {} : (i.a.overrides || {});
      this._inRoom = z.name;
      const hand = Object.keys(ov).map((e) => this._name(e)).sort(byName);
      this._inRoom = null;
      i.handTxt = hand.length ? `Changed by hand: ${hand.join(", ")}` : "";
      return `<div class="rt-r ${open ? "open" : ""} ${i.off ? "off" : ""} ${hand.length && !open ? "hand" : ""}" data-z="${esc(z.id)}">
          <button class="rt-o" aria-expanded="${open}" title="${open ? "Hide" : "Show"} ${esc(z.name)}'s details">
            <span class="rt-c"><ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></span>
            <span class="rt-n">${esc(z.name)}</span>
            <span class="rt-rl"><span class="sbk-chip ${i.held ? "warn" : ""}" title="${esc(i.src)}">${esc(i.rule)}</span></span>
            <span class="rt-d">${esc(i.doing)}</span>
            <span class="rt-hd">${i.hold ? `<span class="sbk-chip ${i.held ? "warn" : "dim"}">${esc(i.hold)}</span>` : `<span class="mut">—</span>`}</span>
          </button>
          ${previewing ? "<span></span>" : `<button class="sbk-kb zkb ${this._dlg === z.id ? "on" : ""}" aria-label="Hold and lights: ${esc(z.name)}" title="Hold and lights: ${esc(z.name)}" aria-haspopup="dialog">${kit().kbIcon}</button>`}
        </div>${hand.length && !open ? `<div class="rt-x">${esc(i.handTxt)}</div>` : ""}${open ? this._roomDetailHtml(z, i, modeName, previewing) : ""}`;
    }).join("");
    return `<div class="sh">Rooms${previewing ? ` in ${esc(modeName)} <span class="pvtag">preview</span>` : ""}<span class="cnt">${zones.length} · tap one for its details</span></div>
      ${previewing ? "" : this._heldHtml(zones)}
      <div class="rt" role="list">${head}${body || `<div class="sbk-empty">No rooms yet.</div>`}</div>`;
  }

  /** One room opened on the page: its lights, sensors, add-ons and recent activity as tables. */
  _roomDetailHtml(z, i, modeName, previewing) {
    this._inRoom = z.name;
    try { return this._roomDetailIn(z, i, modeName, previewing); } finally { this._inRoom = null; }
  }
  _roomDetailIn(z, i, modeName, previewing) {
    const a = i.a, st = (e) => this._hass.states[e];
    const dims = new Set(a.dimmable || []);
    const dir = this._sortOf("lights").dir;
    const now = (e) => {
      const s = st(e)?.state ?? "missing";
      if (s === "on") return `<span class="dot on"></span>${st(e).attributes.brightness != null ? `${Math.round(st(e).attributes.brightness * 100 / 255)}%` : "on"}`;
      return s === "off" ? `<span class="dot"></span>off` : `<span class="dot na"></span>${esc(s)}`;
    };
    const ent = (e) => `<button class="ent" data-info="${esc(e)}" title="${esc(e)} — tap for its controls">${esc(this._name(e))}</button>`;
    const lights = [...(a.lights || [])].sort((x, y) => byName(this._name(x), this._name(y)) * dir)
      .map((e) => `<tr><td>${ent(e)}</td><td>${now(e)}</td><td class="mut">${dims.has(e) ? "dims" : "on/off"}</td></tr>`).join("");
    const sens = [...(a.occupancy || []).map((e) => [e, "presence"]), ...(a.illuminance || []).map((e) => [e, "light"])]
      .sort((x, y) => byName(this._name(x[0]), this._name(y[0])) || byName(x[1], y[1]))
      .map(([e, kind]) => {
        const s = st(e)?.state ?? "missing";
        const val = kind === "light" ? (Number.isNaN(Number(s)) ? s : `${Math.round(Number(s))} lx`) : s === "on" ? "<span class=\"dot occ\"></span>occupied" : s === "off" ? "<span class=\"dot\"></span>clear" : `<span class="dot na"></span>${esc(s)}`;
        return `<tr><td>${ent(e)}</td><td class="mut">${kind}</td><td>${val}</td></tr>`;
      }).join("");
    const addons = (a.addons || []).map((x) => {
      const f = this._addonInfo(x), live = (a.addon_status || {})[x.id];
      return `<tr class="${x.enabled === false ? "dimrow" : ""}"><td>${esc(x.name || x.id)}</td><td>${esc(f.when)}</td><td class="wrap">${esc(f.does)}${f.big ? ` <button class="lk adk" data-adet="${esc(z.id)}|${esc(x.id)}">Details ›</button>` : ""}</td>
        <td class="mut">${x.enabled === false ? "off" : esc(live || "")}</td></tr>`;
    }).join("");
    const mine = this._act ? this._act.entries.filter((e) => e.entity_id === z.entity) : null;
    const recent = !mine ? `<div class="mut">Loading…</div>` : !mine.length ? `<div class="mut">Nothing in the last 24 hours.</div>`
      : `<table class="mt"><colgroup><col style="width:96px"><col></colgroup><tbody>${mine.slice(0, 5).map((e) =>
          `<tr><td class="mut">${esc(actTime(e.when))}</td><td class="wrap">${esc(fmtTimes(e.message))}</td></tr>`).join("")}</tbody></table>`;
    const lightsHead = `<th>${this._th("lights", "name", "Light")}</th>`;
    return `<div class="rd">
        <div class="rd-in"><b>In ${esc(modeName)}</b> ${esc(i.brief)} <span class="mut">(${esc(i.src)})</span></div>
        ${i.handTxt ? `<div class="rd-in rd-hand">${esc(i.handTxt)}</div>` : ""}
        <div class="rd-two">
          <div><div class="sub">Lights · ${(a.lights || []).length}</div>${lights ? `<table class="mt"><colgroup><col><col style="width:110px"><col style="width:70px"></colgroup>
            <thead><tr>${lightsHead}<th>State</th><th>Kind</th></tr></thead><tbody>${lights}</tbody></table>` : `<div class="mut">None.</div>`}</div>
          <div><div class="sub">Sensors · ${(a.occupancy || []).length + (a.illuminance || []).length}</div>${sens ? `<table class="mt"><colgroup><col><col style="width:80px"><col style="width:100px"></colgroup>
            <thead><tr><th>Sensor</th><th>Kind</th><th>Now</th></tr></thead><tbody>${sens}</tbody></table>` : `<div class="mut">None.</div>`}</div>
        </div>
        ${addons ? `<div class="sub">Add-ons · ${(a.addons || []).length}</div><table class="mt ad"><colgroup><col style="width:22%"><col style="width:24%"><col><col style="width:16%"></colgroup>
          <thead><tr><th>Add-on</th><th>When</th><th>Does</th><th>Now</th></tr></thead><tbody>${addons}</tbody></table>` : ""}
        ${previewing ? "" : `<div class="sub">Recent <button class="lk" data-x-open="__activity">all activity ›</button></div>${recent}`}
      </div>`;
  }

  /** Errors and warnings, closed to one line; warnings about the same unavailable device as one entry. */
  _attentionHtml(errs, warns, oos = []) {
    if (!errs.length && !warns.length && !oos.length) return "";
    const groups = new Map(), other = [];
    for (const w of warns) {
      const m = /\b([a-z_]+\.[a-z0-9_]+) is unavailable/.exec(w);
      if (m) { if (!groups.has(m[1])) groups.set(m[1], []); groups.get(m[1]).push(w.replace(/:\s*[a-z_]+\.[a-z0-9_]+ is unavailable\.?$/, "")); }
      else other.push(w);
    }
    const devs = [...groups].sort((x, y) => byName(this._name(x[0]), this._name(y[0])));
    const bits = [errs.length ? `${errs.length} error${errs.length === 1 ? "" : "s"} in the table` : "",
      ...devs.map(([e, l]) => `${this._name(e)} unavailable · ${l.length} setting${l.length === 1 ? "" : "s"}`),
      other.length ? `${other.length} other warning${other.length === 1 ? "" : "s"}` : "",
      oos.length ? `out of service: ${oos.map((o) => o.name).join(", ")}` : ""].filter(Boolean);
    const quiet = !errs.length && !warns.length;      // only out of service: nothing to fix, just a reminder
    const open = this._isOpen("__attention");
    const body = !open ? "" : `<div class="rules">
        ${errs.length ? `<div class="sub err">Errors</div><ul class="att err">${errs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
        ${devs.map(([e, list]) => `<div class="att-d"><button class="ent" data-info="${esc(e)}">${esc(this._name(e))}</button> <span class="mut">unavailable · used in ${list.length} setting${list.length === 1 ? "" : "s"}</span>
          <ul class="att">${list.sort(byName).map((x) => `<li>${esc(x.replace(/^Zone /, ""))}</li>`).join("")}</ul></div>`).join("")}
        ${other.length ? `<div class="sub">Other warnings</div><ul class="att">${other.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
        ${oos.length ? `<div class="sub">Out of service</div><ul class="att">${oos.map((o) => `<li>${esc(o.name)} <span class="mut">(${esc(o.entity_id)}) — disabled in Home Assistant, so SB Modes skips it; its settings are kept. Enable it again to bring it back.</span></li>`).join("")}</ul>` : ""}</div>`;
    return `<section class="pc"><button class="zh zt att-h ${errs.length ? "bad" : ""} ${quiet ? "quiet" : ""}" data-x="__attention" aria-expanded="${open}">
        <ha-icon class="lead" icon="${quiet ? "mdi:power-plug-off-outline" : "mdi:alert-outline"}"></ha-icon>${quiet ? "Out of service" : "Needs attention"} <span class="cnt">${esc(quiet ? oos.map((o) => o.name).join(", ") : bits.join(" · "))}</span>
        <ha-icon icon="${open ? "mdi:chevron-down" : "mdi:chevron-right"}"></ha-icon></button>${body}</section>`;
  }

  _row(z, viewed, modeName, previewing) {
    const a = z.attr;
    const pick = (a.by_mode || {})[viewed] || { behaviour: z.state, params: a.params, source: a.source };
    const beh = pick.behaviour;
    const begins = (a.addons || []).filter((x) => x.type === "lights" && x.enabled !== false && (x.trigger?.mode_begins || []).includes(viewed));
    const off = pick.source === "disabled";
    const what = off ? "Off — SB Modes leaves this room alone" : this._describe(beh, pick.params) + begins.map((x) => ` · when ${modeName} begins: ${x.name}`).join("");
    const held = !previewing && String(a.status || "").startsWith("held");
    // live: "Vacant since 12:12 PM · lights off" — occupancy first, then what the behaviour is doing
    const occ = a.occupancy_state === "occupied" ? `Occupied since ${hhmm(a.occupancy_since)}`
      : a.occupancy_state === "vacant" ? `Vacant since ${hhmm(a.occupancy_since)}`
      : a.occupancy_state === "unknown" ? "Occupancy unknown (sensor unavailable)" : "";
    const rest = off ? "room switched off" : held ? a.status : beh === "hands_off" ? `not controlled in ${modeName}`
      : String(a.status || "").replace(/^(vacant|occupied)(;\s*|$)/, "").replace(/^overlay:\s*/, "");
    const doing = previewing ? what : [occ, fmtTimes(rest)].filter(Boolean).join(" · ");
    const l = previewing ? { icon: look(beh, "").icon, tint: "idle" } : look(beh, a.status);
    const open = this._isOpen(z.id);
    const src = off ? "switched off in the editor" : pick.source === "override" ? "this zone's own setting for this mode" : pick.source === "zone" ? "this room's default rule" : "no rule";
    const ent = (eid, kind) => {
      const st = this._hass.states[eid];
      const name = st?.attributes.friendly_name || eid;
      const s = st?.state ?? "missing";
      const pct = s === "on" && st?.attributes.brightness != null ? ` ${Math.round(st.attributes.brightness * 100 / 255)}%` : "";
      const watching = eid.startsWith("media_player.") && ["playing", "paused", "idle", "buffering", "on"].includes(s);
      const cls = s === "on" || watching ? (kind === "occ" ? "occ" : "on") : ["unavailable", "unknown", "missing"].includes(s) && !eid.startsWith("media_player.") ? "na" : "";
      return `<button class="ent ${cls}" data-info="${esc(eid)}" title="${esc(eid)} — tap for its controls"><i></i>${esc(name)}${esc(pct)}${cls === "na" ? ` (${esc(s)})` : ""}</button>`;
    };
    const detail = open ? `<div class="detail">
        <div><b>In ${esc(modeName)}</b> ${esc(what)} <span class="src">(${esc(src)})</span></div>
        <div><b>Areas</b> ${esc((a.areas || []).join(", "))}</div>
        ${(a.addons || []).map((x) => this._addonLine(x, (a.addon_status || {})[x.id])).join("")}
        ${(a.occupancy || []).length ? `<div><b>Occupancy</b> ${a.occupancy.map((e) => ent(e, "occ")).join("")}</div>` : ""}
        ${(a.lights || []).length ? `<div><b>Lights</b> ${a.lights.map((e) => ent(e, "light")).join("")}</div>` : ""}
        ${previewing ? "" : this._recentHtml(z)}
      </div>` : "";
    // live view only: lights changed by hand, and the hold / release menu
    const ov = previewing ? {} : (a.overrides || {});
    const ovName = (e) => this._hass.states[e]?.attributes.friendly_name || e;
    const ovNames = Object.keys(ov).filter((e) => !ov[e].off).map(ovName);
    const offNames = Object.keys(ov).filter((e) => ov[e].off && ov[e].until_back).map(ovName);
    const offEmpty = Object.keys(ov).filter((e) => ov[e].off && !ov[e].until_back).map(ovName);
    // the same delay SB Modes uses (bookkeeping.release_delay): delay / vacant_after / off-when-empty (capped)
    const pp = pick.params || {};
    let rel = pp.delay ?? pp.vacant_after_minutes;
    if (rel == null && pp.off_after_seconds != null) rel = Math.min(pp.off_after_seconds / 60, pp.max_on_minutes || Infinity);
    rel = rel ?? 10;
    const relTxt = rel < 1 ? `${Math.round(rel * 60)} s` : `${Math.round(rel * 10) / 10} min`;
    const third = [
      ovNames.length ? `Changed by hand: ${ovNames.join(", ")} · left alone until ${z.name} has been empty ${relTxt} or the mode changes` : "",
      offEmpty.length ? `Turned off by hand: ${offEmpty.join(", ")} · left alone until ${z.name} has been empty ${relTxt} or the mode changes` : "",
      offNames.length ? `Turned off by hand: ${offNames.join(", ")} · stays off until someone comes back after ${relTxt} empty, or the mode changes` : "",
    ].filter(Boolean).map((t) => `<span class="sbk-m warn">${esc(t)}</span>`).join("");
    return `<div class="sbk-row ${previewing ? "pv" : ""}" data-z="${esc(z.id)}">
        <button class="sbk-open" aria-expanded="${open}" title="${open ? "Hide" : "Show"} what ${esc(z.name)} holds">
          <span class="sbk-ico ${l.tint}"><ha-icon icon="${esc(l.icon)}"></ha-icon></span>
          <span class="sbk-txt"><span class="sbk-t">${esc(z.name)}</span>
            <span class="sbk-s"><span class="sbk-chip" title="${esc(src)}">${esc(BEHAVIOUR_NAMES[beh] || beh)}</span>${esc(doing)}</span>${third}</span>
        </button>
        ${previewing ? "" : `<button class="sbk-kb zkb ${this._dlg === z.id ? "on" : ""}" aria-label="Hold and lights: ${esc(z.name)}" title="Hold and lights: ${esc(z.name)}" aria-haspopup="dialog">${kit().kbIcon}</button>`}
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
    root.querySelectorAll("button.zt").forEach((b) => b.addEventListener("click", (e) => {
      const k = e.currentTarget.dataset.x;
      if (k === "__zones") foldSet(this._zonesKey(), this._zonesOpen());          // open by default; remembered
      else this._toggleOpen(k);
      this._render();
    }));
    root.querySelectorAll(".actf .actc").forEach((b) => b.addEventListener("click", () => { this._actZone = b.dataset.f; this._render(); }));
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
    root.querySelectorAll(".sbk-row .zkb, .rt-r .zkb").forEach((b) => b.addEventListener("click", () => {
      this._openDialog(b.closest("[data-z]").dataset.z);
    }));
    root.querySelectorAll(".sbk-menu button").forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      this._setMenu(null);
      if (act === "page" || act === "edit") {
        navigate(act === "edit" ? "/sb-modes/house" : "/sb-modes");
        return;
      }
    }));
    root.querySelectorAll("[data-info]").forEach((b) => b.addEventListener("click", () => fire(this, "hass-more-info", { entityId: b.dataset.info })));
    root.querySelectorAll("[data-release-zone]").forEach((b) => b.addEventListener("click", async () => {
      const zone = b.dataset.releaseZone;
      b.disabled = true;
      try { await this._hass.callService("sb_modes", "release_zone", { zone }); this._msg = ""; }
      catch (e) { this._msg = `Could not release ${zone}: ${e?.message || e}`; }
      this._sig = ""; this._render();
    }));
    root.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
      const [table, key] = b.dataset.sort.split("|"); this._setSort(table, key); this._render();
    }));
    root.querySelectorAll("[data-x-open]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.xOpen;
      if (!this._isOpen(k)) this._toggleOpen(k);
      this._render();
      this.shadowRoot.querySelector(`[data-x="${k}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" });
    }));
    this._wireRows(root);
    this._renderDialog();
  }

  // The room dialog (the row's ⋮): hold the room, then set its lights live. Picking a hold applies it at
  // once; the light controls work only while the room is held. Cancel puts the lights and the hold back
  // as they were when the dialog opened; Set keeps them.
  _openDialog(zid) {
    const z = this._zones().find((x) => x.id === zid);
    if (!z) return;
    const lights = {};
    for (const e of z.attr.lights || []) {
      const st = this._hass.states[e];
      if (st?.state === "off") lights[e] = { state: "off" };
      else if (st?.state === "on") lights[e] = st.attributes.brightness != null ? { brightness_pct: Math.max(1, Math.round(st.attributes.brightness * 100 / 255)) } : { state: "on" };
    }
    const h = z.attr.hold;
    const mins = h?.hold === "timed" ? Math.round((Date.parse(h.until) - Date.parse(h.since)) / 60000) : 15;
    this._dlg = zid;
    this._dlgEntry = { hold: h ? { ...h } : null, lights };
    this._dlgTouched = false;
    this._dlgMin = HOLD_MINUTES.reduce((b, m) => (Math.abs(m - mins) < Math.abs(b - mins) ? m : b), 15);
    this._msg = "";
    this._renderDialog();
  }

  async _dlgCall(svc, data, what) {
    try { await this._hass.callService("sb_modes", svc, { zone: this._dlg, ...data }); this._msg = ""; return true; }
    catch (e) { this._msg = `Could not ${what}: ${e?.message || e}`; return false; }
    finally { this._sig = ""; this._render(); }
  }

  async _dlgCancel() {
    const dlg = this.shadowRoot.querySelector("dialog.lights");
    const entry = this._dlgEntry, h = entry?.hold;
    let back = null;                                     // the hold to put back, null = not held
    if (h?.hold === "timed") { const left = Math.ceil((Date.parse(h.until) - Date.now()) / 60000); back = left > 0 ? left : null; }
    else if (h) back = h.hold;
    if (this._dlgTouched && Object.keys(entry.lights).length) await this._dlgCall("set_lights", { lights: entry.lights, hold: back ?? "next" }, "put the lights back");
    else if (back != null) await this._dlgCall("hold_zone", { hold: back }, "put the hold back");
    if (back == null && this._zones().find((x) => x.id === this._dlg)?.attr.hold) await this._dlgCall("release_zone", {}, "release");
    if (!this._msg) dlg.close();
  }

  _renderDialog() {
    const dlg = this.shadowRoot.querySelector("dialog.lights");
    const z = this._dlg && this._zones().find((x) => x.id === this._dlg);
    if (!z) { if (dlg.open) dlg.close(); return; }
    if (!dlg._wired) {
      dlg._wired = true;
      dlg.addEventListener("close", () => { this._dlg = null; this._sliding = false; this._sig = ""; this._render(); });
      dlg.addEventListener("cancel", (e) => { e.preventDefault(); this._dlgCancel(); });   // Esc / back = Cancel
    }
    const a = z.attr, st = (e) => this._hass.states[e];
    const h = a.hold, held = !!h, byHand = Object.keys(a.overrides || {}).length > 0;
    const kind = !h ? "" : h.hold === "timed" ? "for" : h.hold === "paused" ? "paused" : "next";
    const sel = this._select(), modeName = sel ? this._modeName(sel.state) : "the mode";
    const dims = a.dimmable || (a.lights || []).filter((e) => (st(e)?.attributes.supported_color_modes || []).some((m) => m !== "onoff"));
    const plain = (a.lights || []).filter((e) => !dims.includes(e));
    const on = dims.map(st).filter((x) => x?.state === "on").map((x) => x.attributes.brightness ?? 255);
    const lvl = on.length ? Math.round(on.reduce((t, x) => t + x, 0) / on.length * 100 / 255) : 0;
    const until = h?.hold === "timed" ? ` (until ${new Date(h.until).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })})` : "";
    const radio = (v, label) => `<label class="opt"><input type="radio" name="hold" value="${v}" ${kind === v ? "checked" : ""}> ${label}</label>`;
    const dis = (ok) => (ok ? "" : "disabled");
    dlg.innerHTML = `<div class="dlg"><h3>${esc(z.name)} Hold</h3>
      <div class="sect"><b>Hold</b></div>
      <div class="opts">
        <div class="opt">${radio("for", "For")}<select data-min aria-label="Minutes">${HOLD_MINUTES.map((m) => `<option ${m === this._dlgMin ? "selected" : ""}>${m}</option>`).join("")}</select> minutes${kind === "for" ? esc(until) : ""}</div>
        ${radio("paused", "Until released")}
        ${radio("next", "Until mode change")}
      </div>
      <div class="chips"><button class="sw" data-release ${dis(held || byHand)}>Release to ${esc(modeName)}</button></div>
      <div class="sect"><b>Light settings</b> <span class="lh">${held ? "live — changes apply now" : "hold the room first"}</span></div>
      <div class="lvl ${held && dims.length ? "" : "off"}"><b>Dimmable</b>
        <input type="range" min="0" max="100" step="5" value="${lvl}" data-lvl aria-label="${esc(z.name)} dimmable level" ${dis(held && dims.length)}>
        <span class="lv">${dims.length ? `${lvl}%` : "none"}</span></div>
      <div class="lvl ${held && plain.length ? "" : "off"}"><b>On/off</b>
        <button class="sw" data-sw="off" ${dis(held && plain.length)}>All off</button><button class="sw" data-sw="on" ${dis(held && plain.length)}>All on</button>
        <span class="lv">${plain.length ? `${plain.filter((e) => st(e)?.state === "on").length} of ${plain.length} on` : "none"}</span></div>
      ${this._msg ? `<p class="err">${esc(this._msg)}</p>` : ""}
      <div class="dlg-foot"><button class="sw" data-cancel>Cancel</button><button class="sw sel" data-set autofocus>Set</button></div></div>`;
    const holdNow = (v) => this._dlgCall("hold_zone", { hold: v === "for" ? this._dlgMin : v }, `hold ${z.name}`);
    dlg.querySelectorAll("input[name=hold]").forEach((r) => r.addEventListener("change", () => holdNow(r.value)));
    const minSel = dlg.querySelector("select[data-min]");
    minSel.addEventListener("focus", () => { this._sliding = true; });
    minSel.addEventListener("blur", () => { this._sliding = false; });
    minSel.addEventListener("change", () => {
      this._dlgMin = Number(minSel.value); this._sliding = false;
      if (kind === "for" || dlg.querySelector("input[value=for]").checked) holdNow("for");
    });
    dlg.querySelector("[data-release]").addEventListener("click", () => this._dlgCall("release_zone", {}, `release ${z.name}`));
    dlg.querySelector("[data-cancel]").addEventListener("click", () => this._dlgCancel());
    dlg.querySelector("[data-set]").addEventListener("click", () => dlg.close());
    const r = dlg.querySelector("input[data-lvl]"), shown = r.parentElement.querySelector(".lv");
    r.addEventListener("input", () => { this._sliding = true; shown.textContent = `${r.value}%`; });
    r.addEventListener("blur", () => { if (this._sliding) { this._sliding = false; this.hass = this._hass; } });
    r.addEventListener("change", async () => {
      this._dlgTouched = true; this._sliding = false;
      await this._dlgCall("set_level", { brightness_pct: Number(r.value), hold: "keep" }, `set ${z.name}`);
    });
    dlg.querySelectorAll("button[data-sw]").forEach((b) => b.addEventListener("click", () => {
      this._dlgTouched = true;
      this._dlgCall("switch_lights", { state: b.dataset.sw, hold: "keep" }, `switch ${z.name}`);
    }));
    if (!dlg.open) dlg.showModal();
  }

  _wireRows(root) {
    root.querySelectorAll(".sbk-row .sbk-open, .rt-r .rt-o").forEach((b) => b.addEventListener("click", () => {
      const id = b.closest("[data-z]").dataset.z;
      this._toggleOpen(id);
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

/** What the ? button shows: the page's words, and what the colours on the House grid mean. */
const LEGEND = `<h3>The house</h3>
  <p>The house is in one <b>mode</b> at a time (Morning, Day, Evening…), changing on a schedule per day type
  (Times). Each <b>room</b> has a <b>rule</b> in charge of its lights, and <b>add-ons</b> that act on top of it.</p>
  <h3>Rooms</h3>
  <div class="lg"><span class="chip">Off when empty</span><span>the rule in charge of the room right now</span></div>
  <div class="lg"><span class="chip warn">until 8:50 PM</span><span>someone held the room: the rule waits</span></div>
  <div class="lg"><span class="mut">Changed by hand</span><span>a light changed at the device is left alone until the room has been empty for its delay</span></div>
  <div class="lg"><span class="mut">Switched off</span><span>SB Modes leaves the room alone in every mode</span></div>
  <h3>House grid</h3>
  <div class="lg"><span class="difr">Nightlight, then dark</span><span>the room's own rule in that mode; a blank square means it uses its default rule</span></div>
  <div class="lg"><span class="mk clk">🕒 7:30 PM</span><span>an add-on at a time of day or sunrise / sunset</span></div>
  <div class="lg"><span class="mk beg">▸ when it begins</span><span>an add-on that runs once as that mode begins</span></div>
  <div class="lg"><span class="mk on">● throughout</span><span>an add-on active the whole time in those modes</span></div>
  <div class="lg"><span class="mk dev">⚡ device</span><span>an add-on triggered by a device's state</span></div>
  <h3>Editing</h3>
  <p>Changes go into a draft: nothing reaches the house until you review and save it, and every save can be undone.
  The bar at the bottom shows while there are unsaved changes.</p>`;

class SbModesPanel extends HTMLElement {
  set hass(h) {
    this._hass = h; this._build(); this._card.hass = h; this._menu.hass = h;
    if (this._editor) this._editor.hass = h;
  }
  set narrow(v) { this._narrow = v; if (this._menu) this._menu.narrow = v; }
  /** /sb-modes = status; /house (or the old /edit), /times, /room/<id>, /mode/<id> = the editor
   *  (its own module, loaded on first use). Room and mode pages have a back arrow to House. */
  set route(r) { this._route = r; if (this._card) this._show(); }
  set panel(_v) {}

  _where() {
    const p = (this._route?.path || "").split("/").filter(Boolean).map(decodeURIComponent);
    if (p[0] === "room" && p[1]) return { view: "room", id: p[1] };
    if (p[0] === "mode" && p[1]) return { view: "mode", id: p[1] };
    if (p[0] === "times") return { view: "times" };
    if (p[0] === "house" || p[0] === "edit") {
      const z = new URLSearchParams(location.search).get("zone");      // old links: /sb-modes/edit?zone=…
      return z ? { view: "room", id: z } : { view: "house" };
    }
    return { view: "status" };
  }

  _build() {
    if (this._card) return;
    const root = this.attachShadow({ mode: "open" });
    const nav = `<a href="/sb-modes" data-nav="status">Status</a><a href="/sb-modes/house" data-nav="house">House</a><a href="/sb-modes/times" data-nav="times">Times</a>`;
    root.innerHTML = `<style>
      :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color); }
      .bar { display: flex; align-items: center; gap: 8px; height: var(--header-height, 56px); padding: 0 8px 0 4px; box-sizing: border-box; position: sticky; top: 0; z-index: 3;
             background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); border-bottom: 1px solid var(--divider-color); }
      .lead { display: flex; align-items: center; }
      .back, .help { width: 48px; height: 48px; display: inline-flex; align-items: center; justify-content: center; border: 0; background: none; color: inherit;
                     border-radius: 24px; cursor: pointer; text-decoration: none; }
      .back:hover, .help:hover { background: rgba(127,127,127,.2); }
      .title { flex: 1; min-width: 0; margin-left: 4px; }
      .title .t { font-size: 20px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .title .c { font-size: 12px; opacity: .7; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .title .c:empty { display: none; }
      nav { display: flex; gap: 4px; padding: 4px; border-radius: 20px; background: rgba(127,127,127,.16); }
      nav a { color: inherit; font-size: 14px; font-weight: 500; text-decoration: none; padding: 7px 16px; border-radius: 16px; opacity: .8; }
      nav a:hover { opacity: 1; background: rgba(127,127,127,.2); }
      nav a.on { opacity: 1; background: var(--primary-color); color: var(--text-primary-color, #fff); }
      nav.under { display: none; margin: 10px 12px 0; background: var(--card-background-color); color: var(--primary-text-color); }
      nav.under a { flex: 1; text-align: center; }
      .page { padding: 16px; } .col { max-width: 1000px; margin: 0 auto; }
      :host([wide]) .col { max-width: 1280px; }
      .ver { color: var(--secondary-text-color); font-size: 12px; margin: 16px 4px 0; }
      [hidden] { display: none !important; }
      dialog.legend { border: 0; padding: 0; border-radius: 16px; width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 64px);
                      background: var(--card-background-color, #fff); color: var(--primary-text-color); box-shadow: 0 12px 40px rgba(0,0,0,.45); }
      dialog.legend::backdrop { background: rgba(0,0,0,.5); }
      .lgd { padding: 16px 22px 18px; font-size: 14px; line-height: 1.5; }
      .lgd .hd { display: flex; align-items: center; }
      .lgd .hd span { flex: 1; font-size: 20px; }
      .lgd h3 { margin: 16px 0 4px; font-size: 12px; font-weight: 500; letter-spacing: .06em; text-transform: uppercase; color: var(--secondary-text-color); }
      .lgd p { margin: 4px 0; }
      .lg { display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 12px; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--divider-color); }
      .lg > :first-child { justify-self: start; max-width: 100%; }
      .chip { display: inline-flex; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; white-space: nowrap;
              background: color-mix(in srgb, var(--primary-color) 16%, transparent); color: var(--primary-color); }
      .chip.warn { background: color-mix(in srgb, var(--warning-color, #ffa600) 18%, transparent); color: var(--warning-color, #ffa600); }
      .mut { color: var(--secondary-text-color); }
      .difr { display: inline-block; padding: 3px 8px; border-radius: 6px; background: rgba(3,169,244,.12); border: 1px solid rgba(3,169,244,.45); }
      .difr small { font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase; color: var(--primary-color); }
      .mk { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 12px; white-space: nowrap; }
      .mk.beg { background: rgba(3,169,244,.12); border: 1px solid rgba(3,169,244,.45); color: var(--primary-color); }
      .mk.on { background: rgba(76,175,80,.14); border: 1px solid rgba(76,175,80,.5); color: #81c784; }
      .mk.clk { background: rgba(255,193,7,.12); border: 1px solid rgba(255,193,7,.45); color: #ffd54f; }
      .mk.dev { background: rgba(156,39,176,.14); border: 1px solid rgba(156,39,176,.5); color: #ce93d8; }
      @media (max-width: 700px) {
        .bar nav { display: none; }
        nav.under { display: flex; }
        :host([sub]) nav.under { display: none; }
        .page { padding: 10px 8px 16px; }
        .lg { grid-template-columns: 1fr; gap: 2px; }
      }
    </style>
    <div class="bar"><span class="lead"><ha-menu-button></ha-menu-button><a class="back" href="/sb-modes/house" aria-label="Back to House" hidden><ha-icon icon="mdi:arrow-left"></ha-icon></a></span>
      <div class="title"><div class="t">SB Modes</div><div class="c"></div></div>
      <nav aria-label="SB Modes pages">${nav}</nav>
      <button class="help" aria-label="Legend: what the words and colours mean" title="Legend"><ha-icon icon="mdi:help-circle-outline"></ha-icon></button></div>
    <nav class="under" aria-label="SB Modes pages">${nav}</nav>
    <div class="page"><div class="col">
      <div class="status"><div class="slot"></div></div>
      <div class="edit" hidden><div class="eslot"></div></div>
      <div class="ver">SB Modes ${VERSION}</div>
    </div></div>
    <dialog class="legend" aria-label="Legend"><div class="lgd"><div class="hd"><span>Legend</span>
      <button class="help" data-close aria-label="Close"><ha-icon icon="mdi:close"></ha-icon></button></div>${LEGEND}</div></dialog>`;
    this._menu = root.querySelector("ha-menu-button");
    this._menu.narrow = this._narrow;
    root.querySelectorAll("a[href^='/sb-modes']").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); navigate(a.getAttribute("href")); }));
    const dlg = root.querySelector("dialog.legend");
    root.querySelector(".bar .help").addEventListener("click", () => dlg.showModal());
    dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    const card = document.createElement(CARD);
    card.setConfig({ title: "Modes", panel: true });
    root.querySelector(".slot").appendChild(card);
    this._card = card;
    this._show();
  }

  _setTitle(title, crumb = "") {
    const root = this.shadowRoot;
    root.querySelector(".title .t").textContent = title;
    root.querySelector(".title .c").textContent = crumb;
  }

  async _show() {
    const root = this.shadowRoot, w = this._where(), edit = w.view !== "status", sub = w.view === "room" || w.view === "mode";
    this.toggleAttribute("wide", w.view === "house");
    this.toggleAttribute("sub", sub);
    root.querySelector(".status").hidden = edit;
    root.querySelector(".edit").hidden = !edit;
    root.querySelector("ha-menu-button").hidden = sub;
    root.querySelector(".back").hidden = !sub;
    const tab = sub ? "house" : w.view;
    root.querySelectorAll("[data-nav]").forEach((a) => { a.classList.toggle("on", a.dataset.nav === tab); a.setAttribute("aria-current", a.dataset.nav === tab ? "page" : "false"); });
    if (!sub) this._setTitle("SB Modes");
    if (!edit) return;
    if (!this._editor) {
      try {
        const mod = await import(new URL(`./sb-modes-editor.js?v=${VERSION}`, import.meta.url).href);
        mod.defineEditor();
        const ed = document.createElement("sb-modes-editor");
        ed.addEventListener("sb-modes-title", (e) => { if (this._where().view === e.detail.view) this._setTitle(e.detail.title, e.detail.crumb); });
        root.querySelector(".eslot").appendChild(ed);
        this._editor = ed;
        ed.hass = this._hass;
      } catch (e) {
        root.querySelector(".eslot").textContent = `Could not load the editor: ${e?.message || e}`;
        return;
      }
    }
    this._editor.route = w;
  }
}

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

"use strict";

const SHOW_ALL_KEY = "cozytouch.show_all";
const GROUP_BY_NAME_KEY = "cozytouch.group_by_name";

const state = {
  devices: [],          // GroupedDeviceOut, one per physical device
  selected: new Set(),  // Set of REAL device_url (never synthetic group ids)
  presets: [],
  filter: "",
  showAll: localStorage.getItem(SHOW_ALL_KEY) === "1",
  groupByName: localStorage.getItem(GROUP_BY_NAME_KEY) !== "0", // default ON
  lastAction: null,     // { command, parameters }
  pendingRefreshTimer: null,
};

// ----- HTTP -----
async function api(method, path, body) {
  const headers = body !== undefined ? { "Content-Type": "application/json" } : {};
  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  if (res.status !== 204) {
    const txt = await res.text();
    try { data = txt ? JSON.parse(txt) : null; } catch { data = txt; }
  }
  if (!res.ok) {
    const detail = (data && data.detail) || `HTTP ${res.status}`;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ----- toast -----
let toastTimer = null;
function toast(msg, kind = "") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.classList.remove("hidden");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 4500);
}

function setStatus(text) {
  const el = document.getElementById("status-line");
  if (el) el.textContent = text;
}

// ----- helpers -----
const CATEGORY_LABEL = {
  heater: "radiateur",
  pod: "box",
  gateway: "passerelle",
  sensor: "capteur",
  other: "autre",
};

function fmtTemp(v) {
  if (v == null) return null;
  return `${Number.isInteger(v) ? v : v.toFixed(1)}°`;
}

function fmtKwh(wh) {
  if (wh == null) return null;
  return `${(wh / 1000).toFixed(0)} kWh`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s).replace(/'/g, "&#39;"); }

function shortUrl(url) {
  if (!url) return "";
  return url.length > 32 ? url.slice(0, 16) + "…" + url.slice(-12) : url;
}

function allSame(arr) {
  const f = arr.filter((v) => v != null);
  if (!f.length) return false;
  return f.every((v) => v === f[0]);
}

// ----- view computation (group-by-name + filter + show-all) -----
function makeSyntheticGroup(name, devs) {
  const setpoints = devs.map((d) => d.setpoint).filter((v) => v != null);
  const minSp = setpoints.length ? Math.min(...setpoints) : null;
  const maxSp = setpoints.length ? Math.max(...setpoints) : null;
  const temps = devs.map((d) => d.sensors && d.sensors.room_temperature).filter((v) => v != null);
  const tempAvg = temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : null;
  const powers = devs.map((d) => d.power_w).filter((v) => v != null);
  const energies = devs.map((d) => d.sensors && d.sensors.energy_consumption_wh).filter((v) => v != null);
  const windowOpen = devs.some((d) => d.sensors && d.sensors.window_contact === "open");
  const anyWindowKnown = devs.some((d) => d.sensors && d.sensors.window_contact != null);
  const occupied = devs.some((d) => d.sensors && d.sensors.occupancy && d.sensors.occupancy !== "noPersonInside");
  const anyOccKnown = devs.some((d) => d.sensors && d.sensors.occupancy != null);

  return {
    __group: true,
    members: devs.map((d) => d.device_url),
    device_url: `__group__:${name}`, // synthetic, only for keying / DOM data attrs
    base_url: `__group__:${name}`,
    label: name,
    place_name: name,
    short_id: `${devs.length} radiateurs`,
    category: "heater",
    controllable_name: devs[0].controllable_name,
    ui_class: devs[0].ui_class,
    widget: devs[0].widget,
    supports_setpoint: devs.every((d) => d.supports_setpoint),
    supports_heating_level: devs.every((d) => d.supports_heating_level),
    setpoint: minSp != null && maxSp != null && minSp === maxSp ? minSp : null,
    setpointRange: minSp != null && maxSp != null && minSp !== maxSp ? [minSp, maxSp] : null,
    heating_level: allSame(devs.map((d) => d.heating_level)) ? devs[0].heating_level : null,
    operating_mode: allSame(devs.map((d) => d.operating_mode)) ? devs[0].operating_mode : null,
    on_off: allSame(devs.map((d) => d.on_off)) ? devs[0].on_off : null,
    power_w: powers.length ? powers.reduce((a, b) => a + b, 0) : null,
    model: allSame(devs.map((d) => d.model)) ? devs[0].model : null,
    sensors: {
      room_temperature: tempAvg,
      window_contact: anyWindowKnown ? (windowOpen ? "open" : "closed") : null,
      occupancy: anyOccKnown ? (occupied ? "active" : "noPersonInside") : null,
      energy_consumption_wh: energies.length ? energies.reduce((a, b) => a + b, 0) : null,
    },
  };
}

function viewItems() {
  let pool = state.devices;
  if (!state.showAll) pool = pool.filter((d) => d.category === "heater");
  if (state.filter) {
    const f = state.filter.toLowerCase();
    pool = pool.filter((d) =>
      [d.place_name, d.label, d.short_id, d.controllable_name, d.ui_class, d.device_url]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(f))
    );
  }
  if (!state.groupByName) return pool;

  // Group by ROOM (place_name) — not by label, because Atlantic devices share
  // a default label of "Radiateur" so a label-based grouping would fuse all
  // radiators of the house into a single mega-group.
  const heaters = pool.filter((d) => d.category === "heater");
  const others = pool.filter((d) => d.category !== "heater");
  const byPlace = new Map();
  const orphans = []; // heaters with no place_name → kept individual
  for (const h of heaters) {
    const room = (h.place_name || "").trim();
    if (!room) {
      orphans.push(h);
      continue;
    }
    if (!byPlace.has(room)) byPlace.set(room, []);
    byPlace.get(room).push(h);
  }
  const result = [];
  for (const [name, devs] of byPlace) {
    if (devs.length === 1) result.push(devs[0]);
    else result.push(makeSyntheticGroup(name, devs));
  }
  result.push(...orphans);
  result.push(...others);
  return result;
}

// ----- selection helpers -----
function isItemSelected(item) {
  if (item.__group) return item.members.every((u) => state.selected.has(u));
  return state.selected.has(item.device_url);
}

function isItemPartiallySelected(item) {
  if (!item.__group) return false;
  const sel = item.members.filter((u) => state.selected.has(u)).length;
  return sel > 0 && sel < item.members.length;
}

function toggleItemSelected(item, checked) {
  const urls = item.__group ? item.members : [item.device_url];
  if (checked) urls.forEach((u) => state.selected.add(u));
  else urls.forEach((u) => state.selected.delete(u));
}

function selectedRealUrls() {
  return [...state.selected];
}

// ----- rendering -----
function render() {
  renderDevices();
  renderPresets();
  updateSelectionCount();
}

function updateSelectionCount() {
  document.getElementById("selection-count").textContent =
    `${state.selected.size} sélectionné(s)`;
}

function deviceCardHTML(d) {
  const checked = isItemSelected(d) ? "checked" : "";
  const partial = isItemPartiallySelected(d);
  const sel = isItemSelected(d) ? "selected" : "";
  const partialAttr = partial ? "data-partial='1'" : "";

  // Prefer the room name (e.g. 'Salle à Manger') over the device label
  // (which is often the manufacturer default 'Radiateur').
  const displayName = d.place_name || d.label || "(sans nom)";
  const subtitle = d.place_name && d.label && d.label !== d.place_name && !d.__group
    ? ` <span class="muted small">[${escapeHtml(d.label)}]</span>` : "";
  const labelLine = `${escapeHtml(displayName)}${subtitle}${
    d.short_id ? ` · <span class="muted">${escapeHtml(d.short_id)}</span>` : ""
  }`;

  const stateLines = [];
  const measure = fmtTemp(d.sensors && d.sensors.room_temperature);
  let setpointTxt = null;
  if (d.setpointRange) setpointTxt = `${fmtTemp(d.setpointRange[0])} → ${fmtTemp(d.setpointRange[1])}`;
  else if (d.setpoint != null) setpointTxt = fmtTemp(d.setpoint);
  if (measure || setpointTxt) {
    const m = measure ? `mesure <b>${measure}</b>` : "";
    const s = setpointTxt ? `consigne <b>${setpointTxt}</b>` : "";
    stateLines.push([m, s].filter(Boolean).join(" · "));
  }
  const modeBits = [];
  if (d.heating_level) modeBits.push(`niveau <b>${escapeHtml(d.heating_level)}</b>`);
  else if (d.__group && d.supports_heating_level) modeBits.push(`niveaux <b class="muted">mixtes</b>`);
  if (d.operating_mode && d.operating_mode !== "basic")
    modeBits.push(`mode <b>${escapeHtml(d.operating_mode)}</b>`);
  if (d.on_off && d.on_off !== "on") modeBits.push(`<b>${escapeHtml(d.on_off)}</b>`);
  if (modeBits.length) stateLines.push(modeBits.join(" · "));

  const sensorBits = [];
  if (d.sensors && d.sensors.window_contact)
    sensorBits.push(
      `fenêtre <b>${d.sensors.window_contact === "closed" ? "fermée" : "ouverte"}</b>`
    );
  if (d.sensors && d.sensors.occupancy)
    sensorBits.push(
      `présence <b>${d.sensors.occupancy === "noPersonInside" ? "absent" : "présent"}</b>`
    );
  if (d.power_w != null) sensorBits.push(`puissance <b>${d.power_w} W</b>`);
  if (d.sensors && d.sensors.energy_consumption_wh != null)
    sensorBits.push(`conso <b>${fmtKwh(d.sensors.energy_consumption_wh)}</b>`);
  if (sensorBits.length) stateLines.push(sensorBits.join(" · "));

  const stateHTML = stateLines.length
    ? stateLines.map((l) => `<div>${l}</div>`).join("")
    : `<span class="muted">—</span>`;

  const tags = [];
  if (d.__group) tags.push(`<span class="tag tag-group" title="Groupe par nom : commandes appliquées aux ${d.members.length} radiateurs">groupe</span>`);
  tags.push(`<span class="tag tag-${d.category}">${CATEGORY_LABEL[d.category] || d.category}</span>`);
  if (d.category === "heater" && !d.supports_setpoint)
    tags.push(`<span class="tag tag-warn" title="Pas de consigne réglable, seulement niveaux (eco/comfort/…)">niveaux uniquement</span>`);

  const subdeviceHint = d.__group
    ? `<div class="meta small">${d.members.map((u) => escapeHtml(shortUrl(u))).join(" · ")}</div>`
    : `<div class="url">${escapeHtml(d.device_url)}</div>`;

  return `
    <div class="device ${sel}" ${partialAttr} data-key="${escapeAttr(d.device_url)}">
      <div class="row1">
        <input type="checkbox" class="dev-check" ${checked}>
        <span class="label">${labelLine}</span>
        <span class="tags">${tags.join("")}</span>
      </div>
      <div class="meta">${escapeHtml(d.controllable_name || "")}${
        d.model ? ` · ${escapeHtml(d.model)}` : ""
      }</div>
      <div class="states">${stateHTML}</div>
      ${subdeviceHint}
    </div>
  `;
}

function renderDevices() {
  const container = document.getElementById("devices-list");
  const items = viewItems();
  if (items.length === 0) {
    container.innerHTML = `<div class="muted">Aucun équipement à afficher.</div>`;
    return;
  }
  container.innerHTML = items.map(deviceCardHTML).join("");
  // Apply indeterminate state on partials (HTML attribute can't do it)
  container.querySelectorAll(".device").forEach((el, idx) => {
    const item = items[idx];
    const cb = el.querySelector(".dev-check");
    cb.indeterminate = isItemPartiallySelected(item);
    cb.addEventListener("change", (e) => {
      toggleItemSelected(item, e.target.checked);
      renderDevices(); // re-render to refresh checked state of related items
      updateSelectionCount();
    });
  });
}

function renderPresets() {
  const container = document.getElementById("presets-list");
  if (!state.presets.length) {
    container.innerHTML = `<div class="muted">Aucun preset enregistré.</div>`;
    return;
  }
  const origin = window.location.origin;
  const labelByUrl = new Map(
    state.devices.map((d) => [d.device_url, `${d.label || "?"}${d.short_id ? ` (${d.short_id})` : ""}`])
  );
  container.innerHTML = state.presets.map((p) => {
    const url = `${origin}/webhooks/${p.webhook_token}/run`;
    const summary = (p.actions || []).map((a) => {
      const friendly = labelByUrl.get(a.device_url) || shortUrl(a.device_url);
      return `${friendly} → ${a.command}(${JSON.stringify(a.parameters || [])})`;
    }).join("\n");
    return `
      <div class="preset" data-id="${escapeAttr(p.id)}">
        <div class="head">
          <div>
            <div class="name">${escapeHtml(p.name)}</div>
            <div class="desc">${escapeHtml(p.description || "")}</div>
          </div>
          <div>
            <button class="primary preset-run">Run</button>
            <button class="ghost preset-rotate" title="Régénérer le webhook">↻</button>
            <button class="danger preset-del">Supprimer</button>
          </div>
        </div>
        <div class="actions-list">${escapeHtml(summary || "(aucune action)")}</div>
        <div class="webhook">
          <span class="muted small">webhook :</span>
          <input type="text" readonly value="${escapeAttr(url)}">
          <button class="ghost preset-copy">copier</button>
        </div>
      </div>
    `;
  }).join("");

  container.querySelectorAll(".preset").forEach((el) => {
    const id = el.dataset.id;
    el.querySelector(".preset-run").addEventListener("click", () => runPreset(id));
    el.querySelector(".preset-del").addEventListener("click", () => deletePreset(id));
    el.querySelector(".preset-rotate").addEventListener("click", () => rotateWebhook(id));
    el.querySelector(".preset-copy").addEventListener("click", () => {
      const input = el.querySelector(".webhook input");
      navigator.clipboard.writeText(input.value).then(
        () => toast("URL copiée", "success"),
        () => toast("Copie échouée", "error")
      );
    });
  });
}

// ----- data loading -----
async function loadDevices({ forceRefresh = false } = {}) {
  setStatus(forceRefresh ? "Re-fetch depuis Cozytouch…" : "Chargement…");
  try {
    const path = forceRefresh ? "/devices/grouped?refresh=true" : "/devices/grouped";
    state.devices = await api("GET", path);
    state.selected = new Set(
      [...state.selected].filter((u) => state.devices.some((d) => d.device_url === u))
    );
    const heaterCount = state.devices.filter((d) => d.category === "heater").length;
    setStatus(`${heaterCount} radiateur(s) · ${state.devices.length} entrée(s)`);
    render();
  } catch (e) {
    setStatus(`Erreur : ${e.message}`);
    toast(e.message, "error");
  }
}

async function loadPresets() {
  try {
    state.presets = await api("GET", "/presets");
    render();
  } catch (e) {
    toast(`Erreur presets : ${e.message}`, "error");
  }
}

async function refreshStates() {
  try {
    await api("POST", "/devices/refresh");
    toast("Refresh demandé · les états se mettent à jour côté Cozytouch", "success");
    setTimeout(() => loadDevices({ forceRefresh: true }), 3000);
  } catch (e) {
    toast(`Refresh échoué : ${e.message}`, "error");
  }
}

// ----- actions -----
function buildActions(command, parameters) {
  const urls = selectedRealUrls();
  return urls.map((device_url) => ({ device_url, command, parameters }));
}

function applyOptimisticUpdate(actions) {
  // Reflect the change locally so the UI updates instantly.
  // Server state will be re-fetched a few seconds later anyway.
  if (!actions.length) return;
  const setUrls = new Set(actions.map((a) => a.device_url));
  for (const d of state.devices) {
    if (!setUrls.has(d.device_url)) continue;
    for (const a of actions) {
      if (a.device_url !== d.device_url) continue;
      if (a.command === "setTargetTemperature" && Array.isArray(a.parameters) && a.parameters.length) {
        const v = parseFloat(a.parameters[0]);
        if (!Number.isNaN(v)) d.setpoint = v;
      }
      if (a.command === "setHeatingLevel" && Array.isArray(a.parameters) && a.parameters.length) {
        d.heating_level = String(a.parameters[0]);
      }
    }
  }
}

function scheduleServerRefresh(delayMs = 3000) {
  if (state.pendingRefreshTimer) clearTimeout(state.pendingRefreshTimer);
  state.pendingRefreshTimer = setTimeout(() => {
    state.pendingRefreshTimer = null;
    loadDevices({ forceRefresh: true });
  }, delayMs);
}

async function runBatch(command, parameters, label) {
  const actions = buildActions(command, parameters);
  if (!actions.length) {
    toast("Aucun équipement sélectionné", "error");
    return;
  }
  state.lastAction = { command, parameters };
  const resBox = document.getElementById("action-result");
  resBox.classList.remove("hidden");
  resBox.textContent = `Envoi vers ${actions.length} équipement(s)…`;
  try {
    const out = await api("POST", "/devices/commands/batch", {
      actions, label, stop_on_error: false,
    });
    resBox.textContent = JSON.stringify(out, null, 2);
    const okN = out.results.filter((r) => r.ok).length;
    const koN = out.results.length - okN;
    toast(`${okN} OK · ${koN} KO sur ${out.results.length} équipement(s)`, koN === 0 ? "success" : "error");
    if (okN > 0) {
      // Optimistic local update so the consigne shows up immediately
      const okActions = out.results.filter((r) => r.ok).map((r) => ({
        device_url: r.device_url,
        command: r.command,
        parameters: r.parameters,
      }));
      applyOptimisticUpdate(okActions);
      renderDevices();
      // Real refresh from Atlantic (laisse le temps au cloud de propager)
      scheduleServerRefresh(3500);
    }
  } catch (e) {
    resBox.textContent = `Erreur : ${e.message}`;
    toast(e.message, "error");
  }
}

async function runPreset(id) {
  try {
    const out = await api("POST", `/presets/${id}/run`);
    const okN = out.results.filter((r) => r.ok).length;
    const koN = out.results.length - okN;
    toast(`Preset exécuté · ${okN} OK · ${koN} KO`, koN === 0 ? "success" : "error");
    if (okN > 0) {
      const okActions = out.results.filter((r) => r.ok).map((r) => ({
        device_url: r.device_url,
        command: r.command,
        parameters: r.parameters,
      }));
      applyOptimisticUpdate(okActions);
      renderDevices();
      scheduleServerRefresh(3500);
    }
  } catch (e) {
    toast(`Preset KO : ${e.message}`, "error");
  }
}

async function deletePreset(id) {
  if (!confirm("Supprimer ce preset ?")) return;
  try {
    await api("DELETE", `/presets/${id}`);
    await loadPresets();
    toast("Preset supprimé", "success");
  } catch (e) {
    toast(`Suppression KO : ${e.message}`, "error");
  }
}

async function rotateWebhook(id) {
  if (!confirm("Régénérer le webhook ? L'ancienne URL ne fonctionnera plus.")) return;
  try {
    await api("POST", `/presets/${id}/rotate-webhook`);
    await loadPresets();
    toast("Webhook régénéré", "success");
  } catch (e) {
    toast(`Rotate KO : ${e.message}`, "error");
  }
}

async function saveAsPreset() {
  const name = document.getElementById("preset-name").value.trim();
  if (!name) { toast("Nom du preset requis", "error"); return; }
  if (!state.lastAction) { toast("Choisis d'abord une action (consigne / raccourci / commande perso)", "error"); return; }
  if (state.selected.size === 0) { toast("Aucun équipement sélectionné", "error"); return; }
  const actions = buildActions(state.lastAction.command, state.lastAction.parameters);
  try {
    await api("POST", "/presets", { name, description: null, actions });
    document.getElementById("preset-name").value = "";
    await loadPresets();
    toast(`Preset « ${name} » créé (${actions.length} action(s))`, "success");
  } catch (e) {
    toast(`Création KO : ${e.message}`, "error");
  }
}

// ----- wiring -----
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("reload").addEventListener("click", () => {
    loadDevices({ forceRefresh: true });
    loadPresets();
  });
  document.getElementById("refresh-states").addEventListener("click", refreshStates);

  document.getElementById("device-filter").addEventListener("input", (e) => {
    state.filter = e.target.value;
    renderDevices();
  });

  const showAllEl = document.getElementById("show-all");
  showAllEl.checked = state.showAll;
  showAllEl.addEventListener("change", (e) => {
    state.showAll = e.target.checked;
    localStorage.setItem(SHOW_ALL_KEY, state.showAll ? "1" : "0");
    renderDevices();
  });

  const groupEl = document.getElementById("group-by-name");
  groupEl.checked = state.groupByName;
  groupEl.addEventListener("change", (e) => {
    state.groupByName = e.target.checked;
    localStorage.setItem(GROUP_BY_NAME_KEY, state.groupByName ? "1" : "0");
    renderDevices();
  });

  document.getElementById("select-all").addEventListener("change", (e) => {
    const items = viewItems();
    if (e.target.checked) items.forEach((it) => toggleItemSelected(it, true));
    else items.forEach((it) => toggleItemSelected(it, false));
    renderDevices();
    updateSelectionCount();
  });

  // Quick actions
  document.getElementById("apply-temp").addEventListener("click", () => {
    const v = parseFloat(document.getElementById("temp-input").value);
    if (Number.isNaN(v)) { toast("Consigne invalide", "error"); return; }
    runBatch("setTargetTemperature", [v], `cozytouch-ui:temp:${v}`);
  });
  document.querySelectorAll(".quick[data-temp]").forEach((b) => {
    b.addEventListener("click", () => {
      const v = parseFloat(b.dataset.temp);
      document.getElementById("temp-input").value = v;
      runBatch("setTargetTemperature", [v], `cozytouch-ui:quick:${v}`);
    });
  });
  document.getElementById("frost-mode").addEventListener("click", () => {
    runBatch("setHeatingLevel", ["frostprotection"], "cozytouch-ui:frost");
  });
  document.getElementById("apply-custom").addEventListener("click", () => {
    const cmd = document.getElementById("custom-command").value.trim();
    const paramsStr = document.getElementById("custom-params").value.trim();
    if (!cmd) { toast("Commande requise", "error"); return; }
    let params = [];
    if (paramsStr) {
      try { params = JSON.parse(paramsStr); }
      catch { toast("Paramètres : JSON invalide (ex: [21.0] ou [\"frostprotection\"])", "error"); return; }
      if (!Array.isArray(params)) { toast("Les paramètres doivent être un tableau JSON", "error"); return; }
    }
    runBatch(cmd, params, `cozytouch-ui:custom:${cmd}`);
  });

  document.getElementById("save-as-preset").addEventListener("click", saveAsPreset);

  loadDevices();
  loadPresets();
});

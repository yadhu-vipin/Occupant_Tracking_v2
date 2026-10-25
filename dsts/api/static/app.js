"use strict";


/* ================================================================
   STATE
   ================================================================ */

const state = {
  layout: null,

  occupancy: {},

  heatmap: {},

  security: [],

  activity: [],

  ws: null,

  connected: false,

  eventsProcessed: 0,

  busy: false,

  activeToken: null,

  persona: "dean",

  tokens: {},
  principals: {}
};


/* ================================================================
   DOM HELPERS
   ================================================================ */

function $(selector) {
  return document.querySelector(selector);
}


function $all(selector) {
  return Array.from(document.querySelectorAll(selector));
}


function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


/* ================================================================
   API HELPER
   ================================================================ */

const PERSONAS = ["dean", "teacher", "student"];


async function loginPersona(persona) {

  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ persona })
  });

  if (!response.ok) {

    let detail = `Sign-in failed (${response.status})`;

    try {
      detail = (await response.json()).detail || detail;
    } catch (error) {
      /* keep default message */
    }

    throw new Error(detail);

  }

  return await response.json();

}


function applyPersonaIds() {
  const p = state.principals;
  const chip = (selector, attr, id, label) => {
    const el = $(selector);
    if (!el || !id) return;
    el.dataset[attr] = id;
    el.textContent = `${label} (${id})`;
  };
  chip("#chip-target", "target", p.target, "Student");
  chip("#chip-teacher", "target", p.teacher, "Teacher");
  chip("#chip-outsider", "target", p.outsider, "Other building");
  chip("#caller-dean", "caller", p.dean, "Dean A");
  chip("#caller-teacher", "caller", p.teacher, "Teacher B");
  chip("#caller-student", "caller", p.student, "Student C");
  if ($("#q-occupant") && !$("#q-occupant").value && p.target) $("#q-occupant").value = p.target;
  if ($("#q-caller") && !$("#q-caller").value && p.dean) $("#q-caller").value = p.dean;
}


async function loginAllPersonas() {

  for (const persona of PERSONAS) {
    const login = await loginPersona(persona);
    state.tokens[persona] = login.token;
    state.principals[persona] = login.principal;
  }

  // Ids of the demo target / an out-of-building student come from the active run's roles (nothing is hardcoded).
  for (const extra of ["target", "outsider"]) {
    try {
      state.principals[extra] = (await loginPersona(extra)).principal;
    } catch (error) {
      /* optional quick pick */
    }
  }

  applyPersonaIds();

  state.activeToken = state.tokens[state.persona] || null;

}


function setPersona(persona) {

  state.persona = persona;

  state.activeToken = state.tokens[persona] || null;

  // Reconnect the live stream so it authenticates as the new persona.
  if (state.ws && state.ws.readyState <= 1) {
    state.ws.close();
  }

  applyPersonaToControls();

  // Everything on screen was fetched as the previous persona: refetch under the new one.
  refreshAll();

}


/*
 * Simulation / demo / reset are Dean-only on the server (403 otherwise); mirror that
 * in the UI so other personas do not get buttons that can only fail.
 */
function applyPersonaToControls() {

  const isDean = state.persona === "dean";

  ["#btn-simulate", "#btn-walk", "#btn-reset"].forEach((selector) => {

    const button = $(selector);

    if (!button) {
      return;
    }

    button.disabled = !isDean;

    button.title = isDean
      ? ""
      : "Dean only: simulation, demo and reset are restricted by RBAC";

  });

}


async function api(url, options = {}) {

  const response = await fetch(url, {
    ...options,

    headers: {
      ...(options.body
        ? { "Content-Type": "application/json" }
        : {}),
      ...(state.activeToken
        ? { Authorization: `Bearer ${state.activeToken}` }
        : {}),
      ...(options.headers || {})
    }
  });

  // Tokens are short-lived; on 401 sign the personas in again once and retry.
  if (response.status === 401 && !options._retried && !url.startsWith("/api/auth/")) {

    await loginAllPersonas();

    return api(url, { ...options, _retried: true });

  }

  if (!response.ok) {

    let detail = `Request failed (${response.status})`;

    try {
      const body = await response.json();

      if (body.detail) {
        detail = body.detail;
      }

    } catch (_) {
      // Ignore JSON parsing errors.
    }

    throw new Error(detail);
  }

  return response.json();
}


/* ================================================================
   TAB / NAVIGATION
   ================================================================ */

const sectionNames = {
  dashboard: "Dashboard",
  occupancy: "Occupancy",
  heatmap: "Heatmap",
  query: "Query & Retrieval",
  security: "Security Monitor",
  metrics: "System Metrics"
};


function activateTab(name) {

  $all(".nav-item").forEach((item) => {
    item.classList.toggle(
      "active",
      item.dataset.tab === name
    );
  });


  $all(".panel").forEach((panel) => {
    panel.classList.toggle(
      "active",
      panel.id === `tab-${name}`
    );
  });


  $("#current-section").textContent =
    sectionNames[name] || name;


  if (name === "occupancy") {
    refreshOccupancy();
  }

  if (name === "heatmap") {
    refreshHeatmap();
  }

  if (name === "security") {
    refreshSecurity();
  }

}


$all(".nav-item").forEach((item) => {

  item.addEventListener("click", () => {

    activateTab(item.dataset.tab);

  });

});


$all("[data-tab-target]").forEach((button) => {

  button.addEventListener("click", () => {

    activateTab(button.dataset.tabTarget);

  });

});


/* ================================================================
   THRESHOLD
   ================================================================ */

const theta = $("#theta");

if (theta) {

  theta.addEventListener("input", (event) => {

    $("#theta-value").textContent =
      (Number(event.target.value) / 100).toFixed(2);

  });

}


/* ================================================================
   LAYOUT
   ================================================================ */

async function loadLayout() {

  try {

    state.layout = await api("/api/layout");

    renderBuildingList();
    updateBuildingStats();
    updateDashboardBuildings();

  } catch (error) {

    console.error("Layout error:", error);

    showActionStatus(
      `Layout error: ${error.message}`,
      true
    );

  }

}


/* ================================================================
   BUILDING LIST
   ================================================================ */

function renderBuildingList() {

  const list = $("#building-list");

  if (!list || !state.layout) {
    return;
  }

  list.innerHTML = "";


  state.layout.buildings.forEach((building) => {

    const item = document.createElement("div");

    item.className = "building-chip";

    item.innerHTML = `
      <span>${escapeHTML(building.id)}</span>
      <span class="count">
        ${building.occupants.length}
      </span>
    `;

    list.appendChild(item);

  });

}


/* ================================================================
   BUILDING STATISTICS
   ================================================================ */

function getOccupancyRows() {

  return Object.values(state.occupancy)
    .flatMap((rows) => rows || []);

}


function getBuildingOccupancy(buildingId) {

  const zones = state.heatmap && state.heatmap[buildingId];

  if (zones) {

    return Object.values(zones)
      .reduce((sum, n) => sum + Number(n || 0), 0);

  }

  return getOccupancyRows()
    .filter((row) => row.building_id === buildingId)
    .length;

}


function getCampusOccupancy() {

  if (state.heatmap && Object.keys(state.heatmap).length) {

    return Object.keys(state.heatmap)
      .reduce((sum, id) => sum + getBuildingOccupancy(id), 0);

  }

  return getOccupancyRows().length;

}


function updateBuildingStats() {

  const buildings = state.layout?.buildings || [];

  $("#stat-buildings").textContent =
    buildings.length || 0;

}


function updateOccupancyStats() {

  const rows = getOccupancyRows();

  $("#stat-occupants").textContent =
    getCampusOccupancy();

  const badge = $("#occupancy-count-badge");

  if (badge) {

    badge.textContent =
      `${rows.length} visible record${rows.length === 1 ? "" : "s"}`;

  }

  updateDashboardBuildings();

}


/* ================================================================
   DASHBOARD BUILDING CARDS
   ================================================================ */

function updateDashboardBuildings() {

  const container = $("#dashboard-buildings");

  if (!container || !state.layout) {
    return;
  }

  const buildings = state.layout.buildings;

  if (!buildings.length) {

    container.innerHTML = `
      <div class="empty-state">
        No buildings configured.
      </div>
    `;

    return;
  }


  const maxOccupancy = Math.max(
    1,
    ...buildings.map(
      (building) => building.occupants.length
    )
  );


  container.innerHTML = buildings.map((building) => {

    const current =
      getBuildingOccupancy(building.id);

    const percentage =
      Math.min(
        100,
        Math.round(
          (current / maxOccupancy) * 100
        )
      );

    return `
      <article class="building-card">

        <div class="building-card-top">

          <div class="building-id">
            ${escapeHTML(building.id)}
          </div>

          <div class="building-status">
            ACTIVE
          </div>

        </div>

        <div class="building-count">
          ${current}
        </div>

        <div class="building-count-label">
          current occupants
        </div>

        <div class="building-progress">
          <span style="width:${percentage}%"></span>
        </div>

      </article>
    `;

  }).join("");

}


/* ================================================================
   OCCUPANCY TABLE
   ================================================================ */

function renderOccupancyTable(bodyElement, snapshot) {

  bodyElement.innerHTML = "";

  const rows = [];


  Object.entries(snapshot || {}).forEach(
    ([zone, zoneRows]) => {

      (zoneRows || []).forEach((row) => {

        rows.push({
          ...row,
          zone
        });

      });

    }
  );


  if (!rows.length) {

    bodyElement.innerHTML = `
      <tr>
        <td colspan="4">
          <div class="empty-state">
            No occupants are visible to your role at this time.
          </div>
        </td>
      </tr>
    `;

    return;

  }


  rows.forEach((row) => {

    const probability =
      row.probability == null
        ? ""
        : Number(row.probability).toFixed(2);


    const tr =
      document.createElement("tr");


    tr.innerHTML = `
      <td>${escapeHTML(row.building_id)}</td>
      <td>${escapeHTML(row.zone)}</td>
      <td>${escapeHTML(row.occupant)}</td>
      <td>${escapeHTML(probability)}</td>
    `;


    bodyElement.appendChild(tr);

  });

}


async function refreshOccupancy() {

  try {

    state.occupancy =
      await api("/api/occupancy");

    renderOccupancyTable(
      $("#occupancy-body"),
      state.occupancy
    );

    updateOccupancyStats();

  } catch (error) {

    console.error(
      "Occupancy refresh failed:",
      error
    );

  }

}


/* ================================================================
   HEATMAP
   ================================================================ */

function colourFor(value, max) {

  const ratio =
    max > 0
      ? Math.min(value / max, 1)
      : 0;


  /*
   * teal → yellow → orange
   */

  const stops = [
    [58, 213, 197],
    [218, 205, 104],
    [239, 135, 63]
  ];


  let a;
  let b;
  let t;


  if (ratio < 0.5) {

    a = stops[0];
    b = stops[1];

    t = ratio / 0.5;

  } else {

    a = stops[1];
    b = stops[2];

    t = (ratio - 0.5) / 0.5;

  }


  const r =
    Math.round(a[0] + (b[0] - a[0]) * t);

  const g =
    Math.round(a[1] + (b[1] - a[1]) * t);

  const blue =
    Math.round(a[2] + (b[2] - a[2]) * t);


  return `rgb(${r}, ${g}, ${blue})`;

}


async function refreshHeatmap() {

  try {

    state.heatmap =
      await api("/api/heatmap");

    updateOccupancyStats();


    const container =
      $("#heatmap-grid");


    if (!container) {
      return;
    }


    container.innerHTML = "";


    const zones = new Set();


    Object.values(state.heatmap)
      .forEach((buildingZones) => {

        Object.keys(buildingZones || {})
          .forEach((zone) => zones.add(zone));

      });


    const zoneList =
      Array.from(zones).sort();


    const allValues =
      Object.values(state.heatmap)
        .flatMap((zones) =>
          Object.values(zones || {})
        )
        .map(Number);


    const maxValue =
      Math.max(1, ...allValues);


    Object.entries(state.heatmap)
      .forEach(([buildingId, zoneCounts]) => {

        const row =
          document.createElement("div");

        row.className = "heatmap-row";


        const label =
          document.createElement("div");

        label.className = "heatmap-label";

        label.textContent = buildingId;


        const cells =
          document.createElement("div");

        cells.className = "heatmap-cells";


        zoneList.forEach((zone) => {

          const value =
            Number(zoneCounts?.[zone] || 0);


          const cell =
            document.createElement("div");

          cell.className = "heatmap-cell";


          cell.style.background =
            colourFor(value, maxValue);


          cell.title =
            `${buildingId} / ${zone}: ${value}`;


          cell.textContent =
            value
              ? `${zone} · ${value}`
              : zone;


          cells.appendChild(cell);

        });


        row.appendChild(label);

        row.appendChild(cells);

        container.appendChild(row);

      });


  } catch (error) {

    console.error(
      "Heatmap refresh failed:",
      error
    );

  }

}


/* ================================================================
   SECURITY
   ================================================================ */

async function refreshSecurity() {

  try {

    state.security =
      await api("/api/security-log");


    const body =
      $("#security-body");


    body.innerHTML = "";


    if (!state.security.length) {

      body.innerHTML = `
        <tr>
          <td colspan="4">
            <div class="empty-state">
              No security events recorded.
            </div>
          </td>
        </tr>
      `;

    } else {

      state.security.forEach((event) => {

        const tr =
          document.createElement("tr");


        const time =
          new Date(
            Number(event.time) * 1000
          ).toLocaleTimeString();


        tr.innerHTML = `
          <td>${escapeHTML(time)}</td>
          <td>${escapeHTML(event.kind)}</td>
          <td>${escapeHTML(event.building_id)}</td>
          <td>${escapeHTML(event.detail)}</td>
        `;


        body.appendChild(tr);

      });

    }


    updateSecurityStats();

    updateActivityFromSecurity();


  } catch (error) {

    console.error(
      "Security refresh failed:",
      error
    );

    // Never keep showing a previous persona's audit log: clear it and say why.
    state.security = [];

    const body = $("#security-body");

    if (body) {

      body.innerHTML = `
        <tr>
          <td colspan="4">
            <div class="empty-state">
              ${escapeHTML(error.message || "Security log unavailable.")}
            </div>
          </td>
        </tr>
      `;

    }

    updateSecurityStats();

  }

}


function updateSecurityStats() {

  const authFailures =
    state.security.filter(
      (event) => event.kind === "auth_failure"
    ).length;


  const replay =
    state.security.filter(
      (event) => event.kind === "replay_detected"
    ).length;


  const unknown =
    state.security.filter(
      (event) => event.kind === "unknown_building"
    ).length;


  $("#security-auth-count").textContent =
    authFailures;

  $("#security-replay-count").textContent =
    replay;

  $("#security-unknown-count").textContent =
    unknown;

  $("#security-total-count").textContent =
    state.security.length;

  $("#stat-security").textContent =
    state.security.length;

}


function updateActivityFromSecurity() {

  const feed =
    $("#activity-feed");


  if (!feed) {
    return;
  }


  const activities =
    state.security
      .slice(0, 5)
      .map((event) => {

        const time =
          new Date(
            Number(event.time) * 1000
          ).toLocaleTimeString();


        return `
          <div class="activity-item">

            <span class="activity-dot"></span>

            <div class="activity-content">

              <div class="activity-main">
                Security event:
                ${escapeHTML(event.kind)}
              </div>

              <div class="activity-meta">
                ${escapeHTML(event.building_id)}
                · ${escapeHTML(time)}
              </div>

            </div>

          </div>
        `;

      });


  if (activities.length) {

    feed.innerHTML =
      activities.join("");

  }

}


/* ================================================================
   ACTIVITY
   ================================================================ */

function addActivity(message, meta = "") {

  const feed =
    $("#activity-feed");


  if (!feed) {
    return;
  }


  state.activity.unshift({
    message,
    meta,
    time: Date.now()
  });


  state.activity =
    state.activity.slice(0, 7);


  feed.innerHTML =
    state.activity
      .map((item) => {

        return `
          <div class="activity-item">

            <span class="activity-dot"></span>

            <div class="activity-content">

              <div class="activity-main">
                ${escapeHTML(item.message)}
              </div>

              <div class="activity-meta">
                ${escapeHTML(item.meta)}
              </div>

            </div>

          </div>
        `;

      })
      .join("");

}


/* ================================================================
   ACTION STATUS
   ================================================================ */

function showActionStatus(message, error = false) {

  const status =
    $("#action-status");


  if (!status) {
    return;
  }


  status.textContent = message;

  status.style.color =
    error
      ? "var(--danger)"
      : "var(--accent)";


  window.clearTimeout(
    showActionStatus.timer
  );


  showActionStatus.timer =
    window.setTimeout(() => {

      status.textContent = "Ready";

      status.style.color =
        "var(--muted-2)";

    }, 4500);

}


/* ================================================================
   BUTTON BUSY STATE
   ================================================================ */

function setButtonBusy(button, busy, text) {

  if (!button) {
    return;
  }


  if (busy) {

    button.dataset.originalText =
      button.innerHTML;

    button.disabled = true;

    button.innerHTML =
      `<span>◌</span> ${escapeHTML(text)}`;

  } else {

    button.disabled = false;

    button.innerHTML =
      button.dataset.originalText ||
      "Ready";

  }

}


/* ================================================================
   SIMULATION
   ================================================================ */

$("#btn-simulate").addEventListener(
  "click",
  async () => {

    const button =
      $("#btn-simulate");


    const n_events =
      Number($("#sim-count").value);


    const seed =
      Number($("#sim-seed").value);


    if (
      !Number.isFinite(n_events) ||
      n_events < 1
    ) {

      showActionStatus(
        "Enter a valid event count.",
        true
      );

      return;

    }


    try {

      setButtonBusy(
        button,
        true,
        "Generating..."
      );


      showActionStatus(
        "Generating occupancy states..."
      );


      const result =
        await api("/api/simulate", {
          method: "POST",

          body: JSON.stringify({
            n_events,
            seed
          })
        });


      state.eventsProcessed +=
        Number(result.events_run || 0);


      $("#stat-events").textContent =
        state.eventsProcessed;


      addActivity(
        `${result.events_run} mobility events processed`,
        `Simulation · seed ${seed}`
      );


      await Promise.all([
        refreshOccupancy(),
        refreshHeatmap()
      ]);


      showActionStatus(
        `${result.events_run} events processed`
      );


    } catch (error) {

      console.error(error);

      showActionStatus(
        error.message,
        true
      );

    } finally {

      setButtonBusy(
        button,
        false
      );

    }

  }
);


/* ================================================================
   B1 → B5 DEMO
   ================================================================ */

$("#btn-walk").addEventListener(
  "click",
  async () => {

    const button =
      $("#btn-walk");


    try {

      setButtonBusy(
        button,
        true,
        "Running..."
      );


      showActionStatus(
        "Running B1 → B5 scenario..."
      );


      const data =
        await api("/api/demo-walk", {
          method: "POST"
        });


      state.eventsProcessed +=
        Number(data.events_run || 0);


      $("#stat-events").textContent =
        state.eventsProcessed;


      addActivity(
        `Demo walk: ${data.home_building} → ${data.away_building}`,
        `${data.events_run} events · ${data.seconds.toFixed(3)}s`
      );


      const note =
        $("#query-note");


      if (note) {

        note.textContent =
          `Demo completed: ${data.home_building} → ${data.away_building} · ` +
          `${data.events_run} events · ${data.seconds.toFixed(3)}s`;

      }


      await Promise.all([
        refreshOccupancy(),
        refreshHeatmap(),
        refreshSecurity()
      ]);


      showActionStatus(
        "B1 → B5 demo completed"
      );


    } catch (error) {

      console.error(error);

      showActionStatus(
        error.message,
        true
      );

    } finally {

      setButtonBusy(
        button,
        false
      );

    }

  }
);


/* ================================================================
   RESET
   ================================================================ */

$("#btn-reset").addEventListener(
  "click",
  async () => {

    const button =
      $("#btn-reset");


    try {

      setButtonBusy(
        button,
        true,
        "Resetting..."
      );


      await api("/api/reset", {
        method: "POST"
      });


      state.activity = [];

      try {

        const health = await api("/api/health");

        state.eventsProcessed =
          Number(health.total_events_loaded || 0);

      } catch (error) {

        state.eventsProcessed = 0;

      }


      $("#stat-events").textContent =
        state.eventsProcessed;


      $("#activity-feed").innerHTML = `
        <div class="empty-state">
          System reset. Waiting for activity...
        </div>
      `;


      await Promise.all([
        refreshOccupancy(),
        refreshHeatmap(),
        refreshSecurity()
      ]);


      showActionStatus(
        "System reset successfully"
      );


    } catch (error) {

      console.error(error);

      showActionStatus(
        error.message,
        true
      );

    } finally {

      setButtonBusy(
        button,
        false
      );

    }

  }
);


/* ================================================================
   SPATIO-TEMPORAL QUERIES & BUILDING-SCOPED RBAC (Q1, Q2, Q3, Q5, Q6)
   ================================================================ */

const queryTemplates = {
  Q1: "Did any occupant stay in building {b} after time {t}?",
  Q2: "Was the number of visitors in building {b} more than the number of registered occupants at time {t}?",
  Q3: "Did occupant {o} leave building {b} before time {t}?",
  Q4: "Did occupant {o} enter the building between times {t1} and {t2}?",
  Q5: "Did occupant {o} visit all the zones in building {b}?",
  Q6: "Where was occupant {o} at time {t}?",
  Q7: "How many zones did occupant {o} visit during the time interval {t1} to {t2}?",
  Q8: "At what time did occupant {o} visit zone {z} for the first time?",
  Q9: "How long did occupant {o} spend at zone {z} during his first visit?",
  Q10: "Who was there in zone {z} at time {t}?",
  Q11: "Where was the occupant {o} during the time interval from {t1} to {t2}?",
  Q12: "Who was there in zone {z} from time {t1} to {t2}?",
  Q13: "Who all visited zone {z}?",
};

function updateQueryTypeUI() {
  const qType = $("#q-type") ? $("#q-type").value : "Q6";
  const badge = $("#q-template-badge");
  const text = $("#q-template-text");
  if (badge) badge.textContent = `Template: ${qType}`;
  if (text) text.textContent = `"${queryTemplates[qType] || ''}"`;

  const targetBox = $("#field-target-container");
  const bldBox = $("#field-building-container");
  const zoneBox = $("#field-zone-container");
  const timeBox = $("#field-time-container");
  const rangeBox = $("#field-range-container");

  // Determine visibility per query type
  const needsTarget = ["Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q11", "TRACK"].includes(qType);
  if (targetBox) targetBox.style.display = needsTarget ? "block" : "none";

  const needsBuilding = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q8", "Q9", "Q10", "Q12", "Q13"].includes(qType);
  if (bldBox) bldBox.style.display = needsBuilding ? "block" : "none";

  const needsZone = ["Q8", "Q9", "Q10", "Q12", "Q13"].includes(qType);
  if (zoneBox) zoneBox.style.display = needsZone ? "block" : "none";

  const needsSingleTime = ["Q1", "Q2", "Q3", "Q6", "Q10"].includes(qType);
  if (timeBox) timeBox.style.display = needsSingleTime ? "block" : "none";

  const needsRange = ["Q4", "Q7", "Q11", "Q12"].includes(qType);
  if (rangeBox) rangeBox.style.display = needsRange ? "block" : "none";
}

// Bind query type change
if ($("#q-type")) {
  $("#q-type").addEventListener("change", updateQueryTypeUI);
}

// Bind quick chip buttons
$all(".btn-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.target && $("#q-occupant")) {
      $("#q-occupant").value = btn.dataset.target;
    }
    if (btn.dataset.caller && $("#q-caller")) {
      $("#q-caller").value = btn.dataset.caller;
      const role = btn.dataset.role;
      $all(".access-role").forEach(r => r.classList.remove("active-role"));
      if (role === "dean") {
        setPersona("dean");
        const c = $("#role-card-a"); if (c) c.classList.add("active-role");
        if ($("#q-purpose")) $("#q-purpose").value = "security_investigation";
        if ($("#header-role")) $("#header-role").textContent = "DEAN A";
      } else if (role === "teacher") {
        setPersona("teacher");
        const c = $("#role-card-b"); if (c) c.classList.add("active-role");
        if ($("#q-purpose")) $("#q-purpose").value = "academic_roster";
        if ($("#header-role")) $("#header-role").textContent = "TEACHER B";
      } else if (role === "student") {
        setPersona("student");
        const c = $("#role-card-c"); if (c) c.classList.add("active-role");
        if ($("#q-purpose")) $("#q-purpose").value = "office_hours";
        if ($("#header-role")) $("#header-role").textContent = "STUDENT C";
      }
    }
  });
});

if ($("#btn-query")) {
  $("#btn-query").addEventListener("click", executeCurrentQuery);
}

["q-occupant", "q-caller", "q-time", "q-t1", "q-t2"].forEach((id) => {
  const input = $(`#${id}`);
  if (input) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        executeCurrentQuery();
      }
    });
  }
});

function executeCurrentQuery() {
  const qType = $("#q-type") ? $("#q-type").value : "Q6";
  const callerId = $("#q-caller") ? $("#q-caller").value.trim() : (state.principals.dean || "");
  const targetId = $("#q-occupant") ? $("#q-occupant").value.trim() : "";
  const buildingId = $("#q-building") ? $("#q-building").value : "building_5";
  const zone = $("#q-zone") ? $("#q-zone").value : "z1";
  const timeT = $("#q-time") ? $("#q-time").value.trim() : "23:59:59";
  const t1 = $("#q-t1") ? $("#q-t1").value.trim() : "08:00:00";
  const t2 = $("#q-t2") ? $("#q-t2").value.trim() : "18:00:00";
  const purpose = $("#q-purpose") ? $("#q-purpose").value : "general_lookup";
  const isOfficeHours = $("#q-office-hours") ? $("#q-office-hours").checked : true;
  const authorizedInvestigation = $("#q-authorized") ? $("#q-authorized").checked : false;

  runQuery({
    query_type: qType,
    caller_id: callerId,
    target_id: targetId,
    building_id: buildingId,
    zone: zone,
    time_t: timeT,
    t1: t1,
    t2: t2,
    purpose: purpose,
    is_office_hours: isOfficeHours,
    authorized_investigation: authorizedInvestigation,
  });
}

async function runQuery(body) {
  const button = $("#btn-query");
  try {
    setButtonBusy(button, true, "Executing...");

    const data = await api("/api/query", {
      method: "POST",
      body: JSON.stringify(body)
    });

    // 1. Update labels and badges
    const label = $("#results-query-label");
    if (label) label.textContent = data.template_query || `Query: ${body.query_type}`;

    const badgesContainer = $("#results-badges");
    if (badgesContainer) {
      badgesContainer.innerHTML = "";
      const dec = data.decision || {};
      const statusBadge = document.createElement("span");
      statusBadge.className = dec.permitted ? "badge-permit" : "badge-deny";
      statusBadge.textContent = dec.permitted ? "PERMITTED" : "DENIED";
      badgesContainer.appendChild(statusBadge);

      if (dec.disclosure_level) {
        const lvlBadge = document.createElement("span");
        lvlBadge.className = "badge-level";
        lvlBadge.textContent = dec.disclosure_level;
        badgesContainer.appendChild(lvlBadge);
      }
    }

    // 2. Update Answer Banner
    const answerBanner = $("#answer-banner");
    const mainText = $("#answer-main-text");
    const subText = $("#answer-sub-text");
    if (answerBanner && mainText) {
      answerBanner.style.display = "block";
      if (!data.success) {
        mainText.textContent = "Query Refused / Denied";
        mainText.style.color = "var(--danger)";
        if (subText) subText.textContent = data.error || data.decision?.reason || "Policy violation";
      } else {
        mainText.style.color = "var(--text)";
        if (typeof data.answer === "boolean") {
          mainText.textContent = data.answer ? "YES" : "NO";
          if (subText) subText.textContent = `Result returned from Phase 4 event log. Evidence count: ${data.evidence?.length || 0}`;
        } else if (Array.isArray(data.answer)) {
          mainText.textContent = `${data.answer.length} occupant(s) detected: ${data.answer.slice(0, 8).join(', ')}${data.answer.length > 8 ? ' ...' : ''}`;
          if (subText) subText.textContent = `Total distinct occupants: ${data.answer.length} · Evidence rows: ${data.evidence?.length || 0}`;
        } else if (typeof data.answer === "number") {
          mainText.textContent = `${data.answer} zones visited`;
          if (subText) subText.textContent = `Evidence rows: ${data.evidence?.length || 0}`;
        } else if (typeof data.answer === "object" && data.answer !== null) {
          if (data.query_type === "TRACK") {
            mainText.textContent = `Trajectory: ${data.answer.total_detections || 0} waypoints across ${data.answer.buildings_visited?.join(', ') || 'campus'}`;
            if (subText) subText.textContent = `Most frequented zone: ${data.answer.most_frequented_zone || 'N/A'} · Pointer history merged: ${data.answer.pointer_history?.length || 0} visit(s)`;
          } else if (data.query_type === "Q6") {
            mainText.textContent = data.answer.status || (data.answer.sector ? `${data.answer.building} (${data.answer.sector})` : JSON.stringify(data.answer));
            if (subText) subText.textContent = `Approx Time: ${data.answer.approx_time || data.answer.time || data.answer.arrival_time || 'N/A'} · Confidence: ${data.answer.confidence || 'High'}`;
          } else if (data.query_type === "Q9") {
            mainText.textContent = `Duration: ${data.answer.duration_formatted} (${data.answer.duration_seconds}s)`;
            if (subText) subText.textContent = `Entry: ${data.answer.entry_time} · Exit: ${data.answer.exit_time} at ${data.answer.building}:${data.answer.zone} (${data.answer.zone_label})`;
          } else if (data.query_type === "Q11") {
            mainText.textContent = `${data.answer.total_observations || 0} observation(s) across ${(data.answer.buildings_visited || []).join(', ') || 'building'}`;
            if (subText) subText.textContent = `Zones visited: ${(data.answer.zones_visited || []).join(', ') || 'None'}`;
          } else if (data.query_type === "Q13") {
            mainText.textContent = `${data.answer.total_visitors || 0} unique visitor(s) detected`;
            if (subText) subText.textContent = `Top visitor: ${data.answer.top_visitors?.[0]?.occupant_id || 'N/A'} (${data.answer.top_visitors?.[0]?.visit_count || 0} detections)`;
          } else {
            mainText.textContent = JSON.stringify(data.answer);
            if (subText) subText.textContent = `Evidence rows: ${data.evidence?.length || 0}`;
          }
        } else {
          mainText.textContent = data.answer != null ? String(data.answer) : "None / Not Observed";
          if (subText) subText.textContent = `Evidence rows: ${data.evidence?.length || 0}`;
        }
      }
    }

    // 3. Update Explainability Card
    const explainCard = $("#explain-card");
    const explainRule = $("#explain-rule");
    const explainReason = $("#explain-reason");
    const exposedTags = $("#explain-exposed-tags");
    const redactedTags = $("#explain-redacted-tags");

    if (explainCard && data.decision) {
      explainCard.style.display = "block";
      if (explainRule) explainRule.textContent = data.decision.policy_rule || "RULE_N/A";
      if (explainReason) explainReason.textContent = data.decision.reason || "N/A";

      if (exposedTags) {
        exposedTags.innerHTML = "";
        (data.decision.exposed_fields || []).forEach(f => {
          const t = document.createElement("span");
          t.className = "tag-exposed";
          t.textContent = f;
          exposedTags.appendChild(t);
        });
        if (!data.decision.exposed_fields?.length) {
          exposedTags.innerHTML = `<span style="font-size:11px;color:var(--muted-2);">None</span>`;
        }
      }

      if (redactedTags) {
        redactedTags.innerHTML = "";
        (data.decision.redacted_fields || []).forEach(f => {
          const t = document.createElement("span");
          t.className = "tag-redacted";
          t.textContent = f;
          redactedTags.appendChild(t);
        });
        if (!data.decision.redacted_fields?.length) {
          redactedTags.innerHTML = `<span style="font-size:11px;color:var(--muted-2);">None</span>`;
        }
      }
    }

    // 4. Update Table
    const thead = $("#query-thead");
    const tbody = $("#query-body");
    if (tbody) {
      tbody.innerHTML = "";
      if ((data.query_type === "TRACK" && data.answer?.waypoints) || (data.query_type === "Q11" && data.answer?.trajectory)) {
        const waypoints = data.answer.waypoints || data.answer.trajectory;
        if (thead) {
          thead.innerHTML = `<tr><th>TIME</th><th>BUILDING</th><th>ZONE</th><th>ZONE LABEL</th><th>CONFIDENCE</th></tr>`;
        }
        waypoints.forEach(pt => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td>${escapeHTML(pt.time)}</td>
            <td>${escapeHTML(pt.building)}</td>
            <td>${escapeHTML(pt.zone)}</td>
            <td>${escapeHTML(pt.zone_label)}</td>
            <td>${pt.probability != null ? (Number(pt.probability)*100).toFixed(1) + '%' : 'N/A'}</td>
          `;
          tbody.appendChild(tr);
        });
      } else if (Array.isArray(data.answer)) {
        if (thead) {
          thead.innerHTML = `<tr><th>INDEX</th><th colspan="4">OCCUPANT ID</th></tr>`;
        }
        data.answer.forEach((occId, i) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `<td>#${i + 1}</td><td colspan="4" style="font-weight:700;font-family:var(--mono);">${escapeHTML(occId)}</td>`;
          tbody.appendChild(tr);
        });
      } else if (data.evidence && data.evidence.length) {
        if (thead) {
          thead.innerHTML = `<tr><th>INDEX</th><th colspan="4">EVIDENCE TRACE</th></tr>`;
        }
        data.evidence.forEach((ev, i) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `<td>#${i + 1}</td><td colspan="4" style="font-family:var(--mono);font-size:12px;">${escapeHTML(typeof ev === 'object' ? JSON.stringify(ev) : ev)}</td>`;
          tbody.appendChild(tr);
        });
      } else {
        if (thead) {
          thead.innerHTML = `<tr><th>PROPERTY</th><th colspan="4">DISCLOSED VALUE</th></tr>`;
        }
        const ansObj = (typeof data.answer === 'object' && data.answer !== null) ? data.answer : { "Answer": data.answer };
        Object.entries(ansObj).forEach(([k, v]) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `<td style="font-weight:700;">${escapeHTML(k)}</td><td colspan="4">${escapeHTML(typeof v === 'object' ? JSON.stringify(v) : String(v))}</td>`;
          tbody.appendChild(tr);
        });
      }
    }

    // Voice response if requested
    const speakCheckbox = $("#q-speak");
    if (speakCheckbox && speakCheckbox.checked && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      const speakText = mainText ? mainText.textContent : "Query executed";
      const u = new SpeechSynthesisUtterance(speakText);
      u.rate = 1.0;
      window.speechSynthesis.speak(u);
    }

    addActivity(`Query ${body.query_type} executed by ${body.caller_id}`, data.decision?.policy_rule || "Completed");

  } catch (error) {
    console.error(error);
    showActionStatus(error.message, true);
  } finally {
    setButtonBusy(button, false);
  }
}


/* ================================================================
   VOICE QUERY
   ================================================================ */

const ZONE_LABELS_TO_CODE = {

  entrance: "z1",
  lounge: "z2",
  office: "z3",
  cafeteria: "z4",
  mail: "z5",
  classroom: "z6",
  exit: "z7",
  corridor: "z8",
  transition: "zT"

};


const ROLE_WORDS = [
  "officer",
  "analyst",
  "self"
];


function parseVoiceQuery(transcript) {

  const text =
    transcript.toLowerCase();


  const result = {
    role: null,
    occupant_id: null,
    zone: null
  };


  const occupantMatch =
    text.match(
      /\bo\s?-?\s?(\d{2,4})\b/i
    );


  if (occupantMatch) {

    result.occupant_id =
      "O" + occupantMatch[1];

  }


  const zoneMatch =
    text.match(
      /\bz\s?-?\s?(t|\d)\b/i
    );


  if (zoneMatch) {

    result.zone =
      zoneMatch[1].toLowerCase() === "t"
        ? "zT"
        : "z" + zoneMatch[1];

  } else {

    for (
      const [label, code]
      of Object.entries(ZONE_LABELS_TO_CODE)
    ) {

      if (text.includes(label)) {

        result.zone = code;

        break;

      }

    }

  }


  for (const role of ROLE_WORDS) {

    if (text.includes(role)) {

      result.role = role;

      break;

    }

  }


  return result;

}


function speakResults(data, query) {

  if (!("speechSynthesis" in window)) {
    return;
  }


  let text;


  if (!data.rows.length) {

    text =
      `No results found` +
      (
        query.occupant_id
          ? ` for ${query.occupant_id}.`
          : "."
      );

  } else {

    const first =
      data.rows[0];


    text =
      `Found ${data.rows.length} result` +
      (
        data.rows.length === 1
          ? ""
          : "s"
      ) +
      `. Most recent: ` +
      `${first.occupant ?? "occupant"} ` +
      `in zone ${first.zone}` +
      (
        first.building_id
          ? `, building ${first.building_id}`
          : ""
      ) +
      ".";


    if (data.redacted) {

      text +=
        " Some detail was withheld for this role.";

    }

  }


  window.speechSynthesis.cancel();


  window.speechSynthesis.speak(
    new SpeechSynthesisUtterance(text)
  );

}


function setupVoiceQuery() {

  const button =
    $("#btn-voice");


  const status =
    $("#voice-status");


  if (!button) {
    return;
  }


  const SpeechRecognitionImpl =
    window.SpeechRecognition ||
    window.webkitSpeechRecognition;


  if (!SpeechRecognitionImpl) {

    button.disabled = true;

    status.textContent =
      "Voice query requires Chrome or another browser " +
      "with Speech Recognition support.";

    return;

  }


  const recognition =
    new SpeechRecognitionImpl();


  recognition.lang =
    "en-US";


  recognition.interimResults =
    false;


  recognition.maxAlternatives =
    1;


  let listening = false;


  button.addEventListener(
    "click",
    () => {

      if (listening) {

        recognition.stop();

        return;

      }


      try {

        recognition.start();

      } catch (error) {

        status.textContent =
          `Could not start microphone: ${error.message}`;

      }

    }
  );


  recognition.onstart =
    () => {

      listening = true;

      button.classList.add("listening");

      status.textContent =
        "Listening… try “where is O217”.";

    };


  recognition.onerror =
    (event) => {

      status.textContent =
        `Voice query error: ${event.error}`;

    };


  recognition.onend =
    () => {

      listening = false;

      button.classList.remove(
        "listening"
      );

    };


  recognition.onresult =
    (event) => {

      const transcript =
        event.results[0][0].transcript;


      const parsed =
        parseVoiceQuery(transcript);


      if (parsed.occupant_id) {

        $("#q-occupant").value =
          parsed.occupant_id;

      }


      if (parsed.zone) {

        $("#q-zone").value =
          parsed.zone;

      }


      if (parsed.role) {
        const rNorm = parsed.role.toLowerCase();
        if (rNorm === "dean") {
          if ($("#q-caller")) $("#q-caller").value = state.principals.dean || "";
          setPersona("dean");
          if ($("#header-role")) $("#header-role").textContent = "DEAN A";
        } else if (rNorm === "teacher") {
          if ($("#q-caller")) $("#q-caller").value = state.principals.teacher || "";
          setPersona("teacher");
          if ($("#header-role")) $("#header-role").textContent = "TEACHER B";
        } else if (rNorm === "student") {
          if ($("#q-caller")) $("#q-caller").value = state.principals.student || "";
          setPersona("student");
          if ($("#header-role")) $("#header-role").textContent = "STUDENT C";
        }
      }

      if (
        !parsed.occupant_id &&
        !parsed.zone
      ) {
        status.textContent =
          `Heard: "${transcript}" — ` +
          `couldn't find an occupant ID or zone.`;
        return;
      }

      status.textContent =
        `Heard: "${transcript}" → running query…`;

      runQuery({
        query_type: "Q6",
        caller_id: $("#q-caller") ? $("#q-caller").value.trim() : (state.principals.dean || ""),
        target_id: $("#q-occupant") ? $("#q-occupant").value.trim() : (parsed.occupant_id || state.principals.target || ""),
        zone: $("#q-zone") ? $("#q-zone").value.trim() : (parsed.zone || "z1"),
      });
    };

}


/* ================================================================
   WEBSOCKET
   ================================================================ */

function setConnectionStatus(connected) {

  state.connected =
    connected;


  const dot =
    $("#live-dot");


  const sidebarDot =
    $("#sidebar-status-dot");


  const text =
    $("#connection-text");


  const sidebarText =
    $("#sidebar-status-text");


  const wsIndicator =
    $("#ws-status-indicator");


  const wsText =
    $("#ws-status-text");


  if (connected) {

    dot.classList.add("online");
    dot.classList.remove("offline");


    sidebarDot.classList.add("online");
    sidebarDot.classList.remove("offline");


    sidebarText.textContent =
      "Connected";


    text.textContent =
      "LIVE";


    wsIndicator.classList.add(
      "online"
    );


    wsText.textContent =
      "CONNECTED";

  } else {

    dot.classList.remove("online");
    dot.classList.add("offline");


    sidebarDot.classList.remove(
      "online"
    );

    sidebarDot.classList.add(
      "offline"
    );


    sidebarText.textContent =
      "Reconnecting";


    text.textContent =
      "OFFLINE";


    wsIndicator.classList.remove(
      "online"
    );


    wsText.textContent =
      "RECONNECTING";

  }

}


function connectLive() {

  const protocol =
    location.protocol === "https:"
      ? "wss"
      : "ws";


  const socket =
    new WebSocket(
      `${protocol}://${location.host}/ws/live?token=` +
      encodeURIComponent(state.activeToken || "")
    );


  state.ws =
    socket;


  socket.onopen =
    () => {

      setConnectionStatus(true);

    };


  socket.onclose =
    () => {

      setConnectionStatus(false);


      window.setTimeout(
        connectLive,
        2000
      );

    };


  socket.onerror =
    () => {

      setConnectionStatus(false);

    };


  socket.onmessage =
    (message) => {

      try {

        const data =
          JSON.parse(message.data);


        if (
          data.type === "occupancy" ||
          data.type === "connected" ||
          data.type === "heartbeat" ||
          data.type === "simulation_tick" ||
          data.type === "demo_walk"
        ) {

          state.heatmap =
            data.heatmap || state.heatmap;


          refreshOccupancy();

          refreshHeatmap();


          if (
            data.type === "simulation_tick" ||
            data.type === "demo_walk" ||
            data.type === "occupancy"
          ) {

            addActivity(
              "Occupancy state updated",
              "Live WebSocket event"
            );

          }

        }


        if (data.type === "security") {

          refreshSecurity();


          addActivity(
            `Security event: ${data.event?.kind || "unknown"}`,
            "Live security stream"
          );

        }

      } catch (error) {

        console.error(
          "WebSocket message error:",
          error
        );

      }

    };

}


/* ================================================================
   GRAFANA
   ================================================================ */

function setupGrafana() {

  const frame = $("#grafana-frame");
  const placeholder = $("#grafana-placeholder");
  const status = $("#metric-status");

  if (!frame || !placeholder) {
    return;
  }

  /*
   * Local Grafana dashboard.
   *
   * Grafana is running on port 3000 through Docker.
   * Anonymous Viewer access and iframe embedding are enabled
   * in docker-compose.yml.
   */

  const GRAFANA_URL =
    "http://localhost:3000/?orgId=1&kiosk";

  frame.style.display = "block";
  placeholder.style.display = "none";

  if (status) {
    status.textContent = "CONNECTING";
  }

  frame.src = GRAFANA_URL;

  frame.addEventListener("load", () => {

    console.log(
      "Grafana dashboard loaded successfully"
    );

    if (status) {
      status.textContent = "CONNECTED";
    }

  });

  frame.addEventListener("error", () => {

    console.error(
      "Grafana iframe failed to load"
    );

    frame.style.display = "none";
    placeholder.style.display = "flex";

    if (status) {
      status.textContent = "UNAVAILABLE";
    }

  });

}


/* ================================================================
   PERIODIC REFRESH
   ================================================================ */

async function refreshAll() {

  await Promise.allSettled([
    refreshOccupancy(),
    refreshHeatmap(),
    refreshSecurity()
  ]);

}


/* ================================================================
   BOOT
   ================================================================ */

async function boot() {

  try {

    await loginAllPersonas();

    await loadLayout();

    await refreshAll();

    try {

      const health = await api("/api/health");

      state.eventsProcessed =
        Number(health.total_events_loaded || 0);

      $("#stat-events").textContent =
        state.eventsProcessed;

    } catch (error) {

      console.error("Health fetch failed:", error);

    }

    setupVoiceQuery();

    setupGrafana();

    connectLive();


    if ($("#header-role")) {
      $("#header-role").textContent = "DEAN A";
    }


    showActionStatus(
      "System ready"
    );


  } catch (error) {

    console.error(
      "Boot error:",
      error
    );

    showActionStatus(
      `Startup error: ${error.message}`,
      true
    );

  }

}


boot();
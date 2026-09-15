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

  busy: false
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

async function api(url, options = {}) {

  const response = await fetch(url, {
    ...options,

    headers: {
      ...(options.body
        ? { "Content-Type": "application/json" }
        : {}),
      ...(options.headers || {})
    }
  });

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

  return getOccupancyRows()
    .filter((row) => row.building_id === buildingId)
    .length;

}


function updateBuildingStats() {

  const buildings = state.layout?.buildings || [];

  $("#stat-buildings").textContent =
    buildings.length || 0;

}


function updateOccupancyStats() {

  const rows = getOccupancyRows();

  $("#stat-occupants").textContent =
    rows.length;

  const badge = $("#occupancy-count-badge");

  if (badge) {

    badge.textContent =
      `${rows.length} record${rows.length === 1 ? "" : "s"}`;

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
            No occupants currently tracked.
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


      state.eventsProcessed = 0;

      state.activity = [];


      $("#stat-events").textContent =
        "0";


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
   QUERY
   ================================================================ */

$("#btn-query").addEventListener(
  "click",
  executeCurrentQuery
);


["q-occupant", "q-zone"].forEach(
  (id) => {

    const input = $(`#${id}`);

    if (!input) {
      return;
    }

    input.addEventListener(
      "keydown",
      (event) => {

        if (event.key === "Enter") {
          executeCurrentQuery();
        }

      }
    );

  }
);


$("#q-role").addEventListener(
  "change",
  () => {

    const role =
      $("#q-role").value;


    $("#header-role").textContent =
      role.toUpperCase();


    $all(".access-role")
      .forEach((item) =>
        item.classList.remove("active-role")
      );


    const roleMap = {
      officer: 0,
      analyst: 1,
      self: 2
    };


    const index =
      roleMap[role];


    const items =
      $all(".access-role");


    if (items[index]) {
      items[index].classList.add(
        "active-role"
      );
    }

  }
);


function executeCurrentQuery() {

  runQuery({
    role: $("#q-role").value,

    occupant_id:
      $("#q-occupant").value.trim() ||
      null,

    zone:
      $("#q-zone").value.trim() ||
      null
  });

}


async function runQuery(body) {

  const button =
    $("#btn-query");


  try {

    setButtonBusy(
      button,
      true,
      "Searching..."
    );


    const data =
      await api("/api/query", {
        method: "POST",

        body: JSON.stringify(body)
      });


    const note =
      $("#query-note");


    note.textContent =
      data.redacted
        ? `Filtered for role="${body.role}" — ` +
          `some rows were withheld or pseudonymised.`
        : `Full results for role="${body.role}".`;


    const bodyElement =
      $("#query-body");


    bodyElement.innerHTML = "";


    if (!data.rows.length) {

      bodyElement.innerHTML = `
        <tr>
          <td colspan="4">
            <div class="empty-state">
              No matching records found.
            </div>
          </td>
        </tr>
      `;

    } else {

      data.rows.forEach((row) => {

        const tr =
          document.createElement("tr");


        tr.innerHTML = `
          <td>${escapeHTML(row.building_id)}</td>
          <td>${escapeHTML(row.zone)}</td>
          <td>${escapeHTML(row.occupant)}</td>
          <td>
            ${
              row.probability != null
                ? Number(row.probability).toFixed(2)
                : ""
            }
          </td>
        `;


        bodyElement.appendChild(tr);

      });

    }


    addActivity(
      `Query executed as ${body.role}`,
      `${data.rows.length} result(s)`
    );


    if ($("#q-speak").checked) {
      speakResults(data, body);
    }


    return data;


  } catch (error) {

    console.error(error);

    $("#query-note").textContent =
      `Query failed: ${error.message}`;


  } finally {

    setButtonBusy(
      button,
      false
    );

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

        $("#q-role").value =
          parsed.role;

        $("#header-role").textContent =
          parsed.role.toUpperCase();

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
        role: $("#q-role").value,

        occupant_id:
          $("#q-occupant").value.trim() ||
          null,

        zone:
          $("#q-zone").value.trim() ||
          null

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
      `${protocol}://${location.host}/ws/live`
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


        if (data.type === "occupancy") {

          state.heatmap =
            data.heatmap || state.heatmap;


          refreshOccupancy();

          refreshHeatmap();


          addActivity(
            "Occupancy state updated",
            "Live WebSocket event"
          );

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

    await loadLayout();

    await refreshAll();

    setupVoiceQuery();

    setupGrafana();

    connectLive();


    $("#header-role").textContent =
      $("#q-role").value.toUpperCase();


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
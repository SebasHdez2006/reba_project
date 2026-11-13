/**
 * REBA Dashboard — cliente WebSocket y actualización del DOM.
 *
 * Flujo:
 *  1. Conecta a ws://<host>/ws
 *  2. Recibe snapshot inicial (hello) con config, stats y progreso de bomba
 *  3. Actualiza contadores, donut chart, progreso del ciclo de bomba y
 *     galería de fotos que llegan por POST /upload desde la ESP32
 *  4. La bomba es autónoma en la ESP32: aquí solo se muestra el progreso
 *     inferido (cada N fotos recibidas).
 *
 * Documentación: docs/dashboard.md
 */

(() => {
  "use strict";

  // ---------------------------------------------------------------------------
  // Paleta por clase (alineada con styles.css)
  // ---------------------------------------------------------------------------
  const CLASS_COLORS = {
    plastico: "#3ea6ff",
    tela: "#c084fc",
    organico: "#4ade80",
    unicel: "#67e8f9",
    metal: "#fbbf24",
  };

  const state = {
    config: null,
    stats: {
      counters: {},
      percentages: {},
      total_objects: 0,
    },
    pump: {},
    ws: null,
    reconnectDelay: 1500,
    captureIds: new Set(),
    captureCount: 0,
    alertTimer: null,
  };

  // ---------------------------------------------------------------------------
  // Helpers DOM
  // ---------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  // ---------------------------------------------------------------------------
  // WebSocket
  // ---------------------------------------------------------------------------
  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/ws`;
  }

  function setWsStatus(online, label) {
    const pill = $("wsStatus");
    const text = $("wsStatusText");
    if (!pill || !text) return;
    pill.classList.toggle("online", online);
    pill.classList.toggle("offline", !online);
    text.textContent = label;
  }

  function connectWs() {
    setWsStatus(false, "Conectando…");
    const ws = new WebSocket(wsUrl());
    state.ws = ws;

    ws.addEventListener("open", () => {
      setWsStatus(true, "En vivo");
      state.reconnectDelay = 1500;
    });

    ws.addEventListener("message", (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      handleMessage(msg);
    });

    ws.addEventListener("close", () => {
      setWsStatus(false, "Reconectando…");
      setTimeout(connectWs, state.reconnectDelay);
      state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 10000);
    });

    ws.addEventListener("error", () => {
      ws.close();
    });
  }

  function handleMessage(msg) {
    if (!msg || typeof msg !== "object") return;
    switch (msg.type) {
      case "hello":
        if (msg.payload?.config) applyConfig(msg.payload.config);
        if (msg.payload?.stats) applyStats(msg.payload.stats);
        if (msg.payload?.detections) applyDetections(msg.payload.detections);
        if (msg.payload?.latest_capture) applyCapture(msg.payload.latest_capture);
        if (msg.payload?.pump) applyPumpStatus(msg.payload.pump);
        break;
      case "detections":
        applyDetections(msg.payload || {});
        if (msg.payload?.stats) applyStats(msg.payload.stats);
        if (msg.payload?.pump) applyPumpStatus(msg.payload.pump);
        break;
      case "capture":
        applyCapture(msg.payload || {}, true);
        break;
      case "pump_event":
        applyPumpStatus(msg.payload || {});
        showPumpAlert(msg.payload || {});
        break;
      default:
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // Config / Stats / Detecciones
  // ---------------------------------------------------------------------------
  function applyConfig(cfg) {
    state.config = cfg;
    const trigger = cfg.pump_trigger_photos ?? 3;
    const runSeconds = cfg.pump_run_seconds ?? 10;
    setText("pumpTriggerInline", String(trigger));
    setText("pumpSecondsInline", formatNum(runSeconds, 0));
    setText("pumpDuration", `${formatNum(runSeconds, 0)} s`);
    buildLegend(cfg.class_names || Object.keys(CLASS_COLORS));
  }

  function applyStats(stats) {
    state.stats = stats;
    const total = stats.total_objects || 0;
    setText("totalObjects", String(total));
    setText("chartTotal", String(total));
    updateLegendValues(stats);
    drawDonut(stats);
  }

  function applyDetections(payload) {
    setText("frameCount", String(payload.count ?? payload.detections?.length ?? 0));
    setText(
      "inferMs",
      payload.inference_ms != null ? formatNum(payload.inference_ms, 1) : "—"
    );
    setText("inferSource", payload.source || "—");
  }

  function applyCapture(capture, isNew = false) {
    if (!capture?.id) return;
    const id = String(capture.id);
    const imageUrl = `/api/captures/${encodeURIComponent(id)}/image`;
    const classes = (capture.class_names || []).join(", ") || "Residuo detectado";
    const timestamp = capture.ts ? new Date(capture.ts).toLocaleString() : "";
    const latestImage = $("latestCaptureImage");
    const emptyState = $("captureEmpty");
    if (latestImage) {
      latestImage.src = `${imageUrl}?v=${encodeURIComponent(id)}`;
      latestImage.hidden = false;
    }
    if (emptyState) emptyState.hidden = true;
    setText("latestCaptureClasses", classes);
    setText("latestCaptureTime", timestamp);

    if (isNew && !state.captureIds.has(id)) state.captureCount += 1;
    state.captureIds.add(id);
    setText("captureCount", `${state.captureCount} fotos`);

    const gallery = $("captureGallery");
    if (!gallery || gallery.querySelector(`[data-capture-id="${id}"]`)) return;
    const card = document.createElement("div");
    card.className = "capture-card";
    card.dataset.captureId = id;
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = `Captura clasificada: ${classes}`;
    image.loading = "lazy";
    const caption = document.createElement("span");
    caption.textContent = classes;
    card.append(image, caption);
    gallery.prepend(card);
    while (gallery.children.length > 6) gallery.lastElementChild.remove();
  }

  // ---------------------------------------------------------------------------
  // Bomba (progreso inferido por uploads recibidos)
  // ---------------------------------------------------------------------------
  function applyPumpStatus(pump) {
    state.pump = pump;
    const trigger = pump.pump_trigger_photos ?? state.config?.pump_trigger_photos ?? 3;
    const current = pump.photos_since_pump ?? 0;
    setText("pumpProgress", `${current} / ${trigger}`);
    setText("uploadsReceived", String(pump.uploads_received ?? 0));
    setText(
      "pumpDuration",
      pump.pump_run_seconds != null
        ? `${formatNum(pump.pump_run_seconds, 0)} s`
        : "— s"
    );
    setText(
      "pumpState",
      current > 0 ? `${current}/${trigger} fotos` : "esperando fotos"
    );

    const last = pump.last_pump_event;
    setText(
      "lastPumpEvent",
      last?.ts ? new Date(last.ts).toLocaleString() : "—"
    );
  }

  function showPumpAlert(pump) {
    const banner = $("alertBanner");
    const alertText = $("alertText");
    if (!banner) return;
    const seconds = pump.pump_run_seconds ?? state.config?.pump_run_seconds ?? 10;
    if (alertText) {
      alertText.textContent = `Bomba activada por la ESP32 (~${seconds} s).`;
    }
    banner.classList.add("visible");
    if (state.alertTimer) clearTimeout(state.alertTimer);
    state.alertTimer = setTimeout(
      () => banner.classList.remove("visible"),
      Math.max(3000, seconds * 1000)
    );
  }

  function formatNum(n, digits) {
    const x = Number(n);
    if (Number.isNaN(x)) return "—";
    return x.toFixed(digits);
  }

  // ---------------------------------------------------------------------------
  // Legend + Donut chart (Canvas 2D, sin dependencias)
  // ---------------------------------------------------------------------------
  function buildLegend(classNames) {
    const ul = $("classLegend");
    if (!ul) return;
    ul.innerHTML = "";
    classNames.forEach((name) => {
      const li = document.createElement("li");
      li.dataset.class = name;
      li.innerHTML = `
        <span class="legend-left">
          <span class="swatch ${name}" style="background:${CLASS_COLORS[name] || "#fff"}"></span>
          <span>${name}</span>
        </span>
        <span class="legend-value" data-value="${name}">0 (0%)</span>
      `;
      ul.appendChild(li);
    });
  }

  function updateLegendValues(stats) {
    const counters = stats.counters || {};
    const percentages = stats.percentages || {};
    Object.keys(counters).forEach((name) => {
      const el = document.querySelector(`[data-value="${name}"]`);
      if (!el) return;
      const count = counters[name] || 0;
      const pct = percentages[name] || 0;
      el.textContent = `${count} (${pct.toFixed(1)}%)`;
    });
  }

  function drawDonut(stats) {
    const canvas = $("donutChart");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) * 0.42;
    const thickness = radius * 0.34;

    ctx.clearRect(0, 0, w, h);

    const counters = stats.counters || {};
    const names = Object.keys(counters);
    const total = names.reduce((s, n) => s + (counters[n] || 0), 0);

    // Anillo vacío de fondo
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = thickness;
    ctx.stroke();

    if (total <= 0) return;

    let angle = -Math.PI / 2;
    names.forEach((name) => {
      const value = counters[name] || 0;
      if (value <= 0) return;
      const slice = (value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, angle, angle + slice);
      ctx.strokeStyle = CLASS_COLORS[name] || "#ffffff";
      ctx.lineWidth = thickness;
      ctx.lineCap = "butt";
      ctx.stroke();
      angle += slice;
    });
  }

  // ---------------------------------------------------------------------------
  // Configuración del dispositivo ESP32-CAM (admin)
  // ---------------------------------------------------------------------------
  const CFG_FIELDS = {
    wifi_ssid: "cfgWifiSsid",
    wifi_pass: "cfgWifiPass",
    server_url: "cfgServerUrl",
    pump_photos: "cfgPumpPhotos",
    pump_seconds: "cfgPumpSeconds",
    dist_min_cm: "cfgDistMin",
    dist_max_cm: "cfgDistMax",
  };

  function setDeviceConfigStatus(text, mode) {
    const el = $("deviceConfigState");
    if (!el) return;
    el.textContent = text;
    el.className = `status-pill ${mode || "offline"}`;
  }

  async function loadDeviceConfig() {
    try {
      const res = await fetch("/api/admin/device-config");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items = data.items || {};
      for (const [key, inputId] of Object.entries(CFG_FIELDS)) {
        const input = $(inputId);
        if (input) input.value = items[key] ?? "";
      }
      setDeviceConfigStatus("guardado", "online");
    } catch (err) {
      console.warn("Device config unavailable", err);
      setDeviceConfigStatus("sin conexión", "offline");
    }
  }

  async function saveDeviceConfig(ev) {
    ev.preventDefault();
    const payload = {};
    for (const [key, inputId] of Object.entries(CFG_FIELDS)) {
      const input = $(inputId);
      const value = input ? input.value.trim() : "";
      if (value !== "") payload[key] = value;
    }
    // wifi_ssid/pass van en pareja: si solo viene uno, el ESP32 esperaría
    // la otra mitad para siempre. Exigir ambas o ninguna.
    const hasSsid = "wifi_ssid" in payload;
    const hasPass = "wifi_pass" in payload;
    if (hasSsid !== hasPass) {
      setDeviceConfigStatus("WiFi incompleto", "offline");
      alert("Para cambiar el WiFi llena SSID y contraseña (o deja ambos vacíos).");
      return;
    }
    try {
      const res = await fetch("/api/admin/device-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setDeviceConfigStatus(`aplicado (${data.applied.length})`, "online");
    } catch (err) {
      console.warn("Save device config failed", err);
      setDeviceConfigStatus("error al guardar", "offline");
    }
  }

  // ---------------------------------------------------------------------------
  // Bootstrap: REST snapshot por si el WS tarda
  // ---------------------------------------------------------------------------
  async function bootstrapFromRest() {
    try {
      const [cfg, stats, pump] = await Promise.all([
        fetch("/api/config").then((r) => r.json()),
        fetch("/api/stats").then((r) => r.json()),
        fetch("/api/pump-status").then((r) => r.json()),
      ]);
      applyConfig(cfg);
      applyStats(stats);
      applyPumpStatus(pump);
    } catch (err) {
      console.warn("Bootstrap REST failed", err);
      buildLegend(Object.keys(CLASS_COLORS));
    }
  }

  async function bootstrapCaptures() {
    try {
      const response = await fetch("/api/captures?limit=6");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      state.captureCount = data.total || 0;
      setText("captureCount", `${state.captureCount} fotos`);
      const gallery = $("captureGallery");
      if (gallery) gallery.replaceChildren();
      state.captureIds.clear();
      for (const capture of data.items || []) applyCapture(capture);
    } catch (err) {
      console.warn("Capture history unavailable", err);
    }
  }

  // Ping periódico para keepalive
  setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 25000);

  const cfgForm = $("deviceConfigForm");
  if (cfgForm) cfgForm.addEventListener("submit", saveDeviceConfig);
  const cfgReload = $("deviceConfigReload");
  if (cfgReload) cfgReload.addEventListener("click", loadDeviceConfig);

  bootstrapFromRest();
  bootstrapCaptures();
  loadDeviceConfig();
  connectWs();
})();

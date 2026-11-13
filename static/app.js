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
    if (stats.device) applyDeviceStatus(stats.device);
  }

  function applyDeviceStatus(device) {
    const pill = $("deviceStatus");
    const txt = $("deviceStatusText");
    if (!pill || !txt) return;
    if (device.online) {
      pill.classList.add("online");
      pill.classList.remove("offline");
      txt.textContent = device.ip ? `ESP32: ${device.ip}` : "ESP32: en línea";
      pill.title = `ESP32-CAM en línea${device.ip ? ` (${device.ip})` : ""}`;
    } else {
      pill.classList.remove("online");
      pill.classList.add("offline");
      txt.textContent = "ESP32: sin señal";
      pill.title = "ESP32-CAM sin heartbeat (>150 s)";
    }
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

    // Reflejar en la galería completa si no está duplicada
    if (!galleryState.items.some((c) => String(c.id) === id)) {
      galleryState.items.unshift(capture);
      renderGallery();
    }
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
  // Galería completa (paginada, filtro por clase, lightbox)
  // ---------------------------------------------------------------------------
  const galleryState = {
    items: [],          // capturas cargadas (más reciente primero)
    offset: 0,
    limit: 24,
    loading: false,
    done: false,
    filter: "",
  };

  function galleryCard(capture) {
    const card = document.createElement("div");
    card.className = "capture-card";
    card.dataset.captureId = capture.id;
    const classes = (capture.class_names || []).join(", ") || "sin clase";
    const ts = capture.ts ? new Date(capture.ts).toLocaleString() : "";
    const img = document.createElement("img");
    img.src = `/api/captures/${encodeURIComponent(capture.id)}/image`;
    img.alt = `Captura ${capture.id}: ${classes}`;
    img.loading = "lazy";
    const meta = document.createElement("div");
    meta.className = "g-meta";
    const cls = document.createElement("span");
    cls.className = "g-cls";
    cls.textContent = classes;
    const time = document.createElement("span");
    time.className = "g-ts";
    time.textContent = `#${capture.id} · ${ts}`;
    meta.append(cls, time);
    card.append(img, meta);
    card.addEventListener("click", () => openLightbox(capture));
    return card;
  }

  function renderGallery() {
    const grid = $("fullGallery");
    if (!grid) return;
    grid.replaceChildren();
    const f = galleryState.filter.trim().toLowerCase();
    const seen = new Set();
    const shown = galleryState.items.filter((c) => {
      if (seen.has(String(c.id))) return false;
      seen.add(String(c.id));
      return !f || (c.class_names || []).join(" ").toLowerCase().includes(f);
    });
    for (const c of shown.slice(0, 120)) grid.appendChild(galleryCard(c));
    const empty = $("galleryEmpty");
    if (empty) empty.hidden = shown.length > 0;
    const more = $("galleryMore");
    if (more) more.style.display = galleryState.done ? "none" : "";
  }

  async function loadFullGallery(reset = false) {
    if (galleryState.loading) return;
    if (reset) {
      galleryState.items = [];
      galleryState.offset = 0;
      galleryState.done = false;
    }
    if (galleryState.done) { renderGallery(); return; }
    galleryState.loading = true;
    try {
      const res = await fetch(
        `/api/captures?limit=${galleryState.limit}&offset=${galleryState.offset}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items = data.items || [];
      galleryState.items.push(...items);
      galleryState.offset += items.length;
      galleryState.done = items.length < galleryState.limit;
      renderGallery();
    } catch (err) {
      console.warn("Gallery load failed", err);
    } finally {
      galleryState.loading = false;
    }
  }

  // Lightbox con detecciones detalladas
  async function openLightbox(capture) {
    const lb = $("lightbox");
    const img = $("lightboxImg");
    const meta = $("lightboxMeta");
    if (!lb || !img) return;
    img.src = `/api/captures/${encodeURIComponent(capture.id)}/image?v=${Date.now()}`;
    meta.textContent = `#${capture.id} · ${capture.ts ? new Date(capture.ts).toLocaleString() : ""}`;
    lb.hidden = false;
    try {
      const res = await fetch(`/api/captures/${capture.id}/detections`);
      if (res.ok) {
        const data = await res.json();
        const chips = document.createElement("div");
        for (const d of data.detections || []) {
          const chip = document.createElement("span");
          chip.className = "det-chip";
          chip.textContent = `${d.class_name} ${(d.confidence * 100).toFixed(0)}%`;
          chips.appendChild(chip);
        }
        meta.appendChild(chips);
      }
    } catch (_) { /* sin detalle: la imagen ya se ve */ }
  }

  function closeLightbox() {
    const lb = $("lightbox");
    if (lb) lb.hidden = true;
  }

  // ---------------------------------------------------------------------------
  // Bomba manual + eventos
  // ---------------------------------------------------------------------------
  async function runManualPump() {
    const input = $("manualPumpSeconds");
    const btn = $("manualPumpBtn");
    const stateEl = $("manualPumpState");
    const secs = parseInt(input?.value, 10);
    if (!secs || secs < 1 || secs > 300) {
      if (stateEl) stateEl.textContent = "Segundos inválidos (1-300).";
      return;
    }
    if (btn) btn.disabled = true;
    if (stateEl) stateEl.textContent = "Encolando…";
    try {
      const res = await fetch("/api/pump/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seconds: secs }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      if (stateEl) stateEl.textContent =
        `Orden encolada (${secs} s) — el ESP32 la ejecuta en su próximo poll.`;
      loadPumpEvents();
    } catch (err) {
      console.warn("pump/run failed", err);
      if (stateEl) stateEl.textContent = `Error: ${err.message}`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function loadPumpEvents() {
    const ul = $("pumpEventsList");
    const stateEl = $("manualPumpState");
    if (!ul) return;
    try {
      const res = await fetch("/api/pump/events?limit=8");
      if (!res.ok) return;
      const data = await res.json();
      ul.replaceChildren();
      const pending = data.pending_cmd;   // "<ts>:<secs>" si hay orden sin ack
      if (pending && stateEl && !stateEl.textContent.startsWith("Orden encolada")) {
        const secs = pending.split(":")[1] || "?";
        stateEl.textContent = `⏳ Orden pendiente de entrega: ${secs} s`;
      }
      for (const ev of data.items || []) {
        const li = document.createElement("li");
        const ts = ev.ts ? new Date(ev.ts).toLocaleString() : "";
        li.innerHTML = `<strong>${ts}</strong> — ${ev.detail || `${ev.photos_trigger} fotos`}`;
        ul.appendChild(li);
      }
    } catch (_) { /* silencioso: se reintenta en el próximo poll */ }
  }

  // ---------------------------------------------------------------------------
  // Gráfica de actividad (barras apiladas por clase, Canvas 2D)
  // ---------------------------------------------------------------------------
  async function loadTimeseries() {
    const sel = $("tsDays");
    const days = sel ? parseInt(sel.value, 10) || 14 : 14;
    try {
      const res = await fetch(`/api/stats/timeseries?days=${days}`);
      if (!res.ok) return;
      const data = await res.json();
      drawTsChart(data.series || [], data.class_names || []);
    } catch (_) { /* silencioso */ }
  }

  function drawTsChart(series, classNames) {
    const canvas = $("tsChart");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const legend = $("tsLegend");
    if (legend) {
      legend.replaceChildren();
      for (const n of classNames) {
        const li = document.createElement("li");
        li.innerHTML = `<span class="legend-left">
          <span class="swatch ${n}" style="background:${CLASS_COLORS[n] || "#fff"}"></span>
          <span>${n}</span></span>`;
        legend.appendChild(li);
      }
    }
    if (!series.length) {
      ctx.fillStyle = "rgba(255,255,255,0.4)";
      ctx.font = "13px sans-serif";
      ctx.fillText("Sin datos en el rango", 16, H / 2);
      return;
    }

    const padL = 30, padR = 8, padT = 12, padB = 26;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const totals = series.map((d) =>
      Object.values(d.detections || {}).reduce((s, n) => s + n, 0)
    );
    const maxV = Math.max(1, ...totals);

    // rejilla horizontal + etiquetas Y
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.font = "10px sans-serif";
    for (let i = 0; i <= 3; i++) {
      const y = padT + plotH - (plotH * i) / 3;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      ctx.fillText(String(Math.round((maxV * i) / 3)), 4, y + 3);
    }

    const n = series.length;
    const slot = plotW / n;
    const barW = Math.min(46, slot * 0.62);

    series.forEach((day, i) => {
      const x = padL + slot * i + (slot - barW) / 2;
      let acc = 0;
      for (const cls of classNames) {
        const v = day.detections?.[cls] || 0;
        if (v <= 0) continue;
        const bh = (v / maxV) * plotH;
        const y = padT + plotH - acc - bh;
        ctx.fillStyle = CLASS_COLORS[cls] || "#888";
        ctx.fillRect(x, y, barW, bh);
        acc += bh;
      }
      // etiqueta del día
      ctx.fillStyle = "rgba(255,255,255,0.5)";
      ctx.textAlign = "center";
      ctx.fillText(day.date.slice(5), x + barW / 2, H - 8);
      ctx.textAlign = "left";
    });
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

  // Poll ligero de stats: actualiza contadores y el estado del ESP32
  // aunque no haya capturas nuevas (heartbeat cada ~60 s del firmware).
  setInterval(async () => {
    try {
      const stats = await fetch("/api/stats").then((r) => r.json());
      applyStats(stats);
      loadPumpEvents();       // refresca eventos + estado de orden pendiente
      loadTimeseries();       // gráfica de actividad
    } catch (_) { /* red local: reintenta en el próximo ciclo */ }
  }, 30000);

  const cfgForm = $("deviceConfigForm");
  if (cfgForm) cfgForm.addEventListener("submit", saveDeviceConfig);
  const cfgReload = $("deviceConfigReload");
  if (cfgReload) cfgReload.addEventListener("click", loadDeviceConfig);

  // --- Módulos nuevos ---
  const galleryMoreBtn = $("galleryMore");
  if (galleryMoreBtn) galleryMoreBtn.addEventListener("click", () => loadFullGallery(false));
  const galleryFilter = $("galleryFilter");
  if (galleryFilter) galleryFilter.addEventListener("input", () => {
    galleryState.filter = galleryFilter.value;
    renderGallery();
  });
  const pumpBtn = $("manualPumpBtn");
  if (pumpBtn) pumpBtn.addEventListener("click", runManualPump);
  const tsDays = $("tsDays");
  if (tsDays) tsDays.addEventListener("change", loadTimeseries);
  const lbClose = $("lightboxClose");
  if (lbClose) lbClose.addEventListener("click", closeLightbox);
  const lb = $("lightbox");
  if (lb) lb.addEventListener("click", (e) => { if (e.target === lb) closeLightbox(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLightbox(); });

  bootstrapFromRest();
  bootstrapCaptures();
  loadDeviceConfig();
  loadFullGallery(true);
  loadPumpEvents();
  loadTimeseries();
  connectWs();
})();

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
    unicel: "#67e8f9",
    metal: "#fbbf24",
  };

  const state = {
    config: null,
    latestImg: null,        // <img> del visor principal, creada al primer dato
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
    captureDets: new Map(),   // captureId -> detections[] (confianza por clase)
    currentCapture: null,     // captura mostrada en el visor principal
    deviceLastSeen: null,     // epoch s del último heartbeat del ESP32
    deviceOnline: false,      // último estado online del ESP32

    donutHover: null,         // clase resaltada en el donut (null = normal)
    tsGeom: null,             // geometría de la gráfica de actividad (tooltips)
    tsData: null,             // {series, classNames} cacheado para repintar al cambiar tema
    pumpSegCount: 0,          // segmentos renderizados en la barra de progreso
    lastWsMsg: null,          // epoch ms del último mensaje WS recibido
    wsOnline: false,          // último estado del WebSocket
    deviceIp: null,           // IP del ESP32 en su último contacto
    deviceLastPoll: null,     // epoch s del último GET /api/device/config
    orders: { items: [], pending: 0 },  // órdenes al dispositivo (ciclo de vida)
    perf: null,               // último /api/perf cacheado
    netMs: null,              // RTT medido al servidor
    cfgMeta: {},              // {key: updated_ts} de overrides del dispositivo
    cfgSavedAt: 0,            // ms epoch del último guardado de config
    cfgPayload: null,         // payload enviado en el último guardado
    busy: { pump: false, capture: false, reconnect: false },
  };

  // ---------------------------------------------------------------------------
  // Helpers DOM
  // ---------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  // Lee una variable CSS del tema activo (los canvas no heredan var() solos)
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
    return v || fallback;
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
    state.wsOnline = online;
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
      state.lastWsMsg = Date.now();
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
      case "pump":
        // ACK de una orden de bomba: el servidor ya fijó pump_active_until.
        applyPumpStatus(msg.payload || {});
        loadCommands();
        break;
      case "orders":
        loadCommands();
        break;
      case "stats":
        applyStats(msg.payload || {});
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
    buildPumpSegments(trigger);
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
    state.deviceOnline = !!device.online;
    state.deviceIp = device.ip || null;
    state.deviceLastPoll = device.last_poll_ts ?? null;
    // Empty state global: sin señal se ocultan las métricas muertas y se
    // muestra el aviso "esperando conexión" en vez del mar de "—".
    document.body.classList.toggle("device-offline", !device.online);
    if (!pill || !txt) return;
    state.deviceLastSeen = device.last_seen ?? null;
    updatePingNote();
    if (device.online) {
      pill.classList.add("online");
      pill.classList.remove("offline");
      txt.textContent = device.ip ? `ESP32 · ${device.ip}` : "ESP32 · en línea";
      pill.title = `ESP32-CAM en línea${device.ip ? ` (${device.ip})` : ""}`;
      // Al volver la señal se redibuja la bomba con el último dato cacheado
      // (el poll de stats no vuelve a pedir /api/pump-status).
      if (state.pump) applyPumpStatus(state.pump);
    } else {
      pill.classList.remove("online");
      pill.classList.add("offline");
      txt.textContent = "ESP32 · sin señal";
      pill.title = "ESP32-CAM sin heartbeat (>150 s)";
      setText("pumpState", "sin señal");
    }
    updateEmptyStates();
    refreshOverview();
  }

  // "Ping · hace 2 min" — última vez que el ESP32 contactó al servidor
  function updatePingNote() {
    const el = $("lastPingText");
    if (!el) return;
    const last = state.deviceLastSeen;
    if (!last) {
      el.textContent = "Ping · sin contacto";
      return;
    }
    const secs = Math.max(0, Date.now() / 1000 - last);
    let rel;
    if (secs < 60) rel = "ahora";
    else if (secs < 3600) rel = `hace ${Math.floor(secs / 60)} min`;
    else if (secs < 86400) rel = `hace ${Math.floor(secs / 3600)} h`;
    else rel = `hace ${Math.floor(secs / 86400)} d`;
    el.textContent = `Ping · ${rel}`;
    el.title = `Último contacto del ESP32-CAM: ${new Date(last * 1000).toLocaleString()}`;
  }

  function applyDetections(payload) {
    const vmeta = document.querySelector(".video-meta");
    if (vmeta) vmeta.hidden = false;
    setText("frameCount", String(payload.count ?? payload.detections?.length ?? 0));
    setText(
      "inferMs",
      payload.inference_ms != null ? formatNum(payload.inference_ms, 1) : "—"
    );
    setText("inferSource", payload.source || "—");

    // Cachear detecciones por captura para el badge de confianza
    const lc = payload?.latest_capture;
    if (lc?.id != null && Array.isArray(payload?.detections)) {
      state.captureDets.set(String(lc.id), payload.detections);
      if (state.currentCapture && String(state.currentCapture.id) === String(lc.id)) {
        updateCaptureBadge(state.currentCapture);
      }
    }
  }

  function applyCapture(capture, isNew = false) {
    if (!capture?.id) return;
    const id = String(capture.id);
    const imageUrl = `/api/captures/${encodeURIComponent(id)}/image`;
    const classes = (capture.class_names || []).join(", ") || "Residuo detectado";
    const emptyState = $("captureEmpty");
    // La <img> solo existe cuando hay captura real: nada de src vacío ni
    // etiquetas rotas en el DOM antes de la primera foto.
    let latestImage = state.latestImg;
    if (!latestImage) {
      const frame = document.querySelector(".capture-frame");
      latestImage = document.createElement("img");
      latestImage.id = "latestCaptureImage";
      latestImage.alt = "Última foto clasificada";
      latestImage.onerror = () => {
        latestImage.hidden = true;
        if (emptyState) {
          emptyState.hidden = false;
          emptyState.textContent = "Imagen no disponible para esta captura.";
        }
      };
      if (frame) frame.prepend(latestImage);
      state.latestImg = latestImage;
    }
    latestImage.src = `${imageUrl}?v=${encodeURIComponent(id)}`;
    latestImage.hidden = false;
    if (emptyState) emptyState.hidden = true;
    const vmeta = document.querySelector(".video-meta");
    if (vmeta) vmeta.hidden = false;
    setText("latestCaptureClasses", classes);
    setText("latestCaptureTime", formatCaptureTs(capture.ts));
    // Metadatos técnicos de la captura mostrada (no solo de la última inferencia)
    setText("frameCount", String(capture.detection_count ?? 0));
    setText(
      "inferMs",
      capture.inference_ms != null ? formatNum(capture.inference_ms, 1) : "—"
    );
    setText("inferSource", capture.source || "—");
    state.currentCapture = capture;
    updateCaptureBadge(capture);
    markActiveThumb(id);

    if (isNew && !state.captureIds.has(id)) state.captureCount += 1;
    state.captureIds.add(id);
    setText("captureCount", `${state.captureCount} fotos`);

    const gallery = $("captureGallery");
    if (!gallery || gallery.querySelector(`[data-capture-id="${id}"]`)) return;
    const card = document.createElement("div");
    card.className = "capture-card";
    card.dataset.captureId = id;
    card._capture = capture;
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = `Captura clasificada: ${classes}`;
    image.loading = "lazy";
    const tag = document.createElement("span");
    tag.className = "thumb-tag";
    tag.textContent = formatHM(capture.ts) || `#${id}`;
    card.append(image, tag);
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `Ver captura ${id}: ${classes}`);
    card.addEventListener("click", () => applyCapture(capture));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        applyCapture(capture);
      }
    });
    gallery.prepend(card);
    while (gallery.children.length > 8) gallery.lastElementChild.remove();

    // Reflejar en la galería completa si no está duplicada
    if (!galleryState.items.some((c) => String(c.id) === id)) {
      galleryState.items.unshift(capture);
      renderGallery();
    }
  }

  // -------------------------------------------------------------------------
  // Helpers de formato y badge de la captura mostrada
  // -------------------------------------------------------------------------
  function formatHM(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "";
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function formatCaptureTs(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "—";
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function markActiveThumb(id) {
    const strip = $("captureGallery");
    if (!strip) return;
    strip.querySelectorAll(".capture-card").forEach((c) => {
      c.classList.toggle("is-active", c.dataset.captureId === String(id));
    });
  }

  // Badge superpuesto: "CLASE · 94%" / "CLASE +N · 94%" / "SIN DETECCIÓN"
  function updateCaptureBadge(capture) {
    const badge = $("captureBadge");
    if (!badge || !capture) return;
    badge.hidden = false;
    const id = String(capture.id);
    const names = capture.class_names || [];
    const count = capture.detection_count ?? names.length;
    const dets = state.captureDets.get(id);

    if (!count) {
      badge.textContent = "Sin detección";
      badge.classList.add("is-empty");
      badge.style.background = "";
      return;
    }
    if (dets && dets.length) {
      const top = dets.reduce((a, b) => (b.confidence > a.confidence ? b : a), dets[0]);
      const extra = dets.length > 1 ? ` +${dets.length - 1}` : "";
      badge.textContent = `${top.class_name} · ${Math.round(top.confidence * 100)}%${extra}`;
      badge.style.background = CLASS_COLORS[top.class_name] || "#8b96a3";
      badge.classList.remove("is-empty");
      return;
    }
    // Sin detalle de confianza todavía: resumir clases y pedir el detalle
    badge.textContent =
      names.slice(0, 2).map((n) => n.toUpperCase()).join(" + ") +
      (names.length > 2 ? ` +${names.length - 2}` : "");
    badge.style.background = "#8b96a3";
    badge.classList.remove("is-empty");
    if (!state.captureDets.has(id)) fetchCaptureDetections(id);
  }

  async function fetchCaptureDetections(id) {
    try {
      const res = await fetch(`/api/captures/${id}/detections`);
      if (!res.ok) return;
      const data = await res.json();
      state.captureDets.set(String(id), data.detections || []);
      if (state.currentCapture && String(state.currentCapture.id) === String(id)) {
        updateCaptureBadge(state.currentCapture);
      }
    } catch (_) { /* silencioso: el badge queda con el resumen de clases */ }
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
    // Contador permanente de la topbar: siempre visible en las 3 vistas.
    const topPill = $("topPumpPill");
    if (topPill) {
      setText("topPumpText", `Bomba · ${current}/${trigger}`);
      topPill.classList.toggle("counting", current > 0);
    }
    updateTopTimer();
    setText(
      "pumpDuration",
      pump.pump_run_seconds != null
        ? `${formatNum(pump.pump_run_seconds, 0)} s`
        : "— s"
    );
    setText(
      "pumpState",
      !state.deviceOnline
        ? "sin señal"
        : current > 0
          ? `${current}/${trigger} fotos`
          : "esperando fotos"
    );
    updatePumpSegments(current, trigger);

    const last = pump.last_pump_event;
    setText(
      "lastPumpEvent",
      last?.ts ? new Date(last.ts).toLocaleString() : "aún no"
    );
    refreshOverview();
  }

  // Barra segmentada: un segmento por foto hacia el ciclo; el siguiente
  // paso queda marcado para anticipar el disparo de la bomba.
  function buildPumpSegments(trigger) {
    const bar = $("pumpSegBar");
    if (!bar) return;
    const n = Math.max(1, Math.min(24, trigger || 3));
    bar.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const seg = document.createElement("i");
      seg.className = "seg";
      bar.appendChild(seg);
    }
    state.pumpSegCount = n;
  }

  function updatePumpSegments(current, trigger) {
    const want = Math.max(1, Math.min(24, trigger || 3));
    if (want !== state.pumpSegCount) buildPumpSegments(trigger);
    const bar = $("pumpSegBar");
    if (!bar) return;
    const segs = bar.children;
    for (let i = 0; i < segs.length; i++) {
      segs[i].classList.toggle("on", i < current);
      segs[i].classList.toggle("next", i === current);
    }
  }

  function showPumpAlert(pump) {
    const banner = $("alertBanner");
    const alertText = $("alertText");
    if (!banner) return;
    const log = $("pumpEventLog");
    if (log) log.open = true;
    const seconds = pump.pump_run_seconds ?? state.config?.pump_run_seconds ?? 10;
    if (alertText) {
      alertText.textContent = `Bomba activada por la ESP32 (~${seconds} s).`;
    }
    banner.hidden = false;
    banner.classList.add("visible");
    if (state.alertTimer) clearTimeout(state.alertTimer);
    state.alertTimer = setTimeout(
      () => {
        banner.classList.remove("visible");
        banner.hidden = true;
      },
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

  // Leyenda interactiva: resalta el arco correspondiente en el donut
  function bindDonutLegend() {
    const legend = $("classLegend");
    if (!legend) return;
    legend.addEventListener("mouseover", (e) => {
      const li = e.target.closest("li[data-class]");
      state.donutHover = li ? li.dataset.class : null;
      drawDonut(state.stats);
    });
    legend.addEventListener("mouseout", (e) => {
      if (!legend.contains(e.relatedTarget)) {
        state.donutHover = null;
        drawDonut(state.stats);
      }
    });
    legend.addEventListener("click", (e) => {
      const li = e.target.closest("li[data-class]");
      if (!li) return;
      state.donutHover = state.donutHover === li.dataset.class ? null : li.dataset.class;
      drawDonut(state.stats);
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
    const thickness = radius * 0.36;

    ctx.clearRect(0, 0, w, h);

    const counters = stats.counters || {};
    const names = Object.keys(counters);
    const total = names.reduce((s, n) => s + (counters[n] || 0), 0);

    // Anillo vacío de fondo
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.strokeStyle = cssVar("--chart-ring", "rgba(255,255,255,0.07)");
    ctx.lineWidth = thickness;
    ctx.stroke();

    if (total <= 0) return;

    const hi = state.donutHover;
    const gap = 0.018; // separación angular entre segmentos
    let angle = -Math.PI / 2;
    names.forEach((name) => {
      const value = counters[name] || 0;
      if (value <= 0) return;
      const slice = (value / total) * Math.PI * 2;
      const seg = Math.max(0.01, slice - gap * 2);
      ctx.globalAlpha = hi && name !== hi ? 0.18 : 1;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, angle + gap, angle + gap + seg);
      ctx.strokeStyle = CLASS_COLORS[name] || "#ffffff";
      ctx.lineWidth = thickness;
      ctx.lineCap = "butt";
      ctx.stroke();
      ctx.globalAlpha = 1;
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
    server_url_alt: "cfgServerUrlAlt",
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

  async function loadDeviceConfig(silent = false) {
    try {
      const res = await fetch("/api/admin/device-config");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items = data.items || {};
      state.cfgMeta = data.updated || {};
      for (const [key, inputId] of Object.entries(CFG_FIELDS)) {
        const input = $(inputId);
        if (input) input.value = items[key] ?? "";
      }
      setDeviceConfigStatus("Cargado", "online");
      updateCfgDelivery();
      updateOtaCurrent();
    } catch (err) {
      if (!silent) console.warn("Device config unavailable", err);
      setDeviceConfigStatus("sin conexión", "offline");
    }
  }

  // Solo refresca la metadata (updated_ts) sin tocar el formulario:
  // permite detectar "descargada por el ESP32" tras un guardado.
  async function refreshCfgMeta() {
    try {
      const res = await fetch("/api/admin/device-config");
      if (!res.ok) return;
      const data = await res.json();
      state.cfgMeta = data.updated || {};
      updateCfgDelivery();
      updateOtaCurrent();
    } catch (_) { /* silencioso */ }
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
    // Un rango invertido confunde al sensor de distancia: validar aquí
    // además del clamp del firmware.
    const dMin = parseFloat(payload.dist_min_cm ?? $("cfgDistMin")?.value);
    const dMax = parseFloat(payload.dist_max_cm ?? $("cfgDistMax")?.value);
    if (!Number.isNaN(dMin) && !Number.isNaN(dMax) && dMin >= dMax) {
      setDeviceConfigStatus("rango inválido", "offline");
      alert("La distancia mínima de disparo debe ser menor que la máxima.");
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
      // Marcamos el guardado para la línea de entrega: "guardado en el
      // servidor" → "descargada por el ESP32" cuando su poll sea posterior.
      state.cfgSavedAt = Date.now();
      state.cfgPayload = payload;
      setDeviceConfigStatus(
        data.applied.length || data.removed.length
          ? "Guardado ✓"
          : "Sin cambios",
        "online"
      );
      refreshCfgMeta();
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
    const classes = capture.class_names || [];
    const ts = capture.ts ? new Date(capture.ts) : null;
    const hm = ts && !Number.isNaN(ts.getTime())
      ? `${String(ts.getHours()).padStart(2, "0")}:${String(ts.getMinutes()).padStart(2, "0")}`
      : "—";
    const img = document.createElement("img");
    img.src = `/api/captures/${encodeURIComponent(capture.id)}/image`;
    img.alt = `Captura ${capture.id}: ${classes.join(", ") || "sin clase"}`;
    img.loading = "lazy";

    const badges = document.createElement("div");
    badges.className = "g-badges";
    const shown = classes.slice(0, 3);
    for (const name of shown) {
      const tag = document.createElement("span");
      tag.className = "g-tag";
      tag.style.background = CLASS_COLORS[name] || "#8b96a3";
      tag.textContent = name;
      badges.appendChild(tag);
    }
    if (classes.length > shown.length) {
      const more = document.createElement("span");
      more.className = "g-tag";
      more.style.background = cssVar("--seg-bg", "rgba(255,255,255,0.14)");
      more.style.color = cssVar("--muted", "#c7d0dd");
      more.textContent = `+${classes.length - shown.length}`;
      badges.appendChild(more);
    }
    const idEl = document.createElement("span");
    idEl.className = "g-id";
    idEl.textContent = `#${capture.id} · ${hm}`;
    badges.appendChild(idEl);

    card.append(img, badges);
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `Ampliar captura ${capture.id}`);
    card.addEventListener("click", () => openLightbox(capture));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openLightbox(capture);
      }
    });
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
    img.hidden = false;
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
    const img = $("lightboxImg");
    if (img) { img.hidden = true; img.removeAttribute("src"); }
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
      if (stateEl) {
        stateEl.textContent = "Segundos inválidos (1-300).";
        stateEl.classList.remove("is-queued");
      }
      return;
    }
    state.busy.pump = true;
    syncCommandButtons();
    if (stateEl) {
      stateEl.textContent = "Encolando…";
      stateEl.classList.remove("is-queued");
    }
    try {
      const res = await fetch("/api/pump/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seconds: secs }),
      });
      const data = await res.json();
      if (res.status === 409) {
        if (stateEl) {
          stateEl.textContent = `⚠ ${data.detail || "Ya hay una orden de bomba en cola."}`;
          stateEl.classList.remove("is-queued");
        }
        loadCommands();
        return;
      }
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const devOffline = data.device && data.device.online === false;
      if (stateEl) {
        stateEl.textContent = devOffline
          ? "Comando en cola · ESP32 sin señal — expirará en ~10 min si no se conecta."
          : "Comando en cola · el ESP32 lo recoge en su próximo poll (≤60 s).";
        stateEl.classList.add("is-queued");
      }
      if (btn) {
        btn.textContent = "En cola ✓";
        setTimeout(() => {
          if (btn) btn.textContent = "Activar bomba";
        }, 3500);
      }
      // La orden manual reinicia el contador de fotos (server + firmware):
      // reflejarlo ya en el panel y en la topbar sin esperar al próximo poll.
      if (state.pump) {
        state.pump = { ...state.pump, photos_since_pump: 0 };
        applyPumpStatus(state.pump);
      }
      loadPumpEvents();
      loadCommands();
    } catch (err) {
      console.warn("pump/run failed", err);
      if (stateEl) {
        stateEl.textContent = `Error: ${err.message}`;
        stateEl.classList.remove("is-queued");
      }
    } finally {
      state.busy.pump = false;
      syncCommandButtons();
    }
  }

  async function runManualCapture() {
    const btn = $("manualCaptureBtn");
    const stateEl = $("manualCaptureState");
    state.busy.capture = true;
    syncCommandButtons();
    if (stateEl) {
      stateEl.textContent = "Encolando…";
      stateEl.classList.remove("is-queued");
    }
    try {
      const res = await fetch("/api/capture/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (res.status === 409) {
        if (stateEl) {
          stateEl.textContent = `⚠ ${data.detail || "Ya hay una orden de foto en cola."}`;
          stateEl.classList.remove("is-queued");
        }
        loadCommands();
        return;
      }
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const devOffline = data.device && data.device.online === false;
      if (stateEl) {
        stateEl.textContent = devOffline
          ? "Orden en cola · ESP32 sin señal — expirará en ~10 min si no se conecta."
          : "Orden en cola · el ESP32 la recoge en su próximo poll (≤60 s).";
        stateEl.classList.add("is-queued");
      }
      if (btn) {
        btn.textContent = "En cola ✓";
        setTimeout(() => {
          if (btn) btn.textContent = "Pedir foto al ESP32";
        }, 3500);
      }
      loadCommands();
    } catch (err) {
      console.warn("capture/run failed", err);
      if (stateEl) {
        stateEl.textContent = `Error: ${err.message}`;
        stateEl.classList.remove("is-queued");
      }
    } finally {
      state.busy.capture = false;
      syncCommandButtons();
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
      if (pending && stateEl &&
          !stateEl.textContent.startsWith("Comando en cola")) {
        const secs = pending.split(":")[1] || "?";
        stateEl.textContent = `⏳ Comando en cola (esperando poll) — ${secs} s`;
        stateEl.classList.add("is-queued");
      }
      const items = data.items || [];
      if (!items.length) {
        const li = document.createElement("li");
        li.className = "empty";
        li.textContent = "Sin eventos registrados";
        ul.appendChild(li);
      }
      for (const ev of items) {
        const li = document.createElement("li");
        const ts = ev.ts ? new Date(ev.ts).toLocaleString() : "";
        li.innerHTML = `<strong>${ts}</strong> — ${ev.detail || `${ev.photos_trigger} fotos`}`;
        ul.appendChild(li);
      }
      setText("pumpEventsCount", String(items.length));
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
      state.tsData = { series: data.series || [], classNames: data.class_names || [] };
      drawTsChart(state.tsData.series, state.tsData.classNames);
    } catch (_) { /* silencioso */ }
  }

  function drawTsChart(series, classNames) {
    const canvas = $("tsChart");
    if (!canvas) return;
    // Resolución adaptable: el canvas sigue el ancho real del panel
    // (importante en celular, donde 520px fijos se verían borrosos).
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 520;
    const cssH = Math.round(cssW * 0.36);
    if (canvas.width !== Math.round(cssW * dpr) ||
        canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      canvas.style.height = `${cssH}px`;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = cssW, H = cssH;
    ctx.clearRect(0, 0, W, H);
    const MONO = "'JetBrains Mono', monospace";

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
      state.tsGeom = null;
      ctx.fillStyle = cssVar("--chart-sub", "rgba(255,255,255,0.4)");
      ctx.font = `11px ${MONO}`;
      ctx.textAlign = "center";
      ctx.fillText("SIN DATOS EN EL RANGO", W / 2, H / 2);
      ctx.textAlign = "left";
      return;
    }

    const padL = 36, padR = 8, padT = 14, padB = 26;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const totals = series.map((d) =>
      Object.values(d.detections || {}).reduce((s, n) => s + n, 0)
    );
    const maxV = Math.max(1, ...totals);

    // Paso "bonito" para el eje Y: 1, 2 o 5 × 10^n (legible en rangos 0-5)
    const rawStep = maxV / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const norm = rawStep / mag;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
    const yMax = Math.max(step, Math.ceil(maxV / step) * step);

    // Rejilla horizontal + etiquetas Y (monoespaciadas, tabulares)
    ctx.font = `10px ${MONO}`;
    ctx.textBaseline = "middle";
    for (let v = 0; v <= yMax + 1e-6; v += step) {
      const y = padT + plotH - (v / yMax) * plotH;
      ctx.strokeStyle = cssVar("--chart-grid", "rgba(255,255,255,0.08)");
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      ctx.fillStyle = cssVar("--chart-text", "rgba(255,255,255,0.5)");
      ctx.textAlign = "right";
      ctx.fillText(String(Math.round(v)), padL - 7, y);
    }
    // Eje X base
    ctx.strokeStyle = cssVar("--chart-axis", "rgba(255,255,255,0.16)");
    ctx.beginPath(); ctx.moveTo(padL, padT + plotH); ctx.lineTo(W - padR, padT + plotH); ctx.stroke();

    const n = series.length;
    const slot = plotW / n;
    const barW = Math.min(44, slot * 0.6);
    const labelEvery = Math.max(1, Math.ceil(n / 10));
    const geom = [];

    series.forEach((day, i) => {
      const x = padL + slot * i + (slot - barW) / 2;
      let acc = 0;
      for (const cls of classNames) {
        const v = day.detections?.[cls] || 0;
        if (v <= 0) continue;
        const bh = (v / yMax) * plotH;
        const y = padT + plotH - acc - bh;
        ctx.fillStyle = CLASS_COLORS[cls] || "#888";
        ctx.fillRect(x, y, barW, bh);
        acc += bh;
      }
      // Etiqueta del día (dd/mm), espaciada para no saturar
      if (i % labelEvery === 0 || i === n - 1) {
        ctx.fillStyle = cssVar("--chart-sub", "rgba(255,255,255,0.45)");
        ctx.textAlign = "center";
        ctx.fillText(
          `${day.date.slice(8, 10)}/${day.date.slice(5, 7)}`,
          x + barW / 2,
          H - 9
        );
      }
      geom.push({ cx: x + barW / 2, day });
    });
    ctx.textAlign = "left";

    state.tsGeom = { geom, slot, classNames, wrapW: W };
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
      const response = await fetch("/api/captures?limit=8");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      state.captureCount = data.total || 0;
      setText("captureCount", `${state.captureCount} fotos`);
      const gallery = $("captureGallery");
      if (gallery) gallery.replaceChildren();
      state.captureIds.clear();
      // items llega DESC (más reciente primero): aplicar en orden inverso
      // para que el visor principal y la tira queden con el más reciente arriba.
      for (const capture of [...(data.items || [])].reverse()) {
        applyCapture(capture);
      }
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
  const capBtn = $("manualCaptureBtn");
  if (capBtn) capBtn.addEventListener("click", runManualCapture);
  const tsDays = $("tsDays");
  if (tsDays) tsDays.addEventListener("change", loadTimeseries);
  let tsResizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(tsResizeTimer);
    tsResizeTimer = setTimeout(loadTimeseries, 250);
  });
  const lbClose = $("lightboxClose");
  if (lbClose) lbClose.addEventListener("click", closeLightbox);
  const lb = $("lightbox");
  if (lb) lb.addEventListener("click", (e) => { if (e.target === lb) closeLightbox(); });
  wireConfirmModal();
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if ($("confirmModal") && !$("confirmModal").hidden) {
      closeConfirmModal(false);
    } else {
      closeLightbox();
    }
  });
  const btnOta = $("btnOta");
  if (btnOta) btnOta.addEventListener("click", programOta);

  // --- Botones de reset / comandos al dispositivo ---
  const btnClearAlbum = $("btnClearAlbum");
  if (btnClearAlbum) btnClearAlbum.addEventListener("click", () => resetServerData("photos", btnClearAlbum));
  const btnResetAll = $("btnResetAll");
  if (btnResetAll) btnResetAll.addEventListener("click", () => resetServerData("all", btnResetAll));
  const btnQueueClear = $("btnQueueClear");
  if (btnQueueClear) btnQueueClear.addEventListener("click", () => queueClearEsp(btnQueueClear));
  const btnReconnect = $("btnReconnect");
  if (btnReconnect) btnReconnect.addEventListener("click", requestReconnect);

  // ---------------------------------------------------------------------------
  // Rendimiento en tiempo real + estado del circuito + reset de datos
  // ---------------------------------------------------------------------------
  function fmtAgo(ms) {
    if (ms == null || Number.isNaN(ms)) return "—";
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 5) return "ahora";
    if (s < 60) return `hace ${s} s`;
    if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
    return `hace ${Math.floor(s / 3600)} h`;
  }

  function fmtUptime(s) {
    if (s == null) return "—";
    s = Math.round(s);
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
    return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
  }

  // RTT al servidor: fetch cronometrado a /api/health (cada 5 s)
  async function measureNetLatency() {
    const el = $("mNetMs");
    if (!el) return;
    const t0 = performance.now();
    try {
      await fetch(`/api/health?_=${Date.now()}`, { cache: "no-store" });
      const ms = Math.round(performance.now() - t0);
      state.netMs = ms;
      el.textContent = String(ms);
      el.classList.remove("warn");
    } catch {
      state.netMs = null;
      el.textContent = "—";
      el.classList.add("warn");
    }
  }

  async function loadPerf() {
    try {
      const perf = await fetch("/api/perf").then((r) => r.json());
      state.perf = perf;
      const inf = perf.inference || {};
      setText("mInferCount", inf.count ? String(inf.count) : "—");
      setText("mInferAvg", inf.count ? String(inf.avg) : "—");
      setText("mInferP95", inf.count ? String(inf.p95) : "—");
      setText("mInferMin", inf.count ? String(inf.min) : "—");
      setText("mInferMax", inf.count ? String(inf.max) : "—");
      setText("mUploadsH", String(perf.uploads_last_hour ?? "—"));
      setText("mCapturesTotal", String(perf.server?.captures_total ?? "—"));
      const upd = $("inferUpdated");
      if (upd) upd.textContent = `actualizado ${new Date().toLocaleTimeString()}`;
      setText(
        "mHeartbeat",
        perf.device?.heartbeat_age_s != null
          ? fmtAgo(perf.device.heartbeat_age_s * 1000)
          : "sin contacto"
      );
      setText("mPollsH", String(perf.device?.polls_last_hour ?? "—"));
      setText("mUptime", fmtUptime(perf.server?.uptime_s));
      setText("mWsClients", String(perf.server?.ws_clients ?? "—"));
      const updSys = $("perfUpdated");
      if (updSys) updSys.textContent = `actualizado ${new Date().toLocaleTimeString()}`;
      state.telemetry = perf.telemetry || null;
      refreshOverview();
    } catch (_) { /* reintenta en el próximo ciclo */ }
  }

  // Edad del último mensaje WS + RTT — refresco ligero cada 5 s
  function updateRealtimeBits() {
    const el = $("mWsAge");
    if (el) {
      el.textContent = state.lastWsMsg ? fmtAgo(Date.now() - state.lastWsMsg) : "—";
      el.classList.toggle("warn", !state.lastWsMsg || Date.now() - state.lastWsMsg > 90000);
    }
    updateCfgDelivery();
    refreshOverview();
  }

  async function loadCircuit() {
    const pill = $("circuitState");
    try {
      const [devRes, pumpRes] = await Promise.all([
        fetch("/api/device/status").then((r) => r.json()),
        fetch("/api/pump-status").then((r) => r.json()),
      ]);
      const dev = devRes.device || {};
      const tel = devRes.telemetry || state.telemetry || {};
      const hasTel = Object.keys(tel).length > 0;
      state.telemetry = tel;

      // pump-status ya viene en esta misma respuesta: refrescar la bomba
      // aquí evita esperar al broadcast WS para ver el countdown del ACK.
      applyPumpStatus(pumpRes);

      // Corrección del countdown de la topbar con la telemetría real del
      // ESP32 (pump_rest_s, cada ~60 s): el deadline estimado por el server
      // puede derivar unos segundos; el dato de la placa es el verdadero.
      // Va DESPUÉS de applyPumpStatus para no ser sobrescrito.
      if (tel.pump_on && tel.pump_rest_s != null && state.pump) {
        state.pump = { ...state.pump, pump_active_until: Date.now() / 1000 + tel.pump_rest_s };
        updateTopTimer();
      }

      const neverSeen = !dev.last_seen;
      setText("cDevState",
        neverSeen ? "nunca conectado" : (dev.online ? "EN LÍNEA" : "sin señal"));
      if (pill) {
        pill.textContent = neverSeen ? "nunca" : (dev.online ? "en línea" : "offline");
        pill.className = `status-pill ${dev.online ? "online" : "offline"}`;
      }
      setText(
        "cDevIp",
        `${dev.ip || "—"} · ${dev.last_seen ? fmtAgo((Date.now() / 1000 - dev.last_seen) * 1000) : "sin contacto"}`
      );
      setText(
        "cPolls",
        dev.polls_total != null
          ? `${dev.polls_total} total · ${dev.polls_last_hour ?? 0}/h`
          : "—"
      );

      const t = (id, v) => setText(id, hasTel ? v : "sin datos");
      t("cDist", tel.dist_cm != null ? `${tel.dist_cm} cm` : "—");
      // rele_pin es el nivel crudo del GPIO (activo en bajo, bomba en NC):
      // pin HIGH -> bomba encendida; pin LOW -> bomba apagada.
      t("cRelay",
        tel.rele_pin != null
          ? (tel.pump_manual ? "bomba manual ON" : (tel.rele_pin ? "bomba encendida" : "bomba apagada"))
          : "—");
      t("cPumpOn",
        tel.pump_on != null
          ? (tel.pump_on
              ? `ENCENDIDA${tel.pump_rest_s != null ? ` · restan ${tel.pump_rest_s} s` : ""}`
              : "apagada")
          : "—");
      t("cQueue",
        tel.cola_n != null ? `${tel.cola_n}/${tel.cola_max ?? "?"} fotos` : "—");
      t("cCam", tel.camara_ok != null ? (tel.camara_ok ? "OK" : "CAÍDA") : "—");
      t("cHeap", tel.heap != null ? `${Math.round(tel.heap / 1024)} KB` : "—");
      t("cRssi", tel.rssi != null ? `${tel.rssi} dBm` : "—");
      t("cPumpManual", tel.pump_manual != null ? (tel.pump_manual ? "ACTIVA" : "inactiva") : "—");

      const trig = pumpRes.pump_trigger_photos ?? state.config?.pump_trigger_photos ?? 3;
      setText("cPumpProg", `${pumpRes.photos_since_pump ?? 0}/${trig} fotos`);

      // Filas nuevas del firmware: failover, versión, reinicios y uptime.
      t("cSrvAct",
        tel.srv_act != null
          ? (tel.srv_act === 0 ? "principal" : tel.srv_act === 1 ? "alternativo" : String(tel.srv_act))
          : "—");
      t("cFw", tel.fw || "—");
      t("cBoots", tel.boots != null ? String(tel.boots) : "—");
      t("cDevUptime", tel.uptime_s != null ? fmtUptime(tel.uptime_s) : "—");
      t("cReset", tel.reset || "—");

      // Línea de hardware en el panel Bomba (vista Operación): mismo dato
      // de telemetría, visible donde el operador decide activar la bomba.
      if (hasTel) {
        setText("pumpRelayState",
          tel.rele_pin != null
            ? (tel.rele_pin ? "ACTIVO · bomba ON" : "inactivo")
            : "sin datos");
        setText("pumpHwState",
          tel.pump_on != null
            ? (tel.pump_on ? "ENCENDIDA" : "apagada")
            : "sin datos");
      } else {
        setText("pumpRelayState", "sin datos");
        setText("pumpHwState", "sin datos");
      }

      setText(
        "circuitNote",
        hasTel
          ? `Telemetría recibida hace ${fmtAgo(((Date.now() / 1000) - (tel._ts || 0)) * 1000)} · llega con cada poll (~60 s).`
          : "La telemetría del dispositivo llega con cada poll (~60 s). Si el firmware es anterior a esta versión, las filas del sensor/relé/cola muestran \"sin datos\"."
      );
      updateEmptyStates();
      updateCfgDelivery();
      refreshOverview();
    } catch (_) { /* silencioso */ }
  }

  // --- Reset de datos de prueba (confirmación contextual obligatoria) ---
  async function resetServerData(scope, btn) {
    const isAll = scope === "all";
    const ok = await askConfirm({
      title: isAll ? "Borrar todos los datos" : "Vaciar álbum de capturas",
      desc: isAll
        ? "Operación irreversible: se eliminan del servidor todos los datos registrados."
        : "Operación irreversible: se eliminan del servidor las fotos y detecciones del álbum.",
      scope: isAll
        ? [
            "Todas las fotos y detecciones del álbum",
            "Contadores de materiales por clase",
            "Historial de eventos de bomba",
            "Progreso del ciclo de bomba",
          ]
        : [
            "Todas las fotos del álbum",
            "Sus detecciones y clases",
            "Los contadores de materiales se reinician",
          ],
      requireText: isAll ? "BORRAR TODO" : "",
      okLabel: isAll ? "Borrar todo" : "Vaciar álbum",
    });
    if (!ok) return;
    const label = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "Borrando…"; }
    try {
      const res = await fetch(`/api/captures?scope=${scope}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Refrescar vistas afectadas
      state.captureIds.clear();
      state.captureCount = 0;
      state.captureDets.clear();
      state.currentCapture = null;
      const gal = $("captureGallery");
      if (gal) gal.replaceChildren();
      if (state.latestImg) {
        state.latestImg.remove();
        state.latestImg = null;
      }
      const emp = $("captureEmpty");
      if (emp) {
        emp.hidden = false;
        emp.textContent = "Esperando que la ESP32 envíe la primera foto.";
      }
      const badge = $("captureBadge");
      if (badge) badge.hidden = true;
      const vmeta = document.querySelector(".video-meta");
      if (vmeta) vmeta.hidden = true;
      setText("latestCaptureClasses", "Sin capturas");
      setText("latestCaptureTime", "—");
      setText("captureCount", "0 fotos");
      galleryState.items = [];
      galleryState.offset = 0;
      galleryState.done = false;
      renderGallery();
      const stats = await fetch("/api/stats").then((r) => r.json());
      applyStats(stats);
      loadPumpEvents();
      loadTimeseries();
      loadPerf();
      loadCircuit();
    } catch (err) {
      console.warn("Reset failed", err);
      alert("No se pudo borrar: " + err.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = label; }
    }
  }

  // Reconexión manual del ESP32: la orden viaja en su próximo poll de
  // config; el firmware fuerza WiFi.disconnect + poll inmediato. Si la
  // placa no alcanza el servidor la orden no puede entregarse (arquitectura
  // push: el servidor no inicia la conexión).
  async function requestReconnect() {
    const btn = $("btnReconnect");
    const stateEl = $("reconnectState");
    state.busy.reconnect = true;
    if (btn) btn.disabled = true;
    const btnLabel = btn ? btn.textContent : "";
    if (stateEl) {
      stateEl.textContent = "Encolando…";
      stateEl.classList.remove("is-queued");
    }
    try {
      const res = await fetch("/api/device/reconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (res.status === 409) {
        if (stateEl) {
          stateEl.textContent = `⚠ ${data.detail || "Ya hay una orden de reconexión en cola."}`;
          stateEl.classList.remove("is-queued");
        }
        loadCommands();
        return;
      }
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const devOffline = data.device && data.device.online === false;
      if (stateEl) {
        stateEl.textContent = devOffline
          ? "ESP32 sin señal: la orden espera en cola (~10 min). Si no está alcanzando el servidor, esta orden no puede entregarse — debe volver a pollar por sus reintentos automáticos."
          : "Orden en cola · el ESP32 fuerza WiFi.disconnect + poll inmediato en su próximo poll (≤60 s).";
        stateEl.classList.add("is-queued");
      }
      if (btn) {
        btn.textContent = "En cola ✓";
        setTimeout(() => {
          if (btn) btn.textContent = btnLabel || "Reconectar ESP32";
        }, 3500);
      }
      loadCommands();
      loadCircuit();
    } catch (err) {
      console.warn("device/reconnect failed", err);
      if (stateEl) {
        stateEl.textContent = `Error: ${err.message}`;
        stateEl.classList.remove("is-queued");
      }
    } finally {
      state.busy.reconnect = false;
      if (btn) btn.disabled = false;
    }
  }

  // Vaciar la cola de fotos pendientes DENTRO de la ESP32 (vía config remota)
  async function queueClearEsp(btn) {
    const label = btn ? btn.textContent : "";
    const ok = await askConfirm({
      title: "Vaciar cola de fotos del ESP32",
      desc: "La ESP32 descarta las fotos tomadas que aún no ha podido subir. Se aplica en su próximo contacto (≤60 s).",
      scope: [
        "Fotos pendientes dentro del dispositivo",
        "No afecta al álbum ya subido al servidor",
      ],
      okLabel: "Vaciar cola",
    });
    if (!ok) return;
    if (btn) { btn.disabled = true; btn.textContent = "Enviando…"; }
    try {
      const res = await fetch("/api/admin/device-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ queue_clear: String(Math.floor(Date.now() / 1000)) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      if (btn) btn.textContent = "En cola ✓";
      setTimeout(() => { if (btn) btn.textContent = label; btn.disabled = false; }, 3000);
      loadCommands();
    } catch (err) {
      console.warn("queue_clear failed", err);
      if (btn) { btn.disabled = false; btn.textContent = label; }
      alert("No se pudo enviar el comando: " + err.message);
    }
  }

  // ---------------------------------------------------------------------------
  // Órdenes al dispositivo (ciclo de vida: en cola → entregada → ejecutada)
  // ---------------------------------------------------------------------------
  const COMMAND_KIND_LABEL = {
    pump: "Bomba",
    capture: "Foto",
    reconnect: "Reconexión",
    queue_clear: "Vaciar cola",
  };
  const COMMAND_STATE_LABEL = {
    queued: "En cola",
    delivered: "Entregada",
    acked: "Ejecutada",
    expired: "Expirada",
    cancelled: "Cancelada",
  };

  function fmtClock(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    if (Number.isNaN(d.getTime())) return "—";
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  async function loadCommands() {
    try {
      const res = await fetch("/api/device/commands?limit=40");
      if (!res.ok) return;
      const data = await res.json();
      state.orders = { items: data.items || [], pending: data.pending ?? 0 };
      renderOrders();
      syncCommandButtons();
      refreshOverview();
    } catch (_) { /* silencioso: reintenta en el próximo ciclo */ }
  }

  function renderOrders() {
    const ul = $("ordersList");
    if (!ul) return;
    ul.replaceChildren();
    const items = state.orders.items;
    setText("ordersPending",
      state.orders.pending > 0 ? `${state.orders.pending} pendientes` : "0 pendientes");
    setText("ordersUpdated", `actualizado ${new Date().toLocaleTimeString()}`);
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "orders-empty";
      li.textContent = "Sin órdenes registradas";
      ul.appendChild(li);
      return;
    }
    for (const cmd of items) {
      const li = document.createElement("li");
      li.className = `order-item st-${cmd.state}`;

      const kind = document.createElement("span");
      kind.className = "order-kind";
      kind.textContent = COMMAND_KIND_LABEL[cmd.kind] || cmd.kind;

      const detail = document.createElement("span");
      detail.className = "order-detail";
      detail.textContent = cmd.detail || "—";
      detail.title = `uid ${cmd.uid}`;

      const time = document.createElement("span");
      time.className = "order-time";
      time.textContent = fmtClock(cmd.created_ts);
      time.title = new Date(cmd.created_ts * 1000).toLocaleString();

      const st = document.createElement("span");
      st.className = "order-state";
      st.textContent = COMMAND_STATE_LABEL[cmd.state] || cmd.state;

      li.append(kind, detail, time, st);

      // Solo se puede cancelar mientras la orden no haya salido del servidor.
      if (cmd.state === "queued") {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "order-cancel";
        btn.textContent = "Cancelar";
        btn.addEventListener("click", () => cancelCommand(cmd.id, btn));
        li.appendChild(btn);
      }
      ul.appendChild(li);
    }
  }

  async function cancelCommand(id, btn) {
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/api/device/commands/${id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      loadCommands();
    } catch (err) {
      console.warn("cancel command failed", err);
      alert("No se pudo cancelar la orden: " + err.message);
      loadCommands();
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // Deshabilita los botones de acción mientras haya una orden del mismo
  // tipo en cola/entregada: el servidor también la rechaza con 409.
  function syncCommandButtons() {
    const pending = (kind) =>
      state.orders.items.some(
        (c) => c.kind === kind && (c.state === "queued" || c.state === "delivered")
      );
    const pumpBtn = $("manualPumpBtn");
    if (pumpBtn) {
      const hasPending = pending("pump");
      pumpBtn.disabled = state.busy.pump || hasPending;
      pumpBtn.title = hasPending
        ? "Ya hay una orden de bomba en cola — cancélala en Órdenes al dispositivo"
        : "La orden viaja en el próximo contacto del ESP32 (hasta ~60 s)";
    }
    const capBtn = $("manualCaptureBtn");
    if (capBtn) {
      const hasPending = pending("capture");
      capBtn.disabled = state.busy.capture || hasPending;
      capBtn.title = hasPending
        ? "Ya hay una orden de foto en cola — cancélala en Órdenes al dispositivo"
        : "La orden viaja en el próximo contacto del ESP32 (hasta ~60 s)";
    }
  }

  // ---------------------------------------------------------------------------
  // Estado general (vista Estado): tarjetas + "Requiere atención"
  // ---------------------------------------------------------------------------
  function devicePhase() {
    if (!state.deviceLastSeen) return "never";
    return state.deviceOnline ? "online" : "offline";
  }

  function renderOverviewCards() {
    const phase = devicePhase();
    const lastSeen = state.deviceLastSeen;
    const lastSeenTxt = lastSeen
      ? fmtAgo((Date.now() / 1000 - lastSeen) * 1000)
      : "sin contacto";

    const devCard = $("ovCardDev");
    if (devCard) {
      devCard.classList.toggle("warn", phase === "offline");
      devCard.classList.toggle("danger", phase === "never");
      devCard.classList.toggle("ok", phase === "online");
    }
    setText("ovDevState",
      phase === "never" ? "Nunca conectado"
      : phase === "online" ? "EN LÍNEA" : "Sin señal");
    setText("ovDevIp", state.deviceIp || "—");
    setText("ovLastSeen", lastSeenTxt);

    // Bomba: prioridad al estado real del hardware (telemetría) y luego
    // al countdown estimado del servidor.
    const tel = state.telemetry || {};
    const pumpCard = $("ovCardPump");
    const until = state.pump?.pump_active_until || 0;
    const restS = until ? Math.ceil(until - Date.now() / 1000) : 0;
    let pumpTxt, pumpSub, pumpCls = "";
    if (tel.pump_on) {
      pumpTxt = "ENCENDIDA";
      pumpSub = tel.pump_rest_s != null ? `restan ~${tel.pump_rest_s} s` : "en curso";
      pumpCls = "ok";
    } else if (restS > 0) {
      pumpTxt = "ENCENDIDA";
      pumpSub = `restan ~${restS} s (estimado)`;
      pumpCls = "ok";
    } else if (phase !== "online") {
      pumpTxt = "—";
      pumpSub = phase === "never" ? "sin datos del dispositivo" : "sin señal";
      pumpCls = "warn";
    } else {
      const cur = state.pump?.photos_since_pump ?? 0;
      const trig = state.pump?.pump_trigger_photos ?? state.config?.pump_trigger_photos ?? 3;
      pumpTxt = "Apagada";
      pumpSub = cur > 0 ? `${cur}/${trig} fotos para el ciclo` : "esperando fotos";
    }
    setText("ovPumpState", pumpTxt);
    setText("ovPumpSub", pumpSub);
    if (pumpCard) {
      pumpCard.classList.remove("ok", "warn", "danger");
      if (pumpCls) pumpCard.classList.add(pumpCls);
    }

    setText("ovCaptures", String(state.captureCount || 0));
    const capTs = state.currentCapture?.ts;
    const capAge = capTs ? Date.now() - new Date(capTs).getTime() : NaN;
    setText("ovCaptureAge",
      !Number.isNaN(capAge) ? `última ${fmtAgo(capAge)}` : "sin capturas");

    const pending = state.orders.pending || 0;
    const active = state.orders.items.filter(
      (c) => c.state === "queued" || c.state === "delivered");
    setText("ovOrders", pending > 0 ? String(pending) : "0");
    if (pending > 0 && active.length) {
      const oldest = Math.min(...active.map((c) => c.created_ts));
      setText("ovOrdersSub",
        `la más antigua ${fmtAgo((Date.now() / 1000 - oldest) * 1000)}`);
    } else {
      setText("ovOrdersSub", "sin órdenes activas");
    }

    const perf = state.perf || {};
    setText("ovServer", fmtUptime(perf.server?.uptime_s));
    setText("ovServerSub",
      `${perf.server?.ws_clients ?? "—"} clientes · RTT ${state.netMs ?? "—"} ms`);
    setText("ovUpdated", `actualizado ${new Date().toLocaleTimeString()}`);
  }

  function renderAttention() {
    const list = $("attentionList");
    if (!list) return;
    const items = [];
    const phase = devicePhase();
    const lastSeen = state.deviceLastSeen;

    if (phase === "never") {
      items.push({ level: "danger",
        text: "El ESP32-CAM nunca ha contactado al servidor. Revisa alimentación y que esté en la red WiFi del servidor." });
    } else if (phase === "offline") {
      items.push({ level: "warn",
        text: `ESP32 sin señal desde ${fmtAgo((Date.now() / 1000 - lastSeen) * 1000)}. Las órdenes en cola expiran a los 10 min y las nuevas no se entregan hasta que vuelva.` });
    }

    // Telemetría desactualizada: dispositivo en línea pero sin reporte
    // dentro del intervalo esperado (~60 s + margen).
    const telTs = state.telemetry?._ts;
    if (phase === "online" && telTs) {
      const ageS = Date.now() / 1000 - telTs;
      if (ageS > 200) {
        items.push({ level: "warn",
          text: `Telemetría desactualizada (hace ${fmtAgo(ageS * 1000)}). El dispositivo reporta cada ~60 s.` });
      }
    }

    const active = state.orders.items.filter(
      (c) => c.state === "queued" || c.state === "delivered");
    if (active.length) {
      const oldest = Math.min(...active.map((c) => c.created_ts));
      items.push({ level: phase === "offline" ? "warn" : "info",
        text: `${active.length} orden(es) esperando al ESP32 · la más antigua lleva ${fmtAgo((Date.now() / 1000 - oldest) * 1000)}.` });
    }

    if (!state.wsOnline) {
      items.push({ level: "warn",
        text: "Sin conexión en vivo (WebSocket) con el servidor: los datos se actualizan solo por sondeo." });
    }

    const until = state.pump?.pump_active_until || 0;
    const restS = until ? Math.ceil(until - Date.now() / 1000) : 0;
    if (restS > 0) {
      items.push({ level: "info",
        text: `Bomba encendida · restan ~${restS} s (orden manual o ciclo automático).` });
    }

    list.replaceChildren();
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "attn-item ok";
      li.innerHTML = `<span class="attn-dot"></span><span><strong>Todo en orden.</strong> Sin incidencias detectadas.</span>`;
      list.appendChild(li);
      setText("attentionCount", "0");
      return;
    }
    setText("attentionCount", String(items.length));
    for (const it of items) {
      const li = document.createElement("li");
      li.className = `attn-item ${it.level}`;
      const dot = document.createElement("span");
      dot.className = "attn-dot";
      const txt = document.createElement("span");
      txt.textContent = it.text;
      li.append(dot, txt);
      list.appendChild(li);
    }
  }

  function refreshOverview() {
    renderOverviewCards();
    renderAttention();
  }

  // ---------------------------------------------------------------------------
  // Estados vacíos diferenciados (nunca conectado / desconectado / sin datos)
  // ---------------------------------------------------------------------------
  function updateEmptyStates() {
    const phase = devicePhase();
    const lastSeen = state.deviceLastSeen;
    const ago = lastSeen
      ? fmtAgo((Date.now() / 1000 - lastSeen) * 1000)
      : null;

    // Banner de la vista Operación
    const bTitle = $("offlineBannerTitle");
    const bText = $("offlineBannerText");
    if (bTitle && bText) {
      if (phase === "never") {
        bTitle.textContent = "El ESP32-CAM nunca ha contactado al servidor";
        bText.textContent =
          "Cuando encienda y se una a la red del servidor, empezará a reportar cada ~60 s.";
      } else if (phase === "offline") {
        bTitle.textContent = `ESP32-CAM sin señal · último contacto ${ago}`;
        bText.textContent =
          "Las capturas y la telemetría se reanudan cuando el dispositivo vuelva a contactar. Las órdenes en cola expiran a los 10 min.";
      } else {
        bTitle.textContent = "Esperando conexión del dispositivo";
        bText.textContent =
          "Las métricas aparecerán cuando la ESP32-CAM haga contacto (reporta una vez por minuto).";
      }
    }

    // Placeholder de la última captura (solo cuando no hay imagen)
    const capEmpty = $("captureEmpty");
    if (capEmpty && !state.currentCapture) {
      capEmpty.textContent =
        phase === "never" ? "El dispositivo aún no ha enviado su primera foto."
        : phase === "offline" ? `Sin capturas nuevas · el ESP32 está sin señal (${ago}).`
        : "El dispositivo está en línea; esperando la primera foto.";
    }

    // Placeholder de telemetría (vista Sensores)
    const telEmpty = $("telemetryEmpty");
    if (telEmpty) {
      telEmpty.textContent =
        phase === "never" ? "El ESP32-CAM nunca ha reportado telemetría."
        : phase === "offline" ? `Sin telemetría nueva · último contacto ${ago}.`
        : "El dispositivo está en línea; la telemetría llega con cada poll (~60 s).";
    }

    // Placeholder de la galería (vista Archivo)
    const galEmpty = $("galleryEmpty");
    if (galEmpty) {
      galEmpty.textContent =
        phase === "online"
          ? "El dispositivo está en línea pero el archivo está vacío."
          : "Sin capturas en el archivo todavía.";
    }
  }

  // ---------------------------------------------------------------------------
  // Estado de entrega de la configuración del dispositivo
  // ---------------------------------------------------------------------------
  function updateCfgDelivery() {
    const el = $("cfgDelivery");
    if (!el) return;
    const meta = state.cfgMeta || {};
    const keys = Object.keys(meta);
    if (!state.cfgSavedAt && !keys.length) {
      el.textContent = "";
      return;
    }
    const parts = [];
    if (state.cfgSavedAt) {
      const lastPoll = state.deviceLastPoll;
      if (lastPoll && lastPoll * 1000 > state.cfgSavedAt) {
        parts.push("guardada en el servidor ✓ · descargada por el ESP32 ✓");
      } else {
        parts.push("guardada en el servidor ✓ · el ESP32 la recoge en su próximo poll (≤60 s)");
      }
      // El firmware no confirma aplicación por clave; la única verificable
      // es pump_photos vía telemetría (pump_photos_max).
      const tel = state.telemetry || {};
      const want = parseInt(state.cfgPayload?.pump_photos, 10);
      if (!Number.isNaN(want) && tel.pump_photos_max === want) {
        parts.push("valores de bomba confirmados por telemetría");
      }
    } else if (keys.length) {
      const newest = Math.max(...keys.map((k) => meta[k] || 0));
      parts.push(
        `${keys.length} ajuste(s) activo(s) · última modificación ${fmtAgo((Date.now() / 1000 - newest) * 1000)}`
      );
    }
    el.textContent = parts.join(" · ") + ".";
  }

  function updateOtaCurrent() {
    const el = $("otaCurrent");
    if (!el) return;
    const meta = state.cfgMeta || {};
    const ota = meta.ota_url;
    el.textContent = ota?.value
      ? `OTA programada: ${ota.value} (se aplica una sola vez)`
      : "";
  }

  async function programOta() {
    const input = $("otaUrlInput");
    const btn = $("btnOta");
    const st = $("otaState");
    let url = (input?.value || "").trim();
    if (!url) url = `${location.origin}/firmware.bin`;
    if (!url.startsWith("http://")) {
      if (st) {
        st.textContent = "La ESP32 solo acepta http:// (HTTP plano, sin TLS).";
        st.classList.remove("is-queued");
      }
      return;
    }
    if (btn) btn.disabled = true;
    const btnLabel = btn ? btn.textContent : "";
    if (st) {
      st.textContent = "Enviando…";
      st.classList.remove("is-queued");
    }
    try {
      const res = await fetch("/api/admin/device-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ota_url: url }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const devOffline = !state.deviceOnline;
      if (st) {
        st.textContent = devOffline
          ? "OTA en cola · ESP32 sin señal — la URL queda activa y se aplicará cuando vuelva a contactar."
          : "OTA programada · el ESP32 descarga y flashea el .bin en su próximo poll (≤60 s).";
        st.classList.add("is-queued");
      }
      if (input) input.value = "";
      refreshCfgMeta();
    } catch (err) {
      console.warn("ota program failed", err);
      if (st) {
        st.textContent = `Error: ${err.message}`;
        st.classList.remove("is-queued");
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = btnLabel || "Programar OTA";
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Modal de confirmación para acciones destructivas / sensibles
  // ---------------------------------------------------------------------------
  let cmResolve = null;

  function askConfirm({ title, desc, scope = [], requireText = "", okLabel = "Confirmar" }) {
    return new Promise((resolve) => {
      const modal = $("confirmModal");
      if (!modal) { resolve(confirm(`${title}\n\n${desc}`)); return; }
      cmResolve = resolve;
      setText("cmTitle", title);
      setText("cmDesc", desc);
      const ul = $("cmScope");
      if (ul) {
        ul.replaceChildren();
        for (const s of scope) {
          const li = document.createElement("li");
          li.textContent = s;
          ul.appendChild(li);
        }
      }
      const wrap = $("cmTypeWrap");
      const input = $("cmTypeInput");
      const okBtn = $("cmOk");
      if (wrap) wrap.hidden = !requireText;
      if (input) {
        input.value = "";
        input.dataset.expect = requireText;
      }
      setText("cmTypeWord", requireText);
      if (okBtn) {
        okBtn.textContent = okLabel;
        okBtn.disabled = !!requireText;
      }
      modal.hidden = false;
      if (requireText && input) input.focus();
    });
  }

  function closeConfirmModal(result) {
    const modal = $("confirmModal");
    if (modal) modal.hidden = true;
    const resolve = cmResolve;
    cmResolve = null;
    if (resolve) resolve(result);
  }

  function wireConfirmModal() {
    const modal = $("confirmModal");
    const okBtn = $("cmOk");
    const cancelBtn = $("cmCancel");
    const input = $("cmTypeInput");
    if (!modal) return;
    if (okBtn) {
      okBtn.addEventListener("click", () => {
        const expect = input?.dataset.expect || "";
        if (expect && (input?.value || "").trim().toUpperCase() !== expect) return;
        closeConfirmModal(true);
      });
    }
    if (cancelBtn) cancelBtn.addEventListener("click", () => closeConfirmModal(false));
    if (input) {
      input.addEventListener("input", () => {
        const expect = input.dataset.expect || "";
        if (okBtn && expect) {
          okBtn.disabled = input.value.trim().toUpperCase() !== expect;
        }
      });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") okBtn?.click();
      });
    }
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeConfirmModal(false);
    });
  }

  // --- Navegación por pestañas (sidebar desktop / bottom-nav móvil) ---
  const VIEW_META = {
    overview: ["Estado", "Salud general · conectividad, bomba y órdenes"],
    operation: ["Operación", "Capturas, bomba y órdenes al dispositivo"],
    sensors: ["Sensores", "Telemetría del ESP32-CAM · reporta cada ~60 s"],
    analytics: ["Analítica", "Materiales, tendencias y rendimiento de la IA"],
    archive: ["Archivo", "Capturas clasificadas · filtros y exportación"],
    admin: ["Administración", "Configuración, diagnóstico y mantenimiento"],
  };

  function activateView(name) {
    document.querySelectorAll(".view").forEach((v) => {
      v.hidden = v.id !== `view-${name}`;
    });
    document.querySelectorAll(".nav-tab").forEach((b) => {
      const on = b.dataset.view === name;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", String(on));
    });
    const meta = VIEW_META[name];
    if (meta) {
      setText("viewTitle", meta[0]);
      setText("viewSub", meta[1]);
      document.title = `REBA — ${meta[0]}`;
    }
    const views = document.querySelector(".views");
    if (views && window.innerWidth < 1024) {
      views.scrollIntoView({ block: "start" });
    }
  }
  document.querySelectorAll(".nav-tab").forEach((b) => {
    b.addEventListener("click", () => activateView(b.dataset.view));
  });

  // --- Tema claro/oscuro (persiste en localStorage, aplica en <html>) ---
  const THEME_KEY = "reba-theme";

  function redrawCharts() {
    drawDonut(state.stats);
    if (state.tsData) drawTsChart(state.tsData.series, state.tsData.classNames);
  }

  function applyTheme(theme, persist = true) {
    document.documentElement.dataset.theme = theme;
    if (persist) {
      try { localStorage.setItem(THEME_KEY, theme); } catch (_) { /* sin storage */ }
    }
    const btn = $("themeToggle");
    const isLight = theme === "light";
    if (btn) {
      btn.setAttribute("aria-checked", String(isLight));
      btn.setAttribute("aria-label",
        isLight ? "Cambiar a tema oscuro" : "Cambiar a tema claro");
    }
    setText("themeToggleLabel", isLight ? "Tema · Claro" : "Tema · Oscuro");
    redrawCharts();   // los canvas se repintan con los colores del nuevo tema
  }

  const themeBtn = $("themeToggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light"
        ? "dark" : "light";
      applyTheme(next);
    });
  }
  // Sincroniza el estado del switch con el tema ya aplicado en <head>
  applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark", false);

  // --- Chips de duración de la bomba manual ---
  // Presets que rellenan el campo; un valor a mano deselecciona el chip.
  const pumpChips = $("pumpChips");
  const manualInput = $("manualPumpSeconds");
  function setPumpChipSelected(chip) {
    if (!pumpChips) return;
    pumpChips.querySelectorAll(".chip").forEach((c) => {
      c.classList.toggle("is-selected", c === chip);
    });
  }
  function syncPumpChips() {
    if (!pumpChips || !manualInput) return;
    const match = [...pumpChips.querySelectorAll(".chip[data-seconds]")]
      .find((c) => c.dataset.seconds === manualInput.value);
    setPumpChipSelected(match || null);
  }
  if (pumpChips) {
    pumpChips.querySelectorAll(".chip[data-seconds]").forEach((chip) => {
      chip.addEventListener("click", () => {
        if (manualInput) manualInput.value = chip.dataset.seconds;
        setPumpChipSelected(chip);
      });
    });
  }
  if (manualInput) {
    manualInput.addEventListener("input", syncPumpChips);
    syncPumpChips();
  }

  // --- Mostrar / ocultar contraseña WiFi ---
  const passToggle = $("cfgPassToggle");
  const passInput = $("cfgWifiPass");
  if (passToggle && passInput) {
    passToggle.addEventListener("click", () => {
      const show = passInput.type === "password";
      passInput.type = show ? "text" : "password";
      passToggle.textContent = show ? "ocultar" : "ver";
      passToggle.setAttribute(
        "aria-label",
        show ? "Ocultar contraseña" : "Mostrar contraseña"
      );
    });
  }

  // --- Tooltip de la gráfica de actividad (hover / touch) ---
  const tsCanvas = $("tsChart");
  const tsTip = $("tsTooltip");
  if (tsCanvas && tsTip) {
    const tipFor = (clientX) => {
      const g = state.tsGeom;
      if (!g) { tsTip.hidden = true; return; }
      const rect = tsCanvas.getBoundingClientRect();
      const x = clientX - rect.left;
      let best = null, bestD = Infinity;
      for (const b of g.geom) {
        const d = Math.abs(b.cx - x);
        if (d < bestD) { bestD = d; best = b; }
      }
      if (!best || bestD > g.slot) { tsTip.hidden = true; return; }

      const day = best.day;
      const dets = day.detections || {};
      const rows = g.classNames.filter((c) => (dets[c] || 0) > 0);
      const total = rows.reduce((s, c) => s + dets[c], 0);

      tsTip.replaceChildren();
      const dateEl = document.createElement("div");
      dateEl.className = "tt-date";
      dateEl.textContent = `${day.date.slice(8, 10)}/${day.date.slice(5, 7)}/${day.date.slice(0, 4)}`;
      tsTip.appendChild(dateEl);

      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "tt-row";
        const name = document.createElement("span");
        name.className = "tt-name";
        name.textContent = "Sin detecciones";
        empty.appendChild(name);
        tsTip.appendChild(empty);
      }
      for (const c of rows) {
        const r = document.createElement("div");
        r.className = "tt-row";
        const dot = document.createElement("span");
        dot.className = "tt-dot";
        dot.style.background = CLASS_COLORS[c] || "#888";
        const name = document.createElement("span");
        name.className = "tt-name";
        name.textContent = c;
        const val = document.createElement("span");
        val.className = "tt-val";
        val.textContent = String(dets[c]);
        r.append(dot, name, val);
        tsTip.appendChild(r);
      }
      const t = document.createElement("div");
      t.className = "tt-total";
      const tLabel = document.createElement("span");
      tLabel.textContent = "Total";
      const tVal = document.createElement("span");
      tVal.className = "tt-val";
      tVal.textContent = String(total);
      t.append(tLabel, tVal);
      tsTip.appendChild(t);

      tsTip.hidden = false;
      const wrapW = tsCanvas.parentElement.clientWidth;
      const left = Math.max(72, Math.min(wrapW - 72, best.cx));
      tsTip.style.left = `${left}px`;
    };
    tsCanvas.addEventListener("pointermove", (e) => tipFor(e.clientX));
    tsCanvas.addEventListener("pointerdown", (e) => tipFor(e.clientX));
    tsCanvas.addEventListener("pointerleave", () => { tsTip.hidden = true; });
  }

  bindDonutLegend();

  // Redibujar cuando las fuentes variables terminen de cargar
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => {
      drawDonut(state.stats);
      loadTimeseries();
    });
  }

  // "Ping · hace X" se actualiza aunque el ESP32 no contacte seguido
  setInterval(updatePingNote, 20000);

  // Cuenta regresiva al próximo poll del ESP32 (heartbeat cada ~60 s):
  // muestra hh:mm:ss.cs en los elementos .poll-cd del dashboard.
  function updatePollCountdown() {
    const els = document.querySelectorAll(".poll-cd");
    if (!els.length) return;
    let label;
    if (state.deviceOnline && state.deviceLastSeen) {
      const remMs = Math.max(0, (state.deviceLastSeen + 60) * 1000 - Date.now());
      const cs = Math.floor((remMs % 1000) / 10);
      const s = Math.floor(remMs / 1000) % 60;
      const m = Math.floor(remMs / 60000) % 60;
      const h = Math.floor(remMs / 3600000);
      label = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
    } else {
      label = "sin señal";
    }
    els.forEach((el) => { el.textContent = label; });
  }
  setInterval(updatePollCountdown, 100);
  updatePollCountdown();

  // Reloj en vivo de la topbar (hora local del navegador que mira el dashboard)
  function updateTopClock() {
    const el = $("topClock");
    if (el) el.textContent = new Date().toLocaleTimeString("es-SV", { hour12: false });
  }
  setInterval(updateTopClock, 1000);
  updateTopClock();

  // Cronómetro de la bomba en la topbar: cuenta regresiva desde
  // pump_active_until (epoch del server) hasta que la bomba se apaga.
  function updateTopTimer() {
    const pill = $("topTimerPill");
    const txt = $("topTimerText");
    if (!pill || !txt) return;
    const until = state.pump?.pump_active_until || 0;
    const restS = until ? Math.ceil(until - Date.now() / 1000) : 0;
    if (restS > 0) {
      txt.textContent = `Bomba · ${restS}s`;
      pill.classList.add("counting");
    } else {
      txt.textContent = "Bomba · --";
      pill.classList.remove("counting");
    }
  }
  setInterval(updateTopTimer, 500);
  updateTopTimer();

  // Métricas de rendimiento + circuito: RTT cada 5 s, REST cada 15 s
  setInterval(() => { measureNetLatency(); updateRealtimeBits(); }, 5000);
  setInterval(() => { loadPerf(); loadCircuit(); }, 15000);
  // Ciclo de vida de las órdenes al dispositivo (entregada/ejecutada/expirada)
  setInterval(loadCommands, 10000);
  measureNetLatency();
  loadPerf();
  loadCircuit();
  loadCommands();

  bootstrapFromRest();
  bootstrapCaptures();
  loadDeviceConfig();
  loadFullGallery(true);
  loadPumpEvents();
  loadTimeseries();
  updateEmptyStates();
  refreshOverview();
  connectWs();
})();

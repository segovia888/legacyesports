// live_timing_runtime - frontend (option A): muestra datos entregados por el bridge
// Conservador: actualiza solo elementos con IDs explícitos:
//   - trackNameBadge, sessionTypeBadge
//   - weatherAir, weatherTrack, weatherRain, weatherStatus
//   - statusDot, statusText
// Si el bridge entrega usage_label / usage_percent los mostrará junto a STATUS.
// Añade efecto visual azul + pulso cuando la pista esté MOJADA.
// Poll cada 1s.

(function(){
  if (window._lt_conn_poll) { try { clearInterval(window._lt_conn_poll); } catch(e){} window._lt_conn_poll = null; }
  window._liveTimingRuntimeLoaded = true;

  const POLL_MS = 1000;
  const STALE_THRESHOLD = 8; // segundos

  function safeText(v){ return v === undefined || v === null ? '' : String(v); }
  function fmtTemp(v){
    if (v === undefined || v === null || v === '') return '--°C';
    const n = Number(v);
    if (isNaN(n)) return String(v);
    return `${Math.round(n*10)/10}°C`;
  }
  function fmtPct(v){
    if (v === undefined || v === null || v === '') return '--%';
    const n = Number(v);
    if (isNaN(n)) return String(v);
    return `${Math.round(n)}%`;
  }

  // Decide si la pista está "wet" a partir del payload
  function isTrackWet(t) {
    if (!t) return false;
    const weather = t.weather || (t.last_payload && t.last_payload.weather) || {};
    const status = (weather.status || t.status || t.flag || '').toString().toLowerCase();
    if (status.includes('wet') || status.includes('rain') || status.includes('mojado')) return true;
    const rain = (weather.rain !== undefined) ? weather.rain : (t.rain !== undefined ? t.rain : 0);
    const rn = Number(rain);
    if (!isNaN(rn) && rn > 0) return true;
    return false;
  }

  // Apply connection visuals to statusDot/statusText without destroying DOM
  function applyConnectionVisuals(isConnected){
    try {
      const dot = document.getElementById('statusDot');
      const text = document.getElementById('statusText');

      if (dot) {
        if (!dot.classList.contains('live-dot')) dot.classList.add('live-dot');
        dot.classList.remove('live-connected','live-disconnected','live-dot-blink','track-wet');
        if (isConnected) dot.classList.add('live-connected','live-dot-blink');
        else dot.classList.add('live-disconnected');
      }

      if (text) {
        text.classList.remove('live-connected','live-disconnected','live-dot-blink','track-wet');
        text.textContent = isConnected ? 'Conectado' : 'Desconectado';
        if (isConnected) text.classList.add('live-connected','live-dot-blink');
        else text.classList.add('live-disconnected');
      }
    } catch(e){ console.warn('applyConnectionVisuals error', e); }
  }

  // Update fields from telemetry (IDs-only)
  function updateFieldsFromTelemetry(t){
    if (!t) return;

    // Track/session badges (template provides these badges)
    const trackBadge = document.getElementById('trackNameBadge');
    const sessionBadge = document.getElementById('sessionTypeBadge');
    const trackName = safeText(t.track_name || t.track || (t.last_payload && t.last_payload.track_name) || '-');
    const sessionType = safeText(t.session_type || t.session || (t.last_payload && t.last_payload.session_type) || '-');

    if (trackBadge) {
      try { trackBadge.textContent = `TRACK: ${trackName || '-'}`; } catch(e){}
    }
    if (sessionBadge) {
      try { sessionBadge.textContent = `SESSION: ${sessionType || '-'}`; } catch(e){}
    }

    // Weather values
    const weather = (t.weather || (t.last_payload && t.last_payload.weather)) || {};
    const air = (weather && weather.air !== undefined) ? weather.air : (t.air || t.air_temp || '');
    const trackTemp = (weather && weather.track !== undefined) ? weather.track : (t.track_temp || t.trackTemperature || '');
    const rain = (weather && weather.rain !== undefined) ? weather.rain : (t.rain || '');

    const weatherAirEl = document.getElementById('weatherAir');
    const weatherTrackEl = document.getElementById('weatherTrack');
    const weatherRainEl = document.getElementById('weatherRain');
    const weatherStatusEl = document.getElementById('weatherStatus');

    if (weatherAirEl) weatherAirEl.textContent = fmtTemp(air);
    if (weatherTrackEl) weatherTrackEl.textContent = fmtTemp(trackTemp);
    if (weatherRainEl) weatherRainEl.textContent = fmtPct(rain);

    // Base status text: SECO/MOJADO or weather.status normalized (Spanish)
    let status = (weather && weather.status) || (t && (t.status || t.flag)) || '';
    status = safeText(status);
    if (!status || status === 'undefined') {
      const rnum = Number(rain);
      status = (!isNaN(rnum) && rnum > 0) ? 'MOJADO' : 'SECO';
    } else {
      const s = status.toLowerCase();
      if (s.includes('dry')) status = 'SECO';
      else if (s.includes('wet') || s.includes('rain') || s.includes('mojado')) status = 'MOJADO';
      else status = status.toUpperCase();
    }

    // Prefer usage_label provided by backend; if not present, use usage_percent if present (and format in Spanish)
    let usageLabel = null;
    if (t && t.usage_label) {
      usageLabel = String(t.usage_label);
      // If backend provided English and you prefer Spanish, backend should send localized label
    } else if (typeof t.usage_percent !== 'undefined' && t.usage_percent !== null) {
      const p = Number(t.usage_percent);
      if (!isNaN(p)) {
        if (p <= 20) usageLabel = `Uso bajo (${p}%)`;
        else if (p <= 50) usageLabel = `Uso moderado (${p}%)`;
        else if (p <= 80) usageLabel = `Uso alto (${p}%)`;
        else usageLabel = `Uso muy alto (${p}%)`;
      }
    }

    // Compose final status text
    let finalStatus = status;
    if (usageLabel) finalStatus = `${status}, ${usageLabel}`;

    if (weatherStatusEl) {
      weatherStatusEl.textContent = finalStatus;
      // store raw tooltip
      if (t && (t.usage_percent || t.usage_label)) {
        weatherStatusEl.title = t.usage_label ? t.usage_label : (`usage: ${t.usage_percent}%`);
      } else {
        weatherStatusEl.title = '';
      }
    }

    // If the track is wet, apply 'track-wet' styling to weatherStatus & statusDot
    const wet = isTrackWet(t);
    if (weatherStatusEl) {
      weatherStatusEl.classList.toggle('track-wet', wet);
    }
    const dot = document.getElementById('statusDot');
    if (dot) {
      dot.classList.toggle('track-wet', wet);
    }
    // Also, to make the statusText more explicit, add class track-wet to it so CSS can color text
    const statusTextEl = document.getElementById('statusText');
    if (statusTextEl) statusTextEl.classList.toggle('track-wet', wet);
  }

  // Fallback heuristics for connection
  function telemetryShowsConnected(t){
    if (!t) return false;
    if (t.connected === true || t.connected === 'true') return true;
    if (Array.isArray(t.grid) && t.grid.length > 0) return true;
    if (t.driver || t.driver_name || t.pilot) return true;
    return false;
  }

  // Poll tick
  async function connectionTick(){
    try {
      const r = await fetch('/api/telemetry/live', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) {
        applyConnectionVisuals(false);
        return;
      }
      const t = await r.json();
      window._lastTelemetry = t;

      // update UI fields
      updateFieldsFromTelemetry(t);

      // decide freshness & connection state (prefer telemetry_age_seconds)
      let isConnected = false;
      if (t && typeof t.telemetry_age_seconds !== 'undefined' && t.telemetry_age_seconds !== null) {
        const age = Number(t.telemetry_age_seconds);
        isConnected = (!isNaN(age) && age <= STALE_THRESHOLD);
      } else if (t && (t.last_ingest || t.timestamp)) {
        const last = t.last_ingest || t.timestamp;
        let lastSec = null;
        if (typeof last === 'number') lastSec = last;
        else {
          const parsed = Date.parse(last);
          if (!isNaN(parsed)) lastSec = parsed/1000.0;
        }
        if (lastSec !== null) {
          const age = Date.now()/1000 - lastSec;
          isConnected = age <= STALE_THRESHOLD;
        } else {
          isConnected = telemetryShowsConnected(t);
        }
      } else {
        isConnected = telemetryShowsConnected(t);
      }

      applyConnectionVisuals(!!isConnected);

    } catch(e){
      console.warn('connectionTick error', e);
      applyConnectionVisuals(false);
    }
  }

  // Init
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function(){
      connectionTick();
      window._lt_conn_poll = setInterval(connectionTick, POLL_MS);
    });
  } else {
    connectionTick();
    window._lt_conn_poll = setInterval(connectionTick, POLL_MS);
  }

  // Public stop
  window.stopLiveTimingConnection = function(){ if (window._lt_conn_poll) { clearInterval(window._lt_conn_poll); window._lt_conn_poll = null; } };

  console.log('live_timing_runtime (option A, usage + wet handling) initialized.');
})();
// status_visuals - solo gestiona la apariencia Conectado/Desconectado del status existente
// Compatible con el resto de tu script (no destruye nodos).
(function(){
  // evita duplicar el poll si el script se recarga
  if (window._lt_status_poll) {
    try { clearInterval(window._lt_status_poll); } catch(e){}
    window._lt_status_poll = null;
  }

  const POLL_MS = 1000;
  const STALE_THRESHOLD = 8; // segundos

  function setStatusVisuals(isConnected, wet=false){
    const dot = document.getElementById('statusDot');
    const txt = document.getElementById('statusText');
    if (dot) {
      dot.classList.remove('live-connected','live-disconnected','track-wet','live-dot-blink');
      // aplicar clases (prefiere track-wet + conectado si ambos true)
      if (wet) dot.classList.add('track-wet');
      if (isConnected) {
        dot.classList.add('live-connected','live-dot-blink');
        dot.setAttribute('aria-label','Live - Conectado');
      } else {
        dot.classList.add('live-disconnected');
        dot.setAttribute('aria-label','Live - Desconectado');
      }
    }
    if (txt) {
      txt.classList.remove('live-connected','live-disconnected','track-wet','live-dot-blink');
      if (wet) txt.classList.add('track-wet');
      if (isConnected) {
        txt.textContent = 'Conectado';
        txt.classList.add('live-connected','live-dot-blink');
      } else {
        txt.textContent = 'Desconectado';
        txt.classList.add('live-disconnected');
      }
    }
  }

  function isWetPayload(t){
    if (!t) return false;
    const w = t.weather || (t.last_payload && t.last_payload.weather) || {};
    const status = (w.status || t.status || '').toString().toLowerCase();
    if (status.includes('wet') || status.includes('rain') || status.includes('mojado')) return true;
    const rain = (w.rain !== undefined) ? w.rain : (t.rain !== undefined ? t.rain : 0);
    if (!isNaN(Number(rain)) && Number(rain) > 0) return true;
    return false;
  }

  async function statusTick(){
    try {
      const resp = await fetch('/api/telemetry/live', { credentials: 'same-origin', cache: 'no-store' });
      if (!resp.ok) {
        setStatusVisuals(false, false);
        return;
      }
      const t = await resp.json();

      // decide connected: prefer telemetry_age_seconds, else last_ingest, else connected flag
      let isConnected = false;
      if (t && typeof t.telemetry_age_seconds !== 'undefined' && t.telemetry_age_seconds !== null) {
        const age = Number(t.telemetry_age_seconds);
        isConnected = (!isNaN(age) && age <= STALE_THRESHOLD);
      } else if (t && (t.last_ingest || t.timestamp)) {
        const last = t.last_ingest || t.timestamp;
        let lastSec = null;
        if (typeof last === 'number') lastSec = last;
        else {
          const parsed = Date.parse(last);
          if (!isNaN(parsed)) lastSec = parsed/1000.0;
        }
        if (lastSec !== null) {
          const age = Date.now()/1000 - lastSec;
          isConnected = age <= STALE_THRESHOLD;
        } else {
          isConnected = (t.connected === true || t.connected === 'true');
        }
      } else {
        isConnected = (t.connected === true || t.connected === 'true');
      }

      const wet = isWetPayload(t);
      setStatusVisuals(!!isConnected, !!wet);
    } catch(e){
      setStatusVisuals(false, false);
    }
  }

  // arrancar
  statusTick();
  window._lt_status_poll = setInterval(statusTick, POLL_MS);

  // helper para detener desde consola
  window.stopStatusVisuals = function(){ if (window._lt_status_poll) { clearInterval(window._lt_status_poll); window._lt_status_poll = null; console.log('Status visuals poll stopped'); } };

  console.log('status_visuals initialized');
})();
// hud_updater - actualiza TIEMPO / FUEL / A META / INCID. en live-timing
// IDs objetivos: hudTime, hudFuel, hudTarget, hudInc
(function(){
  if (window._hud_updater) {
    try { clearInterval(window._hud_updater); } catch(e) {}
  }
  const POLL_MS = 1000;

  function safeText(v){ return (v === undefined || v === null) ? '' : String(v); }
  function fmtFuelVal(v){
    if (v === undefined || v === null || v === '') return '--.-';
    const n = Number(v);
    if (isNaN(n)) return String(v);
    // mantiene formato con 1 decimal (sin unidad para no romper diseño)
    return (Math.round(n*10)/10).toFixed(1);
  }

  function extractTargetFromStrat(strat){
    // si viene algo como "-12.3" o "- 12.3" devuelve "12.3"
    if (!strat && strat !== 0) return null;
    try {
      const s = String(strat).trim();
      // buscar primer número con signo opcional
      const m = s.match(/-?\s*([0-9]+(?:\.[0-9]+)?)/);
      if (m && m[1]) {
        const num = Number(m[1]);
        if (!isNaN(num)) return (Math.round(Math.abs(num)*10)/10).toFixed(1);
      }
      // si la cadena ya es un número
      if (!isNaN(Number(s))) return (Math.round(Number(s)*10)/10).toFixed(1);
    } catch(e){}
    return null;
  }

  async function fetchTelemetryOnce(){
    try {
      const r = await fetch('/api/telemetry/live', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) return null;
      return await r.json();
    } catch(e){
      return null;
    }
  }

  async function updateHUD(){
    let t = window._lastTelemetry;
    if (!t) {
      t = await fetchTelemetryOnce();
      if (!t) return;
      // do not overwrite window._lastTelemetry here (but it's fine if you do)
      window._lastTelemetry = t;
    }

    // TIEMPO
    const sessionTimer = safeText(t.session_timer || t.sessionTimer || t.timer || '');
    const timerNode = document.getElementById('hudTime');
    if (timerNode) timerNode.textContent = sessionTimer || '--:--:--';

    // FUEL (1 decimal)
    const fuelVal = (t && t.my_car && typeof t.my_car.fuel !== 'undefined') ? t.my_car.fuel : (t && t.fuel ? t.fuel : null);
    const fuelNode = document.getElementById('hudFuel');
    if (fuelNode) fuelNode.textContent = fmtFuelVal(fuelVal);

    // A META -> preferimos my_car.strat si contiene "-N.N"
    let targetText = '';
    const strat = (t && t.my_car && typeof t.my_car.strat !== 'undefined') ? t.my_car.strat : (t && t.strat ? t.strat : null);
    const extracted = extractTargetFromStrat(strat);
    if (extracted !== null) {
      // mostramos solo el número (sin unidad) para no tocar tu CSS; si prefieres "L" añádelo
      targetText = extracted;
    } else {
      // fallback: intenta usar un campo explícito fuel_needed si existe
      if (t && typeof t.fuel_needed !== 'undefined' && t.fuel_needed !== null) {
        targetText = (Math.round(Number(t.fuel_needed)*10)/10).toFixed(1);
      } else {
        // último recurso: mostrar "--" o la fuel actual como indicación
        const fallbackFuel = fuelVal;
        targetText = fallbackFuel !== null && fallbackFuel !== undefined ? fmtFuelVal(fallbackFuel) : '--';
      }
    }
    const targetNode = document.getElementById('hudTarget');
    if (targetNode) targetNode.textContent = targetText;

    // INCID. -> incidents / inc_limit
    const incidents = (t && t.my_car && typeof t.my_car.incidents !== 'undefined') ? t.my_car.incidents : (t && t.incidents ? t.incidents : null);
    let incLimit = (t && t.my_car && typeof t.my_car.inc_limit !== 'undefined') ? t.my_car.inc_limit : (t && t.inc_limit ? t.inc_limit : null);
    if (incLimit === null || incLimit === undefined) incLimit = '--';
    const incNode = document.getElementById('hudInc');
    if (incNode) {
      if (incidents === null || incidents === undefined) incNode.textContent = '--/--';
      else incNode.textContent = `${incidents} / ${incLimit}`;
    }
  }

  // arrancar y exponer
  updateHUD();
  window._hud_updater = setInterval(updateHUD, POLL_MS);
  window.stopHUDUpdater = function(){ if (window._hud_updater) { clearInterval(window._hud_updater); window._hud_updater = null; } };

  console.log('hud_updater initialized (hudTime, hudFuel, hudTarget, hudInc).');
})();
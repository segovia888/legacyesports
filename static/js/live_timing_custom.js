// live_timing_custom.js
// Integración robusta para live_timing.html
// - contiene renderStrategyTable, renderGrid, renderMap y el poller update()
// - detiene el poller del runtime al iniciar para evitar actualizaciones duplicadas
// - normaliza el botón "CARGAR ESTRATEGIA" para evitar handlers duplicados
// - no usa código inline para evitar CSP issues
// NOTA: esta versión restablece el indicador de estado (Conectado/Desconectado)
//       usando hysteresis para evitar parpadeos y minimizar escrituras DOM.

(function(){
  'use strict';

  // Indicar que el custom runtime está cargado (evita que otros runtimes se inicien si consultan esta bandera)
  window._liveTimingCustomLoaded = true;

  // --- Block runtime-originated fetches to /api/telemetry/live (permanent) ---
  try {
    if (!window.__blockRuntimeFetch) {
      window.__blockRuntimeFetch = true;
      (function(){
        const _origFetch = window.fetch.bind(window);
        window.fetch = async function(input, init){
          try {
            const url = String(input || '');
            if (url.includes('/api/telemetry/live')) {
              const stack = (new Error()).stack || '';
              if (stack.indexOf('live_timing_runtime.js') !== -1) {
                console.warn('Blocked runtime fetch to', url);
                return new Response(null, { status: 204, statusText: 'Blocked by custom' });
              }
            }
          } catch(e){ console.error('Fetch-blocker error', e); }
          return _origFetch(input, init);
        };
        console.log('Runtime fetch blocker installed (permanent).');
      })();
    }
  } catch(e){ console.error('Install fetch blocker failed', e); }

  // Config / small app state
  const SIM_DRIVER_ID = 668063;
  let connectedPilots = ["Manolo Segovia", "Pepe Lopez"];
  let lastUserScroll = 0;

  // --- status hysteresis (to avoid flicker) ---
  const STATUS_TIMEOUT_MS = 1500; // ms without live to consider disconnected
  window._lt_lastSeenLive = window._lt_lastSeenLive || 0;
  window._lt_status = window._lt_status || ''; // 'connected' | 'disconnected' | ''

  // Scroll tracking (attach to gridContainer if present)
  function attachScrollTracking(){
    const gridContainer = document.getElementById('gridContainer');
    if (gridContainer) {
      gridContainer.addEventListener('scroll', () => { lastUserScroll = Date.now(); });
    }
  }

  // Utilities
  function escapeHtml(s){
    if(s === null || s === undefined) return '';
    return String(s).replace(/[&<>"'`]/g, function(m){ return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','`':'&#96;'})[m]; });
  }

// Reemplazar la función renderStrategyTable por esta implementación:
function renderStrategyTable() {
  const tbody = document.getElementById('strategyBody');
  if (!tbody) return;
  tbody.innerHTML = '';

  // Helpers
  function esc(s){ return typeof escapeHtml === 'function' ? escapeHtml(s) : String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function fmtPit(v){
    if (v === true) return 'YES';
    if (v === false || v == null) return '';
    if (typeof v === 'object') {
      if (v.duration) v = v.duration;
      else if (v.time) v = v.time;
      else return String(JSON.stringify(v));
    }
    if (typeof v === 'number' && v > 1000) v = Math.round(v/1000);
    if (typeof v === 'number') {
      const mins = Math.floor(v/60); const secs = v%60;
      return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    }
    return String(v);
  }
  function wxIconClass(wx){
    if (!wx) return 'sun';
    wx = String(wx).toLowerCase();
    if (wx.includes('storm') || wx.includes('bolt') || wx.includes('thunder')) return 'bolt';
    if (wx.includes('rain') || wx.includes('wet') || wx.includes('lluv')) return 'cloud-rain';
    if (wx.includes('snow')) return 'snowflake';
    if (wx.includes('cloud') || wx.includes('overcast')) return 'cloud';
    return 'sun';
  }

  // Try to get stints from window.currentStrategyStints
  let stints = Array.isArray(window.currentStrategyStints) && window.currentStrategyStints.length
    ? window.currentStrategyStints
    : null;

  // If not present, try to parse the existing DOM rows into stints (covers the case where another apply function inserted rows)
  if (!stints) {
    const rows = Array.from(document.querySelectorAll('#strategyBody tr'));
    if (rows.length) {
      try {
        stints = rows.map(r => {
          const tds = r.querySelectorAll('td');
          return {
            driver: (tds[1] && tds[1].textContent || '').trim(),
            start: (tds[2] && tds[2].textContent || '').trim(),
            end: (tds[3] && tds[3].textContent || '').trim(),
            laps: (tds[4] && tds[4].textContent || '').trim(),
            fuel: (tds[5] && tds[5].textContent || '').trim(),
            wx: (tds[6] && (tds[6].textContent || tds[6].innerText) || '').trim(),
            pit: (tds[7] && tds[7].textContent || '').trim(),
            notes: (tds[8] && tds[8].textContent || '').trim()
          };
        });
        // expose for future renders
        window.currentStrategyStints = stints;
        console.log('renderStrategyTable: parsed stints from DOM (' + stints.length + ' rows)');
      } catch (e) {
        console.warn('renderStrategyTable: fallo parsing DOM rows', e);
        stints = null;
      }
    }
  }

  // If we have stints (either from window.currentStrategyStints or parsed from DOM), render them using the app markup
  if (stints && stints.length) {
    const connected = Array.isArray(window.connectedPilots) ? window.connectedPilots : [];

    stints.forEach((s, idx) => {
      const wx = (s.wx || '').toString().toLowerCase();
      let status = '';
      // Detect finished/active based on common fields
      if (s.done === true || s.completed === true || (s.status && /done|finished|completed/i.test(String(s.status)))) status = 'stint-done';
      else if (s.active === true || s.now === true || (s.status && /active/i.test(String(s.status)))) status = 'stint-active';
      else if (wx.indexOf('rain') >= 0 || wx.indexOf('wet') >= 0 || wx.indexOf('storm') >= 0) status = 'stint-storm';

      const pilotName = (s.driver || s.name || s.pilot || '---').toString();
      const isConn = connected.indexOf(pilotName) >= 0;
      const connClass = isConn ? 'pilot-on' : 'pilot-off';
      const pilotDisplay = (s.now === true || s.active === true) ? 'TU (NOW)' : pilotName;

      const icon = wxIconClass(s.wx || s.weather);

      const tr = document.createElement('tr');
      if (status) tr.className = status;

      const tdIndex = document.createElement('td'); tdIndex.style.fontWeight = 'bold'; tdIndex.textContent = String(idx + 1);
      const tdPilot = document.createElement('td'); tdPilot.className = 'col-pilot ' + connClass; tdPilot.style.textAlign = 'left'; tdPilot.textContent = pilotDisplay;
      const tdStart = document.createElement('td'); tdStart.textContent = s.start || s.inicio || '--:--';
      const tdEnd = document.createElement('td'); tdEnd.textContent = s.end || s.fin || '--:--';
      const tdLaps = document.createElement('td'); tdLaps.style.color = 'var(--color-blue)'; tdLaps.textContent = s.laps || s.laps_est || '';
      const tdFuel = document.createElement('td'); tdFuel.style.color = 'var(--color-orange)'; tdFuel.textContent = s.fuel || '';
      const tdWx = document.createElement('td'); tdWx.innerHTML = `<i class="fas fa-${icon}"></i>`;
      const tdPit = document.createElement('td'); tdPit.textContent = fmtPit(s.pit || s.pit_duration || s.pitDuration);
      const tdNotes = document.createElement('td'); tdNotes.style.color = '#aaa'; tdNotes.textContent = s.notes || s.notas || s.note || '';

      tr.appendChild(tdIndex);
      tr.appendChild(tdPilot);
      tr.appendChild(tdStart);
      tr.appendChild(tdEnd);
      tr.appendChild(tdLaps);
      tr.appendChild(tdFuel);
      tr.appendChild(tdWx);
      tr.appendChild(tdPit);
      tr.appendChild(tdNotes);

      tbody.appendChild(tr);
    });

    // Call any existing decorator to apply animations/strikethroughs if present
    if (typeof renderStrategyTableDecorators === 'function') {
      try { renderStrategyTableDecorators(); } catch (e) { /* ignore */ }
    }
    // Also call the original renderStrategyTable "post" hook if exists (some apps provide it)
    if (typeof window.postRenderStrategy === 'function') {
      try { window.postRenderStrategy(); } catch (e) { /* ignore */ }
    }

    return;
  }

  // FALLBACK: original demo behavior (keeps the old UI if no stints found)
  const activeIdx = 6;
  const drivers = ["Manolo Segovia", "Pepe Lopez", "Juan Martinez", "Valentino Rossi"];

  for(let i=1; i<=15; i++) {
    if (i < activeIdx - 4) continue;
    let name = drivers[(i-1)%4];
    let isConn = (window.connectedPilots && window.connectedPilots.includes(name));
    let connClass = isConn ? 'pilot-on' : 'pilot-off';
    let status = "";
    let wxIcon = "sun";
    let note = "OK";

    if (i < activeIdx) { status = "stint-done"; } 
    else if (i === activeIdx) { status = "stint-active"; note = "PUSHING"; } 
    else if (i === 15) { status = "stint-storm"; wxIcon = "bolt"; note = "STORM"; }

    let pilotDisplay = (i === activeIdx) ? 'TU (NOW)' : name;

    tbody.insertAdjacentHTML('beforeend', `<tr class="${status}">
        <td style="font-weight:bold">${i}</td>
        <td class="col-pilot ${connClass}" style="text-align:left;">${esc(pilotDisplay)}</td>
        <td>12:00</td><td>12:45</td>
        <td style="color:var(--color-blue)">28</td>
        <td style="color:var(--color-orange)">55</td>
        <td><i class="fas fa-${wxIcon}"></i></td>
        <td>01:05</td>
        <td style="color:#aaa">${esc(note)}</td>
    </tr>`);
  }
}

  // --- GRID rendering (with logo fallback) ---
  function renderGrid(data) {
    const tbody = document.getElementById('gridBody');
    if (!tbody) return;
    tbody.innerHTML = '';
    try {
      const classesPresent = [...new Set(data.map(d => d.c_name || ''))].filter(Boolean).sort();
      if(classesPresent.length === 0) classesPresent.push("GT3");

      classesPresent.forEach(cls => {
        tbody.insertAdjacentHTML('beforeend', `<tr class="class-separator"><td colspan="11" style="color:#ccff00; border-left:5px solid #ccff00; text-align:left; padding-left:15px">${escapeHtml(cls)} CLASS</td></tr>`);
        
        const drivers = data.filter(c => (c.c_name || '') === cls);
        drivers.forEach((c, index) => {
            const isMe = c.is_me ? 'row-hero' : '';
            const picValue = index + 1;
            let podiumClass = (picValue === 1) ? "pic-p1" : (picValue === 2) ? "pic-p2" : (picValue === 3) ? "pic-p3" : "";
            
            let stratBadge = `<span class="st-equal">EQUAL</span>`;
            if(c.strat_cls === "lead") stratBadge = `<span class="strat-badge st-lead">${escapeHtml(c.strat_txt||'')}</span>`;
            if(c.strat_cls === "lag") stratBadge = `<span class="strat-badge st-lag">${escapeHtml(c.strat_txt||'')}</span>`;

            const carLogo = (c.car_logo || "iracing").toString().toLowerCase().replace(/\s+/g,'-');
            const localLogo = `/static/img/cars/${encodeURIComponent(carLogo)}.svg`;

            let lastLapClass = ''; 
            if (String(c.last_lap).toUpperCase() === "PIT") { lastLapClass = 'class="state-pit"'; } 
            else if (String(c.last_lap).toUpperCase() === "OUT") { lastLapClass = 'class="state-out"'; }

            tbody.insertAdjacentHTML('beforeend', `<tr class="${isMe}" id="${c.is_me ? 'myRow' : ''}">
                <td style="color:#666">${escapeHtml(c.pos !== undefined ? String(c.pos) : '-')}</td>
                <td><span class="pic-badge ${podiumClass}">P${picValue}</span></td>
                <td><div class="name-grid">
                    <img class="car-logo" data-brand="${escapeHtml(carLogo)}" src="${escapeHtml(localLogo)}" alt="${escapeHtml(carLogo)}" style="width:16px; opacity:0.7">
                    <div class="cell-num">#${escapeHtml(c.num || '')}</div>
                    <span class="fi fi-${escapeHtml(c.flag || 'es')}"></span>
                    <div class="cell-name">${escapeHtml(c.name || '')}</div>
                </div></td>
                <td ${lastLapClass}>${escapeHtml(c.last_lap || '')}</td>
                <td>${escapeHtml(c.best_lap || '')}</td>
                <td style="color:var(--neon-green)">${escapeHtml(c.gap || '')}</td>
                <td style="color:#aaa">${escapeHtml(c.int || '')}</td>
                <td>${stratBadge}</td>
                <td style="color:var(--color-orange)">${escapeHtml(c.s1 || '')}</td>
                <td style="color:#666">${escapeHtml(c.s2 || '')}</td>
                <td style="color:#444">${escapeHtml(c.s3 || '')}</td>
            </tr>`);
        });
      });

      // attach handlers for logos AFTER we've inserted the HTML
      ensureCarLogoHandlers();

      // auto-scroll to myRow if user hasn't scrolled recently
      if (Date.now() - lastUserScroll > 10000) {
        const myRow = document.getElementById('myRow');
        if(myRow) try { myRow.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch(e){}
      }
    } catch (e) {
      console.error('renderGrid internal error', e);
    }
  }

  // --- logo handlers (local -> placeholder fallback) ---
  function ensureCarLogoHandlers() {
    const imgs = document.querySelectorAll('img.car-logo');
    imgs.forEach(img => {
      if (img.dataset._logoHandlerAttached) return;
      img.dataset._logoHandlerAttached = "1";

      img.addEventListener('error', function onErr() {
        try {
          this.removeEventListener('error', onErr);
          this.src = '/static/img/car-placeholder.svg';
        } catch(e) {
          try { this.src = '/static/img/car-placeholder.svg'; } catch(err){}
        }
      });

      img.addEventListener('load', function onLoad() {
        try {
          if (this.naturalWidth === 0) {
            this.dispatchEvent(new Event('error'));
          }
        } catch(e){}
      });
    });
  }

  // --- MAP rendering ---
  function renderMap(grid) {
    const container = document.getElementById('trackMapLine');
    if (!container) return;
    container.innerHTML = '';
    try {
      grid.forEach(car => {
        const pctCandidate = (car.pct !== undefined) ? car.pct : (car.lap_pct !== undefined ? car.lap_pct : (car.lapDistPct !== undefined ? car.lapDistPct : null));
        const pct = (pctCandidate !== null) ? Number(pctCandidate) : null;
        if (pct === null || isNaN(pct)) return;

        const dot = document.createElement('div');
        dot.className = 'map-dot';
        const leftPct = Math.max(0, Math.min(100, (pct > 1 ? pct : pct * 100)));
        dot.style.left = `${leftPct}%`;
        if (car.is_me) {
          dot.classList.add('is-me');
          const img = document.createElement('img');
          img.src = `/static/drivers/${SIM_DRIVER_ID}.jpg`;
          img.onerror = function(){ this.style.background = 'var(--neon-green)'; };
          dot.appendChild(img);
        } else {
          dot.innerText = car.pos || '';
        }
        container.appendChild(dot);
      });
    } catch (err) {
      console.error('renderMap error', err);
    }
  }

  // Expose renderers globally
  window.renderGrid = renderGrid;
  window.renderMap = renderMap;
  window.renderStrategyTable = renderStrategyTable;

  // --- Robust updater/poller (uses payload = data.last_payload || data) ---
  async function update() {
    try {
      const res = await fetch('/api/telemetry/live', { cache: 'no-store', credentials: 'same-origin' });
      if (!res.ok) {
        console.error('Live endpoint error', res.status, res.statusText);
        // Update status immediately as "no data" source seen by this request:
        // mark last seen only if response was ok; here do not update lastSeen.
        const st = document.getElementById('statusText'); if (st) { /* leave hysteresis to decide */ }
        const sd = document.getElementById('statusDot'); if (sd) { /* leave hysteresis to decide */ }
        return;
      }

      const data = await res.json();
      const payload = (data && data.last_payload) ? data.last_payload : data;

      const isLive = (data && (data.connected === true || data.connected === 'true')) || (payload && payload.connected === true);

      // --- Hysteresis-driven status update (avoid flicker) ---
      try {
        if (isLive) {
          window._lt_lastSeenLive = Date.now();
        }
        const now = Date.now();
        const consideredLive = (now - (window._lt_lastSeenLive || 0)) <= STATUS_TIMEOUT_MS;
        const prevStatus = window._lt_status || '';
        const newStatus = consideredLive ? 'connected' : 'disconnected';

        // debug: uncomment if you need verbose tracing
        // console.debug('LT status', {isLive, lastSeen: window._lt_lastSeenLive, consideredLive, prevStatus, newStatus});

        if (prevStatus !== newStatus) {
          window._lt_status = newStatus;
          const st = document.getElementById('statusText');
          const sd = document.getElementById('statusDot');

          if (newStatus === 'connected') {
            if (st) { st.innerText = 'Conectado'; st.style.color = 'var(--neon-green)'; }
            if (sd) { sd.className = 'status-dot online'; }
          } else {
            if (st) { st.innerText = 'Desconectado'; st.style.color = '#d9534f'; }
            if (sd) { sd.className = 'status-dot'; }
          }
        }
      } catch(e) {
        console.warn('status hysteresis update failed', e);
      }

      // If not currently live, show waiting message for grid and skip rendering heavy parts
      if (!isLive) {
        const tbody = document.getElementById('gridBody'); if (tbody) tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; padding:30px; color:#555;">CONECTANDO CON BRIDGE...</td></tr>';
        return;
      }

      // Session and HUD
      const sessionTimer = payload.session_timer || payload.sessionTimer || payload.session_timer_display || payload.session_timer_text || '';
      const hudTime = document.getElementById('hudTime'); if (hudTime) hudTime.innerText = sessionTimer || '--:--:--';

      const mycar = payload.my_car || payload.myCar || data.my_car || data.myCar || {};
      const hudFuel = document.getElementById('hudFuel'); if (hudFuel) {
        const f = (mycar && mycar.fuel !== undefined) ? Number(mycar.fuel) : (payload.my_car && payload.my_car.fuel !== undefined ? Number(payload.my_car.fuel) : NaN);
        hudFuel.innerText = (!isNaN(f) ? f.toFixed(1) : '--.-');
      }
      const hudTarget = document.getElementById('hudTarget'); if (hudTarget) {
        const fn = (payload.fuel_needed !== undefined) ? payload.fuel_needed : (mycar && mycar.fuel_needed !== undefined ? mycar.fuel_needed : '');
        hudTarget.innerText = (fn !== '' && fn !== undefined && fn !== null) ? String(fn) : '--';
      }
      const hudInc = document.getElementById('hudInc'); if (hudInc) {
        const incVal = (mycar && (mycar.incidents !== undefined)) ? `${mycar.incidents}/${(mycar.inc_limit||'x')}` : '--/--';
        hudInc.innerText = incVal;
      }

      // Track / session badges and weather
      const trackBadge = document.getElementById('trackNameBadge'); if (trackBadge) trackBadge.innerText = payload.track_name || payload.track || (payload.last_payload && payload.last_payload.track_name) || trackBadge.innerText || '-';
      const sessionBadge = document.getElementById('sessionTypeBadge'); if (sessionBadge) sessionBadge.innerText = payload.session_type || payload.session || (payload.last_payload && payload.last_payload.session_type) || sessionBadge.innerText || '-';

      const weather = payload.weather || data.weather || {};
      const weatherAirEl = document.getElementById('weatherAir'); if (weatherAirEl) weatherAirEl.innerText = (weather.air !== undefined ? String(weather.air) + '°C' : '--°C');
      const weatherTrackEl = document.getElementById('weatherTrack'); if (weatherTrackEl) weatherTrackEl.innerText = (weather.track !== undefined ? String(weather.track) + '°C' : '--°C');
      const weatherRainEl = document.getElementById('weatherRain'); if (weatherRainEl) weatherRainEl.innerText = (weather.rain !== undefined ? String(weather.rain) + '%' : '--%');
      const weatherStatusEl = document.getElementById('weatherStatus'); if (weatherStatusEl) weatherStatusEl.innerText = (weather.status !== undefined ? weather.status : (payload.status || '--'));

      // Grid and map
      const grid = payload.grid || data.grid || [];
      if (Array.isArray(grid) && grid.length > 0) {
        try { renderGrid(grid); } catch(e) { console.error('renderGrid error', e); }
        const anyPct = grid.some(c => (c && (typeof c.pct === 'number' || typeof c.pct === 'string' || typeof c.lap_pct === 'number')));
        if (anyPct) {
          try { renderMap(grid); } catch(e) { console.error('renderMap error', e); }
        } else {
          const container = document.getElementById('trackMapLine'); if (container) container.innerHTML = '';
        }
      } else {
        const tbody = document.getElementById('gridBody'); if (tbody) tbody.innerHTML = '<tr><td colspan="11" style="text-align:center; padding:30px; color:#555;">CONECTANDO CON BRIDGE...</td></tr>';
      }

    } catch (err) {
      console.error('update() exception:', err);
      // keep hysteresis responsible for status; do not aggressively change DOM here
    }
  }

  // Initialize UI and polling (with stop logic to avoid duplicates)
  function init(){
    attachScrollTracking();
    try { renderStrategyTable(); } catch(e){}

    // Stop runtime poller if present (avoid double updates)
    try {
      if (typeof window.stopLiveTimingConnection === 'function') {
        try { window.stopLiveTimingConnection(); console.log('Stopped live_timing_runtime poller (stopLiveTimingConnection).'); } catch(e){}
      }
    } catch(e){ console.warn(e); }
    try { if (window._lt_conn_poll) { clearInterval(window._lt_conn_poll); window._lt_conn_poll = null; console.log('_lt_conn_poll cleared'); } } catch(e){}

    // Normalize "CARGAR ESTRATEGIA" button: keep first, remove duplicates and rebind a single handler
    try {
      const btns = Array.from(document.querySelectorAll('button, a')).filter(el => (el.innerText||'').trim().toUpperCase().includes('CARGAR ESTRATEGIA'));
      if (btns.length > 0) {
        const first = btns[0];
        for (let i = 1; i < btns.length; i++) { try { btns[i].remove(); } catch(e){} }
        const clone = first.cloneNode(true);
        first.parentNode.replaceChild(clone, first);
        clone.id = clone.id || 'btnLoadStrat';
        // If openStratModal exists, use it; otherwise provide a safe fallback
// Reemplazar la asignación del listener que normaliza el botón
// Anterior:
// clone.addEventListener('click', function(){ if (typeof openStratModal === 'function') openStratModal(); else alert('Cargar estrategia'); });

// Nuevo:
clone.addEventListener('click', function(ev){
  try {
    ev.preventDefault();
    // Si existe la función que abre el modal, la llamamos
    if (typeof openStratModal === 'function') {
      openStratModal();
      return;
    }
    // Intentamos disparar cualquier trigger DOM que abra el modal (p.ej. un botón oculto)
    const fallbackTrigger = document.querySelector('[data-action="open-strategy"], #btnOpenStrategy');
    if (fallbackTrigger && typeof fallbackTrigger.click === 'function') {
      fallbackTrigger.click();
      return;
    }
    // Fallback silencioso: registrar para depuración pero NO mostrar alert()
    console.warn('openStratModal no encontrada; botón "Cargar estrategia" activado sin modal.');
  } catch (e) {
    console.error('Error en handler cargar estrategia', e);
  }
});
        console.log('Load strategy button normalized and handler bound.');
      } else {
        console.log('No load-strat button found to normalize.');
      }
    } catch(e) {
      console.warn('Error normalizing load-strat button', e);
    }

    // start own poller (clear any previous)
    update(); // immediate
    if (window.liveTimingCustomHandle) clearInterval(window.liveTimingCustomHandle);
    window.liveTimingCustomHandle = setInterval(update, 500);
    console.log('live_timing_custom.js initialized (renderers + poller).');
  }

  // Start when DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // expose for debugging
  window._liveTimingCustom = {
    renderGrid, renderMap, renderStrategyTable, update
  };

})();
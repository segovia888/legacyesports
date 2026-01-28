// live_timing_strategies.js
// Modal / loader de estrategias — versión robusta para integrarse con renderStrategyTable/applyStrategyToPlan existentes.
// Colocar en static/js/ y recargar la página (Disable cache + Ctrl+F5).
(function () {
  // Idempotencia
  if (window.__lt_strat_loaded) {
    console.log('live_timing_strategies: ya cargado');
    return;
  }
  window.__lt_strat_loaded = true;
  console.log('live_timing_strategies: cargando...');

  const API_LIST = '/api/estrategias';
  const API_DETAIL = id => `/api/estrategia/${id}`;
  const TELEMETRY = '/api/telemetry/live';

  // small helper to create elements
  function el(tag, attrs = {}, children = []) {
    const d = document.createElement(tag);
    for (let k in attrs) {
      if (k === 'class') d.className = attrs[k];
      else if (k === 'html') d.innerHTML = attrs[k];
      else d.setAttribute(k, attrs[k]);
    }
    (Array.isArray(children) ? children : [children]).forEach(c => {
      if (!c) return;
      if (typeof c === 'string') d.appendChild(document.createTextNode(c));
      else d.appendChild(c);
    });
    return d;
  }

  function createModal() {
    try {
      const overlay = el('div', { class: 'modal-overlay', id: 'lt-strat-modal', style: 'display:flex; align-items:center; justify-content:center; z-index:9999;' });
      const modal = el('div', { class: 'modal', style: 'max-width:900px; width:90%; max-height:80vh; overflow:auto; padding:18px; background:#000; border:1px solid #333; border-radius:6px;' });
      const header = el('div', { style: 'display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;' }, [
        el('h3', { style: 'margin:0; color:#fff;' }, ['Cargar Estrategia']),
        el('button', { id: 'lt-close-btn', class: 'btn' }, ['Cerrar'])
      ]);
      const content = el('div', { id: 'lt-modal-content', style: 'color:#fff;' });
      modal.appendChild(header);
      modal.appendChild(content);
      overlay.appendChild(modal);
      document.body.appendChild(overlay);

      document.getElementById('lt-close-btn').addEventListener('click', () => { try { overlay.remove(); } catch(e){} });
      overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

      return { overlay, content };
    } catch (e) {
      console.error('createModal error', e);
      return { overlay: null, content: document.body };
    }
  }

  // fetch helpers
  async function fetchStrategies() {
    const r = await fetch(API_LIST, { credentials: 'same-origin' });
    if (!r.ok) throw new Error('API /api/estrategias error ' + r.status);
    const json = await r.json();
    if (Array.isArray(json)) return json;
    if (Array.isArray(json.strategies)) return json.strategies;
    if (Array.isArray(json.data)) return json.data;
    if (Array.isArray(json.items)) return json.items;
    for (let k in json) if (Array.isArray(json[k])) return json[k];
    return [];
  }
  async function fetchStrategy(id) {
    const r = await fetch(API_DETAIL(id), { credentials: 'same-origin' });
    if (!r.ok) throw new Error('API /api/estrategia/' + id + ' error ' + r.status);
    const json = await r.json();
    if (json.strategy) return json.strategy;
    if (json.data) return json.data;
    if (json.detail) return json.detail;
    if (json.payload || json.stints) return json;
    return json;
  }

// (Fragmento) función exposeStints actualizada — reemplaza la versión existente en live_timing_strategies.js
function exposeStints(strategyDetail) {
  try {
    const stints = (strategyDetail.payload && strategyDetail.payload.stints) || strategyDetail.stints || (strategyDetail.data && strategyDetail.data.stints) || strategyDetail.payload && strategyDetail.payload.relays || strategyDetail.relays || [];
    window.currentStrategyStints = stints;
    console.log('live_timing_strategies: window.currentStrategyStints asignado (' + (stints && stints.length) + ' stints)');

    // --- NUEVO: guardar copia en localStorage para que Live Timing pueda leerla ---
    try {
      const store = { meta: { name: strategyDetail.name || strategyDetail.title || null, id: strategyDetail.id || null, ts: Date.now() }, stints: stints };
      localStorage.setItem('lt_current_strategy', JSON.stringify(store));
      // notificar otras pestañas en la misma ventana
      window.dispatchEvent(new CustomEvent('lt:strategy:updated', { detail: store }));
      console.log('live_timing_strategies: estrategia guardada en localStorage lt_current_strategy');
    } catch (e) {
      console.warn('live_timing_strategies: no se pudo guardar en localStorage', e);
    }

    return stints;
  } catch (e) {
    console.error('exposeStints error', e);
    window.currentStrategyStints = [];
    return [];
  }
}

  // Render preview (local fallback if no global preview renderer)
  function localRenderPreview(container, strat) {
    container.innerHTML = '';
    const title = el('div', { style: 'font-weight:700;color:#fff;margin-bottom:8px;' }, [strat.name || strat.title || 'Sin nombre']);
    container.appendChild(title);
    const stints = (strat.payload && strat.payload.stints) || strat.stints || [];
    if (!stints.length) {
      container.appendChild(el('div', { style: 'color:#aaa;' }, ['No hay stints.']));
      return;
    }
    const table = el('table', { style: 'width:100%; color:#fff; border-collapse:collapse;' });
    const thead = el('thead', {}, [ el('tr', {}, [
      el('th', {}, ['#']), el('th', {}, ['PILOTO']), el('th', {}, ['INICIO']), el('th', {}, ['FIN']), el('th', {}, ['LAPS']), el('th', {}, ['FUEL']), el('th', {}, ['WX']), el('th', {}, ['PIT'])
    ])]);
    const tbody = el('tbody', {});
    stints.forEach((s,i)=> {
      const wx = s.wx || s.weather || s.wx_type || '';
      const tr = el('tr', { class: (String(wx).toLowerCase().includes('rain') ? 'stint-storm' : '') }, [
        el('td', {}, [String(i+1)]),
        el('td', {}, [s.driver || s.name || '---']),
        el('td', {}, [s.start || s.inicio || '--:--']),
        el('td', {}, [s.end || s.fin || '--:--']),
        el('td', {}, [String(s.laps || s.laps_est || '')]),
        el('td', {}, [String(s.fuel || '')]),
        el('td', {}, [s.wx || '-']),
        el('td', {}, [s.pit ? 'YES' : ''])
      ]);
      tbody.appendChild(tr);
    });
    table.appendChild(thead);
    table.appendChild(tbody);
    container.appendChild(table);
  }

  // Attach handlers to "Cargar Estrategia" UI (tries multiple strategies to find the button)
  function attachLoadButtons() {
    try {
      // Candidate selectors in order of reliability
      const candidates = [
        '.btn-load',
        '#btnLoadStrategy',
        'button#btnLoadStrategy',
        'button.load-strategy',
        'a.load-strategy',
        'button',
        'a'
      ];
      const textsToMatch = ['cargar estrategia', 'cargar estrategia', 'cargar', 'load strategy', 'load'];
      const seen = new Set();
      Array.from(document.querySelectorAll(candidates.join(','))).forEach(elm => {
        if (seen.has(elm)) return;
        seen.add(elm);
        const txt = (elm.innerText || '').trim().toLowerCase();
        // limit attachment to likely candidates to avoid messing with all buttons
        const likely = textsToMatch.some(t => txt.includes(t)) || elm.id && (elm.id.toLowerCase().includes('load') || elm.id.toLowerCase().includes('strat'));
        if (!likely) return;
        if (elm.dataset.ltAttached) return;
        elm.dataset.ltAttached = '1';

        elm.addEventListener('click', async (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          console.log('live_timing_strategies: botón "Cargar Estrategia" pulsado (hot attach).');

          const modalObj = createModal();
          const content = modalObj.content;
          content.innerHTML = '<div style="padding:8px;color:#ccc;">Cargando estrategias…</div>';

          let strategies;
          try {
            strategies = await fetchStrategies();
          } catch (err) {
            console.error('Error listando estrategias', err);
            content.innerHTML = '<div style="padding:8px;color:#f88;">Error cargando estrategias</div>';
            return;
          }

          if (!Array.isArray(strategies) || strategies.length === 0) {
            content.innerHTML = '<div style="padding:8px;color:#f88;">No se encontraron estrategias.</div>';
            return;
          }

          // build list
          content.innerHTML = '';
          const listWrap = el('div', { style: 'display:flex; flex-direction:column; gap:8px; max-height:60vh; overflow:auto;' });
          strategies.forEach(s => {
            const id = s.id || s._id || s.key;
            const row = el('div', { style: 'display:flex; justify-content:space-between; align-items:center; padding:8px; border-bottom:1px solid #222;' }, [
              el('div', {}, [
                el('div', { style: 'font-weight:700;color:#fff;' }, [s.name || s.title || 'Sin nombre']),
                el('div', { style: 'color:#aaa;font-size:12px;' }, [`${s.car_name || ''} · ${s.car_class || ''} ${s.created_at ? ' · ' + new Date(s.created_at).toLocaleString() : ''}`])
              ]),
              el('div', {}, [
                el('button', { type: 'button', 'data-id': id, style: 'margin-right:8px;padding:6px 8px;background:#444;color:#fff;border-radius:4px;border:0;' }, ['Previsualizar']),
                el('button', { type: 'button', 'data-id': id, style: 'padding:6px 8px;background:#2b8aef;color:#fff;border-radius:4px;border:0;' }, ['Cargar'])
              ])
            ]);
            listWrap.appendChild(row);
          });
          content.appendChild(listWrap);

          // delegate clicks inside list
          listWrap.addEventListener('click', async (evt) => {
            const b = evt.target.closest('button');
            if (!b) return;
            const id = b.getAttribute('data-id');
            if (!id) return;
            let detail;
            try {
              detail = await fetchStrategy(id);
            } catch (err) {
              console.error('Error fetching detail', err);
              alert('Error cargando detalle (ver consola)');
              return;
            }
            const strat = detail && (detail.strategy || detail.data || detail) || detail;
            exposeStints(strat);

            const action = (b.textContent || '').trim().toLowerCase();
            // preview
            if (action.includes('previsualizar') || action.includes('preview')) {
              // remove old preview
              const prev = content.querySelector('#lt-preview'); if (prev) prev.remove();
              const preview = el('div', { id: 'lt-preview', style: 'padding:8px;border-top:1px solid #222;margin-top:8px;' });
              if (typeof window.renderStrategyPreview === 'function') {
                try { window.renderStrategyPreview(preview, strat); } catch(e) { localRenderPreview(preview, strat); }
              } else {
                localRenderPreview(preview, strat);
              }
              content.appendChild(preview);
              // also update main table so user sees preview applied
              if (typeof window.renderStrategyTable === 'function') {
                try { window.renderStrategyTable(); } catch(e) { console.warn('renderStrategyTable fallo', e); }
              }
              return;
            }

            // cargar (apply)
            if (action.includes('cargar') || action.includes('load')) {
              try {
                if (typeof window.applyStrategyToPlan === 'function') {
                  await Promise.resolve(window.applyStrategyToPlan(strat));
                  console.log('live_timing_strategies: applyStrategyToPlan usada');
                } else if (typeof window.applyStrategy === 'function') {
                  await Promise.resolve(window.applyStrategy(strat));
                  console.log('live_timing_strategies: applyStrategy usada');
                } else {
                  // fallback: call local apply to populate table body and then call renderStrategyTable
                  await applyStrategyToPlan(strat);
                }
              } catch (err) {
                console.warn('applyStrategy fallback falló', err);
                try { await applyStrategyToPlan(strat); } catch(e2){ console.error('applyStrategyToPlan fallo', e2); }
              }

              // ensure main renderer is run
              if (typeof window.renderStrategyTable === 'function') {
                try { window.renderStrategyTable(); } catch(e) { console.warn('renderStrategyTable fallo', e); }
              }
              // close modal
              try { modalObj.overlay.remove(); } catch(e){}
            }
          });
        }, { capture: true });
      });
    } catch (e) {
      console.error('attachLoadButtons error', e);
    }
  }

  // initial attach + observer
  function init() {
    try {
      attachLoadButtons();
      // re-run occasionally in case DOM changes
      setInterval(attachLoadButtons, 2500);
    } catch (e) { console.error('init error', e); }
  }

  // Expose for debugging
  window.__lt_strat = {
    exposeStints, fetchStrategies, fetchStrategy
  };

  // run
  if (document.readyState === 'complete' || document.readyState === 'interactive') init();
  else window.addEventListener('DOMContentLoaded', init);

  console.log('live_timing_strategies: listo');
})();
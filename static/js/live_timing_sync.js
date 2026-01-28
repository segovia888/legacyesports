// live_timing_sync.js
// Sincroniza la estrategia guardada en localStorage ('lt_current_strategy')
// con la tabla #tblRelay en la página de Live Timing.
// Incluye: render exacto de filas, recalculo sencillo de totales, escucha de cambios.

(function(){
  if(window.__lt_sync_file_loaded) return;
  window.__lt_sync_file_loaded = true;

  function safe(v, d=''){ return (v==null||v==='')?d:String(v); }
  function pad(n){ return (n<10?'0':'')+n; }
  function hhmmToSec(h){
    if(!h) return 0;
    const p = String(h).split(':').map(x=>Number(x)); if(p.length===3) return p[0]*3600 + p[1]*60 + p[2]; if(p.length===2) return p[0]*3600 + p[1]*60; return Number(h)||0;
  }
  function secToHHMM(sec){
    sec = Math.floor(sec);
    const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60);
    return `${pad(h)}:${pad(m)}`;
  }
  function calcDurText(start, end){
    const s = hhmmToSec(start), e = hhmmToSec(end);
    let d = e - s;
    if(d < 0) d += 86400;
    const mm = Math.floor(d/60), ss = d%60;
    return `${(mm<10?'0':'')+mm}:${(ss<10?'0':'')+ss}`;
  }

  function buildRowFromStint(s, idx){
    const driver = safe(s.driver || s.name || s.pilot || '');
    const start = safe(s.start || s.start_local || s.inicio || '00:00');
    const end = safe(s.end || s.end_local || s.fin || '00:00');
    const laps = (s.laps != null) ? String(s.laps) : (s.laps_est!=null?String(s.laps_est):'0');
    const fuel = (s.fuel != null) ? String(s.fuel) : (s.fuel_est!=null?String(s.fuel_est):'0');
    const pit = safe(s.pit || s.pitTime || s.pit_duration || '');
    const notes = safe(s.notes || s.note || s.notas || ('Stint ' + (idx+1)));

    const tr = document.createElement('tr');
    tr.innerHTML = ''
      + `<td class="idx text-white fw-bold">${idx+1}</td>`
      + `<td><input class="form-control form-control-sm driver fw-bold" placeholder="Nombre" value="${driver}"></td>`
      + `<td><input type="time" class="form-control form-control-sm start" value="${start}"></td>`
      + `<td><input type="time" class="form-control form-control-sm end" value="${end}"></td>`
      + `<td class="startS text-white text-center small"></td>`
      + `<td class="endS text-white text-center small"></td>`
      + `<td class="dur text-center text-white fw-bold">${calcDurText(start,end)}</td>`
      + `<td class="laps text-center text-info fw-bold">${laps}</td>`
      + `<td class="fuel text-center text-warning fw-bold">${fuel}</td>`
      + `<td class="inc-cell text-center" style="color:#aaa;">0x</td>`
      + `<td class="neu-cell text-center"><input class="form-control form-control-sm avg-input" placeholder="%" style="width:45px;background:transparent;border:none;text-align:center;color:#aaa;"></td>`
      + `<td class="pit text-white text-center small">${pit}</td>`
      + `<td><input class="form-control form-control-sm notes" value="${notes}"></td>`
      + `<td><button class="btn btn-sm btn-outline-danger border-0 del"><i class="fas fa-trash"></i></button></td>`;

    const del = tr.querySelector('.del');
    if(del) del.addEventListener('click', ()=>{ tr.remove(); renumRelay(); recalcRelayTotals(); });

    // When user edits start/end/laps/note update totals quickly
    ['change','input'].forEach(ev => {
      tr.addEventListener(ev, (e)=> { if(e.target.matches('.start') || e.target.matches('.end') || e.target.matches('.laps') || e.target.matches('.fuel')) { renumRelay(); recalcRelayTotals(); }});
    });

    return tr;
  }

  function renumRelay(){
    document.querySelectorAll('#tblRelay tbody tr .idx').forEach((el,i)=> el.textContent = i+1);
  }

  function recalcRelayTotals(){
    try{
      const rows = Array.from(document.querySelectorAll('#tblRelay tbody tr'));
      let totLaps = 0, totFuel = 0, totSec = 0;
      rows.forEach(tr=>{
        const lEl = tr.querySelector('.laps');
        const fEl = tr.querySelector('.fuel');
        let l = 0, f = 0;
        if(lEl) l = Number(lEl.textContent || 0);
        if(fEl) f = Number(fEl.textContent || 0);
        const durText = (tr.querySelector('.dur') && tr.querySelector('.dur').textContent) || '00:00';
        const parts = durText.split(':').map(Number);
        let sec = 0;
        if(parts.length===2) sec = parts[0]*60 + parts[1];
        else if(parts.length===3) sec = parts[0]*3600 + parts[1]*60 + parts[2];
        totLaps += l; totFuel += f; totSec += sec;
      });
      const ftLaps = document.getElementById('ftRelayLaps'); if(ftLaps) ftLaps.textContent = totLaps;
      const ftFuel = document.getElementById('ftRelayFuel'); if(ftFuel) ftFuel.textContent = totFuel;
      const ftDur = document.getElementById('ftRelayDur'); if(ftDur) ftDur.textContent = secToHHMM(totSec);
      const statLaps = document.getElementById('statTotalLaps'); if(statLaps) statLaps.textContent = totLaps;
      const statStints = document.getElementById('statStints'); if(statStints) statStints.textContent = document.querySelectorAll('#tblRelay tbody tr').length;
    }catch(e){ console.warn('recalcRelayTotals err', e); }
  }

  function applyStintsArray(stints){
    if(!Array.isArray(stints)) return;
    const tbody = document.querySelector('#tblRelay tbody');
    if(!tbody) return;
    tbody.innerHTML = '';
    stints.forEach((s,i)=> tbody.appendChild(buildRowFromStint(s,i)));
    renumRelay(); recalcRelayTotals();
  }

  // read persistent strategy object from localStorage
  function readStrategyFromLocal(){
    try{
      const raw = localStorage.getItem('lt_current_strategy');
      if(!raw) return null;
      const parsed = JSON.parse(raw);
      // recent format uses { meta:{...}, stints: [...] }
      if(parsed && Array.isArray(parsed.stints)) return parsed.stints;
      // fallback if stored array directly
      if(Array.isArray(parsed)) return parsed;
      // if payload.relays
      if(parsed && parsed.payload && Array.isArray(parsed.payload.relays)) return parsed.payload.relays;
      if(parsed && parsed.relays && Array.isArray(parsed.relays)) return parsed.relays;
      return null;
    }catch(e){ console.warn('readStrategyFromLocal err', e); return null; }
  }

  // public apply now
  function applyFromLocalNow(){
    const st = readStrategyFromLocal();
    if(st) { applyStintsArray(st); console.info('live_timing_sync: estrategia aplicada (len=' + st.length + ')'); }
    else console.info('live_timing_sync: no hay lt_current_strategy en localStorage');
  }

  // listen storage events (other tab updates)
  window.addEventListener('storage', function(e){
    if(e.key === 'lt_current_strategy'){
      setTimeout(applyFromLocalNow, 50);
    }
  });

  // listen custom event from same tab dispatch
  window.addEventListener('lt:strategy:updated', function(e){
    setTimeout(applyFromLocalNow, 50);
  });

  // run on load
  document.addEventListener('DOMContentLoaded', function(){
    // short delay to allow DOM to be ready
    setTimeout(applyFromLocalNow, 200);
  });

  // expose small API
  window.ltSync = { applyNow: applyFromLocalNow, stop: function(){ window.removeEventListener('storage', applyFromLocalNow); } };

  console.info('live_timing_sync: cargado — aplicará lt_current_strategy en #tblRelay si existe (escucha storage & lt:strategy:updated).');
})();
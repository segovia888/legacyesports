#!/usr/bin/env python3
# Bridge completo y corregido - versión final integrada
# - Lectura defensiva desde irsdk (usa ir_get para acceder a campos)
# - Estimador de usage (actividad + temp) con EMA
# - Cálculo de fuel_needed para "A META"
# - Actualiza payload con usage_percent, usage_label, usage_debug y fuel_needed
# - Detecta escala de CarIdxLapDistPct (0..1 vs 0..100) y normaliza
# - Mantiene el resto de la lógica original (stints, fuel model, grid)

import time
import requests
import irsdk
import math
import traceback
import pickle
import os
import sys

URL_DESTINO = "http://127.0.0.1:5000/api/telemetry/ingest"
AVG_PIT_LOSS = 50.0

# ===========================
# Configuración estimador usage
# ===========================
DT_SLEEP = 0.5                # segundos entre ticks
TAU = 8.0                     # tiempo de suavizado (s)
K_ACTIVITY = 0.6              # sensibilidad actividad -> 100% (base history scale)
USAGE_UPDATE_INTERVAL = 180   # segundos entre actualizaciones visibles enviadas

# Estado global del estimador
CUMULATIVE_CAR_LAPS = 0.0
PREV_LAP_PCTS = None
EMA_USAGE = None
LAST_USAGE_SEND_TS = 0
USAGE_SENT_PERCENT = None
USAGE_SENT_LABEL = ""

# ===========================
# Estado y utilidades
# ===========================
STATE_FILE = "stint_state.pkl"

class State:
    ir_connected = False
    stint_history = {}
    current_stint_start = {}
    my_last_fuel = None
    my_last_lap = None
    my_fuel_samples = []
    my_fuel_per_lap = None
    my_tank_capacity = None

def ir_get(ir, key, default=None):
    """Acceso seguro a ir[...] (IRSDK no tiene .get)."""
    try:
        return ir[key]
    except Exception:
        return default

def check_iracing(ir, state):
    """
    Comprueba el estado de conexión con iRacing y actualiza state.ir_connected.
    Defensiva: no lanza excepciones si algo falla.
    """
    try:
        if state.ir_connected and not (getattr(ir, 'is_initialized', False) and getattr(ir, 'is_connected', False)):
            state.ir_connected = False
            print("\n[!] iRacing desconectado.")
        elif not state.ir_connected:
            try:
                started = ir.startup() if hasattr(ir, 'startup') else False
            except Exception:
                started = False
            if started and getattr(ir, 'is_initialized', False) and getattr(ir, 'is_connected', False):
                state.ir_connected = True
                print("\n[+] CONECTADO A IRACING.")
    except Exception:
        pass

# FUNCIONES AUXILIARES
def safe_float(val, default=0.0):
    try:
        return float(val)
    except Exception:
        return default

def safe_int(val, default=0):
    try:
        return int(val)
    except Exception:
        return default

def format_time(seconds):
    val = safe_float(seconds)
    if val <= 0:
        return ""
    m = int(val // 60)
    s = int(val % 60)
    ms = int((val - int(val)) * 1000)
    if m > 0:
        return f"{m}:{s:02d}.{ms:03d}"
    else:
        return f"{s:02d}.{ms:03d}"

def format_session_timer(seconds):
    val = safe_float(seconds)
    if val < 0:
        return "00:00:00"
    m, s = divmod(int(val), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def get_brand_logo(car_name_raw):
    try:
        name = str(car_name_raw).lower()
        if "porsche" in name: return "porsche"
        if "ferrari" in name: return "ferrari"
        if "bmw" in name: return "bmw"
        if "mercedes" in name: return "mercedes"
        if "audi" in name: return "audi"
        if "lamborghini" in name: return "lamborghini"
        if "mclaren" in name: return "mclaren"
        if "ford" in name: return "ford"
    except Exception:
        pass
    return "iracing"

# ===========================
# Modelo fuel (tu coche)
# ===========================
def update_my_fuel_model(ir, state, my_idx, max_samples=20):
    """
    Calcula consumo medio (L/vuelta) del coche de referencia (tu coche).
    También estima capacidad de depósito si hay FuelLevelPct.
    """
    try:
        my_lap = safe_int(ir['CarIdxLapCompleted'][my_idx])
        fuel = safe_float(ir['FuelLevel'])
        fuel_pct = None
        try:
            fuel_pct = safe_float(ir['FuelLevelPct'])
        except Exception:
            fuel_pct = None

        if fuel_pct is not None and fuel_pct > 0.01:
            cap = fuel / fuel_pct
            if cap > 0:
                state.my_tank_capacity = cap

        if state.my_last_fuel is not None and state.my_last_lap is not None:
            dlaps = my_lap - state.my_last_lap
            dfuel = state.my_last_fuel - fuel
            if dlaps > 0 and dfuel > 0:
                per_lap = dfuel / dlaps
                state.my_fuel_samples.append(per_lap)
                if len(state.my_fuel_samples) > max_samples:
                    state.my_fuel_samples.pop(0)
                state.my_fuel_per_lap = sum(state.my_fuel_samples) / len(state.my_fuel_samples)

        state.my_last_fuel = fuel
        state.my_last_lap = my_lap
    except Exception:
        pass

# ===========================
# STINTS persistence
# ===========================
def save_state(state):
    try:
        # create a backup of previous state file (if exists)
        try:
            if os.path.exists(STATE_FILE):
                os.replace(STATE_FILE, STATE_FILE + ".bak")
        except Exception:
            pass
        with open(STATE_FILE, "wb") as f:
            pickle.dump({
                "current_stint_start": state.current_stint_start,
                "stint_history": state.stint_history,
                "cumulative_car_laps": CUMULATIVE_CAR_LAPS,
                "prev_lap_pcts": PREV_LAP_PCTS,
                "ema_usage": EMA_USAGE
            }, f)
    except Exception as e:
        print("[!] Error al guardar estado:", e)

def load_state(state):
    global CUMULATIVE_CAR_LAPS, PREV_LAP_PCTS, EMA_USAGE
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "rb") as f:
                data = pickle.load(f)
                state.current_stint_start = data.get("current_stint_start", {})
                state.stint_history = data.get("stint_history", {})
                CUMULATIVE_CAR_LAPS = data.get("cumulative_car_laps", CUMULATIVE_CAR_LAPS)
                PREV_LAP_PCTS = data.get("prev_lap_pcts", PREV_LAP_PCTS)
                EMA_USAGE = data.get("ema_usage", EMA_USAGE)
    except Exception as e:
        print("[!] Error al cargar estado:", e)

def process_stints(ir, state):
    try:
        on_pit = ir['CarIdxOnPitRoad']
        laps = ir['CarIdxLapCompleted']
        if not on_pit or not laps:
            return

        for i in range(len(on_pit)):
            if i not in state.current_stint_start:
                state.current_stint_start[i] = safe_int(laps[i])
                state.stint_history[i] = []
                save_state(state)

            is_in_pit_mem = state.current_stint_start.get(f"in_pit_{i}", False)
            curr_lap = safe_int(laps[i])

            if on_pit[i] and not is_in_pit_mem:
                stint_len = curr_lap - state.current_stint_start[i]
                if stint_len > 3:
                    state.stint_history[i].append(stint_len)
                    if len(state.stint_history[i]) > 5:
                        state.stint_history[i].pop(0)
                state.current_stint_start[f"in_pit_{i}"] = True
                save_state(state)

            if not on_pit[i] and is_in_pit_mem:
                state.current_stint_start[i] = curr_lap
                state.current_stint_start[f"in_pit_{i}"] = False
                save_state(state)
    except Exception:
        pass

def calculate_stops_remaining(laps_done, total_laps, avg_stint):
    if avg_stint <= 0:
        return 0
    laps_to_go = total_laps - laps_done
    if laps_to_go <= 0:
        return 0
    return math.ceil(laps_to_go / avg_stint) - 1

# ===========================
# Estimador de usage
# ===========================
def compute_active_lap_delta(ir, prev_pcts):
    """
    Devuelve (delta_sum, nuevo_prev_pcts)
    delta_sum = suma de fracciones de vuelta completadas por todos los coches entre ticks.
    Detecta automática si CarIdxLapDistPct está en 0..1 o en 0..100 y normaliza internamente.
    """
    try:
        cur = ir_get(ir, 'CarIdxLapDistPct', None)
        if not cur:
            return 0.0, prev_pcts

        # convert items to floats where possible
        cur_nums = []
        for v in cur:
            try:
                cur_nums.append(float(v))
            except Exception:
                cur_nums.append(None)

        # detect scale: if most values <= 1.0, assume 0..1 scale (multiply by 100)
        valid_vals = [x for x in cur_nums if x is not None]
        scale_factor = 1.0
        if len(valid_vals) > 0:
            sorted_vals = sorted(valid_vals)
            median = sorted_vals[len(sorted_vals)//2]
            if median <= 1.0:
                scale_factor = 100.0

        # normalize current values to 0..100
        cur_norm = []
        for v in cur_nums:
            if v is None:
                cur_norm.append(None)
            else:
                cur_norm.append(float(v) * scale_factor)

        # initialize prev_pcts to zeros matching length (in normalized scale)
        delta = 0.0
        if prev_pcts is None:
            prev_pcts = [0.0] * len(cur_norm)

        n = min(len(cur_norm), len(prev_pcts))
        for i in range(n):
            try:
                c = cur_norm[i]
                p = float(prev_pcts[i]) if prev_pcts[i] is not None else 0.0
                if c is None:
                    continue
                d = (c - p) / 100.0
                if d < 0:
                    # wrap / reset handling: if negative, use current fraction as increment
                    d = c / 100.0
                if d < 0:
                    d = 0.0
                delta += d
            except Exception:
                continue

        # if new entries present (cur longer than prev)
        if len(cur_norm) > n:
            for j in range(n, len(cur_norm)):
                try:
                    v = cur_norm[j]
                    if v is None:
                        continue
                    delta += float(v) / 100.0
                except Exception:
                    continue

        # ensure prev_pcts returned as numeric list (normalized)
        return delta, [ (v if v is not None else 0.0) for v in cur_norm ]
    except Exception:
        return 0.0, prev_pcts

def estimate_usage_from_activity_temp(cumulative_laps, track_temp, is_raining):
    # base raw activity uses global K_ACTIVITY; this can be further tuned when computing combined signal
    raw_activity = min(100.0, cumulative_laps * K_ACTIVITY)
    temp_factor = 0.0
    try:
        if track_temp is not None:
            t = float(track_temp)
            LOW, HIGH = 15.0, 55.0
            temp_factor = max(0.0, min(1.0, (t - LOW) / (HIGH - LOW)))
    except Exception:
        temp_factor = 0.0
    raw = 0.6 * raw_activity + 0.4 * (raw_activity * (0.5 + 0.5 * temp_factor))
    if is_raining:
        raw *= 0.5
    return max(0.0, min(100.0, raw))

def usage_label_from_percent(p):
    if p is None:
        return ""
    p = int(round(p))
    if p <= 20:
        return f"Uso bajo ({p}%)"
    if p <= 50:
        return f"Uso moderado ({p}%)"
    if p <= 80:
        return f"Uso alto ({p}%)"
    return f"Uso muy alto ({p}%)"

# ===========================
# Loop principal
# ===========================
def loop(ir, state):
    global PREV_LAP_PCTS, CUMULATIVE_CAR_LAPS, EMA_USAGE, LAST_USAGE_SEND_TS, USAGE_SENT_PERCENT, USAGE_SENT_LABEL

    if not state.ir_connected:
        return

    try:
        ir.freeze_var_buffer_latest()

        # Seguridad
        try:
            if not ir['DriverInfo']:
                return
        except Exception:
            return

        process_stints(ir, state)

        # TIEMPO
        session_remain = safe_float(ir_get(ir, 'SessionTimeRemain', 0))
        display_timer = format_session_timer(session_remain)

        # TIPO SESIÓN y TRACK_NAME (robusto)
        session_type = "-"
        track_name = "-"
        try:
            sessinfo = ir_get(ir, 'SessionInfo', {}) or {}
            sess_num = safe_int(ir_get(ir, 'SessionNum', 0))
            sessions = None
            try:
                sessions = sessinfo.get('Sessions') if isinstance(sessinfo, dict) else None
            except Exception:
                sessions = None

            sess = {}
            try:
                if sessions:
                    if isinstance(sessions, dict):
                        sess = sessions.get(sess_num, {}) or {}
                    elif isinstance(sessions, list):
                        if 0 <= sess_num < len(sessions):
                            sess = sessions[sess_num] or {}
                        else:
                            sess = sessions[0] or {}
                    else:
                        sess = {}
            except Exception:
                sess = {}

            raw = str(sess.get('SessionType') or sess.get('SessionName') or "-")
        except Exception:
            raw = "-"

        try:
            r = (raw or "").lower()
            if r.startswith("qual"):
                session_type = "QUALY"
            elif r.startswith("prac"):
                session_type = "PRACTICE"
            elif r.startswith("race"):
                session_type = "RACE"
            elif r.startswith("warm"):
                session_type = "WARMUP"
            else:
                session_type = raw.upper() if raw else "-"
        except Exception:
            session_type = "-"

        try:
            wk = {}
            try:
                wk = (ir_get(ir, 'SessionInfo', {}) or {}).get('WeekendInfo', {}) or {}
            except Exception:
                wk = {}

            direct = {}
            try:
                direct = ir_get(ir, 'WeekendInfo') or {}
            except Exception:
                direct = {}

            base = (
                wk.get('TrackDisplayName') or
                wk.get('TrackName') or
                direct.get('TrackDisplayName') or
                direct.get('TrackName') or
                (sess.get('TrackDisplayName') if isinstance(sess, dict) else None) or
                (sess.get('TrackName') if isinstance(sess, dict) else None) or
                '-'
            )

            cfg = (
                wk.get('TrackConfigName') or
                wk.get('TrackConfig') or
                direct.get('TrackConfigName') or
                direct.get('TrackConfig') or
                (sess.get('TrackConfigName') if isinstance(sess, dict) else None) or
                (sess.get('TrackConfig') if isinstance(sess, dict) else None) or
                ''
            )

            base = str(base) if base is not None else '-'
            cfg = str(cfg) if cfg is not None else ''
            if cfg and cfg != "-" and cfg.lower() not in base.lower():
                track_name = f"{base} ({cfg})"
            else:
                track_name = base
        except Exception:
            track_name = "-"

        # MI COCHE
        my_idx = safe_int(ir_get(ir, 'DriverInfo', {}).get('DriverCarIdx', 0))
        update_my_fuel_model(ir, state, my_idx)
        fuel_now = 0.0
        try:
            fuel_now = safe_float(ir_get(ir, 'FuelLevel', 0))
        except Exception:
            fuel_now = 0.0

        avg_cons = 3.2
        avg_lap_time = 100.0
        try:
            est = safe_float(ir_get(ir, 'DriverInfo', {}).get('DriverCarEstLapTime', 0))
            if est > 0:
                avg_lap_time = est
        except Exception:
            pass

        display_strat = "OK"
        if session_remain < 36000:
            laps_remaining = session_remain / avg_lap_time
            fuel_needed_est = (laps_remaining * avg_cons) - fuel_now
            if fuel_needed_est > 0:
                display_strat = f"-{fuel_needed_est:.1f}"
        my_car = {
            "fuel": fuel_now,
            "strat": str(display_strat),
            "incidents": safe_int(ir_get(ir, 'PlayerCarTeamIncidentCount', 0)),
            "inc_limit": 0
        }

        # RIVALES / GRID
        drivers_data = []
        positions = ir_get(ir, 'CarIdxPosition', None)
        pcts = ir_get(ir, 'CarIdxLapDistPct', None)

        official_results = []
        try:
            official_results = ir_get(ir, 'SessionInfo', {}).get('Sessions', {}) if isinstance(ir_get(ir, 'SessionInfo', {}), dict) else []
            # If it's a dict, ensure we get a list from Sessions key
            if isinstance(official_results, dict):
                official_results = list(official_results.values())
        except Exception:
            official_results = []

        res_map = {}
        leader_laps = 0
        p1_best_time = 99999.0
        if official_results:
            for r in official_results:
                try:
                    c_idx = r.get('CarIdx')
                    best = safe_float(r.get('FastestTime', 0))
                    laps = safe_int(r.get('LapsComplete', 0))
                    res_map[c_idx] = {'best': best, 'last': safe_float(r.get('LastTime', 0)), 'laps': laps}
                    if r.get('Position') == 1:
                        leader_laps = laps
                    if best > 0 and best < p1_best_time:
                        p1_best_time = best
                except Exception:
                    continue

        total_laps_est = leader_laps + (session_remain / avg_lap_time)

        for d in ir_get(ir, 'DriverInfo', {}).get('Drivers', []):
            try:
                idx = d['CarIdx']
            except Exception:
                continue
            if idx < 0 or d.get('IsSpectator', 0):
                continue

            pos = 999
            if positions and idx < len(positions):
                try:
                    p = positions[idx]
                    if p > 0:
                        pos = p
                except Exception:
                    pass

            pct = 0.0
            if pcts and idx < len(pcts):
                try:
                    pct = safe_float(pcts[idx])
                except Exception:
                    pct = 0.0

            off = res_map.get(idx, {'best': 0.0, 'last': 0.0, 'laps': 0})

            raw_best = off['best']
            try:
                if raw_best <= 0 and ir['CarIdxBestLapTime']:
                    raw_best = safe_float(ir['CarIdxBestLapTime'][idx])
            except Exception:
                pass

            raw_last = off['last']
            try:
                if raw_last <= 0 and ir['CarIdxLastLapTime']:
                    raw_last = safe_float(ir['CarIdxLastLapTime'][idx])
            except Exception:
                pass

            display_gap = "--"
            sort_value = 0.0
            if session_type == "RACE":
                diff = leader_laps - off['laps']
                if pos == 1:
                    display_gap = "LDR"
                elif diff > 0:
                    display_gap = f"+{diff} L"
                    sort_value = diff * 1000.0
                else:
                    gap_val = (1.0 - pct) * 100.0
                    display_gap = f"+{gap_val:.1f}"
                    sort_value = gap_val
            else:
                if raw_best <= 0:
                    sort_value = 99999.0
                elif raw_best == p1_best_time:
                    display_gap = "-"
                    sort_value = raw_best
                else:
                    diff = raw_best - p1_best_time
                    display_gap = f"+{diff:.3f}"
                    sort_value = raw_best

            strat_txt = "-"
            strat_cls = "equal"

            if session_type == "RACE" and idx != my_idx and total_laps_est and total_laps_est > 0:
                # estrategia comparativa (similar a tu lógica)
                try:
                    my_lap = safe_int(ir_get(ir, 'CarIdxLapCompleted', [0])[my_idx])
                    my_laps_left = max(0, total_laps_est - my_lap)

                    my_full_stint = None
                    my_remaining_laps = None

                    my_fuel_per_lap = getattr(state, "my_fuel_per_lap", None)
                    my_tank_capacity = getattr(state, "my_tank_capacity", None)
                    my_fuel_level = safe_float(ir_get(ir, 'FuelLevel', 0))

                    if my_fuel_per_lap is not None and my_fuel_per_lap > 0.0001:
                        my_remaining_laps = my_fuel_level / my_fuel_per_lap
                        if my_tank_capacity is not None and my_tank_capacity > 0:
                            my_full_stint = my_tank_capacity / my_fuel_per_lap

                    if my_full_stint is None or my_full_stint < 1:
                        my_hist = state.stint_history.get(my_idx, [])
                        my_full_stint = (sum(my_hist) / len(my_hist)) if len(my_hist) > 0 else 30.0

                        my_start = state.current_stint_start.get(my_idx, my_lap)
                        my_curr_stint = my_lap - my_start
                        my_remaining_laps = max(0.0, my_full_stint - my_curr_stint)

                    my_need = my_laps_left - my_remaining_laps
                    my_stops = math.ceil(my_need / my_full_stint) if my_need > 0 else 0

                    riv_lap = safe_int(ir_get(ir, 'CarIdxLapCompleted', [0])[idx])
                    riv_laps_left = max(0, total_laps_est - riv_lap)

                    riv_hist = state.stint_history.get(idx, [])
                    riv_start = state.current_stint_start.get(idx, riv_lap)
                    riv_curr_stint = riv_lap - riv_start

                    if len(riv_hist) > 0:
                        riv_full = sum(riv_hist) / len(riv_hist)
                    elif riv_curr_stint > 5:
                        riv_full = float(riv_curr_stint)
                    else:
                        riv_full = my_full_stint

                    riv_remaining = max(0.0, riv_full - riv_curr_stint)
                    riv_need = riv_laps_left - riv_remaining
                    riv_stops = math.ceil(riv_need / riv_full) if riv_need > 0 else 0

                    diff_stops = riv_stops - my_stops
                    seconds_diff = diff_stops * AVG_PIT_LOSS

                    if diff_stops != 0:
                        if seconds_diff > 0:
                            strat_txt = f"+{seconds_diff:.0f}s"
                            strat_cls = "lead"
                        else:
                            strat_txt = f"{seconds_diff:.0f}s"
                            strat_cls = "lag"
                    else:
                        strat_txt = "EQUAL"
                except Exception:
                    strat_txt = "-"
                    strat_cls = "equal"

            curr_stint_lap = safe_int(ir_get(ir, 'CarIdxLapCompleted', [0])[idx]) - state.current_stint_start.get(idx, 0)
            hist = state.stint_history.get(idx, [])

            prev = hist[-1] if len(hist) >= 1 else "-"
            prevprev = hist[-2] if len(hist) >= 2 else "-"

            s1 = str(curr_stint_lap)
            s2 = str(prev) if prev != "-" else "-"
            s3 = str(prevprev) if prevprev != "-" else "-"

            car_logo = get_brand_logo(d.get('CarScreenName', ''))

            drivers_data.append({
                "pos": pos,
                "name": str(d['UserName']),
                "num": str(d['CarNumberRaw']),
                "is_me": (idx == my_idx),
                "c_name": "GT3",
                "car_logo": car_logo,
                "flag": "es",
                "last_lap": format_time(raw_last),
                "best_lap": format_time(raw_best),
                "gap": str(display_gap),
                "sort_val": float(sort_value),
                "s1": s1,
                "s2": s2,
                "s3": s3,
                "strat_txt": strat_txt,
                "strat_cls": strat_cls
            })

        # ordenar y calcular intervals
        if session_type == "RACE":
            drivers_data.sort(key=lambda x: x['pos'])
        else:
            drivers_data.sort(key=lambda x: x['sort_val'])

        for i in range(len(drivers_data)):
            if i == 0:
                drivers_data[i]["int"] = "-"
            else:
                curr = drivers_data[i]
                prev = drivers_data[i - 1]
                val = abs(curr['sort_val'] - prev['sort_val'])
                if session_type == "RACE":
                    drivers_data[i]["int"] = f"+{val:.1f}"
                else:
                    drivers_data[i]["int"] = f"+{val:.3f}" if val < 5000 else "--"

        # -----------------------------
        # Estimación y actualización del usage (tuning + debug)
        # -----------------------------
        try:
            # Parámetros de tuning (prueba segura)
            _K_ACTIVITY_TUNE = 2.5    # amplifica efecto del acumulado (histórico)
            _TAU_TUNE = 4.0           # EMA más reactiva
            _ALT_SCALE = 2.0          # escala para transformar laps_per_min_total a %
            _WEIGHT_HIST = 0.6
            _WEIGHT_RATE = 0.4

            # ¿está lloviendo?
            weather = {}
            try:
                weather = ir_get(ir, 'SessionInfo', {}).get('Sessions', {}) or {}
            except Exception:
                weather = {}
            is_raining = False
            try:
                # best effort to detect rain from available values
                w = ir_get(ir, 'SessionInfo', {}) or {}
                sess = {}
                try:
                    sessions = w.get('Sessions') if isinstance(w, dict) else None
                    if isinstance(sessions, list) and len(sessions) > 0:
                        sess = sessions[safe_int(ir_get(ir, 'SessionNum', 0)) if safe_int(ir_get(ir, 'SessionNum', 0)) < len(sessions) else 0] or {}
                except Exception:
                    sess = {}
                wobj = sess.get('Weather') or {}
                if isinstance(wobj, dict):
                    r = wobj.get('rain') or wobj.get('Rain') or wobj.get('RainPercent') or wobj.get('Precipitation')
                    if r:
                        try:
                            rn = float(r)
                            if rn > 0:
                                is_raining = True
                        except Exception:
                            pass
            except Exception:
                is_raining = False

            # delta: fracciones de vuelta entre ticks
            delta, PREV_LAP_PCTS = compute_active_lap_delta(ir, PREV_LAP_PCTS)
            CUMULATIVE_CAR_LAPS += delta

            # convertir delta a vueltas/min (total)
            try:
                laps_per_second_total = float(delta) / max(0.0001, float(DT_SLEEP))
            except Exception:
                laps_per_second_total = 0.0
            laps_per_min_total = laps_per_second_total * 60.0

            # raw histórico + temp (multiplicamos el acumulado por K_ACTIVITY para amplificar)
            raw_percent_hist = estimate_usage_from_activity_temp(CUMULATIVE_CAR_LAPS * _K_ACTIVITY_TUNE, safe_float(ir_get(ir, 'TrackTempCrew', ir_get(ir, 'TrackTemp', 0))), is_raining)

            # raw por ritmo instantáneo (rate)
            raw_percent_rate = max(0.0, min(100.0, (laps_per_min_total / _ALT_SCALE) * 100.0))

            # combinar señales
            combined_raw = (_WEIGHT_HIST * raw_percent_hist) + (_WEIGHT_RATE * raw_percent_rate)
            combined_raw = max(0.0, min(100.0, combined_raw))

            # EMA con TAU ajustado
            alpha = 1.0 - math.exp(-DT_SLEEP / _TAU_TUNE) if _TAU_TUNE > 0 else 0.12
            if EMA_USAGE is None:
                EMA_USAGE = combined_raw
            else:
                EMA_USAGE = alpha * combined_raw + (1.0 - alpha) * EMA_USAGE

            computed_usage_percent = int(round(max(0.0, min(100.0, EMA_USAGE))))
            computed_usage_label = usage_label_from_percent(computed_usage_percent)

            # cuándo publicar visible (igual que antes)
            now_ts = time.time()
            if (USAGE_SENT_PERCENT is None) or (now_ts - LAST_USAGE_SEND_TS >= USAGE_UPDATE_INTERVAL):
                USAGE_SENT_PERCENT = computed_usage_percent
                USAGE_SENT_LABEL = computed_usage_label
                LAST_USAGE_SEND_TS = now_ts

            # usage_debug para enviar y revisar
            usage_debug = {
                "delta": float(delta),
                "cumulative_car_laps": float(CUMULATIVE_CAR_LAPS),
                "laps_per_min_total": float(round(laps_per_min_total, 4)),
                "raw_percent_history_based": float(round(raw_percent_hist, 3)),
                "raw_percent_rate_based": float(round(raw_percent_rate, 3)),
                "combined_raw": float(round(combined_raw, 3)),
                "ema_usage": float(round(EMA_USAGE, 3)),
                "computed_usage_percent": int(computed_usage_percent),
                "tuning": {
                    "K_activity": _K_ACTIVITY_TUNE,
                    "TAU": _TAU_TUNE,
                    "alt_scale": _ALT_SCALE,
                    "weights": [_WEIGHT_HIST, _WEIGHT_RATE]
                }
            }

            # imprimir en consola cada 5s para ver evolución
            try:
                if int(now_ts) % 5 == 0:
                    print("USAGE DBG| delta={:.4f} cum={:.2f} lpm={:.4f} raw_hist={:.2f}% raw_rate={:.2f}% combined={:.2f}% ema={:.2f}% -> sent={}%"\
                          .format(delta, CUMULATIVE_CAR_LAPS, laps_per_min_total, raw_percent_hist, raw_percent_rate, combined_raw, EMA_USAGE, USAGE_SENT_PERCENT), end='\r')
            except Exception:
                pass

        except Exception:
            usage_debug = {"error":"usage_calc_failed"}

        # -----------------------------
        # Estimar fuel_needed para llegar a meta
        # -----------------------------
        fuel_needed = None
        try:
            my_curr_lap = None
            try:
                my_curr_lap = safe_int(ir_get(ir, 'CarIdxLapCompleted', [0])[my_idx])
            except Exception:
                my_curr_lap = None

            laps_to_finish = None
            try:
                if total_laps_est is not None and my_curr_lap is not None:
                    laps_to_finish = max(0.0, float(total_laps_est) - float(my_curr_lap))
            except Exception:
                laps_to_finish = None

            fuel_per_lap = None
            try:
                fuel_per_lap = getattr(state, "my_fuel_per_lap", None)
            except Exception:
                fuel_per_lap = None

            if (fuel_per_lap is None or fuel_per_lap <= 0) and my_curr_lap is not None:
                try:
                    hist = state.stint_history.get(my_idx, [])
                    if hist and len(hist) > 0:
                        fuel_per_lap = 3.0
                    else:
                        fuel_per_lap = 3.0
                except Exception:
                    fuel_per_lap = 3.0

            if laps_to_finish is not None and fuel_per_lap is not None and fuel_now is not None:
                try:
                    need = (laps_to_finish * float(fuel_per_lap)) - float(fuel_now)
                    if need < 0:
                        need = 0.0
                    fuel_needed = round(need, 1)
                except Exception:
                    fuel_needed = None
        except Exception:
            fuel_needed = None

        # Asegurarnos de que usage_debug existe para evitar NameError
        try:
            usage_debug
        except NameError:
            usage_debug = {}

        # Preparar payload final (incluye usage, usage_debug y fuel_needed)
        payload = {
            "connected": True,
            "timestamp": time.time(),
            "session_type": session_type,
            "track_name": track_name,
            "session_timer": display_timer,
            "weather": {
                "air": float("{:.2f}".format(safe_float(ir_get(ir, 'AirTemp', 0)))),
                "track": float("{:.2f}".format(safe_float(ir_get(ir, 'TrackTempCrew', ir_get(ir, 'TrackTemp', 0))))),
                "rain": 0,
                "status": "DRY"
            },
            "my_car": {
                "fuel": float("{:.1f}".format(fuel_now)),
                "strat": my_car.get("strat", "OK"),
                "incidents": my_car.get("incidents", 0),
                "inc_limit": my_car.get("inc_limit", 0),
                "fuel_needed": fuel_needed
            },
            "grid": drivers_data,
            "usage_percent": USAGE_SENT_PERCENT,
            "usage_label": USAGE_SENT_LABEL,
            "usage_debug": usage_debug,
            "fuel_needed": fuel_needed
        }

        # Envío al backend (fire-and-forget, timeout corto)
        try:
            requests.post(URL_DESTINO, json=payload, timeout=1)
            print("OK | T: {} | {} Cars | Track: {} | usage:{}%".format(display_timer, len(drivers_data), track_name, USAGE_SENT_PERCENT), end='\r')
        except Exception:
            pass

    except Exception as e:
        print("Error loop:", e)
        # traceback.print_exc()

# ===========================
# Main
# ===========================
if __name__ == '__main__':
    ir = irsdk.IRSDK()
    state = State()
    load_state(state)
    print("--- BRIDGE V28 (with usage estimator & fuel_needed) ---")
    try:
        while True:
            try:
                check_iracing(ir, state)
                loop(ir, state)
            except Exception as inner_e:
                print("Loop internal error:", inner_e)
            time.sleep(DT_SLEEP)
    except KeyboardInterrupt:
        print("\nFin.")
    except Exception as outer_e:
        print("Fatal error:", outer_e)
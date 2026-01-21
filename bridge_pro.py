import time
import requests
import irsdk
import math
import traceback
import pickle
import os

URL_DESTINO = "http://127.0.0.1:5000/api/telemetry/ingest" 
AVG_PIT_LOSS = 50.0 

class State:
    ir_connected = False
    stint_history = {} 
    current_stint_start = {} 
    my_last_fuel = None
    my_last_lap = None
    my_fuel_samples = []      # últimas N muestras de fuel/lap
    my_fuel_per_lap = None    # media fuel/lap
    my_tank_capacity = None   # estimada (L)

def check_iracing(ir, state):
    if state.ir_connected and not (ir.is_initialized and ir.is_connected):
        state.ir_connected = False
        print("\n❌ iRacing desconectado.")
    elif not state.ir_connected and ir.startup() and ir.is_initialized and ir.is_connected:
        state.ir_connected = True
        print(f"\n✅ CONECTADO A IRACING.")

# FUNCIONES AUXILIARES SEGURAS (Evitan crash por nulos)
def safe_float(val, default=0.0):
    try: return float(val)
    except: return default

def safe_int(val, default=0):
    try: return int(val)
    except: return default

def format_time(seconds):
    val = safe_float(seconds)
    if val <= 0: return "" 
    m = int(val // 60)
    s = int(val % 60)
    ms = int((val - int(val)) * 1000)
    if m > 0: return f"{m}:{s:02d}.{ms:03d}"
    else: return f"{s:02d}.{ms:03d}"

def format_session_timer(seconds):
    val = safe_float(seconds)
    if val < 0: return "00:00:00"
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
    except: pass
    return "iracing"

def update_my_fuel_model(ir, state, my_idx, max_samples=20):
    """
    Calcula consumo medio (L/vuelta) del coche de referencia (tu coche) usando FuelLevel y el avance de vueltas.
    También estima capacidad de depósito con FuelLevel / FuelLevelPct (si existe).
    """
    try:
        my_lap = safe_int(ir['CarIdxLapCompleted'][my_idx])
        fuel = safe_float(ir['FuelLevel'])  # iRacing suele exponer FuelLevel para TU coche
        fuel_pct = None
        try:
            fuel_pct = safe_float(ir['FuelLevelPct'])
        except Exception:
            fuel_pct = None

        # Estimar capacidad del depósito (si hay FuelLevelPct)
        if fuel_pct is not None and fuel_pct > 0.01:
            cap = fuel / fuel_pct
            if cap > 0:
                state.my_tank_capacity = cap

        # Calcular consumo por vuelta cuando avanzan las vueltas
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


# LÓGICA DE STINTS

# Ruta para guardar el estado de los stints y su historial
STATE_FILE = "stint_state.pkl"

def save_state(state):
    """
    Guarda en disco el momento de inicio del stint actual y el historial de stints para cada coche.
    Esto permite que, si se reinicia el bridge durante una carrera, se puedan restaurar las vueltas ya completadas.
    """
    try:
        with open(STATE_FILE, "wb") as f:
            pickle.dump({
                "current_stint_start": state.current_stint_start,
                "stint_history": state.stint_history
            }, f)
    except Exception as e:
        print(f"⚠️ Error al guardar estado: {e}")

def load_state(state):
    """
    Carga de disco la información del stint actual y el historial de stints para cada coche.
    Si no existe el fichero, no modifica el estado.
    """
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "rb") as f:
                data = pickle.load(f)
                state.current_stint_start = data.get("current_stint_start", {})
                state.stint_history   = data.get("stint_history", {})
    except Exception as e:
        print(f"⚠️ Error al cargar estado: {e}")

def process_stints(ir, state):
    try:
        on_pit = ir['CarIdxOnPitRoad']
        laps = ir['CarIdxLapCompleted']
        if not on_pit or not laps:
            return

        for i in range(len(on_pit)):
            if i not in state.current_stint_start:
                # Primera vez que vemos este coche: inicializamos el inicio del stint y el historial
                state.current_stint_start[i] = safe_int(laps[i])
                state.stint_history[i] = []
                # Guardamos el estado inicial para poder restaurarlo tras un reinicio
                save_state(state)

            is_in_pit_mem = state.current_stint_start.get(f"in_pit_{i}", False)
            curr_lap = safe_int(laps[i])

            # Entra (fin de un stint)
            if on_pit[i] and not is_in_pit_mem:
                stint_len = curr_lap - state.current_stint_start[i]
                if stint_len > 3:
                    state.stint_history[i].append(stint_len)
                    if len(state.stint_history[i]) > 5:
                        state.stint_history[i].pop(0)
                state.current_stint_start[f"in_pit_{i}"] = True
                # Guardamos el estado al entrar en boxes
                save_state(state)

            # Sale (inicio de un stint)
            if not on_pit[i] and is_in_pit_mem:
                state.current_stint_start[i] = curr_lap
                state.current_stint_start[f"in_pit_{i}"] = False
                # Guardamos el estado al salir de boxes
                save_state(state)
    except:
        pass

def calculate_stops_remaining(laps_done, total_laps, avg_stint):
    if avg_stint <= 0: return 0
    laps_to_go = total_laps - laps_done
    if laps_to_go <= 0: return 0
    return math.ceil(laps_to_go / avg_stint) - 1

def loop(ir, state):
    if state.ir_connected:
        try:
            ir.freeze_var_buffer_latest()
            
            # CONTROL DE SEGURIDAD
            if not ir['DriverInfo']: return 

            process_stints(ir, state)
            
            # 1. TIEMPO
            session_remain = safe_float(ir['SessionTimeRemain'])
            display_timer = format_session_timer(session_remain)

            # 2. TIPO SESIÓN
            is_race = False
            try:
                if "Race" in ir['SessionInfo']['Sessions'][ir['SessionNum']]['SessionType']: is_race = True
            except: pass

            # 3. MI COCHE
            my_idx = ir['DriverInfo']['DriverCarIdx']
            update_my_fuel_model(ir, state, my_idx)
            fuel_level = safe_float(ir['FuelLevel'])
            
            avg_cons = 3.2
            avg_lap_time = 100.0
            try:
                est = safe_float(ir['DriverInfo']['DriverCarEstLapTime'])
                if est > 0: avg_lap_time = est
            except: pass

            display_strat = "OK"
            if session_remain < 36000: # Menos de 10h (no práctica infinita)
                laps_remaining = session_remain / avg_lap_time
                fuel_needed = (laps_remaining * avg_cons) - fuel_level
                if fuel_needed > 0: display_strat = f"-{fuel_needed:.1f}"

            my_car = {
                "fuel": fuel_level,
                "strat": str(display_strat),
                "incidents": safe_int(ir['PlayerCarTeamIncidentCount']),
                "inc_limit": 0 # Simplificado
            }

            # 4. RIVALES
            drivers_data = []
            positions = ir['CarIdxPosition'] 
            pcts = ir['CarIdxLapDistPct']
            
            # Tiempos Oficiales
            official_results = []
            try: official_results = ir['SessionInfo']['Sessions'][ir['SessionNum']]['ResultsPositions']
            except: pass
            
            res_map = {}
            leader_laps = 0
            p1_best_time = 99999.0

            if official_results:
                for r in official_results:
                    c_idx = r['CarIdx']
                    best = safe_float(r.get('FastestTime', 0))
                    laps = safe_int(r.get('LapsComplete', 0))
                    res_map[c_idx] = { 'best': best, 'last': safe_float(r.get('LastTime', 0)), 'laps': laps }
                    
                    if r['Position'] == 1: leader_laps = laps
                    if best > 0 and best < p1_best_time: p1_best_time = best

            # Estimación vueltas totales
            total_laps_est = leader_laps + (session_remain / avg_lap_time)

            for d in ir['DriverInfo']['Drivers']:
                idx = d['CarIdx']
                if idx < 0 or d.get('IsSpectator', 0): continue
                
                # Datos brutos
                pos = 999
                if positions and idx < len(positions):
                    p = positions[idx]
                    if p > 0: pos = p

                pct = 0.0
                if pcts and idx < len(pcts): pct = safe_float(pcts[idx])

                off = res_map.get(idx, {'best':0.0, 'last':0.0, 'laps':0})
                
                # Fallback tiempos
                raw_best = off['best']
                if raw_best <= 0 and ir['CarIdxBestLapTime']: raw_best = safe_float(ir['CarIdxBestLapTime'][idx])
                
                raw_last = off['last']
                if raw_last <= 0 and ir['CarIdxLastLapTime']: raw_last = safe_float(ir['CarIdxLastLapTime'][idx])

                # GAP
                display_gap = "--"
                sort_value = 0.0

                if is_race:
                    diff = leader_laps - off['laps']
                    if pos == 1: display_gap = "LDR"
                    elif diff > 0: 
                        display_gap = f"+{diff} L"
                        sort_value = diff * 1000.0
                    else: 
                        gap_val = (1.0 - pct) * 100.0
                        display_gap = f"+{gap_val:.1f}"
                        sort_value = gap_val
                else:
                    if raw_best <= 0: sort_value = 99999.0
                    elif raw_best == p1_best_time: 
                        display_gap = "-"
                        sort_value = raw_best
                    else:
                        diff = raw_best - p1_best_time
                        display_gap = f"+{diff:.3f}"
                        sort_value = raw_best

                # ESTRATEGIA (PARADAS RESTANTES HASTA FINAL — referencia: tu coche)
                strat_txt = "-"
                strat_cls = "equal"

                if is_race and idx != my_idx and total_laps_est and total_laps_est > 0:
                    # --------------------------
                    # 1) Referencia: TU coche
                    # --------------------------
                    my_lap = safe_int(ir['CarIdxLapCompleted'][my_idx])
                    my_laps_left = max(0, total_laps_est - my_lap)

                    my_full_stint = None
                    my_remaining_laps = None

                    my_fuel_per_lap = getattr(state, "my_fuel_per_lap", None)
                    my_tank_capacity = getattr(state, "my_tank_capacity", None)
                    my_fuel_level = safe_float(ir['FuelLevel'])

                    if my_fuel_per_lap is not None and my_fuel_per_lap > 0.0001:
                        my_remaining_laps = my_fuel_level / my_fuel_per_lap
                        if my_tank_capacity is not None and my_tank_capacity > 0:
                            my_full_stint = my_tank_capacity / my_fuel_per_lap

                    # Fallback si todavía no hay consumo suficiente
                    if my_full_stint is None or my_full_stint < 1:
                        my_hist = state.stint_history.get(my_idx, [])
                        my_full_stint = (sum(my_hist) / len(my_hist)) if len(my_hist) > 0 else 30.0

                        my_start = state.current_stint_start.get(my_idx, my_lap)
                        my_curr_stint = my_lap - my_start
                        my_remaining_laps = max(0.0, my_full_stint - my_curr_stint)

                    my_need = my_laps_left - my_remaining_laps
                    my_stops = math.ceil(my_need / my_full_stint) if my_need > 0 else 0

                    # --------------------------
                    # 2) Rival: estimación por historial de stints
                    # --------------------------
                    riv_lap = safe_int(ir['CarIdxLapCompleted'][idx])
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

                    # --------------------------
                    # 3) Diferencia (rival - tú)
                    # --------------------------
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


                # STINTS FORMATO TEXTO
                # Calculamos las vueltas del stint actual (ST‑1) y recuperamos el historial de stints completados.
                curr_stint_lap = safe_int(ir['CarIdxLapCompleted'][idx]) - state.current_stint_start.get(idx, 0)
                hist = state.stint_history.get(idx, [])

                # Tomamos los dos stints anteriores del historial (el último y el penúltimo).
                prev = hist[-1] if len(hist) >= 1 else "-"
                prevprev = hist[-2] if len(hist) >= 2 else "-"

                # Asignamos ST‑1 al stint actual, ST‑2 al anterior y ST‑3 al penúltimo.
                s1 = str(curr_stint_lap)
                s2 = str(prev) if prev != "-" else "-"
                s3 = str(prevprev) if prevprev != "-" else "-"

                # Imagen de marca del vehículo.
                car_logo = get_brand_logo(d.get('CarScreenName', ''))

                # Añadimos la información del piloto a la tabla de datos.
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


            # ORDENAR
            if is_race: drivers_data.sort(key=lambda x: x['pos'])
            else: drivers_data.sort(key=lambda x: x['sort_val'])

            # INT
            for i in range(len(drivers_data)):
                if i == 0: drivers_data[i]["int"] = "-"
                else:
                    curr = drivers_data[i]
                    prev = drivers_data[i-1]
                    val = abs(curr['sort_val'] - prev['sort_val'])
                    if is_race: drivers_data[i]["int"] = f"+{val:.1f}"
                    else: drivers_data[i]["int"] = f"+{val:.3f}" if val < 5000 else "--"

            # CLIMA
            air_temp = safe_float(ir['AirTemp'])
            track_temp = safe_float(ir['TrackTempCrew'])
            # Truco para detectar lluvia si iRacing no da el dato directo
            # Si la pista está muy fría o húmeda (simulado con AirDensity aquí como placeholder)
            rain_val = "0" 

# --- SESIÓN Y CIRCUITO ---
session_type = "-"
track_name = "-"

try:
    sess_num = safe_int(ir['SessionNum'])
    sessions = ir['SessionInfo'].get('Sessions', [])
    if sessions and 0 <= sess_num < len(sessions):
        session_type = sessions[sess_num].get('SessionType') or sessions[sess_num].get('SessionName') or "-"
except:
    pass

# Normalizamos a lo que quieres ver (Race / Practice / Qualy)
st = (session_type or "").lower()
if st.startswith("qual"):
    session_type = "QUALY"
elif st.startswith("prac"):
    session_type = "PRACTICE"
elif st.startswith("race"):
    session_type = "RACE"
elif st.startswith("warm"):
    session_type = "WARMUP"
else:
    session_type = session_type.upper() if session_type else "-"

try:
    wi = ir['SessionInfo'].get('WeekendInfo', {})
    track_name = wi.get('TrackDisplayName') or wi.get('TrackName') or "-"
except:
    pass

            
            # --- SESIÓN Y CIRCUITO ---
session_type = "-"
track_name = "-"

try:
    sess_num = safe_int(ir['SessionNum'])
    sessions = ir['SessionInfo'].get('Sessions', [])
    if sessions and 0 <= sess_num < len(sessions):
        session_type = sessions[sess_num].get('SessionType') or sessions[sess_num].get('SessionName') or "-"
except:
    pass

# Normalizamos a lo que quieres ver (Race / Practice / Qualy)
st = (session_type or "").lower()
if st.startswith("qual"):
    session_type = "QUALY"
elif st.startswith("prac"):
    session_type = "PRACTICE"
elif st.startswith("race"):
    session_type = "RACE"
elif st.startswith("warm"):
    session_type = "WARMUP"
else:
    session_type = session_type.upper() if session_type else "-"

try:
    wi = ir['SessionInfo'].get('WeekendInfo', {})
    track_name = wi.get('TrackDisplayName') or wi.get('TrackName') or "-"
except:
    pass

            
            # ENVÍO
            try:
                requests.post(URL_DESTINO, json=payload, timeout=1)
                print(f"📡 OK | T: {display_timer} | {len(drivers_data)} Cars", end='\r')
            except: pass

        except Exception as e:
            # Si falla algo, que no se pare el script
            print(f"⚠️ Error loop: {e}")
            pass

if __name__ == '__main__':
    ir = irsdk.IRSDK()
    state = State()
    # Restaura el historial de stints y el inicio del stint actual si existe
    load_state(state)
    print("--- BRIDGE V28 ---")
    try:
        while True:
            check_iracing(ir, state)
            loop(ir, state)
            time.sleep(0.5)
    except:
        print("\n🛑 Fin.")

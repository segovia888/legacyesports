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
                state.current_stint_start[i] = safe_int(laps[i])
                state.stint_history[i] = []

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

                # ESTRATEGIA (MATEMÁTICA PURA)
                strat_txt = "-"
                strat_cls = "equal"
                
                if is_race and idx != my_idx:
                    # Mis datos
                    my_hist = state.stint_history.get(my_idx, [])
                    my_avg = sum(my_hist)/len(my_hist) if len(my_hist) > 0 else 30.0
                    
                    # Sus datos
                    riv_hist = state.stint_history.get(idx, [])
                    riv_start = state.current_stint_start.get(idx, 0)
                    riv_curr_lap = safe_int(ir['CarIdxLapCompleted'][idx])
                    riv_curr_stint = riv_curr_lap - riv_start
                    
                    riv_avg = 30.0
                    if len(riv_hist) > 0: riv_avg = sum(riv_hist)/len(riv_hist)
                    elif riv_curr_stint > 5: riv_avg = float(riv_curr_stint)
                    else: riv_avg = my_avg # Asumimos igual si no hay datos

                    # Calculo paradas
                    my_stops = calculate_stops_remaining(safe_int(ir['CarIdxLapCompleted'][my_idx]), total_laps_est, my_avg)
                    riv_stops = calculate_stops_remaining(riv_curr_lap, total_laps_est, riv_avg)
                    
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
            
            payload = {
                "session_timer": display_timer,
                "my_car": my_car,
                "weather": {"air": f"{air_temp:.1f}", "track": f"{track_temp:.1f}", "rain": rain_val, "status": "DRY"},
                "grid": drivers_data,
                "connected": True
            }
            
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

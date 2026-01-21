import os
import requests
import re
import json
import sqlite3
import time
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# ==========================================
# CONFIGURACIÓN DEL SERVIDOR
# ==========================================

# 1. URL PÚBLICA (Para enlaces en Discord)
WEB_PUBLIC_URL = "https://legacyesportsclub.etern8.app"

# 2. Configuración de Base de Datos y Carpetas
basedir = os.path.abspath(os.path.dirname(__file__))
db_name = 'legacy_strategy.db'

# Preferencia: Carpeta instance > Carpeta raíz
instance_path = os.path.join(basedir, 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)
    
db_path = os.path.join(instance_path, db_name)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'LEGACY_2026_KEY' 
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 

UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 3. Webhook Discord
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1461067385736794285/ocjLAfJE2en90MjwsftvESrPp5OduaySwxhaFhY8yBevQbD_i3R1Ktwwl5__pPpixezL"

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# ==========================================
# MODELOS DE BASE DE DATOS
# ==========================================

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    members = db.relationship('User', backref='team', lazy=True)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='privateer')
    is_approved = db.Column(db.Boolean, default=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)
    requested_team = db.Column(db.String(100), nullable=True)
    driver_profile = db.relationship('Driver', backref='user', uselist=False)
    def set_password(self, p): self.password_hash = generate_password_hash(p)
    def check_password(self, p): return check_password_hash(self.password_hash, p)

class Driver(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    discord = db.Column(db.String(100))
    iracing_id = db.Column(db.String(50))
    simulators = db.Column(db.String(200))
    hardware = db.Column(db.String(200))
    number = db.Column(db.String(10))
    photo = db.Column(db.String(120), default='default_driver.png')
    
    # Perfil PRO
    biography = db.Column(db.Text, nullable=True)
    social_twitter = db.Column(db.String(100), nullable=True)
    social_instagram = db.Column(db.String(100), nullable=True)
    social_twitch = db.Column(db.String(100), nullable=True)
    country = db.Column(db.String(50), default="España")
    
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Relaciones
    palmares = db.relationship('Palmares', backref='driver', lazy=True, cascade="all, delete-orphan")
    achievements = db.relationship('Achievement', backref='driver', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        email_val = ""
        if self.user_id:
            usuario = User.query.get(self.user_id)
            if usuario: email_val = usuario.email
        return {'id': self.id, 'name': self.name, 'discord': self.discord, 'iracing_id': self.iracing_id, 'simulators': self.simulators, 'hardware': self.hardware, 'number': self.number, 'photo': self.photo, 'email': email_val}

class Palmares(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title_name = db.Column(db.String(150), nullable=False)
    year = db.Column(db.String(10), nullable=True)
    image = db.Column(db.String(150), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=False)

class Achievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.String(10), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=False)

event_drivers = db.Table('event_drivers', db.Column('event_id', db.Integer, db.ForeignKey('event.id'), primary_key=True), db.Column('driver_id', db.Integer, db.ForeignKey('driver.id'), primary_key=True))

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    track = db.Column(db.String(100), nullable=False)
    date_str = db.Column(db.String(50), nullable=False)
    time_str = db.Column(db.String(50))
    car_class = db.Column(db.String(100))
    week = db.Column(db.Integer)
    alert_sent = db.Column(db.Boolean, default=False)
    broadcast = db.Column(db.String(500), nullable=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)
    driver_id = db.Column(db.Integer, nullable=True)
    drivers = db.relationship('Driver', secondary=event_drivers, backref=db.backref('events_participated', lazy=True))

class Strategy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    car_class = db.Column(db.String(50), nullable=False)
    car_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    payload = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)
    is_shared = db.Column(db.Boolean, default=False)

class Car(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(120), nullable=False)

# ==========================================
# UTILIDADES Y MIGRACIÓN AUTOMÁTICA
# ==========================================

def allowed_file(filename): return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def check_and_migrate():
    with app.app_context():
        db.create_all()
        
        legacy = Team.query.filter_by(name="Legacy eSports").first()
        if not legacy: db.session.add(Team(name="Legacy eSports")); db.session.commit()
        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", email="admin@legacy.es", role="admin", is_approved=True, team_id=legacy.id)
            admin.set_password("LEGACY2026"); db.session.add(admin); db.session.commit()

        # Migración Inteligente
        try:
            real_db_path = db_path
            if not os.path.exists(real_db_path) and os.path.exists(os.path.join(basedir, db_name)):
                real_db_path = os.path.join(basedir, db_name)
            
            conn = sqlite3.connect(real_db_path)
            c = conn.cursor()
            
            cols_to_check = [
                ("driver", "biography TEXT"),
                ("driver", "social_twitter TEXT"),
                ("driver", "social_instagram TEXT"),
                ("driver", "social_twitch TEXT"),
                ("driver", "country TEXT DEFAULT 'España'"),
                ("driver", "user_id INTEGER"),
                ("strategy", "user_id INTEGER"),
                ("strategy", "team_id INTEGER"),
                ("strategy", "is_shared BOOLEAN DEFAULT 0"),
                ("event", "team_id INTEGER"),
                ("event", "alert_sent BOOLEAN DEFAULT 0"),
                ("event", "broadcast TEXT")
            ]
            
            for table, col_def in cols_to_check:
                try:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                    print(f"🔧 [Auto-Fix] Columna añadida: {table}.{col_def}")
                except sqlite3.OperationalError: pass 

            conn.commit(); conn.close()
        except Exception as e: print(f"⚠️ Nota DB: {e}")

check_and_migrate()

MONTH_MAP = {'ene': 1, 'enero': 1, 'jan': 1, 'feb': 2, 'febrero': 2, 'mar': 3, 'marzo': 3, 'abr': 4, 'abril': 4, 'apr': 4, 'may': 5, 'mayo': 5, 'jun': 6, 'junio': 6, 'jul': 7, 'julio': 7, 'ago': 8, 'agosto': 8, 'aug': 8, 'sep': 9, 'sept': 9, 'septiembre': 9, 'oct': 10, 'octubre': 10, 'nov': 11, 'noviembre': 11, 'dic': 12, 'diciembre': 12, 'dec': 12}
def parse_smart_date(date_text):
    try:
        first = date_text.split(',')[0].strip()
        day = int(re.search(r'(\d+)', first).group(1))
        txt = re.search(r'([a-zA-Z]+)', first).group(1).lower()[:3]
        month = next((v for k, v in MONTH_MAP.items() if k in txt), 0)
        if month > 0: return date(max(datetime.now().year, 2026), month, day)
    except: return None

def check_events_status():
    today = date.today()
    for ev in Event.query.all():
        rd = parse_smart_date(ev.date_str)
        if rd and rd < today: db.session.delete(ev)
        elif rd == today and ev.type == "Private" and not ev.alert_sent:
            send_race_day_alert(ev); ev.alert_sent = True
    db.session.commit()

# --- NOTIFICACIONES CON ENLACE (DEEP LINKING) ---
def send_race_day_alert(ev):
    if "PEGAR_AQUI" in DISCORD_WEBHOOK_URL: return
    d_list = "\n".join([f"🏎️ **{d.name}** #{d.number}" for d in ev.drivers]) if ev.drivers else "TBD"
    embed = {"title": f"🎥 ¡HOY CORRE EL EQUIPO! | {ev.name}", "description": f"Cita en **{ev.track}**.", "color": 0xCCFF00, "fields": [{"name": "📍 Info", "value": f"{ev.track}\n{ev.car_class}", "inline": True}, {"name": "⏰ Hora", "value": ev.time_str, "inline": True}, {"name": "👥 Pilotos", "value": d_list}, {"name": "📺 TV", "value": ev.broadcast or "No TV"}]}
    try: requests.post(DISCORD_WEBHOOK_URL, json={"content": "@everyone", "embeds": [embed]})
    except: pass

def send_discord_alert(title, description, color=0xCCFF00, fields=[], url=None):
    if "PEGAR_AQUI" in DISCORD_WEBHOOK_URL: return
    
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "fields": fields,
        "footer": {"text": "Legacy Strategy Center • Click para acceder"}
    }
    
    if url: embed["url"] = url # Hace el título clicable con la ID

    try: requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
    except: pass

# ==========================================
# RUTAS DE LA APLICACIÓN
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.check_password(request.form.get('password')):
            if not user.is_approved: flash('Pendiente.'); return render_template('login.html')
            login_user(user); return redirect(url_for('index'))
        else: flash('Error.')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if User.query.filter((User.username==request.form.get('username')) | (User.email==request.form.get('email'))).first(): flash('Ya existe.'); return redirect(url_for('register'))
        req_team = request.form.get('team_name_input'); role_type = request.form.get('reg_type')
        new_user = User(username=request.form.get('username'), email=request.form.get('email'), role='privateer', is_approved=False, requested_team=req_team if role_type == 'team' else None)
        new_user.set_password(request.form.get('password')); db.session.add(new_user); db.session.commit()
        
        send_discord_alert("🔔 Nuevo Registro", f"Usuario: **{new_user.username}** solicita acceso.", 0xFFA500, url=f"{WEB_PUBLIC_URL}/admin")
        
        flash('Enviado.'); return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('index'))

@app.route("/live-timing")
@login_required
def live_timing():
    return render_template("live_timing.html")

@app.route("/")
def index(): check_events_status(); return render_template("index.html", user=current_user)

# --- DRIVERS ---

@app.route("/drivers", methods=["GET", "POST"])
@login_required
def drivers():
    if current_user.role != 'admin' and current_user.role != 'member': return redirect(url_for('index'))
    if request.method == "POST" and current_user.role == 'admin':
        name = request.form.get("name")
        f = request.files.get('photo'); photo_filename = 'default_driver.png'
        if f and allowed_file(f.filename):
            fn = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{f.filename}")
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], fn)); photo_filename = fn
        d = Driver(name=name, discord=request.form.get("discord"), iracing_id=request.form.get("iracing_id"), simulators=request.form.get("simulators"), hardware=request.form.get("hardware"), number=request.form.get("number"), photo=photo_filename, country="España")
        db.session.add(d); db.session.commit()
    return render_template("drivers.html", drivers=Driver.query.all())

@app.route("/drivers/update/<int:id>", methods=["POST"])
@login_required
def update_driver(id):
    d = Driver.query.get_or_404(id)
    if d.user_id != current_user.id and current_user.role != 'admin':
        flash("⛔ Sin permisos."); return redirect(url_for('drivers'))

    d.name = request.form.get("name", d.name)
    d.country = request.form.get("country", d.country)
    d.number = request.form.get("number", d.number)
    d.iracing_id = request.form.get("iracing_id", d.iracing_id)
    d.discord = request.form.get("discord", d.discord)
    d.simulators = request.form.get("simulators", d.simulators)
    d.hardware = request.form.get("hardware", d.hardware)
    d.biography = request.form.get("biography", d.biography)
    d.social_twitter = request.form.get("social_twitter", d.social_twitter)
    d.social_instagram = request.form.get("social_instagram", d.social_instagram)
    d.social_twitch = request.form.get("social_twitch", d.social_twitch)

    f = request.files.get('photo')
    if f and allowed_file(f.filename):
        fn = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{f.filename}")
        f.save(os.path.join(app.config['UPLOAD_FOLDER'], fn)); d.photo = fn

    if d.user_id:
        u = User.query.get(d.user_id)
        if u:
            if "account_email" in request.form: u.email = request.form.get("account_email")
            if "account_password" in request.form and len(request.form.get("account_password")) > 0:
                u.set_password(request.form.get("account_password")); flash("🔐 Contraseña cambiada.")

    try: db.session.commit(); flash("✅ Perfil actualizado.")
    except Exception as e: db.session.rollback(); flash(f"⚠️ Error: {str(e)}")
    return redirect(url_for('drivers'))

@app.route("/drivers/palmares/add/<int:id>", methods=["POST"])
@login_required
def add_palmares(id):
    d = Driver.query.get_or_404(id)
    if d.user_id != current_user.id and current_user.role != 'admin': return jsonify({"error":"No auth"}), 403
    title = request.form.get("title"); year = request.form.get("year"); f = request.files.get("diploma")
    if f and title and allowed_file(f.filename):
        fn = secure_filename(f"palm_{d.id}_{datetime.now().strftime('%H%M%S')}_{f.filename}"); f.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
        db.session.add(Palmares(title_name=title, year=year, image=fn, driver_id=d.id)); db.session.commit(); flash("🏆 Diploma añadido.")
    return redirect(url_for('drivers'))

@app.route("/drivers/palmares/delete/<int:pid>", methods=["POST"])
@login_required
def delete_palmares(pid):
    p = Palmares.query.get_or_404(pid)
    if p.driver.user_id == current_user.id or current_user.role == 'admin': db.session.delete(p); db.session.commit()
    return redirect(url_for('drivers'))

@app.route("/drivers/achievement/add/<int:id>", methods=["POST"])
@login_required
def add_achievement(id):
    d = Driver.query.get_or_404(id)
    if d.user_id != current_user.id and current_user.role != 'admin': return jsonify({"error":"No auth"}), 403
    title = request.form.get("title"); year = request.form.get("year")
    if title:
        db.session.add(Achievement(title=title, year=year, driver_id=d.id)); db.session.commit(); flash("✅ Logro añadido.")
    return redirect(url_for('drivers'))

@app.route("/drivers/achievement/delete/<int:aid>", methods=["POST"])
@login_required
def delete_achievement(aid):
    a = Achievement.query.get_or_404(aid)
    if a.driver.user_id == current_user.id or current_user.role == 'admin': db.session.delete(a); db.session.commit()
    return redirect(url_for('drivers'))

@app.route("/drivers/delete/<int:id>", methods=["POST"])
@login_required
def delete_driver(id):
    if current_user.role == 'admin': d = Driver.query.get_or_404(id); db.session.delete(d); db.session.commit()
    return redirect(url_for('drivers'))

# --- CALENDARIO & ESTRATEGIA (CON DEEP LINKING) ---

@app.route("/calendar", methods=["GET", "POST"])
def calendar():
    if not current_user.is_authenticated: return redirect(url_for('login'))
    check_events_status()
    if request.method == "POST":
        if current_user.role == 'privateer': return redirect(url_for('calendar'))
        e_type = request.form.get("type"); name = request.form.get("name")
        track = request.form.get("track"); date_str = request.form.get("date_str")
        time_str = request.form.get("time_str"); car_class = request.form.get("car_class")
        week_val = request.form.get("week"); broadcast = request.form.get("broadcast")
        driver_ids = request.form.getlist("driver_ids")
        new_event = Event(type=e_type, name=name, track=track, date_str=date_str, time_str=time_str, car_class=car_class, week=int(week_val or 0), broadcast=broadcast, team_id=current_user.team_id)
        for d_id in driver_ids:
            if d := Driver.query.get(int(d_id)): new_event.drivers.append(d)
        db.session.add(new_event); db.session.commit()
        today = date.today(); parsed_date = parse_smart_date(date_str)
        if e_type == "Private" and parsed_date == today:
            send_race_day_alert(new_event); new_event.alert_sent = True; db.session.commit()
        return redirect(url_for('calendar'))
    query = Event.query
    if current_user.team_id: query = query.filter((Event.team_id == current_user.team_id) | (Event.team_id == None))
    def event_sorter(e): d = parse_smart_date(e.date_str); return d if d else date(3000, 1, 1)
    events = query.all()
    specials = sorted([e for e in events if e.type == "Special"], key=event_sorter)
    enduros = sorted([e for e in events if e.type == "Endurance"], key=event_sorter)
    dailies = sorted([e for e in events if e.type == "Series"], key=event_sorter)
    privates = sorted([e for e in events if e.type == "Private"], key=event_sorter)
    return render_template("calendar.html", specials=specials, enduros=enduros, dailies=dailies, privates=privates, drivers=Driver.query.all())

@app.route("/calendar/delete/<int:id>", methods=["POST"])
@login_required
def delete_event(id):
    ev = Event.query.get_or_404(id)
    if current_user.role == 'admin' or ev.team_id == current_user.team_id: db.session.delete(ev); db.session.commit()
    return redirect(url_for('calendar'))

@app.route("/estrategia")
@login_required
def estrategia():
    cars_by_cat = {}
    for c in Car.query.order_by(Car.category, Car.name).all():
        if c.category not in cars_by_cat: cars_by_cat[c.category] = []
        cars_by_cat[c.category].append(c.name)
    query = Strategy.query.filter((Strategy.user_id == current_user.id) | ((Strategy.team_id == current_user.team_id) & (Strategy.is_shared == True)))
    return render_template("race_strategy.html", car_categories=cars_by_cat, strategies=query.order_by(Strategy.created_at.desc()).all(), drivers_db=Driver.query.all())

@app.route("/estrategia/guardar", methods=["POST"])
@login_required
def estrategia_guardar():
    data = request.get_json(force=True)
    s = Strategy(name=data.get("name", "Sin"), car_class=data.get("car_class", "GT3"), car_name=data.get("car_name", "Desc"), payload=json.dumps(data.get("payload", {})), user_id=current_user.id, team_id=current_user.team_id, is_shared=True)
    db.session.add(s); db.session.commit()
    try:
        fields = [{"name": "📂 Nombre", "value": s.name, "inline": True}, {"name": "🏎️ Coche", "value": f"{s.car_name} ({s.car_class})", "inline": True}, {"name": "👨‍🔧 Autor", "value": current_user.username, "inline": False}]
        
        # LINK DIRECTO: Al guardar, envía el link con ?id=
        send_discord_alert("🆕 Nueva Estrategia Creada", "El equipo de ingeniería ha publicado un nuevo plan de carrera.", color=0x00FF00, fields=fields, url=f"{WEB_PUBLIC_URL}/estrategia?id={s.id}")
        
    except: pass
    return jsonify({"ok": True, "id": s.id})

@app.route("/estrategia/actualizar/<int:sid>", methods=["POST"])
@login_required
def estrategia_actualizar(sid):
    strategy = Strategy.query.get_or_404(sid)
    if strategy.user_id != current_user.id and current_user.role != 'admin': return jsonify({"ok": False})
    data = request.get_json(force=True)
    strategy.name = data.get("name", strategy.name); strategy.car_class = data.get("car_class", strategy.car_class); strategy.car_name = data.get("car_name", strategy.car_name); strategy.payload = json.dumps(data.get("payload", {})); strategy.created_at = datetime.utcnow()
    db.session.commit()
    try:
        fields = [{"name": "📂 Nombre", "value": strategy.name, "inline": True}, {"name": "🏎️ Coche", "value": f"{strategy.car_name} ({strategy.car_class})", "inline": True}, {"name": "👨‍🔧 Editor", "value": current_user.username, "inline": False}]
        
        # LINK DIRECTO: Al actualizar, también envía el link
        send_discord_alert("📝 Estrategia Actualizada", "Se han guardado cambios en la estrategia.", color=0xFFA500, fields=fields, url=f"{WEB_PUBLIC_URL}/estrategia?id={strategy.id}")
        
    except: pass
    return jsonify({"ok": True})

@app.route("/estrategia/cargar/<int:sid>")
@login_required
def estrategia_cargar(sid):
    s = Strategy.query.get_or_404(sid)
    if s.team_id != current_user.team_id and s.user_id != current_user.id: return jsonify({"error": "No auth"}), 403
    return jsonify({"id": s.id, "name": s.name, "car_class": s.car_class, "car_name": s.car_name, "payload": json.loads(s.payload)})

@app.route("/estrategia/borrar/<int:sid>", methods=["POST"])
@login_required
def estrategia_borrar(sid):
    s = Strategy.query.get_or_404(sid)
    if s.user_id == current_user.id or current_user.role == 'admin': db.session.delete(s); db.session.commit()
    return jsonify({"ok": True})

@app.route("/fuel")
def fuel(): return render_template("fuel_calc.html", cars=Car.query.order_by(Car.category, Car.name).all())
@app.route("/setup-doctor")
def setup_doctor(): return render_template("setup_doctor.html")
@app.route("/garage", methods=["GET", "POST"])
def garage():
    if request.method == "POST":
        if not current_user.is_authenticated or current_user.role != 'admin': return redirect(url_for('login'))
        if cat := request.form.get("category"): db.session.add(Car(category=cat, name=request.form.get("name"))); db.session.commit()
    return render_template("cars.html", cars=Car.query.order_by(Car.category, Car.name).all())
@app.route("/garage/delete/<int:id>", methods=["POST"])
@login_required
def delete_car(id):
    if current_user.role == 'admin': c = Car.query.get_or_404(id); db.session.delete(c); db.session.commit()
    return redirect(url_for('garage'))

@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin_panel():
    if current_user.role != 'admin': flash("⛔ Zona restringida."); return redirect(url_for('index'))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create_team":
            name = request.form.get("team_name"); 
            if name: db.session.add(Team(name=name)); db.session.commit(); flash(f"✅ Equipo '{name}' creado.")
        elif action == "rename_team":
            team = Team.query.get(request.form.get("team_id"))
            if team and request.form.get("new_name"): team.name = request.form.get("new_name"); db.session.commit(); flash("✅ Equipo renombrado.")
        elif action == "delete_team":
            team = Team.query.get(request.form.get("team_id"))
            if team:
                for member in team.members: member.team_id = None
                db.session.delete(team); db.session.commit(); flash(f"🗑️ Equipo eliminado.")
        elif request.form.get("user_id"):
            user = User.query.get(request.form.get("user_id"))
            if user:
                if action == "approve": user.is_approved = True; flash(f"✅ {user.username} aprobado.")
                elif action == "delete_user": db.session.delete(user); flash(f"🗑️ Usuario eliminado.")
                elif action == "promote": user.role = 'admin'; user.is_approved = True; flash(f"👑 {user.username} ahora es Admin.")
                elif action == "update_team": tid = request.form.get("new_team_id"); user.team_id = int(tid) if tid and tid != 'none' else None; db.session.commit(); flash(f"🛡️ Equipo actualizado.")
                elif action == "edit_user_data":
                    new_user = request.form.get("username"); new_email = request.form.get("email")
                    duplicado = User.query.filter((User.username == new_user) | (User.email == new_email)).filter(User.id != user.id).first()
                    if duplicado: flash("⚠️ Nombre o email ya en uso.")
                    else: user.username = new_user; user.email = new_email; db.session.commit(); flash("✅ Datos actualizados.")
            db.session.commit()
        return redirect(url_for('admin_panel'))
    return render_template("admin_panel.html", users=User.query.all(), teams=Team.query.all())

# --- RUTAS DE ARCHIVOS ESTÁTICOS ---
@app.route('/manifest.json')
def manifest(): return send_from_directory('static', 'manifest.json')
@app.route('/sw.js')
def service_worker(): return send_from_directory('static', 'sw.js')

# --- TELEMETRÍA ---
telemetry_data = {"connected": False, "laps": 0, "fuel": 0, "driver": "", "flag": "green", "timestamp": 0}
@app.route('/api/telemetry/ingest', methods=['POST'])
def ingest_telemetry():
    global telemetry_data
    try: data = request.json; telemetry_data.update(data); telemetry_data["connected"] = True; return jsonify({"status": "ok"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/api/telemetry/live', methods=['GET'])
def get_live_telemetry():
    if time.time() - telemetry_data.get("timestamp", 0) > 5: telemetry_data["connected"] = False
    return jsonify(telemetry_data)

# --- ZONA DE DESCARGAS (BRIDGE AUTOMÁTICO) ---
@app.route('/client/download/bridge')
def download_bridge_script():
    # Código del Bridge v8.6 (El que te pasé antes)
    # Lo guardamos aquí para inyectarle la URL del servidor automáticamente.
    bridge_code = f'''import time
import requests
import irsdk
import math

# --- CONFIGURACIÓN AUTOMÁTICA ---
# Esta URL se ha inyectado desde el servidor al descargar
SERVER_URL = "{WEB_PUBLIC_URL}/api/telemetry/ingest"

class State:
    ir_connected = False

def check_iracing(ir, state):
    if state.ir_connected and not (ir.is_initialized and ir.is_connected):
        state.ir_connected = False
        print("❌ iRacing desconectado")
    elif not state.ir_connected and ir.startup() and ir.is_initialized and ir.is_connected:
        state.ir_connected = True
        print("✅ iRacing CONECTADO - Enviando datos a Legacy HQ...")

def loop(ir, state):
    ir.freeze_var_buffer_latest()
    if state.ir_connected:
        try:
            # 1. Datos Básicos
            fuel = ir['FuelLevel'] or 0
            laps = ir['LapCompleted'] or 0
            inc = ir['PlayerCarTeamIncidentCount'] or 0
            on_pit = ir['OnPitRoad']
            
            # 2. Driver Info
            d_id = ir['PlayerCarDriverRaw']
            d_name = "Unknown"
            try:
                if ir['SessionInfo']:
                    drivers = ir['SessionInfo']['DriverInfo']['Drivers']
                    info = next((d for d in drivers if d['UserID'] == d_id), None)
                    if info: d_name = info['UserName']
            except: pass

            # 3. Neumáticos (Estimación por sectores)
            tires = {{
                "fl": int((ir['LFwearL'] + ir['LFwearM'] + ir['LFwearR']) / 3 * 100),
                "fr": int((ir['RFwearL'] + ir['RFwearM'] + ir['RFwearR']) / 3 * 100),
                "rl": int((ir['LRwearL'] + ir['LRwearM'] + ir['LRwearR']) / 3 * 100),
                "rr": int((ir['RRwearL'] + ir['RRwearM'] + ir['RRwearR']) / 3 * 100)
            }}
            tires['avg'] = int((tires['fl'] + tires['fr'] + tires['rl'] + tires['rr']) / 4)

            # 4. Enviar
            payload = {{
                "fuel": fuel, "laps": laps, "incidents": inc, "on_pit_road": on_pit,
                "driver_id": d_id, "driver_name": d_name, "tires": tires, "timestamp": time.time()
            }}
            requests.post(SERVER_URL, json=payload, timeout=1)
            # print(f"📡 Enviado: {{d_name}} ({{fuel:.1f}}L)")
        except Exception: pass

# ==========================================
# BLOQUE LIVE TIMING & PWA (INSERTAR AQUÍ)
# ==========================================

# Variables globales para la telemetría
telemetry_data = {"connected": False, "timestamp": 0}

@app.route('/api/telemetry/ingest', methods=['POST'])
def ingest_telemetry():
    global telemetry_data
    try:
        data = request.json
        telemetry_data.update(data)
        telemetry_data["connected"] = True
        telemetry_data["timestamp"] = time.time()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/telemetry/live', methods=['GET'])
def get_live_telemetry():
    global telemetry_data
    # Si hace más de 5 segundos que no recibimos datos, marcamos como desconectado
    if time.time() - telemetry_data.get("timestamp", 0) > 5:
        telemetry_data["connected"] = False
    return jsonify(telemetry_data)

@app.route("/live-timing")
@login_required
def live_timing():
    return render_template("live_timing.html")

# Rutas para evitar el error 500 del Manifest
@app.route('/manifest.json')
def manifest():
    return jsonify({
        "short_name": "Legacy",
        "name": "Legacy eSports Club",
        "icons": [{"src": "/static/img/favicon.png", "sizes": "192x192", "type": "image/png"}],
        "start_url": "/",
        "display": "standalone",
        "theme_color": "#FF5A00",
        "background_color": "#0a0a0a"
    })

@app.route('/sw.js')
def service_worker():
    return app.send_static_file('js/sw.js') if os.path.exists('static/js/sw.js') else ("", 204)

# ==========================================
# FIN BLOQUE LIVE TIMING
# ==========================================
if __name__ == '__main__':
    ir = irsdk.IRSDK()
    state = State()
    print("--- LEGACY BRIDGE CLIENT ---")
    print(f"🌍 Conectando a: {{SERVER_URL}}")
    print("Esperando a iRacing...")
    try:
        while True:
            check_iracing(ir, state)
            loop(ir, state)
            time.sleep(1)
    except KeyboardInterrupt: pass
'''
    from flask import Response
    return Response(bridge_code, mimetype='text/x-python', headers={'Content-Disposition': 'attachment;filename=legacy_bridge.py'})

if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=5000)
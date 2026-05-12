from flask import Flask, render_template, request, send_file, jsonify, session, redirect, url_for
import os, uuid
from ia_interpreter_llm import interpretar_comando
from ai_editor import procesar_video

from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-cambiame-en-produccion")

login_manager = LoginManager(app)
login_manager.login_view = "login_page"
login_manager.login_message = None

# ── Usuarios en memoria ───────────────────────────────────────────────────
USERS: dict = {}
USERS_BY_ID: dict = {}

class User(UserMixin):
    def __init__(self, data):
        self.id = data["id"]; self.username = data["username"]
        self.email = data["email"]; self._data = data

    def check_password(self, pwd): return check_password_hash(self._data["password_hash"], pwd)

    @staticmethod
    def create(username, email, password):
        uid = uuid.uuid4().hex
        data = {"id":uid,"username":username,"email":email,"password_hash":generate_password_hash(password)}
        USERS[email] = data; USERS_BY_ID[uid] = data
        return User(data)

    @staticmethod
    def get_by_email(email):
        d = USERS.get(email.lower()); return User(d) if d else None

    @staticmethod
    def get_by_id(uid):
        d = USERS_BY_ID.get(uid); return User(d) if d else None

    @staticmethod
    def email_exists(email): return email.lower() in USERS

    @staticmethod
    def username_exists(name): return any(u["username"].lower()==name.lower() for u in USERS.values())

@login_manager.user_loader
def load_user(uid): return User.get_by_id(uid)

# ── Config uploads ────────────────────────────────────────────────────────
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
ALLOWED_EXTENSIONS = {"mp4","mov","avi","mkv","webm"}
ALLOWED_AUDIO = {"mp3","wav","ogg","m4a"}

def allowed_file(f): return "." in f and f.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

# ── Auth routes ───────────────────────────────────────────────────────────
@app.route("/login")
def login_page():
    if current_user.is_authenticated: return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/auth/register", methods=["POST"])
def register():
    d = request.get_json()
    username = d.get("username","").strip()
    email    = d.get("email","").strip().lower()
    password = d.get("password","")
    if not username or not email or not password:
        return jsonify({"error":"Todos los campos son obligatorios."}), 400
    if len(password) < 8:
        return jsonify({"error":"La contraseña debe tener al menos 8 caracteres."}), 400
    if User.email_exists(email):
        return jsonify({"error":"Ya existe una cuenta con ese email."}), 409
    if User.username_exists(username):
        return jsonify({"error":"Ese nombre de usuario ya está en uso."}), 409
    user = User.create(username, email, password)
    login_user(user, remember=True)
    return jsonify({"ok":True,"username":user.username}), 201

@app.route("/auth/login", methods=["POST"])
def login():
    d = request.get_json()
    email    = d.get("email","").strip().lower()
    password = d.get("password","")
    user = User.get_by_email(email)
    if not user or not user.check_password(password):
        return jsonify({"error":"Email o contraseña incorrectos."}), 401
    login_user(user, remember=True)
    session["historial"] = []
    return jsonify({"ok":True,"username":user.username}), 200

@app.route("/auth/logout", methods=["POST"])
@login_required
def logout():
    logout_user(); return jsonify({"ok":True}), 200

@app.route("/auth/me")
@login_required
def me():
    return jsonify({"username":current_user.username,"email":current_user.email})

@app.route("/ping")
def ping():
    return jsonify({"ok":True}), 200

# ── Rutas principales ─────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    session.setdefault("historial", [])
    return render_template("index.html")

@app.route("/reset", methods=["POST"])
@login_required
def reset():
    session["historial"] = []
    return jsonify({"ok":True})

@app.route("/interpretar", methods=["POST"])
@login_required
def interpretar():
    data      = request.get_json()
    comando   = data.get("comando","")
    historial = session.get("historial",[])

    resultado = interpretar_comando(comando, historial)

    historial.append({"role":"user",      "content":comando})
    historial.append({"role":"assistant", "content":str(resultado)})
    session["historial"] = historial[-20:]

    # ── CLAVE: manejar str (chat) vs dict (acciones) ──────────────────
    if isinstance(resultado, str):
        return jsonify({"chat": resultado})

    return jsonify({"acciones": resultado, "resumen": resumir_acciones(resultado)})

@app.route("/upload", methods=["POST"])
@login_required
def upload():
    if "video" not in request.files:
        return jsonify({"error":"No se recibió ningún archivo."}), 400
    video   = request.files["video"]
    comando = request.form.get("comando","")
    if video.filename == "":
        return jsonify({"error":"El archivo no tiene nombre."}), 400
    if not allowed_file(video.filename):
        return jsonify({"error":"Formato no soportado."}), 400
    try:
        historial = session.get("historial",[])
        acciones  = interpretar_comando(comando, historial)

        # Si el intérprete devolvió string (chat) en vez de acciones
        if isinstance(acciones, str):
            return jsonify({"error":"Escribí qué querés hacer con el video (ej: 'quitar silencios')."}), 400

        uid      = uuid.uuid4().hex[:8]
        ext      = video.filename.rsplit(".",1)[1].lower()
        ruta_in  = os.path.join(UPLOAD_FOLDER, f"{uid}_input.{ext}")
        ruta_out = os.path.join(UPLOAD_FOLDER, f"{uid}_editado.mp4")
        video.save(ruta_in)

        if "music" in request.files and request.files["music"].filename != "":
            mf = request.files["music"]
            me = mf.filename.rsplit(".",1)[1].lower()
            if me in ALLOWED_AUDIO:
                rm = os.path.join(UPLOAD_FOLDER, f"{uid}_music.{me}")
                mf.save(rm)
                acciones["music_path"]   = rm
                acciones["music_volume"] = acciones.get("music_volume") or 0.3

        salida = procesar_video(ruta_in, acciones, ruta_salida=ruta_out)

        historial.append({"role":"user",      "content":comando})
        historial.append({"role":"assistant", "content":str(acciones)})
        session["historial"] = historial[-20:]

        return send_file(salida, as_attachment=True)

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

def resumir_acciones(a):
    p = []
    if a.get("remove_silence"): p.append("✂️ Eliminar silencios")
    if a.get("subtitles"):       p.append("💬 Subtítulos")
    if a.get("duration"):        p.append(f"⏱️ {a['duration']}s")
    if a.get("speed"):           p.append(f"⚡ {a['speed']}x")
    if a.get("volume"):          p.append(f"🔊 {a['volume']}x")
    if a.get("zoom"):            p.append(f"🔍 zoom {a['zoom']}x")
    if a.get("blackwhite"):      p.append("🎞️ B&N")
    if a.get("fade_in"):         p.append(f"🎬 fade in {a['fade_in']}s")
    if a.get("fade_out"):        p.append(f"🎬 fade out {a['fade_out']}s")
    if a.get("music_path"):      p.append(f"🎵 música al {int(a.get('music_volume',0.3)*100)}%")
    if a.get("highlights"):      p.append(f"⭐ highlights {a.get('highlights_duration',30)}s")
    return " | ".join(p) if p else "ℹ️ Sin acciones específicas"

if __name__ == "__main__":
    app.run(debug=True)

#!/usr/bin/env python3
"""AITA — Asistente flotante Mac para Esteban."""
from __future__ import annotations

import os, sys, subprocess, tempfile, base64, time, json, socket, datetime
import urllib.parse
from pathlib import Path

# ── Dependencias opcionales (voz) ─────────────────────────────────────────────
try:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    _VOICE_OK = True
except Exception:
    _VOICE_OK = False

# ── Dependencias obligatorias ─────────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types as gt
except Exception as _e:
    import tkinter as _tk, tkinter.messagebox as _mb
    _tk.Tk().withdraw()
    _mb.showerror("AITA", f"Falta google-genai.\nEjecuta run.command para instalarlo.\n\n{_e}")
    sys.exit(1)

try:
    from PySide6.QtWidgets import (QApplication, QWidget, QLineEdit,
                                    QVBoxLayout, QHBoxLayout, QPushButton,
                                    QLabel, QScrollArea, QSizePolicy)
except Exception as _e:
    import tkinter as _tk, tkinter.messagebox as _mb
    _tk.Tk().withdraw()
    _mb.showerror("AITA", f"Falta PySide6.\nEjecuta run.command para instalarlo.\n\n{_e}")
    sys.exit(1)
from PySide6.QtCore import Qt, QThread, Signal, QPoint, QTimer, QRect
from PySide6.QtGui import (QPainter, QColor, QFont, QPen,
                            QPainterPath, QPixmap)

_env = Path(__file__).parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except Exception:
        pass
API_KEY        = os.getenv("GEMINI_API_KEY", "")
MODEL          = "gemini-2.5-flash"
SHORTCUTS_FILE = Path(__file__).parent / "shortcuts.json"
MEMORY_FILE    = Path(__file__).parent / "memory.json"

SAMPLE_RATE       = 16000
SILENCE_THRESHOLD = 0.018
SILENCE_SECONDS   = 1.5
MAX_SECONDS       = 20
GEMINI_TIMEOUT    = 45    # segundos antes de error por red colgada
BUBBLE_AUTOHIDE   = 20    # segundos antes de cerrar el bocadillo solo
MAX_HISTORY       = 6     # turnos de conversación recordados
SHOE_ZONE         = 0.75  # fracción desde arriba; por debajo → salir

# ── Colores ───────────────────────────────────────────────────────────────────
C_IDLE   = QColor("#2C2C2E")
C_LISTEN = QColor("#FF3B30")
C_THINK  = QColor("#FF9500")
C_DONE   = QColor("#30D158")
C_BUBBLE = QColor("#1C1C1E")

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_BASE = """Eres AITA, asistente personal de Esteban en su Mac.

ESTILO: español, cálido y claro. Máximo 2 frases por respuesta. Sin tecnicismos. Usa "Esteban" de vez en cuando.

HERRAMIENTAS — cuándo usar cada una:
• open_thing(target) → abrir apps, carpetas, archivos o URLs. Target puede ser nombre natural ("el correo", "fotos") o ruta.
• create_folder(name, location) → crear carpeta nueva. Ubicación por defecto: Escritorio.
• explain_screen() → capturar pantalla y explicar qué hay. Usar cuando Esteban no entiende algo que ve.
• open_press() → abrir El Correo, Marca y El Confidencial sin publicidad. Trigger: "prensa", "periódicos", "noticias".
• web_search(query) → buscar en Google. Usar cuando quiere información de internet.
• set_volume(action) → volumen del Mac. Acciones: subir · bajar · silenciar · máximo · normal.
• save_shortcut(trigger, description) → guardar atajo de voz. Usar cuando diga "cuando diga X haz Y" o "crea un atajo para X".
• list_shortcuts() → listar atajos guardados. Trigger: "mis atajos", "qué atajos tengo".
• delete_shortcut(trigger) → borrar un atajo.
• remember(topic, info) → guardar en memoria algo duradero y útil.
  GUARDAR cuando: Esteban dice "recuerda que..." / comparte teléfono, nombre, dirección, contraseña, preferencia personal, fecha importante.
  NO GUARDAR: peticiones normales ("abre el correo"), preguntas puntuales, cosas ya guardadas.
  topic = categoría corta ("médico", "WiFi", "cumpleaños Ana"). info = dato completo.
• recall() → mostrar todo lo guardado en memoria. Trigger: "qué recuerdas", "qué tienes guardado".
• forget(topic) → borrar algo de la memoria. Trigger: "olvida que...", "borra que...".

REGLAS: actúa siempre sin pedir confirmación. Si algo falla, explícalo con palabras sencillas."""


# ── Atajos personalizados (shortcuts.json) ────────────────────────────────────
def _load_custom_shortcuts() -> dict[str, str]:
    try:
        if SHORTCUTS_FILE.exists():
            return json.loads(SHORTCUTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_custom_shortcuts(data: dict[str, str]) -> None:
    SHORTCUTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# ── Memoria persistente (memory.json) ─────────────────────────────────────────
def _load_memory() -> dict[str, str]:
    try:
        if MEMORY_FILE.exists():
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_memory(data: dict[str, str]) -> None:
    MEMORY_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def _build_system() -> str:
    """System prompt con memoria y atajos personalizados de Esteban."""
    parts = [SYSTEM_BASE]

    memory = _load_memory()
    if memory:
        lines = ["\nLO QUE AITA RECUERDA DE ESTEBAN (úsalo cuando sea relevante):"]
        for topic, info in memory.items():
            lines.append(f"  • {topic}: {info}")
        parts.append("\n".join(lines))

    custom = _load_custom_shortcuts()
    if custom:
        lines = ["\nATAJOS PERSONALIZADOS DE ESTEBAN:",
                 "Si Esteban dice una de estas frases (o algo muy parecido), ejecuta la acción:"]
        for phrase, action in custom.items():
            lines.append(f'  • "{phrase}" → {action}')
        parts.append("\n".join(lines))

    return "\n".join(parts)

# ── Mapeo de nombres naturales → app real ─────────────────────────────────────
APP_ALIASES: dict[str, str] = {
    "correo": "Mail", "el correo": "Mail", "mail": "Mail", "email": "Mail",
    "fotos": "Photos", "las fotos": "Photos", "mis fotos": "Photos",
    "safari": "Safari", "chrome": "Google Chrome", "firefox": "Firefox",
    "el navegador": "Safari", "internet": "Safari",
    "facetime": "FaceTime", "videollamada": "FaceTime",
    "whatsapp": "WhatsApp", "mensajes": "Messages", "sms": "Messages",
    "telegram": "Telegram",
    "word": "Microsoft Word", "excel": "Microsoft Excel",
    "pages": "Pages", "numbers": "Numbers", "keynote": "Keynote",
    "pdf": "Preview", "visor": "Preview", "preview": "Preview",
    "ajustes": "System Preferences", "configuración": "System Preferences",
    "finder": "Finder", "escritorio": "Finder", "terminal": "Terminal",
    "música": "Music", "la música": "Music", "spotify": "Spotify",
    "videos": "QuickTime Player", "quicktime": "QuickTime Player",
    "notas": "Notes", "las notas": "Notes",
    "calendario": "Calendar", "el calendario": "Calendar",
    "recordatorios": "Reminders",
    "calculadora": "Calculator", "mapas": "Maps",
}

def _resolve_app(name: str) -> str | None:
    return APP_ALIASES.get(name.lower().strip())

def _open_app(app_name: str) -> bool:
    return subprocess.run(["open", "-a", app_name], capture_output=True).returncode == 0

def _find_and_open(query: str) -> str:
    r = subprocess.run(["mdfind", "-name", query], capture_output=True, text=True, timeout=6)
    paths = [p for p in r.stdout.strip().split("\n")
             if p and "/Library/Caches" not in p and "/.Trash" not in p]
    if not paths:
        r2 = subprocess.run(["mdfind", query], capture_output=True, text=True, timeout=6)
        paths = [p for p in r2.stdout.strip().split("\n")
                 if p and "/Library/Caches" not in p and "/.Trash" not in p]
    if not paths:
        return f"No encontré ningún archivo o carpeta llamado '{query}'."
    home = str(Path.home())
    best = next((p for p in paths if p.startswith(home)), paths[0])
    subprocess.Popen(["open", best])
    return f"Abrí '{Path(best).name}'."


# ── Tool declarations ─────────────────────────────────────────────────────────
TOOL_DECLS = [
    gt.FunctionDeclaration(
        name="open_thing",
        description="Abre una app, carpeta, archivo o página web.",
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={"target": gt.Schema(type=gt.Type.STRING,
                        description="Qué abrir: nombre de app, carpeta, archivo o URL")},
            required=["target"],
        ),
    ),
    gt.FunctionDeclaration(
        name="create_folder",
        description="Crea una carpeta nueva y la abre. Por defecto en el Escritorio.",
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={
                "name":     gt.Schema(type=gt.Type.STRING, description="Nombre de la carpeta"),
                "location": gt.Schema(type=gt.Type.STRING, description="Ruta (opcional)"),
            },
            required=["name"],
        ),
    ),
    gt.FunctionDeclaration(
        name="explain_screen",
        description="Captura la pantalla y la analiza para explicar qué hay en ella.",
        parameters=gt.Schema(type=gt.Type.OBJECT, properties={}),
    ),
    gt.FunctionDeclaration(
        name="open_press",
        description="Abre El Correo, Marca y El Confidencial sin muros de pago.",
        parameters=gt.Schema(type=gt.Type.OBJECT, properties={}),
    ),
    gt.FunctionDeclaration(
        name="web_search",
        description="Busca algo en Google y abre los resultados en el navegador.",
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={"query": gt.Schema(type=gt.Type.STRING, description="Qué buscar")},
            required=["query"],
        ),
    ),
    gt.FunctionDeclaration(
        name="set_volume",
        description="Controla el volumen del Mac. Acciones: subir, bajar, silenciar, máximo, normal.",
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={"action": gt.Schema(type=gt.Type.STRING,
                        description="subir | bajar | silenciar | normal | máximo")},
            required=["action"],
        ),
    ),
    gt.FunctionDeclaration(
        name="save_shortcut",
        description=(
            "Guarda un atajo de voz personalizado de Esteban. "
            "'trigger' es la frase que dirá Esteban. "
            "'description' es la instrucción que debe ejecutarse cuando la diga."
        ),
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={
                "trigger":     gt.Schema(type=gt.Type.STRING,
                               description="Frase que dirá Esteban (en minúsculas)"),
                "description": gt.Schema(type=gt.Type.STRING,
                               description="Qué hacer cuando Esteban diga esa frase"),
            },
            required=["trigger", "description"],
        ),
    ),
    gt.FunctionDeclaration(
        name="list_shortcuts",
        description="Muestra todos los atajos de voz personalizados que tiene guardados Esteban.",
        parameters=gt.Schema(type=gt.Type.OBJECT, properties={}),
    ),
    gt.FunctionDeclaration(
        name="delete_shortcut",
        description="Elimina un atajo de voz personalizado de Esteban.",
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={"trigger": gt.Schema(type=gt.Type.STRING,
                        description="Frase del atajo a eliminar")},
            required=["trigger"],
        ),
    ),
    gt.FunctionDeclaration(
        name="remember",
        description=(
            "Guarda en memoria algo útil y duradero sobre Esteban: "
            "teléfonos, nombres importantes, preferencias, contraseñas, fechas clave. "
            "NO usar para peticiones normales del día a día."
        ),
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={
                "topic": gt.Schema(type=gt.Type.STRING,
                         description="Categoría breve (ej: 'médico', 'WiFi', 'cumpleaños nieto')"),
                "info":  gt.Schema(type=gt.Type.STRING,
                         description="Información completa a recordar"),
            },
            required=["topic", "info"],
        ),
    ),
    gt.FunctionDeclaration(
        name="recall",
        description="Muestra todo lo que AITA recuerda de Esteban.",
        parameters=gt.Schema(type=gt.Type.OBJECT, properties={}),
    ),
    gt.FunctionDeclaration(
        name="forget",
        description="Olvida algo guardado en memoria.",
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={"topic": gt.Schema(type=gt.Type.STRING,
                        description="Tema a olvidar")},
            required=["topic"],
        ),
    ),
]

# ── Tool handlers ─────────────────────────────────────────────────────────────
def open_thing(target: str) -> str:
    try:
        t = target.strip()
        if t.startswith("http://") or t.startswith("https://"):
            subprocess.Popen(["open", t])
            return f"Abriendo {t}."
        if t.startswith("/") or t.startswith("~"):
            p = Path(t).expanduser()
            subprocess.Popen(["open", str(p)])
            return f"Abriendo {p.name}."
        real = _resolve_app(t)
        if real:
            return f"Abriendo {real}." if _open_app(real) else f"No pude abrir {real}."
        if _open_app(t):
            return f"Abriendo {t}."
        return _find_and_open(t)
    except Exception as e:
        return f"No pude abrir '{target}': {e}"


def create_folder(name: str, location: str = "~/Desktop") -> str:
    try:
        base = Path(location).expanduser()
        folder = base / name
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(folder)])
        return f"Carpeta '{name}' creada y abierta."
    except Exception as e:
        return f"No pude crear la carpeta: {e}"


def explain_screen() -> str:
    try:
        tmp = Path(tempfile.mktemp(suffix=".png"))
        subprocess.run(["screencapture", "-x", str(tmp)], timeout=5)
        if not tmp.exists():
            return "No pude capturar la pantalla."
        try:
            from PIL import Image as _Img
            img = _Img.open(tmp)
            if img.width > 1280:
                ratio = 1280 / img.width
                img = img.resize((1280, int(img.height * ratio)), _Img.LANCZOS)
                img.save(tmp)
        except ImportError:
            pass
        with open(tmp, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        tmp.unlink(missing_ok=True)
        return f"__SCREENSHOT_B64__{data}"
    except Exception as e:
        return f"Error capturando pantalla: {e}"


def web_search(query: str) -> str:
    try:
        url = "https://www.google.es/search?q=" + urllib.parse.quote_plus(query)
        subprocess.Popen(["open", url])
        return f"Buscando '{query}' en Google."
    except Exception as e:
        return f"No pude abrir la búsqueda: {e}"


def save_shortcut(trigger: str, description: str) -> str:
    try:
        data = _load_custom_shortcuts()
        key  = trigger.lower().strip()
        data[key] = description.strip()
        _save_custom_shortcuts(data)
        return f"¡Listo! Cuando digas \"{key}\", haré: {description}."
    except Exception as e:
        return f"No pude guardar el atajo: {e}"


def list_shortcuts() -> str:
    data = _load_custom_shortcuts()
    if not data:
        return "Aún no tienes atajos personalizados. Puedes crear uno diciendo: \"cuando diga X, haz Y\"."
    lines = [f"Tienes {len(data)} atajo(s) guardado(s):"]
    for phrase, action in data.items():
        lines.append(f'• "{phrase}" → {action}')
    return "\n".join(lines)


def delete_shortcut(trigger: str) -> str:
    try:
        data = _load_custom_shortcuts()
        key  = trigger.lower().strip()
        if key in data:
            del data[key]
            _save_custom_shortcuts(data)
            return f"Atajo \"{key}\" eliminado."
        # Búsqueda aproximada por si el nombre no coincide exactamente
        similar = [k for k in data if trigger.lower() in k or k in trigger.lower()]
        if similar:
            return f"No encontré \"{trigger}\" exactamente. ¿Querías borrar: {', '.join(similar)}?"
        return f"No encontré ningún atajo con ese nombre."
    except Exception as e:
        return f"No pude eliminar el atajo: {e}"


def remember(topic: str, info: str) -> str:
    try:
        data = _load_memory()
        data[topic.strip()] = info.strip()
        _save_memory(data)
        return f"Guardado en memoria: {topic} → {info}"
    except Exception as e:
        return f"No pude guardar en memoria: {e}"


def recall() -> str:
    data = _load_memory()
    if not data:
        return "Todavía no tengo nada guardado sobre ti, Esteban."
    lines = [f"Esto es lo que recuerdo ({len(data)} cosa(s)):"]
    for topic, info in data.items():
        lines.append(f"• {topic}: {info}")
    return "\n".join(lines)


def forget(topic: str) -> str:
    try:
        data = _load_memory()
        key  = topic.strip()
        if key in data:
            del data[key]
            _save_memory(data)
            return f"Ya no recuerdo nada sobre \"{key}\"."
        similar = [k for k in data if topic.lower() in k.lower() or k.lower() in topic.lower()]
        if similar:
            return f"No encontré \"{topic}\" exactamente. ¿Querías que olvidara: {', '.join(similar)}?"
        return f"No tenía nada guardado sobre \"{topic}\"."
    except Exception as e:
        return f"No pude borrar el recuerdo: {e}"


def set_volume(action: str) -> str:
    try:
        a = action.lower().strip()
        if a == "silenciar":
            subprocess.run(["osascript", "-e", "set volume output muted true"], timeout=5)
            return "Silenciado."
        elif a in ("máximo", "maximo"):
            subprocess.run(["osascript",
                "-e", "set volume output muted false",
                "-e", "set volume output volume 100"], timeout=5)
            return "Volumen al máximo."
        elif a in ("normal", "medio"):
            subprocess.run(["osascript",
                "-e", "set volume output muted false",
                "-e", "set volume output volume 50"], timeout=5)
            return "Volumen al 50%."
        elif a == "subir":
            subprocess.run(["osascript",
                "-e", "set volume output muted false",
                "-e", "set v to output volume of (get volume settings)",
                "-e", "set v to v + 20",
                "-e", "if v > 100 then set v to 100",
                "-e", "set volume output volume v"], timeout=5)
            return "Subiendo el volumen."
        elif a == "bajar":
            subprocess.run(["osascript",
                "-e", "set v to output volume of (get volume settings)",
                "-e", "set v to v - 20",
                "-e", "if v < 0 then set v to 0",
                "-e", "set volume output volume v"], timeout=5)
            return "Bajando el volumen."
        else:
            return f"No entiendo la acción '{action}'."
    except Exception as e:
        return f"No pude cambiar el volumen: {e}"


# ── Prensa ────────────────────────────────────────────────────────────────────
CHROME        = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_DIR    = Path.home() / "Library/Application Support/Google/Chrome"
PRESS_PROFILE = "AITA_Prensa"
PRESS_URLS    = [
    "https://12ft.io/proxy?q=https://www.elcorreo.com",
    "https://www.marca.com",
    "https://www.elconfidencial.com",
]
PRESS_DOMAINS = [
    "https://[*.]marca.com:443,*",
    "https://[*.]elconfidencial.com:443,*",
]

def _setup_press_profile() -> None:
    profile_dir = CHROME_DIR / PRESS_PROFILE
    prefs_path  = profile_dir / "Preferences"
    js_exc = {d: {"expiration": "0", "last_modified": "0", "model": 0, "setting": 2}
              for d in PRESS_DOMAINS}
    prefs    = {"profile": {"name": "AITA Prensa", "using_default_name": False},
                "content_settings": {"exceptions": {"javascript": js_exc}}}
    expected = json.dumps(prefs, indent=2)
    if prefs_path.exists():
        try:
            if prefs_path.read_text(encoding="utf-8") == expected:
                return
        except Exception:
            pass
    profile_dir.mkdir(parents=True, exist_ok=True)
    prefs_path.write_text(expected, encoding="utf-8")


def open_press() -> str:
    try:
        if Path(CHROME).exists():
            _setup_press_profile()
            subprocess.Popen([CHROME, f"--profile-directory={PRESS_PROFILE}"] + PRESS_URLS)
        else:
            for url in PRESS_URLS:
                subprocess.Popen(["open", "-a", "Safari", url])
        return "Abriendo los periódicos."
    except Exception as e:
        return f"No pude abrir los periódicos: {e}"


# ── Atajos directos ───────────────────────────────────────────────────────────
SHORTCUTS: dict[str, callable] = {
    "prensa": open_press, "periódicos": open_press, "periodicos": open_press,
    "noticias": open_press, "los periódicos": open_press,
}

HANDLERS = {
    "open_thing":      lambda **kw: open_thing(**kw),
    "create_folder":   lambda **kw: create_folder(**kw),
    "explain_screen":  lambda **kw: explain_screen(**kw),
    "open_press":      lambda **kw: open_press(),
    "web_search":      lambda **kw: web_search(**kw),
    "set_volume":      lambda **kw: set_volume(**kw),
    "save_shortcut":   lambda **kw: save_shortcut(**kw),
    "list_shortcuts":  lambda **kw: list_shortcuts(),
    "delete_shortcut": lambda **kw: delete_shortcut(**kw),
    "remember":        lambda **kw: remember(**kw),
    "recall":          lambda **kw: recall(),
    "forget":          lambda **kw: forget(**kw),
}

# ── Gemini worker ─────────────────────────────────────────────────────────────
class GeminiWorker(QThread):
    done  = Signal(str)
    error = Signal(str)

    def __init__(self, message: str, audio_path: str | None = None,
                 history: list[tuple[str, str]] | None = None,
                 system: str | None = None):
        super().__init__()
        self._message    = message
        self._audio_path = audio_path
        self._history    = history or []
        self._system     = system or SYSTEM_BASE

    def run(self):
        try:
            client = genai.Client(api_key=API_KEY)
            config = gt.GenerateContentConfig(
                system_instruction=self._system,
                tools=[gt.Tool(function_declarations=TOOL_DECLS)],
                temperature=0.7,
            )

            # Reconstruir historial
            contents: list = []
            for (utxt, atxt) in self._history:
                contents.append(gt.Content(role="user",   parts=[gt.Part(text=utxt or "[voz]")]))
                contents.append(gt.Content(role="model",  parts=[gt.Part(text=atxt)]))

            # Turno actual
            if self._audio_path and Path(self._audio_path).exists():
                with open(self._audio_path, "rb") as f:
                    audio_bytes = f.read()
                user_parts = [
                    gt.Part(inline_data=gt.Blob(mime_type="audio/wav", data=audio_bytes)),
                    gt.Part(text="El usuario acaba de decir esto en audio. Responde apropiadamente."),
                ]
                Path(self._audio_path).unlink(missing_ok=True)
            else:
                user_parts = [gt.Part(text=self._message)]
            contents.append(gt.Content(role="user", parts=user_parts))

            while True:
                resp      = client.models.generate_content(model=MODEL, contents=contents, config=config)
                candidate = resp.candidates[0]
                contents.append(gt.Content(role="model", parts=candidate.content.parts))

                fc_parts = [p for p in candidate.content.parts if p.function_call]
                if fc_parts:
                    tool_results = []
                    screenshot_b64 = None
                    for part in fc_parts:
                        fc     = part.function_call
                        result = HANDLERS.get(fc.name, lambda **kw: "herramienta no disponible")(**dict(fc.args))
                        if isinstance(result, str) and result.startswith("__SCREENSHOT_B64__"):
                            screenshot_b64 = result[len("__SCREENSHOT_B64__"):]
                            result = "[captura tomada]"
                        tool_results.append(gt.Part(function_response=gt.FunctionResponse(
                            name=fc.name, response={"result": result}
                        )))
                    contents.append(gt.Content(role="user", parts=tool_results))
                    if screenshot_b64:
                        contents.append(gt.Content(role="user", parts=[
                            gt.Part(inline_data=gt.Blob(
                                mime_type="image/png",
                                data=base64.b64decode(screenshot_b64)
                            )),
                            gt.Part(text="Esta es la pantalla de Esteban. Explícale qué hay y qué significa."),
                        ]))
                    continue

                text = "".join(p.text for p in candidate.content.parts if hasattr(p, "text") and p.text)
                self.done.emit(text.strip())
                break

        except Exception as e:
            self.error.emit(str(e))


# ── Voice worker ──────────────────────────────────────────────────────────────
class VoiceWorker(QThread):
    done  = Signal(str)
    error = Signal(str)

    def run(self):
        if not _VOICE_OK:
            self.error.emit("El micrófono no está disponible.\nUsa el texto (clic derecho).")
            return
        try:
            chunk = int(SAMPLE_RATE * 0.1)
            chunks, silence_count = [], 0
            max_silence   = int(SILENCE_SECONDS / 0.1)
            voice_started = False
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
                deadline = time.time() + MAX_SECONDS + 5
                while time.time() < deadline:
                    data, _ = stream.read(chunk)
                    rms = float(np.sqrt(np.mean(data ** 2)))
                    if not voice_started:
                        if rms > SILENCE_THRESHOLD:
                            voice_started = True
                        else:
                            continue
                    chunks.append(data.copy())
                    if rms < SILENCE_THRESHOLD:
                        silence_count += 1
                        if silence_count >= max_silence:
                            break
                    else:
                        silence_count = 0
                    if len(chunks) > MAX_SECONDS * 10:
                        break
            if not chunks:
                self.error.emit("No escuché nada. Habla más cerca del micrófono.")
                return
            audio = np.concatenate(chunks)
            path  = tempfile.mktemp(suffix=".wav")
            sf.write(path, audio, SAMPLE_RATE)
            self.done.emit(path)
        except OSError:
            self.error.emit("No puedo acceder al micrófono.\nVe a Ajustes → Privacidad → Micrófono.")
        except Exception as e:
            self.error.emit(f"Error de micrófono: {e}")


# ── Speech bubble ─────────────────────────────────────────────────────────────
class BubbleWidget(QWidget):
    send_text = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint)
        import sys as _sys
        self._transparent = not (_sys.platform == "darwin" and _sys.version_info < (3, 10))
        if self._transparent:
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(320)
        self._drag_pos = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)

        # Área de respuesta con scroll para textos largos
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollBar:vertical { width: 4px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #555; border-radius: 2px; }"
        )
        self._scroll.setMaximumHeight(260)

        self._output = QLabel("", self)
        self._output.setWordWrap(True)
        self._output.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._output.setFont(QFont("SF Pro Text", 13))
        self._output.setStyleSheet("color: #EBEBF5; background: transparent;")
        self._output.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._scroll.setWidget(self._output)
        lay.addWidget(self._scroll)

        # Fila de entrada
        row = QHBoxLayout()
        row.setSpacing(6)
        self._input = QLineEdit(self)
        self._input.setPlaceholderText("Escribe aquí…")
        self._input.setFont(QFont("SF Pro Text", 13))
        self._input.setStyleSheet("""
            QLineEdit {
                background: #3A3A3C; color: #EBEBF5;
                border: none; border-radius: 10px;
                padding: 6px 12px;
            }
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        btn = QPushButton("↑", self)
        btn.setFixedSize(32, 32)
        btn.setFont(QFont("SF Pro Text", 16))
        btn.setStyleSheet("""
            QPushButton { background: #0A84FF; color: white; border: none; border-radius: 16px; }
            QPushButton:hover { background: #409CFF; }
        """)
        btn.clicked.connect(self._send)
        row.addWidget(btn)
        lay.addLayout(row)

        # Auto-cierre tras inactividad
        self._autohide = QTimer(self)
        self._autohide.setSingleShot(True)
        self._autohide.timeout.connect(self.hide)

        self.adjustSize()

    def _send(self):
        txt = self._input.text().strip()
        if txt:
            self._input.clear()
            self._autohide.stop()
            self.send_text.emit(txt)

    def show_response(self, text: str):
        self._output.setText(text)
        self.adjustSize()
        self.show()
        self._autohide.start(BUBBLE_AUTOHIDE * 1000)

    def show_thinking(self):
        self._output.setText("…")
        self.adjustSize()
        self.show()
        self._autohide.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.fillPath(path, C_BUBBLE)
        p.setPen(QPen(QColor("#3A3A3C"), 1))
        p.drawPath(path)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _):
        self._drag_pos = None


# ── Saludo según la hora ──────────────────────────────────────────────────────
def _greeting() -> str:
    h = datetime.datetime.now().hour
    if 6 <= h < 14:
        return "¡Buenos días, Esteban! Aquí estoy cuando me necesites."
    elif 14 <= h < 21:
        return "¡Buenas tardes, Esteban! ¿En qué te puedo ayudar?"
    else:
        return "¡Buenas noches, Esteban! Aquí estoy si me necesitas."


# ── Main floating window ──────────────────────────────────────────────────────
_ICON_PATH = Path(__file__).parent / "aita_icon.png"
DOT_R      = 10

class AitaWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        import sys as _sys
        self._transparent = not (_sys.platform == "darwin" and _sys.version_info < (3, 10))
        if self._transparent:
            self.setAttribute(Qt.WA_TranslucentBackground)

        self._pixmap = QPixmap(str(_ICON_PATH)) if _ICON_PATH.exists() else QPixmap()
        iw  = self._pixmap.width()  if not self._pixmap.isNull() else 80
        ih  = self._pixmap.height() if not self._pixmap.isNull() else 80
        pad = 14
        self.setFixedSize(iw + pad, ih + pad)

        self._color        = C_IDLE
        self._drag_pos     = None
        self._click_pos    = QPoint()
        self._listening    = False
        self._worker       = None
        self._voice_worker = None
        self._history: list[tuple[str, str]] = []

        self._bubble = BubbleWidget()
        self._bubble.send_text.connect(self._on_text)

        self._pulse     = 0.0
        self._pulse_dir = 1
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)

        # Timeout por si la API se cuelga
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(
            lambda: self._on_error("Sin respuesta. Comprueba tu conexión a internet.")
        )

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 20,
                  screen.bottom() - self.height() - 20)
        self.show()

        # macOS: evitar que la ventana se oculte cuando otra app toma el foco
        if sys.platform == "darwin":
            try:
                from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
                NSApplication.sharedApplication().setActivationPolicy_(
                    NSApplicationActivationPolicyAccessory
                )
            except Exception:
                pass

        # Saludo inicial
        QTimer.singleShot(800, self._show_greeting)

    def _show_greeting(self):
        self._bubble.show_response(_greeting())
        self._reposition_bubble()

    # ── Pintar ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        if not self._transparent:
            p.fillRect(self.rect(), QColor(30, 30, 30, 220))

        if self._listening and self._pulse > 0:
            halo = QColor(self._color)
            halo.setAlpha(int(self._pulse * 100))
            p.setBrush(halo)
            p.setPen(Qt.NoPen)
            hw = self._pixmap.width() + 20
            hh = self._pixmap.height() + 20
            p.drawRoundedRect((self.width() - hw) // 2, (self.height() - hh) // 2, hw, hh, 12, 12)

        if not self._pixmap.isNull():
            ox = (self.width()  - self._pixmap.width())  // 2
            oy = (self.height() - self._pixmap.height()) // 2
            p.drawPixmap(ox, oy, self._pixmap)
        else:
            r = 28
            cx, cy = self.width() // 2, self.height() // 2
            p.setBrush(self._color); p.setPen(Qt.NoPen)
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(QFont("SF Pro Text", 20, QFont.Bold))
            p.drawText(QRect(cx - r, cy - r, r * 2, r * 2), Qt.AlignCenter, "A")

        dot_x = self.width()  - DOT_R - 2
        dot_y = self.height() - DOT_R - 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawEllipse(dot_x - 1, dot_y + 1, DOT_R, DOT_R)
        p.setBrush(self._color)
        p.drawEllipse(dot_x, dot_y, DOT_R, DOT_R)

    def _tick(self):
        self._pulse += self._pulse_dir * 0.07
        if self._pulse >= 1.0:   self._pulse_dir = -1
        elif self._pulse <= 0.0: self._pulse_dir =  1
        self.update()

    # ── Drag & click ──────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos  = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._click_pos = e.globalPosition().toPoint()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            moved = (e.globalPosition().toPoint() - self._click_pos).manhattanLength()
            if moved < 5:
                # Zona de zapatos (cuarto inferior) → salir
                if e.position().y() >= self.height() * SHOE_ZONE:
                    QApplication.quit()
                    return
                self._on_click()
            self._drag_pos = None
        elif e.button() == Qt.RightButton:
            if self._bubble.isVisible():
                self._bubble.hide()
            else:
                self._reposition_bubble()
                self._bubble.show()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            self._reposition_bubble()

    # ── Lógica ───────────────────────────────────────────────────────────────
    def _on_click(self):
        if self._listening:
            self._stop_listening()
        elif self._worker and self._worker.isRunning():
            pass
        else:
            self._start_listening()

    def _start_listening(self):
        self._listening = True
        self._set_color(C_LISTEN)
        self._anim_timer.start(50)
        self._voice_worker = VoiceWorker()
        self._voice_worker.done.connect(self._on_audio)
        self._voice_worker.error.connect(self._on_error)
        self._voice_worker.start()

    def _stop_listening(self):
        self._listening = False
        self._anim_timer.stop()
        self._pulse = 0
        if self._voice_worker:
            self._voice_worker.terminate()
            self._voice_worker = None
        self._set_color(C_IDLE)

    def _on_audio(self, wav_path: str):
        self._listening = False
        self._anim_timer.stop()
        self._pulse = 0
        self._run_gemini(message="", audio_path=wav_path)

    def _on_text(self, text: str):
        key = text.strip().lower()
        # 1. Atajos predefinidos (sin pasar por Gemini, respuesta instantánea)
        fn = SHORTCUTS.get(key)
        if fn:
            self._bubble.show_response(fn())
            self._reposition_bubble()
            return
        # 2. Atajos personalizados de Esteban (envía la acción guardada a Gemini)
        custom = _load_custom_shortcuts()
        if key in custom:
            self._run_gemini(message=custom[key])
            return
        # 3. Pregunta libre
        self._run_gemini(message=text)

    def _run_gemini(self, message: str, audio_path: str | None = None):
        if self._worker and self._worker.isRunning():
            return
        self._set_color(C_THINK)
        self._bubble.show_thinking()
        self._reposition_bubble()
        self._worker = GeminiWorker(message, audio_path, list(self._history), _build_system())
        self._worker.done.connect(self._on_response)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        self._timeout_timer.start(GEMINI_TIMEOUT * 1000)

    def _on_response(self, text: str):
        self._timeout_timer.stop()
        self._set_color(C_DONE)
        self._bubble.show_response(text)
        self._reposition_bubble()
        self.show(); self.raise_()
        # Guardar en historial
        user_msg = (self._worker._message if self._worker else "") or ""
        self._history.append((user_msg, text))
        if len(self._history) > MAX_HISTORY:
            self._history.pop(0)
        QTimer.singleShot(1500, lambda: self._set_color(C_IDLE))

    def _on_error(self, err: str):
        self._timeout_timer.stop()
        self._set_color(C_IDLE)
        self._anim_timer.stop()
        self._listening = False
        self._bubble.show_response(f"⚠️ {err}")
        self._reposition_bubble()
        self.show(); self.raise_()

    def _set_color(self, c: QColor):
        self._color = c
        self.update()

    def _reposition_bubble(self):
        geo    = self.frameGeometry()
        screen = QApplication.primaryScreen().availableGeometry()
        bw     = self._bubble.width()
        bh     = self._bubble.height()
        bx = geo.left() - bw - 12
        if bx < screen.left():
            bx = geo.right() + 12
        by = geo.top() + (geo.height() - bh) // 2
        by = max(screen.top() + 8, min(by, screen.bottom() - bh - 8))
        self._bubble.move(bx, by)


# ── Instancia única ───────────────────────────────────────────────────────────
_LOCK_SOCK: socket.socket | None = None

def _acquire_instance_lock() -> bool:
    global _LOCK_SOCK
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", 54782))
        s.listen(1)
        _LOCK_SOCK = s
        return True
    except OSError:
        return False


# ── Autostart (LaunchAgent) ───────────────────────────────────────────────────
def setup_autostart() -> None:
    plist_dir  = Path.home() / "Library/LaunchAgents"
    plist_path = plist_dir / "com.aita.app.plist"
    script_dir = Path(__file__).resolve().parent
    run_cmd    = script_dir / "run.command"

    if plist_path.exists():
        try:
            if str(run_cmd) in plist_path.read_text(encoding="utf-8"):
                return
        except Exception:
            pass

    plist_dir.mkdir(parents=True, exist_ok=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>com.aita.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>{run_cmd}</string>
    </array>
    <key>WorkingDirectory</key>  <string>{script_dir}</string>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <false/>
    <key>StandardOutPath</key>   <string>{script_dir}/aita.log</string>
    <key>StandardErrorPath</key> <string>{script_dir}/aita.log</string>
</dict>
</plist>
"""
    plist_path.write_text(plist, encoding="utf-8")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout",    f"gui/{uid}", str(plist_path)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap",  f"gui/{uid}", str(plist_path)], capture_output=True)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not _acquire_instance_lock():
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    setup_autostart()

    win = AitaWindow()
    sys.exit(app.exec())

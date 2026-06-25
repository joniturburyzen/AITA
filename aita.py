#!/usr/bin/env python3
"""AITA — Asistente flotante Mac para Esteban."""
from __future__ import annotations

import os, sys, subprocess, tempfile, threading, base64, time, json, socket
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
from PySide6.QtWidgets import QMessageBox
from PySide6.QtGui import (QPainter, QColor, QFont, QPen, QBrush,
                            QPainterPath, QFontMetrics, QPixmap)

_env = Path(__file__).parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except Exception:
        pass
# Clave embebida como fallback — el .env la sobreescribe si existe
API_KEY = os.getenv("GEMINI_API_KEY") or "AIzaSyBT4Ab9uabmtcZSZmK2xs6C5QwGic_dj1A"
MODEL   = "gemini-2.5-flash"

SAMPLE_RATE      = 16000
SILENCE_THRESHOLD = 0.018
SILENCE_SECONDS  = 1.5
MAX_SECONDS      = 20

# ── Colores ───────────────────────────────────────────────────────────────────
C_IDLE    = QColor("#2C2C2E")
C_LISTEN  = QColor("#FF3B30")
C_THINK   = QColor("#FF9500")
C_DONE    = QColor("#30D158")
C_TEXT    = QColor("#FFFFFF")
C_BUBBLE  = QColor("#1C1C1E")
C_INPUT   = QColor("#2C2C2E")

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM = """Eres AITA, el asistente personal de Esteban en su Mac.

Hablas en español. Eres cálido, paciente y claro. Usas "Esteban" ocasionalmente.
Respuestas cortas, máximo 2 frases. Sin tecnicismos.

HERRAMIENTAS:
- open_thing(target): abre apps ("el correo", "fotos", "safari"), carpetas por nombre, archivos o URLs.
  Ejemplos: "ábreme la carpeta Prueba" → open_thing(target="Prueba"). "abre el correo" → open_thing(target="el correo").
- create_folder(name, location): crea una carpeta nueva y la abre. Por defecto en el Escritorio.
  Ejemplo: "crea la carpeta Prueba2" → create_folder(name="Prueba2").
- explain_screen(): captura la pantalla y la analiza. Úsala cuando Esteban pregunta qué hay en pantalla o qué significa algo que ve.
- open_press(): abre El Correo, Marca y El Confidencial sin muros de pago. Úsala cuando diga "prensa", "periódicos", "noticias" o similar.

REGLA: actúa directamente, sin pedir confirmación. Si algo falla, explícalo con palabras sencillas."""

# ── Mapeo de nombres naturales en español → nombre real de app Mac ────────────
APP_ALIASES: dict[str, str] = {
    # Correo
    "correo": "Mail", "el correo": "Mail", "mail": "Mail", "email": "Mail",
    # Fotos
    "fotos": "Photos", "las fotos": "Photos", "mis fotos": "Photos",
    # Navegadores
    "safari": "Safari", "chrome": "Google Chrome", "firefox": "Firefox",
    "el navegador": "Safari", "internet": "Safari",
    # Comunicación
    "facetime": "FaceTime", "videollamada": "FaceTime",
    "whatsapp": "WhatsApp", "mensajes": "Messages", "sms": "Messages",
    "telegram": "Telegram",
    # Documentos
    "word": "Microsoft Word", "excel": "Microsoft Excel",
    "pages": "Pages", "numbers": "Numbers", "keynote": "Keynote",
    "pdf": "Preview", "visor": "Preview", "preview": "Preview",
    # Sistema
    "ajustes": "System Preferences", "configuración": "System Preferences",
    "finder": "Finder", "escritorio": "Finder",
    "terminal": "Terminal",
    # Música / Video
    "música": "Music", "la música": "Music", "spotify": "Spotify",
    "videos": "QuickTime Player", "quicktime": "QuickTime Player",
    # Notas / Calendario
    "notas": "Notes", "las notas": "Notes",
    "calendario": "Calendar", "el calendario": "Calendar",
    "recordatorios": "Reminders",
    # Otros
    "calculadora": "Calculator",
    "mapas": "Maps",
}

def _resolve_app(name: str) -> str | None:
    """Devuelve el nombre real de la app si es un alias conocido."""
    return APP_ALIASES.get(name.lower().strip())

def _open_app(app_name: str) -> bool:
    r = subprocess.run(["open", "-a", app_name], capture_output=True, text=True)
    return r.returncode == 0

def _find_and_open(query: str) -> str:
    """Busca un archivo/carpeta por nombre y lo abre directamente."""
    # Búsqueda por nombre exacto primero
    r = subprocess.run(
        ["mdfind", "-name", query],
        capture_output=True, text=True, timeout=6
    )
    paths = [p for p in r.stdout.strip().split("\n")
             if p and "/Library/Caches" not in p and "/.Trash" not in p]

    # Si no hay resultados, búsqueda más amplia
    if not paths:
        r2 = subprocess.run(
            ["mdfind", query],
            capture_output=True, text=True, timeout=6
        )
        paths = [p for p in r2.stdout.strip().split("\n")
                 if p and "/Library/Caches" not in p and "/.Trash" not in p]

    if not paths:
        return f"No encontré ningún archivo o carpeta llamado '{query}'."

    # Priorizar resultados en el home del usuario
    home = str(Path.home())
    home_paths = [p for p in paths if p.startswith(home)]
    best = home_paths[0] if home_paths else paths[0]

    subprocess.Popen(["open", best])
    name = Path(best).name
    parent = Path(best).parent
    return f"Abrí '{name}' (en {parent})"


# ── Tool declarations ─────────────────────────────────────────────────────────
TOOL_DECLS = [
    gt.FunctionDeclaration(
        name="open_thing",
        description=(
            "Abre una app, carpeta, archivo o página web. "
            "Usa esto para abrir cualquier cosa: apps por nombre ('el correo', 'fotos'), "
            "carpetas por nombre ('carpeta Prueba'), URLs, o rutas absolutas."
        ),
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={"target": gt.Schema(type=gt.Type.STRING,
                        description="Qué abrir: nombre de app, nombre de carpeta/archivo, o URL")},
            required=["target"],
        ),
    ),
    gt.FunctionDeclaration(
        name="create_folder",
        description=(
            "Crea una carpeta nueva y la abre. "
            "Si no se especifica ubicación, la crea en el Escritorio."
        ),
        parameters=gt.Schema(
            type=gt.Type.OBJECT,
            properties={
                "name":     gt.Schema(type=gt.Type.STRING, description="Nombre de la carpeta"),
                "location": gt.Schema(type=gt.Type.STRING,
                            description="Ruta donde crearla (opcional, por defecto ~/Desktop)"),
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
        description=(
            "Abre los periódicos de Esteban (El Correo, Marca, El Confidencial) "
            "en Chrome sin muros de pago. Úsala cuando diga 'prensa', 'periódicos' o 'noticias'."
        ),
        parameters=gt.Schema(type=gt.Type.OBJECT, properties={}),
    ),
]

# ── Tool handlers ─────────────────────────────────────────────────────────────
def open_thing(target: str) -> str:
    try:
        t = target.strip()

        # 1. URL
        if t.startswith("http://") or t.startswith("https://"):
            subprocess.Popen(["open", t])
            return f"Abriendo {t} en el navegador."

        # 2. Ruta absoluta
        if t.startswith("/") or t.startswith("~"):
            path = Path(t).expanduser()
            subprocess.Popen(["open", str(path)])
            return f"Abriendo {path.name}."

        # 3. Alias conocido de app
        real_app = _resolve_app(t)
        if real_app:
            if _open_app(real_app):
                return f"Abriendo {real_app}."
            return f"No pude abrir {real_app}."

        # 4. Intentar como nombre de app directamente
        if _open_app(t):
            return f"Abriendo {t}."

        # 5. Buscar como archivo/carpeta
        return _find_and_open(t)

    except Exception as e:
        return f"No pude abrir '{target}': {e}"


def create_folder(name: str, location: str = "~/Desktop") -> str:
    try:
        base = Path(location).expanduser()
        new_folder = base / name
        new_folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(new_folder)])
        return f"Carpeta '{name}' creada en {base} y abierta."
    except Exception as e:
        return f"No pude crear la carpeta: {e}"


def explain_screen() -> str:
    try:
        tmp = Path(tempfile.mktemp(suffix=".png"))
        subprocess.run(["screencapture", "-x", str(tmp)], timeout=5)
        if not tmp.exists():
            return "No pude capturar la pantalla."
        # Redimensionar a max 1280px para no saturar la API
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


# ── Prensa ────────────────────────────────────────────────────────────────────
CHROME      = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_DIR  = Path.home() / "Library/Application Support/Google/Chrome"
PRESS_PROFILE = "AITA_Prensa"
PRESS_URLS  = [
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

    js_exceptions = {
        domain: {"expiration": "0", "last_modified": "0", "model": 0, "setting": 2}
        for domain in PRESS_DOMAINS
    }
    prefs = {
        "profile": {"name": "AITA Prensa", "using_default_name": False},
        "content_settings": {"exceptions": {"javascript": js_exceptions}},
    }
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
        _setup_press_profile()
        cmd = [CHROME, f"--profile-directory={PRESS_PROFILE}"] + PRESS_URLS
        subprocess.Popen(cmd)
        return "Abriendo los periódicos sin muros de pago."
    except Exception as e:
        return f"No pude abrir los periódicos: {e}"


# ── Atajos directos (bypasan Gemini para mayor rapidez) ──────────────────────
SHORTCUTS: dict[str, callable] = {
    "prensa":       open_press,
    "periódicos":   open_press,
    "periodicos":   open_press,
    "noticias":     open_press,
    "los periódicos": open_press,
}

HANDLERS = {
    "open_thing":     lambda **kw: open_thing(**kw),
    "create_folder":  lambda **kw: create_folder(**kw),
    "explain_screen": lambda **kw: explain_screen(**kw),
    "open_press":     lambda **kw: open_press(),
}

# ── Gemini worker ─────────────────────────────────────────────────────────────
class GeminiWorker(QThread):
    done  = Signal(str)
    error = Signal(str)

    def __init__(self, message: str, audio_path: str | None = None):
        super().__init__()
        self._message    = message
        self._audio_path = audio_path

    def run(self):
        try:
            client = genai.Client(api_key=API_KEY)
            tools  = gt.Tool(function_declarations=TOOL_DECLS)
            config = gt.GenerateContentConfig(
                system_instruction=SYSTEM,
                tools=[tools],
                temperature=0.7,
            )

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

            contents = [gt.Content(role="user", parts=user_parts)]

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
                        # Screenshot especial
                        if isinstance(result, str) and result.startswith("__SCREENSHOT_B64__"):
                            screenshot_b64 = result[len("__SCREENSHOT_B64__"):]
                            result = "[captura tomada]"
                        tool_results.append(
                            gt.Part(function_response=gt.FunctionResponse(
                                name=fc.name, response={"result": result}
                            ))
                        )
                    contents.append(gt.Content(role="user", parts=tool_results))

                    # Si había screenshot, añadirlo al siguiente turno
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
    done  = Signal(str)   # ruta del wav
    error = Signal(str)

    def run(self):
        if not _VOICE_OK:
            self.error.emit("El micrófono no está disponible en este equipo.\nUsa el texto escribiendo (doble clic en el personaje).")
            return
        try:
            chunk = int(SAMPLE_RATE * 0.1)
            chunks = []
            silence_count = 0
            max_silence = int(SILENCE_SECONDS / 0.1)
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
            self.error.emit("No puedo acceder al micrófono.\nVe a Ajustes → Privacidad → Micrófono y activa AITA.")
        except Exception as e:
            self.error.emit(f"Error de micrófono: {e}")


# ── Speech bubble ─────────────────────────────────────────────────────────────
class BubbleWidget(QWidget):
    send_text = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.Tool)
        import sys as _sys
        self._transparent = not (_sys.platform == "darwin" and _sys.version_info < (3, 10))
        if self._transparent:
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(320)
        self._drag_pos = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)

        # Output
        self._output = QLabel("", self)
        self._output.setWordWrap(True)
        self._output.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._output.setFont(QFont("SF Pro Text", 13))
        self._output.setStyleSheet("color: #EBEBF5;")
        self._output.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        lay.addWidget(self._output)

        # Input row
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
            QPushButton {
                background: #0A84FF; color: white;
                border: none; border-radius: 16px;
            }
            QPushButton:hover { background: #409CFF; }
        """)
        btn.clicked.connect(self._send)
        row.addWidget(btn)
        lay.addLayout(row)

        self.adjustSize()

    def _send(self):
        txt = self._input.text().strip()
        if txt:
            self._input.clear()
            self.send_text.emit(txt)

    def show_response(self, text: str):
        self._output.setText(text)
        self.adjustSize()
        self.show()

    def show_thinking(self):
        self._output.setText("…")
        self.adjustSize()
        self.show()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.fillPath(path, C_BUBBLE)
        pen = QPen(QColor("#3A3A3C"), 1)
        p.setPen(pen)
        p.drawPath(path)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


# ── Main floating window ──────────────────────────────────────────────────────
_ICON_PATH = Path(__file__).parent / "aita_icon.png"
DOT_R      = 10   # radio del punto de estado

class AitaWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        import sys as _sys
        self._transparent = not (_sys.platform == "darwin" and _sys.version_info < (3, 10))
        if self._transparent:
            self.setAttribute(Qt.WA_TranslucentBackground)

        # Cargar imagen del personaje
        self._pixmap = QPixmap(str(_ICON_PATH)) if _ICON_PATH.exists() else QPixmap()
        iw = self._pixmap.width()  if not self._pixmap.isNull() else 80
        ih = self._pixmap.height() if not self._pixmap.isNull() else 80
        pad = 14  # margen para el punto de estado
        self.setFixedSize(iw + pad, ih + pad)

        # Estado
        self._color        = C_IDLE
        self._drag_pos     = None
        self._click_pos    = QPoint()
        self._listening    = False
        self._worker       = None
        self._voice_worker = None

        # Bubble
        self._bubble = BubbleWidget()
        self._bubble.send_text.connect(self._on_text)

        # Timer pulso (escuchando)
        self._pulse      = 0.0
        self._pulse_dir  = 1
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)

        # Posición inicial: esquina inferior derecha
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

    # ── Pintar ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # En modo no-transparente (Python 3.9 macOS) limpiar fondo explícitamente
        if not self._transparent:
            p.fillRect(self.rect(), QColor(30, 30, 30, 220))

        # Pulso de escucha: halo de color alrededor del personaje
        if self._listening and self._pulse > 0:
            halo = QColor(self._color)
            halo.setAlpha(int(self._pulse * 100))
            p.setBrush(halo)
            p.setPen(Qt.NoPen)
            hw = self._pixmap.width()  + 20
            hh = self._pixmap.height() + 20
            p.drawRoundedRect(
                (self.width()  - hw) // 2,
                (self.height() - hh) // 2,
                hw, hh, 12, 12,
            )

        # Imagen del personaje (con alpha del PNG) o círculo de fallback
        if not self._pixmap.isNull():
            ox = (self.width()  - self._pixmap.width())  // 2
            oy = (self.height() - self._pixmap.height()) // 2
            p.drawPixmap(ox, oy, self._pixmap)
        else:
            r = 28
            cx, cy = self.width() // 2, self.height() // 2
            p.setBrush(self._color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(QFont("SF Pro Text", 20, QFont.Bold))
            p.drawText(QRect(cx - r, cy - r, r * 2, r * 2), Qt.AlignCenter, "A")

        # Punto de estado (esquina inferior derecha)
        dot_x = self.width()  - DOT_R - 2
        dot_y = self.height() - DOT_R - 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawEllipse(dot_x - 1, dot_y + 1, DOT_R, DOT_R)
        p.setBrush(self._color)
        p.drawEllipse(dot_x, dot_y, DOT_R, DOT_R)

    def _tick(self):
        self._pulse += self._pulse_dir * 0.07
        if self._pulse >= 1.0:
            self._pulse_dir = -1
        elif self._pulse <= 0.0:
            self._pulse_dir = 1
        self.update()

    # ── Drag ──────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos  = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._click_pos = e.globalPosition().toPoint()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            moved = (e.globalPosition().toPoint() - self._click_pos).manhattanLength()
            if moved < 5:
                self._on_click()   # clic izquierdo = voz
            self._drag_pos = None
        elif e.button() == Qt.RightButton:
            # clic derecho = toggle bocadillo de texto
            if self._bubble.isVisible():
                self._bubble.hide()
            else:
                self._reposition_bubble()
                self._bubble.show()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            self._reposition_bubble()

    # ── Lógica de clic ───────────────────────────────────────────────────────
    def _on_click(self):
        if self._listening:
            self._stop_listening()
        elif self._worker and self._worker.isRunning():
            pass  # pensando, ignorar
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
        shortcut = SHORTCUTS.get(text.strip().lower())
        if shortcut:
            result = shortcut()
            self._bubble.show_response(result)
            self._reposition_bubble()
            return
        self._run_gemini(message=text)

    def _run_gemini(self, message: str, audio_path: str | None = None):
        self._set_color(C_THINK)
        self._bubble.show_thinking()
        self._reposition_bubble()
        self._worker = GeminiWorker(message, audio_path)
        self._worker.done.connect(self._on_response)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_response(self, text: str):
        self._set_color(C_DONE)
        self._bubble.show_response(text)
        self._reposition_bubble()
        self.show()
        self.raise_()
        QTimer.singleShot(1500, lambda: self._set_color(C_IDLE))

    def _on_error(self, err: str):
        self._set_color(C_IDLE)
        self._anim_timer.stop()
        self._listening = False
        self._bubble.show_response(f"Error: {err}")
        self._reposition_bubble()
        self.show()
        self.raise_()

    def _set_color(self, c: QColor):
        self._color = c
        self.update()

    def _reposition_bubble(self):
        geo    = self.frameGeometry()
        screen = QApplication.primaryScreen().availableGeometry()
        bw     = self._bubble.width()
        bh     = self._bubble.height()

        # Intentar a la izquierda del botón
        bx = geo.left() - bw - 12
        if bx < screen.left():
            bx = geo.right() + 12  # a la derecha

        # Vertical: centrado con el botón
        by = geo.top() + (geo.height() - bh) // 2
        by = max(screen.top() + 8, min(by, screen.bottom() - bh - 8))

        self._bubble.move(bx, by)


# ── Instancia única ───────────────────────────────────────────────────────────
_LOCK_SOCK: socket.socket | None = None

def _acquire_instance_lock() -> bool:
    """Devuelve True si somos la única instancia, False si ya hay una corriendo."""
    global _LOCK_SOCK
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", 54782))
        s.listen(1)
        _LOCK_SOCK = s   # mantener referencia para que no se cierre
        return True
    except OSError:
        return False     # puerto ocupado → ya hay otra instancia


# ── Autostart (LaunchAgent) ───────────────────────────────────────────────────
def setup_autostart() -> None:
    plist_dir  = Path.home() / "Library/LaunchAgents"
    plist_path = plist_dir / "com.aita.app.plist"
    script_dir = Path(__file__).resolve().parent
    run_cmd    = script_dir / "run.command"

    # Verificar si ya apunta al run.command correcto
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
    <key>Label</key>
    <string>com.aita.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>{run_cmd}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{script_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{script_dir}/aita.log</string>
    <key>StandardErrorPath</key>
    <string>{script_dir}/aita.log</string>
</dict>
</plist>
"""
    plist_path.write_text(plist, encoding="utf-8")
    uid = os.getuid()
    # Desregistrar versión anterior (si existía) y registrar la nueva
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
        capture_output=True,
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        capture_output=True,
    )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not _acquire_instance_lock():
        sys.exit(0)   # ya hay una instancia corriendo, salir silenciosamente

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    setup_autostart()

    win = AitaWindow()
    sys.exit(app.exec())

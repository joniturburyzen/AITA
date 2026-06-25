#!/bin/bash
cd "$(dirname "$0")"

# ── Buscar Python 3.9+ ────────────────────────────────────────────────────────
PYTHON=""
for py in python3 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$py" &>/dev/null; then
        ver=$("$py" -c "import sys; print(sys.version_info >= (3,9))" 2>/dev/null)
        if [ "$ver" = "True" ]; then
            PYTHON="$py"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    osascript -e 'display alert "AITA — Error" message "No se encontró Python 3.9 o superior.\nDescárgalo desde python.org e inténtalo de nuevo." buttons {"Entendido"} as critical'
    exit 1
fi

# ── Instalar dependencias si hacen falta ──────────────────────────────────────
NEEDS_INSTALL=false
for pkg in PySide6 google.genai sounddevice soundfile numpy dotenv PIL; do
    if ! "$PYTHON" -c "import $pkg" &>/dev/null 2>&1; then
        NEEDS_INSTALL=true
        break
    fi
done

if [ "$NEEDS_INSTALL" = "true" ]; then
    osascript -e 'display notification "Preparando AITA, un momento…" with title "AITA"'
    "$PYTHON" -m pip install -q --break-system-packages -r requirements.txt 2>/dev/null \
    || "$PYTHON" -m pip install -q -r requirements.txt 2>/dev/null
fi

# ── Acceso directo en el Escritorio (solo la primera vez) ────────────────────
AITA_DIR="$(cd "$(dirname "$0")" && pwd)"
SHORTCUT="$HOME/Desktop/Abrir AITA.command"
if [ ! -f "$SHORTCUT" ]; then
    printf '#!/bin/bash\nexec "%s/run.command"\n' "$AITA_DIR" > "$SHORTCUT"
    chmod +x "$SHORTCUT"
fi

# ── Lanzar AITA ───────────────────────────────────────────────────────────────
exec "$PYTHON" aita.py

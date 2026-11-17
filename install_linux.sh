#!/usr/bin/env bash
# ETS2 Radio DJ — установка на Linux: изолированный venv + команда ets2radio.
# Повторный запуск обновляет код и зависимости, личные данные не трогает.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${ETS2RADIO_HOME:-$HOME/.local/share/ets2-radio-dj}"
BIN_DIR="$HOME/.local/bin"
PY="${PYTHON:-python3}"

say() { printf '  %s\n' "$*"; }
fail() { printf '\nОшибка: %s\n' "$*" >&2; exit 1; }

printf '\n=== ETS2 Radio DJ: установка для Linux ===\n\n'

# --- 1. Зависимости системы ---
command -v "$PY" >/dev/null 2>&1 || fail "не найден python3. На Bazzite: sudo rpm-ostree install python3"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "нужен Python 3.10+, сейчас $("$PY" -V 2>&1)"
command -v ffmpeg >/dev/null 2>&1 || fail "не найден ffmpeg. На Bazzite: sudo rpm-ostree install ffmpeg"
say "Python: $("$PY" -V 2>&1)"
say "ffmpeg: $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3)"

# --- 2. Файлы (только рантайм; config.json, история и токены не затираются) ---
mkdir -p "$INSTALL_DIR/static" "$BIN_DIR"
for f in server.py index.html overlay.html requirements.txt LICENSE; do
  cp -f "$SRC_DIR/$f" "$INSTALL_DIR/$f"
done
cp -f "$SRC_DIR"/static/* "$INSTALL_DIR/static/"
say "Файлы: $INSTALL_DIR"

# --- 3. venv + зависимости ---
[ -d "$INSTALL_DIR/.venv" ] || "$PY" -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install -q --disable-pip-version-check \
  -r "$INSTALL_DIR/requirements.txt"
say "Зависимости: OK"

# --- 4. Команда ets2radio ---
cat > "$BIN_DIR/ets2radio" <<EOF
#!/bin/sh
exec "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/server.py" "\$@"
EOF
chmod +x "$BIN_DIR/ets2radio"
say "Команда: $BIN_DIR/ets2radio"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "ВНИМАНИЕ: $BIN_DIR не в PATH — добавь строку: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

cat <<EOF

Готово.
  Запуск:      ets2radio
  Интерфейс:   http://127.0.0.1:5000
  OBS:         http://127.0.0.1:5000/overlay

Радио в игру: вкладка «Настройки» → «Прописать» (live_streams.sii найдётся
в нативной Linux-версии ETS2/ATS или в Proton-префиксе Steam).
Остановка: Ctrl+C в терминале, где запущен ets2radio.
EOF

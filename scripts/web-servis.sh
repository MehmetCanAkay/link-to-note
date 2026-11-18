#!/bin/zsh
# Web arayüzünü macOS açılışında arka planda başlatan LaunchAgent.
# Kullanım: scripts/web-servis.sh kur | kaldir | yenile | durum
set -euo pipefail

PROJE="${0:A:h:h}"
ETIKET="com.videotoinfo.web"
PLIST="$HOME/Library/LaunchAgents/$ETIKET.plist"
LOG_KLASOR="$HOME/Library/Logs/VideoToInfo"
PORT="${VTI_PORT:-8765}"
HEDEF="gui/$(id -u)"

paketi_kur() {
  # Paket editable olmadan kurulu; kod değişince yeniden kurulmalı.
  (cd "$PROJE" && UV_NO_EDITABLE=1 uv sync --no-editable --group dev --reinstall-package videotoinfo >/dev/null)
}

xml_kacis() {
  local s="$1"
  s=${s//&/&amp;}
  s=${s//</&lt;}
  s=${s//>/&gt;}
  print -r -- "$s"
}

# Kabukta tanımlı değişkenler. LaunchAgent ~/.zshrc okumaz.
langfuse_ortam() {
  local ad deger
  for ad in LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_BASE_URL LANGFUSE_TRACING_ENABLED VIDEOTOINFO_CONFIG; do
    if [[ -v $ad ]]; then
      deger="${(P)ad}"
      print -r -- "    <key>${ad}</key><string>$(xml_kacis "$deger")</string>"
    fi
  done
}

plist_yaz() {
  mkdir -p "$LOG_KLASOR" "$HOME/Library/LaunchAgents"
  local langfuse
  langfuse="$(langfuse_ortam)"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ETIKET</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJE/.venv/bin/vti</string>
    <string>web</string>
    <string>--acma</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJE</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
${langfuse}
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>Nice</key><integer>5</integer>
  <key>StandardOutPath</key><string>$LOG_KLASOR/web.log</string>
  <key>StandardErrorPath</key><string>$LOG_KLASOR/web.log</string>
</dict>
</plist>
EOF
}

durdur() {
  launchctl bootout "$HEDEF/$ETIKET" 2>/dev/null || true
}

case "${1:-}" in
  kur)
    paketi_kur
    plist_yaz
    durdur
    launchctl bootstrap "$HEDEF" "$PLIST"
    echo "Kuruldu. Arayüz: http://127.0.0.1:$PORT  (log: $LOG_KLASOR/web.log)"
    ;;
  yenile)
    paketi_kur
    launchctl kickstart -k "$HEDEF/$ETIKET"
    echo "Yeniden başlatıldı: http://127.0.0.1:$PORT"
    ;;
  kaldir)
    durdur
    rm -f "$PLIST"
    echo "Kaldırıldı."
    ;;
  durum)
    launchctl print "$HEDEF/$ETIKET" 2>/dev/null | grep -E "state|pid|last exit" || echo "Kurulu değil."
    ;;
  *)
    echo "Kullanım: $0 kur | kaldir | yenile | durum" >&2
    exit 1
    ;;
esac

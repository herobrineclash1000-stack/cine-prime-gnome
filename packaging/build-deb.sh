#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(sed -n 's/^version = "\([^"]*\)"$/\1/p' "$ROOT/pyproject.toml" | head -n 1)
ARCH=all
STAGE="$ROOT/dist/deb-root"
OUT="$ROOT/dist/cineprime_${VERSION}_${ARCH}.deb"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/lib/cineprime" "$STAGE/usr/bin" \
  "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/scalable/apps" \
  "$STAGE/usr/share/metainfo" "$STAGE/usr/share/doc/cineprime" \
  "$STAGE/usr/share/cineprime/branding"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: cineprime
Version: $VERSION-1
Section: video
Priority: optional
Architecture: $ARCH
Maintainer: Cine Prime contributors
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, gstreamer1.0-tools, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-plugins-ugly, gstreamer1.0-libav, gstreamer1.0-gtk4, ffmpeg
Description: Biblioteca pessoal de filmes e séries
 Aplicação GTK4/libadwaita para organizar e reproduzir filmes e séries
 com GStreamer integrado, pesquisa, favoritos e retoma de reprodução.
CONTROL
for module in app.py main.py config.py database.py scanner.py classifier.py covers.py posters.py gstreamer_player.py player_controls.py effects.py media_integrity.py backup_manager.py; do
  install -m 0644 "$ROOT/$module" "$STAGE/usr/lib/cineprime/$module"
done
cat > "$STAGE/usr/bin/cineprime" <<'LAUNCHER'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/cineprime/app.py "$@"
LAUNCHER
chmod 0755 "$STAGE/usr/bin/cineprime"
install -m 0644 "$ROOT/data/io.github.helder.cineprime.desktop" "$STAGE/usr/share/applications/io.github.helder.cineprime.desktop"
install -m 0644 "$ROOT/data/icons/hicolor/scalable/apps/io.github.helder.cineprime.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/io.github.helder.cineprime.svg"
install -m 0644 "$ROOT/data/metainfo/io.github.helder.cineprime.metainfo.xml" "$STAGE/usr/share/metainfo/io.github.helder.cineprime.metainfo.xml"
install -m 0644 "$ROOT/data/branding/cine-prime-wordmark.svg" "$STAGE/usr/share/cineprime/branding/cine-prime-wordmark.svg"
install -m 0644 "$ROOT/README.md" "$STAGE/usr/share/doc/cineprime/README.md"
mkdir -p "$ROOT/dist"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
printf 'Built: %s\n' "$OUT"
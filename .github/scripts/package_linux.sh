#!/usr/bin/env bash
# Turn the linuxdeploy AppDir into .deb and .rpm packages.
#
# Why the AppDir and not dist/BabelChat: the PyInstaller onedir is a *partial*
# bundle. collect_all("gi") drags in libgtk-4.so.1, libgdk_pixbuf and libtiff
# from the build host, and those link against libjpeg.so.8 — an Ubuntu soname
# that does not exist on Debian 13 (libjpeg.so.62) or Fedora. Packaging the
# onedir produced a .deb that installed cleanly and could not start. The AppDir
# is what linuxdeploy already made self-contained, so that is what ships.
#
# Consequence: the packages are fat and carry almost no dependencies. For an
# app distributed through GitHub Releases rather than a distro archive, that is
# the right trade — one payload to test instead of a per-distro dependency
# matrix.
#
# Usage: package_linux.sh <version> <outdir>
set -euo pipefail

VERSION="${1:?usage: package_linux.sh <version> <outdir>}"
OUTDIR="${2:?usage: package_linux.sh <version> <outdir>}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APPDIR="$ROOT/AppDir"
STAGE="$ROOT/.pkgroot"

[ -d "$APPDIR" ] || { echo "ERROR: $APPDIR not found - run build-linux.fish first" >&2; exit 1; }
mkdir -p "$OUTDIR"

# ── staging tree ────────────────────────────────────────────────────────────
rm -rf "$STAGE"
install -d "$STAGE/opt/babelchat" "$STAGE/usr/bin" \
           "$STAGE/usr/share/applications" \
           "$STAGE/usr/share/icons/hicolor/256x256/apps"

cp -a "$APPDIR/." "$STAGE/opt/babelchat/"

# Launcher. AppRun's GTK hook derives APPDIR from $0, which resolves correctly
# through the symlink, but we set it explicitly rather than depend on that.
#
# LD_PRELOAD is set here and not left to the AppDir's own hook: linuxdeploy
# generates apprun-hooks/ (hyphen) and sources exactly one named file from it,
# while build-linux.fish writes its preload into apprun_hooks/ (underscore).
# That hook is never sourced. gtk4-layer-shell must be loaded before
# libwayland-client for its interposition to work, so without this the Wayland
# overlay silently degrades to a plain window.
cat > "$STAGE/usr/bin/babelchat" <<'LAUNCHER'
#!/bin/sh
APPDIR=/opt/babelchat
export APPDIR

# LD_LIBRARY_PATH must be set together with LD_PRELOAD, never without it.
# AppRun's shebang is `/usr/bin/env bash`, and LD_PRELOAD is inherited across
# that exec: preloading a bundled library while the system loader has no way to
# find its dependencies kills /usr/bin/env before bash ever starts, with
# "libharfbuzz.so.0: cannot open shared object file".
LD_LIBRARY_PATH="$APPDIR/usr/lib:$APPDIR/usr/bin/_internal${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH

for lsh in "$APPDIR/usr/lib/libgtk4-layer-shell.so.0" "$APPDIR/usr/lib/libgtk4-layer-shell.so"; do
    if [ -f "$lsh" ]; then
        LD_PRELOAD="$lsh${LD_PRELOAD:+:$LD_PRELOAD}"
        export LD_PRELOAD
        break
    fi
done
exec "$APPDIR/AppRun" "$@"
LAUNCHER
chmod 0755 "$STAGE/usr/bin/babelchat"

sed 's|^Exec=.*|Exec=babelchat|' "$ROOT/packaging/babelchat.desktop" \
    > "$STAGE/usr/share/applications/babelchat.desktop"
cp "$APPDIR/usr/share/icons/hicolor/256x256/apps/babelchat.png" \
   "$STAGE/usr/share/icons/hicolor/256x256/apps/babelchat.png"

COMMON=(
  -s dir -C "$STAGE"
  --name babelchat
  --version "$VERSION"
  --license MIT
  --vendor "BabelChat"
  --description "Real-time World of Warcraft chat translation overlay"
  --url "https://github.com/Yumash/BabelChat"
  --maintainer "BabelChat maintainers"
  --force
)

# The bundle carries its own GTK, so there is no GTK dependency to declare.
# What it does not carry is the handful of libraries linuxdeploy deliberately
# leaves to the host (its excludelist). Those four came out of `ldd` against the
# installed payload, not out of a guess:
#   libxcb.so.1  libwayland-client.so.0  libcom_err.so.2  libgpg-error.so.0
# Any desktop has them; a minimal container does not, which is precisely why
# the smoke test runs in one.
fpm "${COMMON[@]}" -t deb \
  --depends "libxcb1" \
  --depends "libwayland-client0" \
  --depends "libcom-err2" \
  --depends "libgpg-error0" \
  --deb-recommends "dbus" \
  --package "$OUTDIR/babelchat_${VERSION}_amd64.deb" \
  opt usr

fpm "${COMMON[@]}" -t rpm \
  --depends "libxcb" \
  --depends "libwayland-client" \
  --depends "libcom_err" \
  --depends "libgpg-error" \
  --package "$OUTDIR/babelchat-${VERSION}-1.x86_64.rpm" \
  opt usr

rm -rf "$STAGE"
ls -la "$OUTDIR"

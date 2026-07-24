#!/usr/bin/env bash
# Install a built package with the distro's own package manager and check that
# what it dropped on disk can actually run.
#
# Building an artefact proves nothing about installing it: dependency names
# differ per distro, and a payload built on one glibc can land on another. This
# script is meant to be run inside a clean container for each target distro.
#
# Usage: smoke_install.sh <path-to-.deb-or-.rpm>
set -euo pipefail

PKG="${1:?usage: smoke_install.sh <package>}"
[ -f "$PKG" ] || { echo "ERROR: no such package: $PKG" >&2; exit 1; }

echo "== distro =="
. /etc/os-release && echo "$PRETTY_NAME"
# `| head` makes the producer die of SIGPIPE, and under `set -o pipefail` that
# aborts the whole script. Swallow it: these lines are informational.
ldd --version 2>&1 | head -1 || true

echo
echo "== install (dependency resolution is the point) =="
case "$PKG" in
  *.deb)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    # apt resolves declared dependencies; a missing one fails here, which is
    # exactly the failure we want surfaced.
    apt-get install -y -qq "$PKG"
    ;;
  *.rpm)
    dnf install -y -q "$PKG"
    ;;
  *)
    echo "ERROR: unknown package type: $PKG" >&2; exit 1 ;;
esac

echo
echo "== launcher =="
test -x /usr/bin/babelchat  || { echo "ERROR: /usr/bin/babelchat missing or not executable"; exit 1; }
test -x /opt/babelchat/AppRun || { echo "ERROR: AppRun missing or not executable"; exit 1; }
test -x /opt/babelchat/usr/bin/BabelChat || { echo "ERROR: payload binary missing"; exit 1; }
echo "launcher and AppRun present"

echo
echo "== the layer-shell preload the launcher promises =="
# build-linux.fish writes its preload hook into apprun_hooks/ (underscore),
# while linuxdeploy sources one named file out of apprun-hooks/ (hyphen), so
# that hook never runs. The package launcher sets LD_PRELOAD itself; check the
# library it points at is actually shipped, or the promise is empty.
if ls /opt/babelchat/usr/lib/libgtk4-layer-shell.so* >/dev/null 2>&1; then
    grep -q 'LD_PRELOAD' /usr/bin/babelchat || { echo "ERROR: launcher does not set LD_PRELOAD"; exit 1; }
    echo "gtk4-layer-shell bundled and preloaded by the launcher"
else
    echo "WARNING: gtk4-layer-shell not bundled - Wayland overlay will fall back to X11"
fi

echo
echo "== shared libraries resolve =="
# `ldd` prints "not found" for every unsatisfied NEEDED entry. Anything unmet
# here means the package installed cleanly and still cannot start.
#
# Resolve the way the app actually resolves: the bundle keeps its libraries in
# two directories and PyInstaller's bootloader points at _internal at runtime.
# A bare `ldd` sees neither and reports the whole bundle as broken.
export LD_LIBRARY_PATH="/opt/babelchat/usr/lib:/opt/babelchat/usr/bin/_internal${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
missing=0
check_ldd() {
    local f="$1" out
    out=$(ldd "$f" 2>/dev/null | grep 'not found' || true)
    if [ -n "$out" ]; then
        echo "UNRESOLVED in $f:"; echo "$out"; missing=1
    fi
}
check_ldd /opt/babelchat/usr/bin/BabelChat
while IFS= read -r so; do check_ldd "$so"; done < <(find /opt/babelchat \( -name '*.so' -o -name '*.so.*' \) -print)
[ "$missing" -eq 0 ] || { echo "ERROR: unresolved shared libraries"; exit 1; }
echo "no unresolved libraries"

echo
echo "== the memory scanner is present =="
find /opt/babelchat -name 'libbabelchat_scanner.so' -print -quit | grep -q . \
  || { echo "ERROR: libbabelchat_scanner.so not shipped"; exit 1; }

echo
echo "== the bundled GTK stack is complete =="
# These packages carry their own GTK rather than depending on the distro's, so
# the thing to verify is that the bundle is whole: the typelibs gi resolves at
# runtime and the pixbuf loader cache both have to be there.
for f in usr/lib/girepository-1.0/Gtk-4.0.typelib \
         usr/lib/girepository-1.0/Gio-2.0.typelib \
         usr/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache; do
    test -e "/opt/babelchat/$f" || { echo "ERROR: missing from bundle: $f"; exit 1; }
done
echo "typelibs and pixbuf loader cache present"

echo
echo "== the binary starts far enough to load its libraries =="
# No display here, so the app cannot come up — but a dynamic-linker failure
# looks nothing like a missing display, and that is the difference worth
# catching. 127/126 or a loader message means the payload does not match this
# distro; anything else means libraries resolved and the app got as far as
# needing a screen.
set +e
out=$(timeout 30 /usr/bin/babelchat --help 2>&1); rc=$?
set -e
echo "exit=$rc"
echo "$out" | head -5
case "$out" in
  *"error while loading shared libraries"*|*"symbol lookup error"*|*"cannot open shared object"*)
      echo "ERROR: dynamic linker could not satisfy the payload"; exit 1 ;;
esac
[ "$rc" -eq 127 ] && { echo "ERROR: launcher not executable / interpreter missing"; exit 1; }
[ "$rc" -eq 126 ] && { echo "ERROR: launcher not runnable"; exit 1; }
echo "no loader failure"

echo
echo "== desktop entry =="
test -f /usr/share/applications/babelchat.desktop || { echo "ERROR: desktop entry missing"; exit 1; }
# desktop-file-validate is a test-time tool, not a runtime dependency of the
# package — its absence must not fail a run that is otherwise healthy.
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate /usr/share/applications/babelchat.desktop && echo "desktop entry valid"
else
    echo "desktop entry present (desktop-file-validate unavailable, skipping validation)"
fi

echo
echo "SMOKE OK"

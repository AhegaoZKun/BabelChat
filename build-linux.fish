#!/usr/bin/env fish
# Build BabelChat as a self-contained ("fat") AppImage.
#
# Pipeline:
#   1. compile the Rust memory scanner (cdylib -> libbabelchat_scanner.so)
#   2. copy it to app/ where the loader + spec expect it
#   3. PyInstaller (onedir) -> dist/BabelChat/
#   4. assemble an AppDir
#   5. linuxdeploy + GTK plugin bundles the whole GTK stack -> *.AppImage
#
# Usage:  ./build-linux.fish
# Run from the project root with your venv activated.
#
# One-time tooling (place on PATH or in ./tools/, see TOOLS_DIR below):
#   - linuxdeploy-x86_64.AppImage
#   - linuxdeploy-plugin-gtk.sh
# Get them from:
#   https://github.com/linuxdeploy/linuxdeploy/releases
#   https://github.com/linuxdeploy/linuxdeploy-plugin-gtk/releases

set -l root (status dirname)
cd $root; or exit 1

set -l APPNAME BabelChat
set -l TOOLS_DIR $root/tools   # where linuxdeploy + gtk plugin live (or PATH)

# ── 0. venv check ─────────────────────────────────────────────────────────
if not set -q VIRTUAL_ENV
    echo "⚠  No virtualenv active. Activate it first:"
    echo "     source .venv/bin/activate.fish"
    echo "   (continuing in 3s — Ctrl-C to abort)"
    sleep 3
end

# ── 1. Rust scanner ───────────────────────────────────────────────────────
echo "▶  Building Rust scanner (release)…"
cargo build --release --manifest-path babelchat_scanner_linux/Cargo.toml
or begin; echo "✗  cargo build failed."; exit 1; end

set -l so babelchat_scanner_linux/target/release/libbabelchat_scanner.so
if not test -f $so
    echo "✗  Expected $so but it wasn't produced."; exit 1
end
cp $so app/libbabelchat_scanner.so
echo "▶  Copied scanner → app/libbabelchat_scanner.so"

# ── 2. PyInstaller (onedir) ───────────────────────────────────────────────
echo "▶  Running PyInstaller (onedir)…"
python -m PyInstaller --noconfirm build-linux.spec
or begin; echo "✗  PyInstaller failed."; exit 1; end

if not test -d dist/$APPNAME
    echo "✗  Expected dist/$APPNAME/ (onedir output) — is the spec set to onedir?"; exit 1
end

# ── 3. assemble AppDir ────────────────────────────────────────────────────
echo "▶  Assembling AppDir…"
set -l APPDIR $root/AppDir
rm -rf $APPDIR
mkdir -p $APPDIR/usr/bin
mkdir -p $APPDIR/usr/share/applications
mkdir -p $APPDIR/usr/share/icons/hicolor/256x256/apps

# PyInstaller onedir payload → usr/bin
cp -r dist/$APPNAME/* $APPDIR/usr/bin/

# gtk4-layer-shell is NOT part of the standard GTK stack the plugin bundles.
# PyInstaller already put it in the onedir, but also place it in usr/lib so the
# runtime loader (and the _MEIPASS/system fallback in overlay_gtk) finds it.
mkdir -p $APPDIR/usr/lib
for cand in /usr/lib/libgtk4-layer-shell.so* /usr/lib/*/libgtk4-layer-shell.so*
    if test -f $cand
        cp $cand $APPDIR/usr/lib/
    end
end
# Its typelib, likewise, so gi can find Gtk4LayerShell.
mkdir -p $APPDIR/usr/lib/girepository-1.0
for cand in /usr/lib/girepository-1.0/Gtk4LayerShell-1.0.typelib /usr/lib/*/girepository-1.0/Gtk4LayerShell-1.0.typelib
    if test -f $cand
        cp $cand $APPDIR/usr/lib/girepository-1.0/
    end
end

# desktop file + icon (names must match: Icon=babelchat)
cp packaging/babelchat.desktop $APPDIR/usr/share/applications/babelchat.desktop
cp assets/icon.png $APPDIR/usr/share/icons/hicolor/256x256/apps/babelchat.png

# ── 4. locate tooling ─────────────────────────────────────────────────────
set -l LD (command -v linuxdeploy-x86_64.AppImage; or echo $TOOLS_DIR/linuxdeploy-x86_64.AppImage)
set -l GTKPLUGIN (command -v linuxdeploy-plugin-gtk.sh; or echo $TOOLS_DIR/linuxdeploy-plugin-gtk.sh)
if not test -f $LD
    echo "✗  linuxdeploy not found. Put linuxdeploy-x86_64.AppImage on PATH or in $TOOLS_DIR/"
    exit 1
end
if not test -f $GTKPLUGIN
    echo "✗  GTK plugin not found. Put linuxdeploy-plugin-gtk.sh on PATH or in $TOOLS_DIR/"
    exit 1
end
chmod +x $LD $GTKPLUGIN

# ── 5. run linuxdeploy + GTK plugin → AppImage ────────────────────────────
echo "▶  Bundling GTK stack + building AppImage…"
# The gtk plugin reads these; keep PATH visible to it.
set -x DEPLOY_GTK_VERSION 4
env PATH="$TOOLS_DIR:$PATH" $LD \
    --appdir $APPDIR \
    --plugin gtk \
    --desktop-file $APPDIR/usr/share/applications/babelchat.desktop \
    --icon-file $APPDIR/usr/share/icons/hicolor/256x256/apps/babelchat.png \
    --executable $APPDIR/usr/bin/$APPNAME \
    --output appimage
or begin; echo "✗  linuxdeploy/AppImage build failed."; exit 1; end

echo ""
echo "✓  Done. AppImage in project root:"
ls -1 $root/*.AppImage 2>/dev/null
echo "   Test it:  ./"(basename (ls -1 $root/*.AppImage 2>/dev/null | head -1))

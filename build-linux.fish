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
# linuxdeploy only accepts standard icon resolutions — scale to exact 256x256
# (source is 784x784). GdkPixbuf is guaranteed present on a GTK4 host.
python -c "
import gi; gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf
pb = GdkPixbuf.Pixbuf.new_from_file_at_scale('assets/icon.png', 256, 256, True)
pb.savev('$APPDIR/usr/share/icons/hicolor/256x256/apps/babelchat.png', 'png', [], [])
"
or begin; echo "✗  Icon scaling failed."; exit 1; end

# gtk4-layer-shell loading, the real story. app/overlay_gtk.py dlopen()s the
# bundled libgtk4-layer-shell before gi pulls in GTK (correct link order), and
# build-linux.spec stages it under the exact soname the loader asks for. What
# that dlopen still needs is a library search path: layer-shell's own deps
# (libwayland-egl, libgtk-4) live in the bundled usr/lib and the PyInstaller
# _internal dir, and neither is on the loader path when the app starts.
#
# The previous approach dropped an LD_PRELOAD script into apprun_hooks/ (note
# the underscore) on the belief that "linuxdeploy sources every file in
# apprun_hooks/". It does not: the AppRun linuxdeploy generates sources exactly
# one file, apprun-hooks/ (hyphen) /linuxdeploy-plugin-gtk.sh. That preload
# never ran, and the AppImage died with "Could not load libgtk4-layer-shell.so"
# on any host without the system library. So instead of a hook nobody sources,
# the LD_LIBRARY_PATH is appended to the hook that IS sourced — see step 4's
# plugin patch below.

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

# Patch the GTK plugin for modern GTK4 hosts (e.g. Arch): they ship no
# /usr/lib/gtk-4.0 module dir, and the plugin's unconditional copy of it is
# fatal. Make it conditional. Idempotent; only touches a writable local copy.
if test -w $GTKPLUGIN; and grep -q 'copy_lib_tree "$gtk4_libdir" "$APPDIR/"' $GTKPLUGIN
    sed -i 's|copy_lib_tree "$gtk4_libdir" "$APPDIR/"|if [ -d "$gtk4_libdir" ]; then copy_lib_tree "$gtk4_libdir" "$APPDIR/"; else echo "No GTK4 module dir on host - skipping"; fi|' $GTKPLUGIN
    echo "▶  Patched GTK plugin: GTK4 module copy is now conditional"
end
# The plugin's AppRun hook hard-forces GDK_BACKEND=x11 (a GTK3-era Wayland
# crash workaround) which makes layer-shell impossible — the overlay came up
# as a plain X11 window. Only force x11 when there is no Wayland session.
if test -w $GTKPLUGIN; and grep -q '^export GDK_BACKEND=x11' $GTKPLUGIN
    sed -i 's|^export GDK_BACKEND=x11.*|if [ -z "$WAYLAND_DISPLAY" ]; then export GDK_BACKEND=x11; fi|' $GTKPLUGIN
    echo "▶  Patched GTK plugin: GDK_BACKEND=x11 now only on non-Wayland sessions"
end
# The plugin's generated hook (apprun-hooks/linuxdeploy-plugin-gtk.sh) is the
# ONE file the generated AppRun sources, but it never sets LD_LIBRARY_PATH — so
# overlay_gtk's dlopen of layer-shell can't reach the deps bundled in usr/lib
# and _internal. Append that path to the hook the plugin writes. The plugin
# builds $HOOKFILE top-to-bottom and the variable is still in scope at its end,
# so appending this block to the plugin runs after its own writes. Idempotent.
if test -w $GTKPLUGIN; and not grep -q 'BabelChat LD_LIBRARY_PATH' $GTKPLUGIN
    printf '\n%s\n' \
        '# BabelChat: let overlay_gtk dlopen the bundled layer-shell + its deps' \
        'cat >> "$HOOKFILE" <<'\''BCEOF'\''' \
        '# BabelChat LD_LIBRARY_PATH: bundled usr/lib + PyInstaller _internal' \
        'export LD_LIBRARY_PATH="$APPDIR/usr/lib:$APPDIR/usr/bin/_internal${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"' \
        'BCEOF' \
        >> $GTKPLUGIN
    echo "▶  Patched GTK plugin: hook now exports LD_LIBRARY_PATH for bundled libs"
end

# ── 5. run linuxdeploy + GTK plugin → AppImage ────────────────────────────
echo "▶  Bundling GTK stack + building AppImage…"
# The gtk plugin reads these; keep PATH visible to it.
set -x DEPLOY_GTK_VERSION 4
# Arch libs carry .relr.dyn sections that linuxdeploy's bundled strip can't
# parse; skip stripping (cosmetic, slightly larger AppImage).
set -x NO_STRIP 1
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

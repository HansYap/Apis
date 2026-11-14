#!/bin/sh
# Add Apis to the GNOME app grid (and other freedesktop menus) for the current user.
# Run it again after moving the Apis folder.
set -eu
here=$(dirname "$(readlink -f "$0")")
case $here in
  *[\"\`\$\\%]*)
    echo "Move Apis to a folder whose path has no quotes, \$, \\ or % characters, then run this again." >&2
    exit 1 ;;
esac
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
mkdir -p "$apps"
cat > "$apps/apis.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Apis
Comment=Turn YouTube videos into connected Obsidian notes
Exec="$here/start-apis.sh"
Icon=$here/assets/apis.png
Terminal=false
Categories=Office;
StartupWMClass=apis
EOF
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$apps" 2>/dev/null || true
fi
echo "Apis is now in your app grid ($apps/apis.desktop)."

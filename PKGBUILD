# PKGBUILD — Arch User Repository package definition for Mural
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE
#
# Install via AUR helper:
#   paru -S mural-git
#   yay  -S mural-git
#
# Or manually:
#   makepkg -si

pkgname=mural-git
pkgver=0.1.0.alpha
pkgrel=1
pkgdesc="Animated wallpaper platform for Linux — video, scene, and web wallpapers with session persistence"
arch=('x86_64' 'aarch64')
url="https://github.com/ian-vinson/mural"
license=('GPL3')
depends=(
    'python>=3.11'
    'python-pyside6>=6.6.0'
    'python-dasbus>=1.7'
    'python-requests>=2.31.0'
    'python-pillow>=10.0.0'
    'python-psutil>=5.9.0'
    'python-watchdog>=3.0.0'
    'python-gobject'          # gi bindings for GLib/D-Bus
    'linux-wallpaperengine'   # rendering backend
    'plasma-framework'        # for Plasma wallpaper plugin
)
makedepends=(
    'git'
    'python-build'
    'python-installer'
    'python-setuptools'
    'python-wheel'
)
optdepends=(
    'steam: Wallpaper Engine Workshop file compatibility'
    'kscreen: Monitor detection via kscreen-doctor'
    'xorg-xrandr: Monitor detection on X11'
)
provides=('mural')
conflicts=('mural')
source=("${pkgname}::git+https://github.com/ian-vinson/mural.git")
sha256sums=('SKIP')

pkgver() {
    cd "${srcdir}/${pkgname}"
    git describe --long --tags --abbrev=7 2>/dev/null | sed 's/\([^-]*-g\)/r\1/;s/-/./g' || \
    printf "0.1.0.r%s.g%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

build() {
    cd "${srcdir}/${pkgname}"
    python -m build --wheel --no-isolation
}

package() {
    cd "${srcdir}/${pkgname}"

    # Install Python package
    python -m installer --destdir="${pkgdir}" dist/*.whl

    # Entry point scripts
    install -Dm755 /dev/stdin "${pkgdir}/usr/bin/mural" << 'EOF'
#!/usr/bin/env python3
from mural.main import main
main()
EOF

    install -Dm755 /dev/stdin "${pkgdir}/usr/bin/mural-core" << 'EOF'
#!/usr/bin/env python3
from mural.core.service import main
main()
EOF

    # systemd user service unit
    install -Dm644 "systemd/mural-core.service" \
        "${pkgdir}/usr/lib/systemd/user/mural-core.service"

    # KDE Plasma wallpaper plugin
    install -dm755 "${pkgdir}/usr/share/plasma/wallpapers/com.mural.wallpaper"
    cp -r plasma-plugin/. "${pkgdir}/usr/share/plasma/wallpapers/com.mural.wallpaper/"

    # License
    install -Dm644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"

    # Desktop entry (optional, for application launchers)
    install -Dm644 /dev/stdin "${pkgdir}/usr/share/applications/mural.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=Mural
GenericName=Animated Wallpaper
Comment=Animated wallpaper platform for Linux
Exec=mural %u
Icon=video-display
Categories=Settings;DesktopSettings;
Keywords=wallpaper;animated;background;
StartupNotify=true
EOF
}

post_install() {
    systemctl --user daemon-reload 2>/dev/null || true
    echo ""
    echo "Mural installed. Enable the session service with:"
    echo "  systemctl --user enable --now mural-core.service"
    echo ""
    echo "Then launch the GUI:"
    echo "  mural"
}

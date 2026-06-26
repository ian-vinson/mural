# Maintainer: Ian Vinson <ian_vinson@foxitsoftware.com>
# PKGBUILD — Arch User Repository package definition for Mural

pkgname=mural-git
pkgver=0.2.0
pkgrel=1
pkgdesc="Animated wallpaper platform for Linux — GUI frontend for linux-wallpaperengine"
arch=('x86_64')
url="https://github.com/ian-vinson/mural"
license=('GPL3')
depends=(
    'python>=3.11'
    'python-pyside6'
    'python-dasbus'
    'python-requests'
    'python-pillow'
    'python-psutil'
    'python-watchdog'
    'python-gobject'
    'linux-wallpaperengine-git'
)
optdepends=(
    'python-pywal: system color scheme integration'
    'matugen: Material You color scheme integration'
    'openrgb: RGB hardware sync'
    'waybar: Waybar module support'
    'nvidia-utils: NVIDIA GPU memory monitoring'
)
makedepends=(
    'python-build'
    'python-installer'
    'python-wheel'
    'python-hatchling'
)
provides=('mural')
conflicts=('mural')
source=("$pkgname::git+https://github.com/ian-vinson/mural.git#tag=v0.2.0")
sha256sums=('SKIP')

pkgver() {
    cd "$pkgname"
    git describe --long --tags --abbrev=7 2>/dev/null | \
        sed 's/^v//;s/\([^-]*-g\)/r\1/;s/-/./g' || \
    printf "r%s.%s" "$(git rev-list --count HEAD)" \
                    "$(git rev-parse --short HEAD)"
}

build() {
    cd "$pkgname"
    python -m build --wheel --no-isolation
}

package() {
    cd "$pkgname"
    python -m installer --destdir="$pkgdir" dist/*.whl

    install -Dm644 systemd/mural-core.service \
        "$pkgdir/usr/lib/systemd/user/mural-core.service"

    install -dm755 \
        "$pkgdir/usr/share/plasma/wallpapers/com.mural.wallpaper"
    cp -r plasma-plugin/. \
        "$pkgdir/usr/share/plasma/wallpapers/com.mural.wallpaper/"

    install -Dm755 mural/waybar/mural-waybar.py \
        "$pkgdir/usr/share/mural/waybar/mural-waybar.py"
    install -Dm644 mural/waybar/mural-waybar.css \
        "$pkgdir/usr/share/mural/waybar/mural-waybar.css"

    install -Dm644 LICENSE \
        "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}

# Flatpak Build

Mural is not yet on Flathub. Use this manifest to build and install locally for testing.

## Prerequisites

```bash
sudo pacman -S flatpak flatpak-builder
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install flathub org.freedesktop.Platform//24.08 org.freedesktop.Sdk//24.08
flatpak install flathub org.freedesktop.Sdk.Extension.python3//24.08
```

## Fill in SHA256 hashes

Before building, replace the `sha256: FILLME` placeholders in
`io.github.ian_vinson.Mural.yml` with the real checksums:

```bash
pip download dasbus==1.7 Pillow psutil watchdog requests -d /tmp/pip-pkgs/
sha256sum /tmp/pip-pkgs/*.tar.gz
```

## Build and install locally

```bash
# From the repo root
flatpak-builder --user --install --force-clean \
    /tmp/mural-flatpak-build \
    flatpak/io.github.ian_vinson.Mural.yml
```

## Run

```bash
flatpak run io.github.ian_vinson.Mural
```

## Notes

- **linux-wallpaperengine** must be installed on the host system — it is not bundled
  in the Flatpak because it requires direct GPU/Wayland surface access.
  Install with: `paru -S linux-wallpaperengine-git`

- **python-gobject** is available inside the Flatpak via the freedesktop SDK runtime
  (`python3-gobject` is included in `org.freedesktop.Platform`).

- The systemd user service (`mural-core.service`) is installed to
  `/app/share/systemd/user/` but must be symlinked into
  `~/.config/systemd/user/` manually when running from Flatpak, since
  the Flatpak sandbox cannot directly manage host systemd units.

## Flathub submission

Once sha256 hashes are filled in and the build passes locally, submit via
the [Flathub new app process](https://github.com/flathub/flathub/wiki/App-Submission).

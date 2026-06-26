.PHONY: appimage flatpak test clean

VERSION := $(shell python3 -c "from mural import __version__; print(__version__)" 2>/dev/null || echo "0.0.0")

appimage:
	@echo "Building AppImage for Mural $(VERSION)..."
	@bash scripts/build-appimage.sh

flatpak:
	@echo "Building Flatpak for Mural $(VERSION)..."
	flatpak-builder --user --install --force-clean \
		/tmp/mural-flatpak-build \
		flatpak/io.github.ian_vinson.Mural.yml

test:
	pytest tests/

clean:
	rm -rf dist/ /tmp/mural-appimage-build /tmp/mural-flatpak-build

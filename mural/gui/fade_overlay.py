# mural/gui/fade_overlay.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Fullscreen black fade overlay for masking wallpaper transitions."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget


class FadeOverlay(QWidget):
    """Frameless top-most window that fades black then fades out.

    Call :meth:`do_transition` before switching wallpapers.  The overlay
    fades to opaque black over ``duration_ms / 2`` ms, calls *on_peak*
    when fully opaque (the ideal moment to trigger the switch), then fades
    back to transparent and hides itself.
    """

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._opacity_val: float = 0.0
        self._on_peak_cb = None
        self._anim_in: QPropertyAnimation | None = None
        self._anim_out: QPropertyAnimation | None = None

    # ------------------------------------------------------------------
    # Qt property (animated by QPropertyAnimation)
    # ------------------------------------------------------------------

    def _get_opacity(self) -> float:
        return self._opacity_val

    def _set_opacity(self, v: float) -> None:
        self._opacity_val = v
        self.update()

    opacity = Property(float, _get_opacity, _set_opacity)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def do_transition(self, duration_ms: int = 400, on_peak=None) -> None:
        """Start a fade-in / fade-out transition.

        Args:
            duration_ms: Total duration (fade-in + fade-out) in ms.
            on_peak: Optional callable invoked when opacity reaches 1.0,
                     before the fade-out begins — the ideal point to switch
                     the wallpaper.
        """
        self._on_peak_cb = on_peak

        for anim in (self._anim_in, self._anim_out):
            if anim and anim.state() == QPropertyAnimation.State.Running:
                anim.stop()

        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.virtualGeometry())
        self.show()
        self.raise_()

        half = max(50, duration_ms // 2)

        self._anim_in = QPropertyAnimation(self, b"opacity")
        self._anim_in.setDuration(half)
        self._anim_in.setStartValue(0.0)
        self._anim_in.setEndValue(1.0)
        self._anim_in.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim_in.finished.connect(self._on_fade_in_done)

        self._anim_out = QPropertyAnimation(self, b"opacity")
        self._anim_out.setDuration(half)
        self._anim_out.setStartValue(1.0)
        self._anim_out.setEndValue(0.0)
        self._anim_out.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim_out.finished.connect(self.hide)

        self._anim_in.start()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_fade_in_done(self) -> None:
        if self._on_peak_cb is not None:
            try:
                self._on_peak_cb()
            except Exception:
                pass
            self._on_peak_cb = None
        if self._anim_out:
            self._anim_out.start()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * self._opacity_val)))

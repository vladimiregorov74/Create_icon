#!/usr/bin/env python3
"""Конвертер изображений в ICO / PNG.

Обрезка, скругление углов, обводка, предпросмотр, прозрачность.
Зависимости:  pip install PySide6 Pillow
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import QPointF, QRect, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

ICO_SIZES = (256, 64, 48, 32, 16)
OPEN_FILTER = (
    "Изображения (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.tga);;"
    "Все файлы (*)"
)
FMT_PREM = QImage.Format.Format_ARGB32_Premultiplied
MAX_PREVIEW_SIDE = 1600  # для быстрого предпросмотра большие картинки уменьшаются


# --------------------------------------------------------------------------
# Вспомогательные функции
# --------------------------------------------------------------------------
def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


_tile_cache: QPixmap | None = None


def checker_tile() -> QPixmap:
    """Плитка «шахматки» для показа прозрачности."""
    global _tile_cache
    if _tile_cache is None:
        t = QPixmap(16, 16)
        p = QPainter(t)
        p.fillRect(0, 0, 16, 16, QColor(225, 225, 225))
        p.fillRect(0, 0, 8, 8, QColor(178, 178, 178))
        p.fillRect(8, 8, 8, 8, QColor(178, 178, 178))
        p.end()
        _tile_cache = t
    return _tile_cache


def checker_pixmap(img: QImage) -> QPixmap:
    pm = QPixmap(img.size())
    p = QPainter(pm)
    p.drawTiledPixmap(pm.rect(), checker_tile())
    p.drawImage(0, 0, img)
    p.end()
    return pm


def pil_to_qimage(im: Image.Image) -> QImage:
    im = im.convert("RGBA")
    w, h = im.size
    data = im.tobytes("raw", "RGBA")
    qi = QImage(data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
    return qi.convertToFormat(FMT_PREM)  # новая копия, не зависит от data


def qimage_to_pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer(
        "RGBA",
        (img.width(), img.height()),
        bytes(img.constBits()),
        "raw",
        "RGBA",
        img.bytesPerLine(),
        1,
    ).copy()


def load_image(path: str) -> QImage:
    """Читает любой формат, который знает Pillow (PNG, JPEG, WebP, BMP, GIF, TIFF, TGA...)."""
    with Image.open(path) as im:
        im.load()
        im = ImageOps.exif_transpose(im)  # учесть поворот из EXIF
        if im.mode in ("I;16", "I;16L", "I;16B", "I"):
            try:
                im = im.point(lambda v: v * (1 / 256)).convert("L")
            except Exception:
                im = im.convert("L")
        return pil_to_qimage(im)


# --------------------------------------------------------------------------
# Обработка: обрезка -> скругление -> обводка
# --------------------------------------------------------------------------
@dataclass
class Settings:
    radius: int = 0  # 0..100 %, 100 = круг/овал
    outline: int = 0  # 0..20 % от меньшей стороны
    color: QColor = field(default_factory=lambda: QColor(0, 0, 0))


def render(src: QImage, crop: QRect, canvas_w: int, canvas_h: int, st: Settings, fit: bool) -> QImage:
    """Рисует результат на прозрачном холсте canvas_w x canvas_h.

    fit=True  — вписать обрезанный кусок в холст (для ICO — квадратные значки),
                скругление и обводка применяются к самому изображению, а не к полям.
    fit=False — растянуть ровно на холст (для PNG и предпросмотра).
    """
    cropped = src.copy(crop)
    cw, ch = cropped.width(), cropped.height()
    if fit:
        k = min(canvas_w / cw, canvas_h / ch)
        w, h = max(1, round(cw * k)), max(1, round(ch * k))
    else:
        w, h = canvas_w, canvas_h
    x, y = (canvas_w - w) // 2, (canvas_h - h) // 2

    scaled = cropped.scaled(
        w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    out = QImage(canvas_w, canvas_h, FMT_PREM)
    out.fill(Qt.GlobalColor.transparent)

    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(x, y, w, h)
    r = min(w, h) / 2 * st.radius / 100
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)

    # Заливаем форму картинкой-«кистью»: края получаются сглаженными
    brush = QBrush(scaled)
    tr = QTransform()
    tr.translate(x, y)
    brush.setTransform(tr)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(brush)
    p.drawPath(path)

    if st.outline > 0 and st.color.alpha() > 0:
        pw = min(max(1.0, min(w, h) * st.outline / 100), min(w, h) / 2)
        inset = pw / 2  # обводка целиком внутри границ картинки
        rr = rect.adjusted(inset, inset, -inset, -inset)
        r2 = max(0.0, r - inset)
        path2 = QPainterPath()
        path2.addRoundedRect(rr, r2, r2)
        pen = QPen(st.color, pw)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawPath(path2)
    p.end()
    return out


def export_png(path: str, src: QImage, crop: QRect, st: Settings) -> None:
    img = render(src, crop, crop.width(), crop.height(), st, fit=False)
    qimage_to_pil(img).save(path, format="PNG", optimize=True)


def export_ico(path: str, src: QImage, crop: QRect, st: Settings, sizes: list[int]) -> None:
    sizes = sorted(set(sizes), reverse=True)
    frames = {s: qimage_to_pil(render(src, crop, s, s, st, fit=True)) for s in sizes}
    base = frames[sizes[0]]
    base.save(
        path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=[frames[s] for s in sizes[1:]],
    )


# --------------------------------------------------------------------------
# Виджет с рамкой обрезки
# --------------------------------------------------------------------------
class CropCanvas(QWidget):
    cropChanged = Signal()
    H = 5  # половина размера маркера, px
    MIN = 4.0  # минимальный размер рамки в пикселях изображения
    CORNERS = ("tl", "tr", "bl", "br")

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(460, 360)
        self.setMouseTracking(True)
        self.display: QImage | None = None  # уменьшенная копия для быстрой отрисовки
        self.iw = self.ih = 0  # размеры полного изображения
        self.crop = QRectF()
        self.ratio: float | None = None
        self._mode: str | None = None
        self._press_rect = QRectF()
        self._press_img = QPointF()
        self._anchor = QPointF()

    # ---- данные ----
    def set_image(self, display: QImage, full_w: int, full_h: int) -> None:
        self.display, self.iw, self.ih = display, full_w, full_h
        self.crop = QRectF(0, 0, full_w, full_h)
        self.update()
        self.cropChanged.emit()

    def reset_crop(self) -> None:
        if self.display is None:
            return
        self.crop = QRectF(0, 0, self.iw, self.ih)
        self.set_ratio(self.ratio)

    def set_ratio(self, ratio: float | None) -> None:
        """Пропорции рамки; при смене — самая большая рамка таких пропорций по центру."""
        self.ratio = ratio
        if self.display is None:
            return
        if ratio:
            w = float(self.iw)
            h = w / ratio
            if h > self.ih:
                h = float(self.ih)
                w = h * ratio
            c = self.crop.center()
            x = clamp(c.x() - w / 2, 0, self.iw - w)
            y = clamp(c.y() - h / 2, 0, self.ih - h)
            self.crop = QRectF(x, y, w, h)
        self.update()
        self.cropChanged.emit()

    # ---- геометрия вида ----
    def _view(self) -> tuple[float, float, float]:
        m = 12
        s = min((self.width() - 2 * m) / self.iw, (self.height() - 2 * m) / self.ih)
        ox = (self.width() - self.iw * s) / 2
        oy = (self.height() - self.ih * s) / 2
        return s, ox, oy

    def to_img(self, pos: QPointF) -> QPointF:
        s, ox, oy = self._view()
        return QPointF((pos.x() - ox) / s, (pos.y() - oy) / s)

    def crop_view(self) -> QRectF:
        s, ox, oy = self._view()
        c = self.crop
        return QRectF(ox + c.x() * s, oy + c.y() * s, c.width() * s, c.height() * s)

    @staticmethod
    def _handles(cr: QRectF) -> dict[str, QPointF]:
        cx, cy = cr.center().x(), cr.center().y()
        return {
            "tl": cr.topLeft(),
            "t": QPointF(cx, cr.top()),
            "tr": cr.topRight(),
            "r": QPointF(cr.right(), cy),
            "br": cr.bottomRight(),
            "b": QPointF(cx, cr.bottom()),
            "bl": cr.bottomLeft(),
            "l": QPointF(cr.left(), cy),
        }

    # ---- отрисовка ----
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().window())
        if self.display is None:
            p.setPen(self.palette().placeholderText().color())
            f = p.font()
            f.setPointSize(f.pointSize() + 3)
            p.setFont(f)
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Перетащите изображение сюда\nили нажмите Ctrl+V\nили «Открыть…»",
            )
            return
        s, ox, oy = self._view()
        target = QRectF(ox, oy, self.iw * s, self.ih * s)
        p.drawTiledPixmap(target.toRect(), checker_tile())
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(target, self.display)

        cr = self.crop_view()
        dim = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(target.left(), target.top(), target.width(), cr.top() - target.top()), dim)
        p.fillRect(QRectF(target.left(), cr.bottom(), target.width(), target.bottom() - cr.bottom()), dim)
        p.fillRect(QRectF(target.left(), cr.top(), cr.left() - target.left(), cr.height()), dim)
        p.fillRect(QRectF(cr.right(), cr.top(), target.right() - cr.right(), cr.height()), dim)

        # сетка третей
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for i in (1, 2):
            p.drawLine(QPointF(cr.left() + cr.width() * i / 3, cr.top()),
                       QPointF(cr.left() + cr.width() * i / 3, cr.bottom()))
            p.drawLine(QPointF(cr.left(), cr.top() + cr.height() * i / 3),
                       QPointF(cr.right(), cr.top() + cr.height() * i / 3))
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(cr)
        p.setPen(QPen(QColor(30, 30, 30), 1))
        p.setBrush(QColor(255, 255, 255))
        for pt in self._handles(cr).values():
            p.drawRect(QRectF(pt.x() - self.H, pt.y() - self.H, 2 * self.H, 2 * self.H))

    # ---- мышь ----
    def _hit(self, pos: QPointF) -> str:
        cr = self.crop_view()
        for name, pt in self._handles(cr).items():
            if abs(pos.x() - pt.x()) <= self.H + 3 and abs(pos.y() - pt.y()) <= self.H + 3:
                return name
        return "move" if cr.contains(pos) else "new"

    def _update_cursor(self, pos: QPointF) -> None:
        cur = {
            "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
            "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
            "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
            "move": Qt.CursorShape.SizeAllCursor, "new": Qt.CursorShape.CrossCursor,
        }
        self.setCursor(cur[self._hit(pos)])

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self.display is None:
            return
        pos = e.position()
        self._mode = self._hit(pos)
        self._press_rect = QRectF(self.crop)
        self._press_img = self.to_img(pos)
        pr = self._press_rect
        if self._mode == "new":
            a = self._press_img
            self._anchor = QPointF(clamp(a.x(), 0, self.iw), clamp(a.y(), 0, self.ih))
            self.crop = QRectF(self._anchor, QSizeF(0, 0))
        elif self._mode in self.CORNERS:
            self._anchor = {"tl": pr.bottomRight(), "tr": pr.bottomLeft(),
                            "bl": pr.topRight(), "br": pr.topLeft()}[self._mode]

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self._mode is None:
            if self.display is not None:
                self._update_cursor(pos)
            return
        p = self.to_img(pos)
        m = self._mode
        if m == "move":
            d = p - self._press_img
            r = QRectF(self._press_rect)
            x = clamp(r.x() + d.x(), 0, self.iw - r.width())
            y = clamp(r.y() + d.y(), 0, self.ih - r.height())
            self.crop = QRectF(x, y, r.width(), r.height())
        elif m == "new" or m in self.CORNERS:
            self.crop = self._rect_from_corner(self._anchor, p)
        else:
            self.crop = self._rect_from_edge(m, p)
        self.update()
        self.cropChanged.emit()

    def mouseReleaseEvent(self, _e) -> None:
        if self._mode is None:
            return
        if self.crop.width() < self.MIN or self.crop.height() < self.MIN:
            self.crop = QRectF(self._press_rect)  # простой клик — рамку не трогаем
        self._mode = None
        self.update()
        self.cropChanged.emit()

    # ---- расчёт рамки ----
    def _rect_from_corner(self, a: QPointF, p: QPointF) -> QRectF:
        px, py = clamp(p.x(), 0, self.iw), clamp(p.y(), 0, self.ih)
        dx, dy = px - a.x(), py - a.y()
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        max_w = self.iw - a.x() if sx > 0 else a.x()
        max_h = self.ih - a.y() if sy > 0 else a.y()
        w, h = min(abs(dx), max_w), min(abs(dy), max_h)
        if self.ratio:
            if w / self.ratio > h:
                w = h * self.ratio
            else:
                h = w / self.ratio
        return QRectF(a.x() if sx > 0 else a.x() - w, a.y() if sy > 0 else a.y() - h, w, h)

    def _rect_from_edge(self, name: str, p: QPointF) -> QRectF:
        r, iw, ih, ratio, MIN = self._press_rect, self.iw, self.ih, self.ratio, self.MIN
        if name in ("l", "r"):
            if name == "r":
                left = r.left()
                w = clamp(p.x(), left + MIN, iw) - left
            else:
                right = r.right()
                w = right - clamp(p.x(), 0, right - MIN)
            if ratio:
                h = w / ratio
                if h > ih:
                    h = float(ih)
                    w = h * ratio
                top = clamp(r.center().y() - h / 2, 0, ih - h)
            else:
                top, h = r.top(), r.height()
            left = r.left() if name == "r" else r.right() - w
            return QRectF(left, top, w, h)
        # "t" / "b"
        if name == "b":
            top = r.top()
            h = clamp(p.y(), top + MIN, ih) - top
        else:
            bottom = r.bottom()
            h = bottom - clamp(p.y(), 0, bottom - MIN)
        if ratio:
            w = h * ratio
            if w > iw:
                w = float(iw)
                h = w / ratio
            left = clamp(r.center().x() - w / 2, 0, iw - w)
        else:
            left, w = r.left(), r.width()
        top = r.top() if name == "b" else r.bottom() - h
        return QRectF(left, top, w, h)


# --------------------------------------------------------------------------
# Главное окно
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    ASPECTS = [("Свободно", None), ("Квадрат 1:1", 1.0), ("Как у исходного", "orig"),
               ("4:3", 4 / 3), ("3:2", 3 / 2), ("16:9", 16 / 9)]

    NEED_SIZE_TIP = "Сначала выберите размер значка"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Конвертер ICO / PNG")
        self.setAcceptDrops(True)
        self.src: QImage | None = None
        self.prev: QImage | None = None
        self.prev_f = 1.0
        self.src_path: str | None = None
        self.color = QColor(0, 0, 0)

        self.canvas = CropCanvas()
        self.canvas.cropChanged.connect(self.schedule)

        # ---------- левая колонка: холст + настройки ----------
        # Обрезка
        self.aspect = QComboBox()
        for name, _ in self.ASPECTS:
            self.aspect.addItem(name)
        self.aspect.currentIndexChanged.connect(self.on_aspect)
        reset_btn = QPushButton("Сбросить рамку")
        reset_btn.clicked.connect(self.canvas.reset_crop)
        crop_box = QGroupBox("Обрезка")
        lay = QVBoxLayout(crop_box)
        lay.addWidget(QLabel("Пропорции рамки:"))
        lay.addWidget(self.aspect)
        lay.addWidget(reset_btn)
        lay.addStretch()

        # Скругление
        self.radius_slider, self.radius_spin = self._slider_spin(0, 100)
        self.radius_slider.setToolTip("100 % — круг (или овал)")
        round_box = QGroupBox("Скругление углов")
        lay = QVBoxLayout(round_box)
        lay.addLayout(self._row(self.radius_slider, self.radius_spin))
        hint = QLabel("100 % = круг")
        hint.setEnabled(False)
        lay.addWidget(hint)

        # Обводка
        self.outline_slider, self.outline_spin = self._slider_spin(0, 20)
        self.swatch = QLabel()
        self.swatch.setFixedSize(34, 22)
        color_btn = QPushButton("Цвет…")
        color_btn.clicked.connect(self.pick_color)
        self._refresh_swatch()
        outline_box = QGroupBox("Обводка")
        lay = QVBoxLayout(outline_box)
        lay.addWidget(QLabel("Толщина (% от меньшей стороны):"))
        lay.addLayout(self._row(self.outline_slider, self.outline_spin))
        crow = QHBoxLayout()
        crow.addWidget(color_btn)
        crow.addWidget(self.swatch)
        crow.addStretch()
        lay.addLayout(crow)

        mid = QVBoxLayout()
        mid.addWidget(round_box)
        mid.addWidget(outline_box)

        # Размеры ICO
        self.size_checks: dict[int, QCheckBox] = {}
        sizes_box = QGroupBox("Размер значка")
        lay = QVBoxLayout(sizes_box)
        for s in ICO_SIZES:
            cb = QCheckBox(f"{s} × {s}")
            cb.toggled.connect(self.on_size_toggled)
            self.size_checks[s] = cb
            lay.addWidget(cb)
        lay.addStretch()

        settings_row = QHBoxLayout()
        settings_row.addWidget(crop_box, 1)
        settings_row.addLayout(mid, 2)
        settings_row.addWidget(sizes_box, 1)

        left = QVBoxLayout()
        left.addWidget(self.canvas, 1)
        left.addLayout(settings_row)

        # ---------- правая колонка: предпросмотр + сохранение ----------
        open_btn = QPushButton("Открыть…")
        open_btn.clicked.connect(self.open_dialog)
        paste_btn = QPushButton("Вставить (Ctrl+V)")
        paste_btn.clicked.connect(self.paste)
        top = QHBoxLayout()
        top.addWidget(open_btn)
        top.addWidget(paste_btn)

        self.preview = QLabel("Предпросмотр")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 320)
        self.preview.setFrameShape(QFrame.Shape.StyledPanel)
        prev_box = QGroupBox("Результат (PNG)")
        QVBoxLayout(prev_box).addWidget(self.preview)

        self.cells: dict[int, tuple[QWidget, QLabel, QLabel]] = {}
        strip_box = QGroupBox("Значки ICO (реальный размер)")
        strip = QHBoxLayout(strip_box)
        for s in ICO_SIZES:
            cell = QWidget()
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            img = QLabel()
            img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap = QLabel(f"{s}" if s < 256 else "256 (×0,5)")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(img, 1)
            cl.addWidget(cap)
            strip.addWidget(cell)
            self.cells[s] = (cell, img, cap)

        self.info = QLabel("Файл не загружен")
        self.info.setWordWrap(True)
        self.ico_btn = QPushButton("Сохранить ICO…")
        self.png_btn = QPushButton("Сохранить PNG…")
        self.ico_btn.clicked.connect(self.save_ico)
        self.png_btn.clicked.connect(self.save_png)
        for b in (self.ico_btn, self.png_btn):
            b.setMinimumHeight(34)
            b.setToolTip(self.NEED_SIZE_TIP)
        save_row = QHBoxLayout()
        save_row.addWidget(self.ico_btn)
        save_row.addWidget(self.png_btn)

        right = QVBoxLayout()
        right.addLayout(top)
        right.addWidget(prev_box)
        right.addWidget(strip_box)
        right.addWidget(self.info)
        right.addStretch()
        right.addLayout(save_row)
        right_w = QWidget()
        right_w.setLayout(right)
        right_w.setFixedWidth(370)

        central = QWidget()
        root = QHBoxLayout(central)
        root.addLayout(left, 1)
        root.addWidget(right_w)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Перетащите изображение в окно или нажмите Ctrl+V")

        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self.paste)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.update_preview)

        for w in (self.radius_slider, self.outline_slider):
            w.valueChanged.connect(self.schedule)
        self.update_preview()

    # ---------- построение UI ----------
    @staticmethod
    def _row(*widgets: QWidget) -> QHBoxLayout:
        h = QHBoxLayout()
        for w in widgets:
            h.addWidget(w)
        return h

    @staticmethod
    def _slider_spin(lo: int, hi: int) -> tuple[QSlider, QSpinBox]:
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setSuffix(" %")
        sl.valueChanged.connect(sp.setValue)
        sp.valueChanged.connect(sl.setValue)
        return sl, sp

    def _refresh_swatch(self) -> None:
        c = self.color
        self.swatch.setStyleSheet(
            f"background-color: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()});"
            "border: 1px solid #777;"
        )

    # ---------- загрузка ----------
    def dragEnterEvent(self, e) -> None:
        md = e.mimeData()
        if md.hasUrls() or md.hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        if self._take_mime(e.mimeData()):
            e.acceptProposedAction()

    def _take_mime(self, md) -> bool:
        for u in md.urls() if md.hasUrls() else []:
            if u.isLocalFile():
                return self.load_file(u.toLocalFile())
        if md.hasImage():
            qi = md.imageData()
            if isinstance(qi, QImage) and not qi.isNull():
                self.set_image(qi.convertToFormat(FMT_PREM), None)
                self.statusBar().showMessage("Изображение вставлено из буфера обмена")
                return True
        return False

    def paste(self) -> None:
        if not self._take_mime(QGuiApplication.clipboard().mimeData()):
            self.statusBar().showMessage("В буфере обмена нет изображения", 5000)

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Открыть изображение", "", OPEN_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> bool:
        try:
            img = load_image(path)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Не удалось открыть файл", f"{Path(path).name}\n\n{ex}")
            return False
        self.set_image(img, path)
        self.statusBar().showMessage(f"Открыто: {path}")
        return True

    def set_image(self, img: QImage, path: str | None) -> None:
        self.src, self.src_path = img, path
        m = max(img.width(), img.height())
        if m > MAX_PREVIEW_SIDE:
            self.prev_f = MAX_PREVIEW_SIDE / m
            self.prev = img.scaled(
                max(1, round(img.width() * self.prev_f)), max(1, round(img.height() * self.prev_f)),
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        else:
            self.prev_f, self.prev = 1.0, img
        self.canvas.set_image(self.prev, img.width(), img.height())
        self.on_aspect(self.aspect.currentIndex())
        self.setWindowTitle(f"Конвертер ICO / PNG — {Path(path).name if path else 'из буфера'}")

    # ---------- настройки и предпросмотр ----------
    def on_aspect(self, idx: int) -> None:
        a = self.ASPECTS[idx][1]
        if a == "orig":
            a = self.src.width() / self.src.height() if self.src else None
        self.canvas.set_ratio(a)

    def pick_color(self) -> None:
        c = QColorDialog.getColor(self.color, self, "Цвет обводки",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.color = c
            self._refresh_swatch()
            self.schedule()

    def settings(self) -> Settings:
        return Settings(self.radius_slider.value(), self.outline_slider.value(), QColor(self.color))

    def crop_int(self) -> QRect:
        r, iw, ih = self.canvas.crop, self.src.width(), self.src.height()
        x0 = int(clamp(round(r.left()), 0, iw - 1))
        y0 = int(clamp(round(r.top()), 0, ih - 1))
        x1 = int(clamp(round(r.right()), x0 + 1, iw))
        y1 = int(clamp(round(r.bottom()), y0 + 1, ih))
        return QRect(x0, y0, x1 - x0, y1 - y0)

    def schedule(self) -> None:
        self.timer.start()

    def selected_size(self) -> int | None:
        for s, cb in self.size_checks.items():
            if cb.isChecked():
                return s
        return None

    def on_size_toggled(self) -> None:
        """Можно выбрать только один размер: остальные блокируются, пока он отмечен."""
        chosen = self.selected_size()
        for s, cb in self.size_checks.items():
            cb.setEnabled(chosen is None or s == chosen)
        self.update_preview()

    def update_preview(self) -> None:
        has = self.src is not None
        size = self.selected_size()
        ready = has and size is not None
        for b in (self.ico_btn, self.png_btn):
            b.setEnabled(ready)
            b.setToolTip("" if ready else (self.NEED_SIZE_TIP if size is None else "Сначала откройте изображение"))
        if not has:
            for cell, *_ in self.cells.values():
                cell.setVisible(False)
            return
        st, crop, f = self.settings(), self.crop_int(), self.prev_f
        pb = self.prev.rect()
        x0, y0 = int(crop.left() * f), int(crop.top() * f)
        x1 = min(pb.width(), max(x0 + 1, int(-(-crop.right() * f // 1))))
        y1 = min(pb.height(), max(y0 + 1, int(-(-crop.bottom() * f // 1))))
        pc = QRect(x0, y0, x1 - x0, y1 - y0)

        k = 300 / max(crop.width(), crop.height())
        ow, oh = max(1, round(crop.width() * k)), max(1, round(crop.height() * k))
        self.preview.setPixmap(checker_pixmap(render(self.prev, pc, ow, oh, st, fit=False)))

        for s, (cell, img, _cap) in self.cells.items():
            on = self.size_checks[s].isChecked()
            cell.setVisible(on)
            if not on:
                continue
            im = render(self.prev, pc, s, s, st, fit=True)
            if s > 64:
                im = im.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            img.setPixmap(checker_pixmap(im))

        text = f"Исходное: {self.src.width()}×{self.src.height()} px\nРамка: {crop.width()}×{crop.height()} px"
        if abs(crop.width() - crop.height()) > 1:
            text += "\nДля ICO рамка вписывается в квадрат, поля прозрачные."
        self.info.setText(text)

    # ---------- сохранение ----------
    def _default_name(self, suffix: str, tag: str = "") -> str:
        if self.src_path:
            p = Path(self.src_path)
            return str(p.with_name(p.stem + tag + suffix))
        return str(Path.home() / ("icon" + suffix))

    def save_ico(self) -> None:
        size = self.selected_size()
        if size is None:
            return
        sizes = [size]
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить ICO", self._default_name(".ico"), "Значок Windows (*.ico)")
        if path:
            self._save(path, ".ico", lambda p: export_ico(p, self.src, self.crop_int(), self.settings(), sizes))

    def save_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить PNG", self._default_name(".png", "_icon"), "PNG (*.png)")
        if path:
            self._save(path, ".png", lambda p: export_png(p, self.src, self.crop_int(), self.settings()))

    def _save(self, path: str, suffix: str, fn) -> None:
        if not path.lower().endswith(suffix):
            path += suffix
        try:
            fn(path)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка сохранения", str(ex))
        else:
            self.statusBar().showMessage(f"Сохранено: {path}", 10000)


def main() -> None:
    # Убираем шумные предупреждения Qt (например, wayland.textinput) из терминала
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.wayland.textinput=false")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.resize(1220, 820)
    win.show()
    if len(sys.argv) > 1:
        win.load_file(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
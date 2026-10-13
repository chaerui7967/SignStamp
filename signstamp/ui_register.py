"""서명 등록 창: 사진 → 영역 선택 → 배경 투명화 → 저장."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from . import library, sigproc


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    return QPixmap.fromImage(ImageQt(img.convert("RGBA")))


class CropView(QWidget):
    """사진을 보여주고 드래그로 서명 영역을 고르는 위젯."""

    def __init__(self, img: Image.Image, on_change):
        super().__init__()
        self.img = img
        self.pix = pil_to_pixmap(img)
        self.on_change = on_change
        self.sel: QRect | None = None  # 위젯 좌표
        self._start: QPoint | None = None
        self.setMinimumSize(420, 420)
        self.setCursor(Qt.CrossCursor)

    def _target(self) -> QRect:
        s = self.pix.size().scaled(self.size(), Qt.KeepAspectRatio)
        return QRect((self.width() - s.width()) // 2, (self.height() - s.height()) // 2,
                     s.width(), s.height())

    def selection_box(self) -> tuple[int, int, int, int] | None:
        """선택 영역을 원본 이미지 픽셀 좌표로."""
        if not self.sel or self.sel.width() < 5 or self.sel.height() < 5:
            return None
        t = self._target()
        r = self.sel.intersected(t)
        k = self.img.width / t.width()
        return (int((r.left() - t.left()) * k), int((r.top() - t.top()) * k),
                int((r.right() - t.left()) * k), int((r.bottom() - t.top()) * k))

    def clear(self):
        self.sel = None
        self.update()
        self.on_change()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#3a3a3a"))
        t = self._target()
        p.drawPixmap(t, self.pix)
        if self.sel:
            shade = QColor(0, 0, 0, 120)
            outer = [QRect(t.left(), t.top(), t.width(), self.sel.top() - t.top()),
                     QRect(t.left(), self.sel.bottom(), t.width(), t.bottom() - self.sel.bottom()),
                     QRect(t.left(), self.sel.top(), self.sel.left() - t.left(), self.sel.height()),
                     QRect(self.sel.right(), self.sel.top(), t.right() - self.sel.right(), self.sel.height())]
            for r in outer:
                if r.width() > 0 and r.height() > 0:
                    p.fillRect(r, shade)
            p.setPen(QPen(QColor("#ff5050"), 2, Qt.DashLine))
            p.drawRect(self.sel)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start = e.position().toPoint()
            self.sel = QRect(self._start, QSize(0, 0))

    def mouseMoveEvent(self, e):
        if self._start is not None:
            self.sel = QRect(self._start, e.position().toPoint()).normalized().intersected(self._target())
            self.update()

    def mouseReleaseEvent(self, e):
        if self._start is not None:
            self._start = None
            if self.sel and (self.sel.width() < 5 or self.sel.height() < 5):
                self.sel = None
            self.update()
            self.on_change()


class Checker(QLabel):
    """투명 영역이 보이도록 체크무늬 배경 위에 결과를 그리는 라벨."""

    def __init__(self):
        super().__init__()
        self.pix: QPixmap | None = None
        self.setMinimumSize(320, 200)

    def set_image(self, img: Image.Image | None):
        self.pix = pil_to_pixmap(img) if img is not None else None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        n = 12
        for y in range(0, self.height(), n):
            for x in range(0, self.width(), n):
                p.fillRect(x, y, n, n, QColor("#ffffff") if (x // n + y // n) % 2 else QColor("#e4e4e4"))
        if self.pix:
            s = self.pix.size().scaled(self.size() * 0.92, Qt.KeepAspectRatio)
            r = QRect((self.width() - s.width()) // 2, (self.height() - s.height()) // 2, s.width(), s.height())
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(r, self.pix)


class RegisterDialog(QDialog):
    def __init__(self, photo_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("서명 등록")
        self.resize(1100, 640)
        self.photo = sigproc.load_photo(photo_path)
        self.result_img: Image.Image | None = None
        self.saved_path: Path | None = None

        self.crop = CropView(self.photo, self._schedule)
        self.preview = Checker()

        self.strength = QSlider(Qt.Horizontal)
        self.strength.setRange(0, 100)
        self.strength.setValue(45)
        self.strength.valueChanged.connect(self._schedule)
        self.color = QComboBox()
        self.color.addItems(list(sigproc.INK_COLORS))
        self.color.currentIndexChanged.connect(self._schedule)
        self.despeckle = QCheckBox("작은 점·잡티 제거")
        self.despeckle.setChecked(True)
        self.despeckle.toggled.connect(self._schedule)
        self.name = QLineEdit(Path(photo_path).stem)
        clear_btn = QPushButton("영역 선택 해제")
        clear_btn.clicked.connect(self.crop.clear)

        form = QFormLayout()
        form.addRow("배경 제거 강도", self.strength)
        form.addRow("서명 색", self.color)
        form.addRow("", self.despeckle)
        form.addRow("서명 이름", self.name)

        hint = QLabel("왼쪽 사진에서 서명 부분을 드래그해 선택하면 더 깔끔해집니다.\n"
                      "(종이 밖 배경·그림자가 찍혔을 때 특히 유용)\n"
                      "획이 끊기면 강도를 낮추고, 배경이 남으면 강도를 높이세요.")
        hint.setStyleSheet("color:#666")

        right = QVBoxLayout()
        right.addWidget(QLabel("결과 미리보기 (체크무늬 = 투명)"))
        right.addWidget(self.preview, 1)
        right.addLayout(form)
        right.addWidget(clear_btn)
        right.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("서명 저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)

        lay = QHBoxLayout(self)
        lay.addWidget(self.crop, 3)
        rw = QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 2)

        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self._process)
        self._process()

    def _schedule(self, *_):
        self._timer.start()

    def _process(self):
        img = self.photo
        box = self.crop.selection_box()
        if box:
            img = img.crop(box)
        self.result_img = sigproc.make_transparent(
            img, strength=self.strength.value() / 100, color=self.color.currentText(),
            despeckle=self.despeckle.isChecked())
        self.preview.set_image(self.result_img)

    def _save(self):
        if self._timer.isActive():
            self._timer.stop()
            self._process()
        if self.result_img is None or self.result_img.getchannel("A").getbbox() is None:
            QMessageBox.warning(self, "서명 등록", "서명이 감지되지 않았습니다. 강도를 낮춰 보세요.")
            return
        self.saved_path = library.save_signature(self.name.text(), self.result_img)
        self.accept()

"""메인 창: 서명 목록 + 문서 미리보기 위에 서명 배치 + 저장."""
from __future__ import annotations

import math
import os
import traceback
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QGraphicsItem, QGraphicsObject, QGraphicsPixmapItem,
    QGraphicsScene, QGraphicsView, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QProgressDialog, QPushButton, QSplitter, QToolBar,
    QVBoxLayout, QWidget,
)

from PIL import Image

from . import documents, library, sigproc
from .documents import Placement
from .ui_register import RegisterDialog, pil_to_pixmap

RENDER_ZOOM = 2.0       # 페이지 렌더링 해상도 (144 dpi)
DEFAULT_SIG_WIDTH = 110  # pt (약 39mm)
HANDLE = 10
ROTATE_GAP = 22  # 회전 핸들이 서명 위로 떨어진 거리(화면 px)


# ---------------------------------------------------------------- 백그라운드 작업

class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.done.emit(self.fn(*self.args))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- 서명 아이템

class SignatureItem(QGraphicsObject):
    """페이지 위에 놓인 서명.

    드래그: 이동 / 오른쪽 아래 사각 핸들: 크기 / 위쪽 동그라미 핸들: 회전.
    회전은 사각형 중심 기준이며, 크기를 바꿔도 왼쪽 위 모서리가 제자리에 있도록 위치를 보정한다."""

    def __init__(self, sig_path: str, width: float):
        super().__init__()
        self.sig_path = sig_path
        self._base = Image.open(sig_path).convert("RGBA")
        self.ratio = self._base.height / max(1, self._base.width)
        self.bold = 0
        self.pix = QPixmap(sig_path)
        self.w, self.h = width, width * self.ratio
        self._drag: str | None = None  # "resize" | "rotate"
        self.setTransformOriginPoint(self.w / 2, self.h / 2)
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemIsFocusable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.SizeAllCursor)

    # 상태 변경 ---------------------------------------------------------
    def set_size(self, w: float, h: float):
        before = self.mapToScene(QPointF(0, 0))
        self.prepareGeometryChange()
        self.w, self.h = w, h
        self.setTransformOriginPoint(w / 2, h / 2)
        self.setPos(self.pos() + before - self.mapToScene(QPointF(0, 0)))
        self.update()

    def set_bold(self, level: int):
        level = max(0, min(sigproc.BOLD_MAX, level))
        if level == self.bold:
            return
        self.bold = level
        self.pix = pil_to_pixmap(sigproc.thicken(self._base, level)) if level else QPixmap(self.sig_path)
        self.update()

    def set_angle(self, angle: float):
        self.setRotation(angle % 360)

    def placement(self, page: int) -> Placement:
        c = self.mapToScene(QPointF(self.w / 2, self.h / 2))
        return Placement(self.sig_path, page, c.x() - self.w / 2, c.y() - self.h / 2,
                         self.w, self.h, self.rotation(), self.bold)

    # 그리기 ------------------------------------------------------------
    def _px(self, n: float) -> float:
        """화면 픽셀 n 개에 해당하는 장면 길이."""
        views = self.scene().views() if self.scene() else []
        return n / (views[0].transform().m11() if views else 1)

    def _resize_rect(self) -> QRectF:
        s = self._px(HANDLE)
        return QRectF(self.w - s / 2, self.h - s / 2, s, s)

    def _rotate_rect(self) -> QRectF:
        s, gap = self._px(HANDLE + 2), self._px(ROTATE_GAP)
        return QRectF(self.w / 2 - s / 2, -gap - s / 2, s, s)

    def boundingRect(self) -> QRectF:
        s, gap = self._px(HANDLE + 2), self._px(ROTATE_GAP)
        return QRectF(-s, -gap - s, self.w + 2 * s, self.h + gap + 2 * s)

    def paint(self, p: QPainter, *_):
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(QRectF(0, 0, self.w, self.h), self.pix, QRectF(self.pix.rect()))
        if self.isSelected():
            blue = QColor("#1e88e5")
            p.setPen(QPen(blue, 0, Qt.DashLine))
            p.drawRect(QRectF(0, 0, self.w, self.h))
            p.setPen(QPen(blue, 0))
            p.drawLine(QPointF(self.w / 2, 0), self._rotate_rect().center())
            p.setBrush(blue)
            p.drawRect(self._resize_rect())
            p.setBrush(QColor("white"))
            p.drawEllipse(self._rotate_rect())

    # 마우스 ------------------------------------------------------------
    def _hit(self, pos: QPointF) -> str | None:
        if not self.isSelected():
            return None
        if self._resize_rect().contains(pos):
            return "resize"
        if self._rotate_rect().contains(pos):
            return "rotate"
        return None

    def hoverMoveEvent(self, e):
        hit = self._hit(e.pos())
        self.setCursor({"resize": Qt.SizeFDiagCursor, "rotate": Qt.PointingHandCursor}.get(hit, Qt.SizeAllCursor))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and (hit := self._hit(e.pos())):
            self._drag = hit
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag == "resize":
            w = max(10.0, e.pos().x())
            if e.modifiers() & Qt.ShiftModifier:   # Shift: 비율 무시
                self.set_size(w, max(5.0, e.pos().y()))
            else:
                self.set_size(w, w * self.ratio)
            return
        if self._drag == "rotate":
            c = self.mapToScene(QPointF(self.w / 2, self.h / 2))
            d = e.scenePos() - c
            angle = math.degrees(math.atan2(d.y(), d.x())) + 90
            if e.modifiers() & Qt.ShiftModifier:   # Shift: 15° 단위
                angle = round(angle / 15) * 15
            elif abs((angle + 45) % 90 - 45) < 3:  # 0/90/180/270° 근처는 붙임
                angle = round(angle / 90) * 90
            self.set_angle(angle)
            self.scene().views()[0].changed.emit()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        menu = QMenu()
        bolder = menu.addAction("굵게  (Ctrl+B)")
        thinner = menu.addAction("가늘게  (Ctrl+Shift+B)")
        bolder.setEnabled(self.bold < sigproc.BOLD_MAX)
        thinner.setEnabled(self.bold > 0)
        menu.addSeparator()
        rot_l = menu.addAction("왼쪽으로 90° 회전")
        rot_r = menu.addAction("오른쪽으로 90° 회전")
        rot_0 = menu.addAction("회전 없애기")
        menu.addSeparator()
        dup = menu.addAction("복제")
        reset = menu.addAction("원래 비율로")
        delete = menu.addAction("삭제")
        act = menu.exec(e.screenPos())
        view = self.scene().views()[0]
        if act == delete:
            view.remove_item(self)
            return
        if act == dup:
            item = view.add_signature(self.sig_path, self.pos() + QPointF(15, 15), self.w)
            item.set_size(self.w, self.h)
            item.set_bold(self.bold)
            item.set_angle(self.rotation())
        elif act == reset:
            self.set_size(self.w, self.w * self.ratio)
        elif act == bolder:
            self.set_bold(self.bold + 1)
        elif act == thinner:
            self.set_bold(self.bold - 1)
        elif act in (rot_l, rot_r, rot_0):
            self.set_angle(0 if act == rot_0 else self.rotation() + (90 if act == rot_r else -90))
        view.changed.emit()


# ---------------------------------------------------------------- 페이지 뷰

class PageView(QGraphicsView):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#5a5a5a"))
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.doc: fitz.Document | None = None
        self.page_index = 0
        self.items_by_page: dict[int, list[SignatureItem]] = {}
        self._page_item: QGraphicsPixmapItem | None = None

    # 문서 / 페이지 ---------------------------------------------------
    def load(self, pdf_path: Path):
        if self.doc:
            self.doc.close()
        self.doc = fitz.open(pdf_path)
        self.items_by_page = {}
        self.page_index = -1
        self.show_page(0)
        QTimer.singleShot(0, self.fit_width)  # 뷰 크기가 확정된 뒤에 맞춤

    @property
    def page_count(self) -> int:
        return len(self.doc) if self.doc else 0

    def show_page(self, index: int):
        if not self.doc or not (0 <= index < len(self.doc)) or index == self.page_index:
            return
        scene = self.scene()
        for it in self.items_by_page.get(self.page_index, []):
            scene.removeItem(it)
        if self._page_item:
            scene.removeItem(self._page_item)
        self.page_index = index
        page = self.doc[index]
        pm = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM), alpha=False)
        img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format_RGB888).copy()
        self._page_item = QGraphicsPixmapItem(QPixmap.fromImage(img))
        self._page_item.setScale(1 / RENDER_ZOOM)  # 장면 좌표 = pt
        self._page_item.setTransformationMode(Qt.SmoothTransformation)
        self._page_item.setZValue(-1)
        scene.addItem(self._page_item)
        for it in self.items_by_page.get(index, []):
            scene.addItem(it)
        r = page.rect
        scene.setSceneRect(QRectF(-40, -40, r.width + 80, r.height + 80))
        self.changed.emit()

    def page_size(self) -> QSize:
        r = self.doc[self.page_index].rect
        return QSize(int(r.width), int(r.height))

    # 확대 ------------------------------------------------------------
    def fit_width(self):
        if not self.doc:
            return
        w = self.doc[self.page_index].rect.width + 40
        s = (self.viewport().width() - 10) / w
        self.resetTransform()
        self.scale(s, s)

    def zoom(self, factor: float):
        self.scale(factor, factor)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
        else:
            super().wheelEvent(e)

    # 서명 ------------------------------------------------------------
    def add_signature(self, sig_path: str, pos: QPointF | None = None, width: float = DEFAULT_SIG_WIDTH):
        if not self.doc:
            return
        item = SignatureItem(sig_path, width)
        if pos is None:  # 화면 가운데
            c = self.mapToScene(self.viewport().rect().center())
            pos = QPointF(c.x() - item.w / 2, c.y() - item.h / 2)
        item.setPos(pos)
        self.scene().addItem(item)
        self.items_by_page.setdefault(self.page_index, []).append(item)
        self.scene().clearSelection()
        item.setSelected(True)
        self.changed.emit()
        return item

    def remove_item(self, item: SignatureItem):
        for items in self.items_by_page.values():
            if item in items:
                items.remove(item)
        self.scene().removeItem(item)
        self.changed.emit()

    def remove_selected(self):
        for it in list(self.scene().selectedItems()):
            if isinstance(it, SignatureItem):
                self.remove_item(it)

    def selected_signatures(self) -> list[SignatureItem]:
        return [i for i in self.scene().selectedItems() if isinstance(i, SignatureItem)]

    def bold_selected(self, delta: int):
        for i in self.selected_signatures():
            i.set_bold(i.bold + delta)

    def rotate_selected(self, delta: float):
        for i in self.selected_signatures():
            i.set_angle(i.rotation() + delta)
        self.changed.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
            return
        sel = self.selected_signatures()
        if sel and e.key() in (Qt.Key_BracketLeft, Qt.Key_BracketRight):  # [ ] : 회전
            deg = 15 if e.modifiers() & Qt.ShiftModifier else 1
            self.rotate_selected(deg if e.key() == Qt.Key_BracketRight else -deg)
            return
        step = 5 if e.modifiers() & Qt.ShiftModifier else 0.5
        moves = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0), Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}
        if sel and e.key() in moves:
            dx, dy = moves[e.key()]
            for i in sel:
                i.moveBy(dx, dy)
            return
        super().keyPressEvent(e)

    def placements(self) -> list[Placement]:
        out = []
        for page, items in self.items_by_page.items():
            for it in items:
                out.append(it.placement(page))
        return out

    def placement_count(self) -> int:
        return sum(len(v) for v in self.items_by_page.values())


# ---------------------------------------------------------------- 메인 창

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SignStamp - 서명 삽입")
        self.resize(1280, 860)
        self.src_path: Path | None = None
        self._worker: Worker | None = None

        # 왼쪽: 서명 목록
        self.sig_list = QListWidget()
        self.sig_list.setIconSize(QSize(180, 70))
        self.sig_list.setSpacing(4)
        self.sig_list.itemDoubleClicked.connect(lambda it: self.place_selected_signature())
        btn_add = QPushButton("+ 서명 등록 (사진)")
        btn_add.clicked.connect(self.register_signature)
        btn_place = QPushButton("현재 페이지에 넣기")
        btn_place.clicked.connect(self.place_selected_signature)
        btn_del = QPushButton("서명 삭제")
        btn_del.clicked.connect(self.delete_signature)
        btn_dir = QPushButton("보관 폴더 열기")
        btn_dir.clicked.connect(lambda: os.startfile(library.SIG_DIR))
        left = QVBoxLayout()
        left.addWidget(QLabel("내 서명  (더블클릭 → 페이지에 넣기)"))
        left.addWidget(self.sig_list, 1)
        for b in (btn_add, btn_place, btn_del, btn_dir):
            left.addWidget(b)
        lw = QWidget()
        lw.setLayout(left)

        # 가운데: 페이지
        self.view = PageView()
        self.view.changed.connect(self._update_status)
        self.placeholder = QLabel("문서를 열어 주세요\n\nPDF · Word(.docx/.doc) · 한글(.hwp/.hwpx)\n\n"
                                  "파일을 이 창에 끌어다 놓아도 됩니다.")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("font-size:16px;color:#777")
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.placeholder)
        cl.addWidget(self.view)
        self.view.hide()

        split = QSplitter()
        split.addWidget(lw)
        split.addWidget(center)
        split.setSizes([260, 1020])
        self.setCentralWidget(split)

        # 툴바
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        self._act(tb, "문서 열기", self.open_document, QKeySequence.Open)
        self.act_save = self._act(tb, "서명 넣어 저장", self.save_document, QKeySequence.Save)
        tb.addSeparator()
        self._act(tb, "◀ 이전", lambda: self.view.show_page(self.view.page_index - 1), "PgUp")
        self.page_label = QLabel("  -  ")
        tb.addWidget(self.page_label)
        self._act(tb, "다음 ▶", lambda: self.view.show_page(self.view.page_index + 1), "PgDown")
        tb.addSeparator()
        self._act(tb, "확대", lambda: self.view.zoom(1.2), QKeySequence.ZoomIn)
        self._act(tb, "축소", lambda: self.view.zoom(1 / 1.2), QKeySequence.ZoomOut)
        self._act(tb, "폭 맞춤", self.view.fit_width, "Ctrl+0")
        tb.addSeparator()
        self._act(tb, "굵게", lambda: self.view.bold_selected(1), "Ctrl+B")
        self._act(tb, "가늘게", lambda: self.view.bold_selected(-1), "Ctrl+Shift+B")
        self._act(tb, "⟲ 90°", lambda: self.view.rotate_selected(-90), "Ctrl+L")
        self._act(tb, "⟳ 90°", lambda: self.view.rotate_selected(90), "Ctrl+R")
        tb.addSeparator()
        self._act(tb, "선택 서명 삭제", self.view.remove_selected)

        self.setAcceptDrops(True)
        self.reload_signatures()
        self._update_status()

    def _act(self, tb, text, slot, shortcut=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(shortcut)
        tb.addAction(a)
        return a

    # 서명 관리 -------------------------------------------------------
    def reload_signatures(self, select: Path | None = None):
        self.sig_list.clear()
        for p in library.list_signatures():
            it = QListWidgetItem(QIcon(str(p)), p.stem)
            it.setData(Qt.UserRole, str(p))
            self.sig_list.addItem(it)
            if select and Path(select) == p:
                self.sig_list.setCurrentItem(it)
        if self.sig_list.currentItem() is None and self.sig_list.count():
            self.sig_list.setCurrentRow(self.sig_list.count() - 1)

    def register_signature(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "서명 사진 선택", "", "이미지 (*.jpg *.jpeg *.png *.bmp *.heic *.webp *.tif *.tiff)")
        if not path:
            return
        try:
            dlg = RegisterDialog(path, self)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "서명 등록", f"사진을 열 수 없습니다.\n{e}")
            return
        if dlg.exec() and dlg.saved_path:
            self.reload_signatures(select=dlg.saved_path)

    def delete_signature(self):
        it = self.sig_list.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "서명 삭제", f"'{it.text()}' 서명을 삭제할까요?") == QMessageBox.Yes:
            library.delete_signature(Path(it.data(Qt.UserRole)))
            self.reload_signatures()

    def place_selected_signature(self):
        if not self.view.doc:
            QMessageBox.information(self, "SignStamp", "먼저 문서를 열어 주세요.")
            return
        it = self.sig_list.currentItem()
        if not it:
            QMessageBox.information(self, "SignStamp", "먼저 서명을 등록해 주세요.")
            return
        self.view.add_signature(it.data(Qt.UserRole))
        self.view.setFocus()

    # 문서 ------------------------------------------------------------
    def open_document(self, path: str | None = None):
        if not path:
            exts = " ".join(f"*{e}" for e in sorted(documents.SUPPORTED_EXT))
            path, _ = QFileDialog.getOpenFileName(self, "문서 열기", "", f"문서 ({exts})")
            if not path:
                return
        if self.view.placement_count() and QMessageBox.question(
                self, "문서 열기", "배치한 서명이 저장되지 않았습니다. 새 문서를 열까요?") != QMessageBox.Yes:
            return
        try:
            documents.kind_of(path)
        except ValueError as e:
            QMessageBox.warning(self, "문서 열기", str(e))
            return

        def done(pdf):
            self.src_path = Path(path)
            self.placeholder.hide()
            self.view.show()
            self.view.load(pdf)
            self.setWindowTitle(f"SignStamp - {self.src_path.name}")

        msg = "문서를 불러오는 중..." if documents.kind_of(path) == "pdf" else \
            "Word/한글 문서를 미리보기용으로 변환하는 중...\n(처음에는 시간이 조금 걸릴 수 있습니다)"
        self._run(msg, documents.make_preview_pdf, (path,), done)

    def save_document(self):
        if not self.src_path:
            return
        placements = self.view.placements()
        if not placements:
            QMessageBox.information(self, "저장", "페이지에 넣은 서명이 없습니다.")
            return
        ext = self.src_path.suffix
        out, _ = QFileDialog.getSaveFileName(
            self, "다른 이름으로 저장", str(documents.default_output_path(self.src_path)), f"*{ext}")
        if not out:
            return
        if Path(out).suffix.lower() != ext.lower():
            out += ext

        def done(warnings):
            note = "\n\n주의:\n- " + "\n- ".join(warnings) if warnings else ""
            r = QMessageBox.question(self, "저장 완료", f"저장했습니다.\n{out}{note}\n\n파일을 열어 볼까요?")
            if r == QMessageBox.Yes:
                os.startfile(out)

        self._run("서명을 넣어 저장하는 중...", documents.save_with_signatures,
                  (self.src_path, out, placements), done)

    def _run(self, message, fn, args, on_done):
        prog = QProgressDialog(message, None, 0, 0, self)
        prog.setWindowTitle("SignStamp")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(300)
        prog.setCancelButton(None)
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        self._worker = Worker(fn, *args)

        def finish():
            QApplication.restoreOverrideCursor()
            prog.close()

        self._worker.done.connect(lambda r: (finish(), on_done(r)))
        self._worker.failed.connect(lambda m: (finish(), QMessageBox.critical(self, "오류", m)))
        self._worker.start()

    def _update_status(self):
        n = self.view.page_count
        self.page_label.setText(f"  {self.view.page_index + 1} / {n} 쪽  " if n else "  -  ")
        self.act_save.setEnabled(self.view.placement_count() > 0)
        cnt = self.view.placement_count()
        self.statusBar().showMessage(
            f"배치한 서명 {cnt}개  |  드래그: 이동 · 파란 모서리: 크기 · 위 동그라미: 회전(Shift: 15°) · "
            f"[ ]: 1° 회전 · Ctrl+B: 굵게 · 방향키: 미세 이동 · Delete: 삭제 · Ctrl+휠: 확대/축소"
            if n else "문서를 열어 주세요.")

    # 드래그 앤 드롭 --------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            ext = Path(p).suffix.lower()
            if ext in documents.SUPPORTED_EXT:
                self.open_document(p)
            elif ext in {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}:
                dlg = RegisterDialog(p, self)
                if dlg.exec() and dlg.saved_path:
                    self.reload_signatures(select=dlg.saved_path)
            break

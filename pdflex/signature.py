"""Hand-written signature: drawn with the mouse or loaded from a picture, kept as a PNG."""
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from pdflex.ui import Toggle, button, load_bytes, save_bytes, warn
from pdflex.i18n import tr


class SignatureCanvas(QWidget):
    """Draw with the mouse; strokes kept as paths so the result is crisp at any size."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 200)
        self.setCursor(Qt.CrossCursor)
        self.paths: list[QPainterPath] = []
        self.picture: QPixmap | None = None  # a loaded image instead of strokes

    def clear(self):
        self.paths, self.picture = [], None
        self.update()

    def mousePressEvent(self, e):
        self.picture = None
        p = QPainterPath(e.position())
        self.paths.append(p)

    def mouseMoveEvent(self, e):
        if self.paths and e.buttons() & Qt.LeftButton:
            self.paths[-1].lineTo(e.position())
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), Qt.white)
        p.setPen(QPen(QColor("#C3C9D4"), 1, Qt.DashLine))
        p.drawLine(30, self.height() - 50, self.width() - 30, self.height() - 50)
        if self.picture:
            pic = self.picture.scaled(self.size() * 0.9, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap((self.width() - pic.width()) // 2, (self.height() - pic.height()) // 2, pic)
        p.setPen(QPen(QColor("#0B2A6F"), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path in self.paths:
            p.drawPath(path)
        if not self.paths and not self.picture:
            p.setPen(QColor("#A9B0BB"))
            p.drawText(self.rect(), Qt.AlignCenter, tr("Firma qui col mouse"))

    def png(self) -> bytes | None:
        """The signature cropped to its ink, transparent background, drawn at 3x for sharp printing."""
        if self.picture:
            return _png(self.picture)
        if not self.paths:
            return None
        box = QRectF()
        for path in self.paths:
            box = box.united(path.boundingRect())
        box = box.adjusted(-4, -4, 4, 4)
        img = QImage((box.size() * 3).toSize(), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(3, 3)
        p.translate(-box.topLeft())
        p.setPen(QPen(QColor("#0B2A6F"), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path in self.paths:
            p.drawPath(path)
        p.end()
        return _png(img)


def _png(img) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


class SignatureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Firma a mano"))
        self.png: bytes | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        lay.addWidget(QLabel(tr("Firma col mouse (o con la penna) nel riquadro, oppure carica un'immagine della firma."),
                             objectName="Muted"))
        self.canvas = SignatureCanvas()
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ border: 1px solid #D9DEE7; border-radius: 8px; }}")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        fl.addWidget(self.canvas)
        lay.addWidget(frame, 1)
        saved = load_bytes("firma.png")
        self.b_saved = button(tr("Usa la firma salvata"), "signature")
        self.b_saved.setVisible(saved is not None)
        self.b_saved.clicked.connect(lambda: self._load(saved))
        self.c_keep = Toggle(tr("Ricorda questa firma per la prossima volta"))
        self.c_keep.setChecked(True)
        b_clear = button(tr("Cancella"), "trash", "Ghost")
        b_file = button(tr("Carica immagine..."), "open")
        b_ok = button(tr("Inserisci firma"), "signature", "Primary")
        b_cancel = button(tr("Annulla"))
        b_clear.clicked.connect(self.canvas.clear)
        b_file.clicked.connect(self._pick)
        b_ok.clicked.connect(self._accept)
        b_cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        for w in (b_clear, b_file, self.b_saved):
            row.addWidget(w)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(self.c_keep)
        row2 = QHBoxLayout()
        row2.addStretch()
        row2.addWidget(b_cancel)
        row2.addWidget(b_ok)
        lay.addLayout(row2)

    def _load(self, data: bytes | None):
        pic = QPixmap()
        if data and pic.loadFromData(data):
            self.canvas.paths, self.canvas.picture = [], pic
            self.canvas.update()

    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Immagine della firma"), "", tr("Immagini (*.png *.jpg *.jpeg)"))
        if path:
            self._load(Path(path).read_bytes())

    def _accept(self):
        self.png = self.canvas.png()
        if not self.png:
            warn(self, tr("Il riquadro è vuoto: firma col mouse o carica un'immagine."))
            return
        if self.c_keep.isChecked():
            save_bytes("firma.png", self.png)
        self.accept()

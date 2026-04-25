"""
캘리브레이션 도구
- 게임에서 카드 보상 화면을 띄운 상태로 실행
- 마우스로 카드 이름 영역 3개를 드래그해서 지정
- 감지용 픽셀도 지정
- capture_config.json에 저장
"""
import sys
import json
import numpy as np
from pathlib import Path
from PIL import Image
import mss

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QRubberBand, QMessageBox
)
from PyQt6.QtCore import Qt, QRect, QPoint, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QFont

from capture import CaptureConfig, Region

CONFIG_PATH = Path(__file__).parent / "capture_config.json"


class OverlayCanvas(QLabel):
    """스크린샷 위에 드래그로 영역 선택"""
    region_selected = pyqtSignal(QRect)

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.setPixmap(pixmap)
        self._origin = QPoint()
        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, e):
        self._origin = e.pos()
        self._rubber.setGeometry(QRect(self._origin, QSize()))
        self._rubber.show()

    def mouseMoveEvent(self, e):
        self._rubber.setGeometry(QRect(self._origin, e.pos()).normalized())

    def mouseReleaseEvent(self, e):
        self._rubber.hide()
        rect = QRect(self._origin, e.pos()).normalized()
        if rect.width() > 10 and rect.height() > 10:
            self.region_selected.emit(rect)


class ModeSelectWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("캘리브레이션 모드 선택")
        self.setStyleSheet("background:#1a1a2e;color:white;")
        layout = QVBoxLayout(self)
        lbl = QLabel("어떤 화면을 캘리브레이션할까요?")
        lbl.setStyleSheet("font-size:14px;padding:10px;")
        layout.addWidget(lbl)
        for text, mode in [("보상 카드 화면 (카드 3장 선택)", "reward"), ("상점 화면 (카드 구매)", "shop")]:
            btn = QPushButton(text)
            btn.setStyleSheet("background:#222;color:white;border:1px solid #444;border-radius:4px;padding:10px;font-size:13px;")
            btn.clicked.connect(lambda _, m=mode: self._open(m))
            layout.addWidget(btn)
        self.setFixedWidth(400)
        self.show()

    def _open(self, mode: str):
        self._cal = CalibrateWindow(mode)
        self._cal.show()
        self.close()


class CalibrateWindow(QWidget):
    def __init__(self, mode: str = "reward"):
        super().__init__()
        self._mode = mode
        mode_text = "보상 카드" if mode == "reward" else "상점"
        self.setWindowTitle(f"STS2 Overlay - 캘리브레이션 [{mode_text}]")
        self.config = CaptureConfig.load()
        self.regions: list[QRect] = []
        self.detect_pixel: dict = {}
        self._max_cards = 3 if mode == "reward" else 7
        self._phase = "cards"  # "cards" → "pixel" → "done"

        self._screenshot = None
        self._scale = 1.0
        self._monitor_offset = (0, 0)

        self._build_ui()
        QTimer.singleShot(3000, self._delayed_screenshot)

    def _take_screenshot(self) -> Image.Image:
        from capture import _find_game_hwnd, _get_client_screen_rect
        with mss.mss() as sct:
            # 게임 창이 있는 모니터만 캡처 (듀얼 모니터 대응)
            mon = sct.monitors[0]  # 기본값: 전체 화면
            hwnd = _find_game_hwnd()
            if hwnd:
                rect = _get_client_screen_rect(hwnd)
                if rect:
                    cx, cy, cw, ch = rect
                    for m in sct.monitors[1:]:
                        ox = max(0, min(cx + cw, m["left"] + m["width"]) - max(cx, m["left"]))
                        oy = max(0, min(cy + ch, m["top"] + m["height"]) - max(cy, m["top"]))
                        if ox * oy > 0:
                            mon = m
                            break
            shot = sct.grab(mon)
            arr = np.array(shot)
        self._monitor_offset = (mon["left"], mon["top"])
        return Image.fromarray(arr[:, :, :3][:, :, ::-1])

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self._status = QLabel("3초 후 스크린샷을 찍습니다. 지금 게임 카드 보상 화면으로 전환하세요!")
        self._status.setFont(QFont("Malgun Gothic", 12))
        self._status.setStyleSheet("color: #ffdd57; background: #1a1a2e; padding: 8px; border-radius: 4px;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._canvas_placeholder = QLabel("잠시 기다리는 중...")
        self._canvas_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas_placeholder.setStyleSheet("color:#888; background:#0d0d1a; min-height:200px;")
        layout.addWidget(self._canvas_placeholder)

        self._canvas = None
        self._layout = layout

        btn_row = QHBoxLayout()
        self._undo_btn = QPushButton("되돌리기")
        self._undo_btn.clicked.connect(self._undo)
        self._done_cards_btn = QPushButton("카드 지정 완료")
        self._done_cards_btn.clicked.connect(self._enter_pixel_phase)
        self._done_cards_btn.setEnabled(False)
        self._done_cards_btn.setVisible(self._mode == "shop")
        self._save_btn = QPushButton("저장 완료")
        self._save_btn.clicked.connect(self._save)
        self._save_btn.setEnabled(False)
        btn_row.addWidget(self._undo_btn)
        btn_row.addWidget(self._done_cards_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        layout.addLayout(btn_row)

    def _delayed_screenshot(self):
        self._screenshot = self._take_screenshot()

        screen = QApplication.primaryScreen().geometry()
        max_w = screen.width() - 40
        max_h = screen.height() - 150
        img_w, img_h = self._screenshot.size
        self._scale = min(max_w / img_w, max_h / img_h, 1.0)
        disp_w = int(img_w * self._scale)
        disp_h = int(img_h * self._scale)

        pil_resized = self._screenshot.resize((disp_w, disp_h), Image.LANCZOS)
        data = pil_resized.tobytes("raw", "RGB")
        from PyQt6.QtGui import QImage
        qimg = QImage(data, disp_w, disp_h, disp_w * 3, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)

        self._canvas_placeholder.hide()
        self._canvas = OverlayCanvas(self._pixmap)
        self._canvas.region_selected.connect(self._on_region)
        self._layout.insertWidget(1, self._canvas)

        self._update_status()
        self.showMaximized()

    def _update_status(self):
        n = len(self.regions)
        if self._phase == "cards":
            nums = "①②③④⑤⑥⑦"
            num = nums[n] if n < len(nums) else f"{n+1}"
            if self._mode == "shop" and n >= 1:
                msg = f"{num} 카드 {n+1}번 이름 영역을 드래그하세요 (최대 {self._max_cards}장, 완료 시 '카드 지정 완료' 버튼)"
            else:
                msg = f"{num} 카드 {n+1}번 이름 영역을 드래그하세요"
            self._status.setText(msg)
        elif self._phase == "pixel":
            self._status.setText(f"카드 {n}장 지정됨. 이제 이 화면에서만 보이는 특징적인 픽셀 하나를 클릭하세요 (감지용)")
        else:
            self._status.setText("✓ 완료! 저장 버튼을 누르세요")

    def _enter_pixel_phase(self):
        if self._phase != "cards" or not self.regions:
            return
        self._phase = "pixel"
        self._done_cards_btn.setEnabled(False)
        self._canvas.region_selected.disconnect(self._on_region)
        self._canvas.mousePressEvent = self._pick_pixel
        self._canvas.mouseReleaseEvent = lambda e: None
        self._canvas.mouseMoveEvent = lambda e: None
        self._update_status()

    def _on_region(self, rect: QRect):
        if self._phase != "cards":
            return
        self.regions.append(rect)
        self._draw_regions()
        if self._mode == "shop" and len(self.regions) >= 1:
            self._done_cards_btn.setEnabled(True)
        if len(self.regions) >= self._max_cards:
            self._enter_pixel_phase()
        else:
            self._update_status()

    def _pick_pixel(self, e):
        x = int(e.pos().x() / self._scale)
        y = int(e.pos().y() / self._scale)
        r, g, b = self._screenshot.getpixel((x, y))
        ox, oy = self._monitor_offset
        self.detect_pixel = {"x": ox + x, "y": oy + y, "r": r, "g": g, "b": b, "tolerance": 25}
        self._phase = "done"
        self._save_btn.setEnabled(True)
        self._update_status()

    def _draw_regions(self):
        pix = self._pixmap.copy()
        painter = QPainter(pix)
        colors = [QColor("#e74c3c"), QColor("#2ecc71"), QColor("#3498db")]
        for i, rect in enumerate(self.regions):
            pen = QPen(colors[i % 3], 2)
            painter.setPen(pen)
            painter.drawRect(rect)
            painter.drawText(rect.topLeft() + QPoint(4, 14), f"카드{i+1}")
        painter.end()
        self._canvas.setPixmap(pix)

    def _undo(self):
        if self._phase == "done":
            # 픽셀 선택 취소 → 픽셀 단계로 돌아감
            self.detect_pixel = {}
            self._phase = "pixel"
            self._save_btn.setEnabled(False)
            self._update_status()
            return
        if self._phase == "pixel":
            # 픽셀 단계 취소 → 카드 단계로 돌아감
            self._phase = "cards"
            self._canvas.region_selected.connect(self._on_region)
            self._canvas.mousePressEvent = OverlayCanvas.mousePressEvent.__get__(self._canvas)
            self._canvas.mouseReleaseEvent = OverlayCanvas.mouseReleaseEvent.__get__(self._canvas)
            self._canvas.mouseMoveEvent = OverlayCanvas.mouseMoveEvent.__get__(self._canvas)
            self._save_btn.setEnabled(False)
            if self._mode == "shop":
                self._done_cards_btn.setEnabled(len(self.regions) >= 1)
            self._update_status()
            return
        if self.regions:
            self.regions.pop()
            self._draw_regions() if self.regions else self._canvas.setPixmap(self._pixmap)
            if self._mode == "shop":
                self._done_cards_btn.setEnabled(len(self.regions) >= 1)
            self._save_btn.setEnabled(False)
            self._update_status()

    def _save(self):
        scale = self._scale
        ox, oy = self._monitor_offset
        card_regions = []
        for rect in self.regions:
            card_regions.append(Region(
                x=ox + int(rect.x() / scale),
                y=oy + int(rect.y() / scale),
                w=int(rect.width() / scale),
                h=int(rect.height() / scale),
            ))
        cfg = self.config  # 기존 설정 유지하면서 해당 모드만 업데이트
        if self._mode == "reward":
            cfg.card_regions = card_regions
            cfg.detect_pixel = self.detect_pixel
        else:
            cfg.shop_card_regions = card_regions
            cfg.shop_detect_pixel = self.detect_pixel
        cfg.save()
        mode_text = "보상 카드" if self._mode == "reward" else "상점"
        QMessageBox.information(self, "저장 완료",
            f"[{mode_text}] 카드 영역 {len(card_regions)}개 저장됨\n{CONFIG_PATH}")
        self.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Malgun Gothic", 10))
    w = ModeSelectWindow()
    sys.exit(app.exec())

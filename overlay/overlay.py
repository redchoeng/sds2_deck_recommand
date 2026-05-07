"""
STS2 카드 추천 오버레이 (스크린 캡처 + OCR 버전)
실행 순서:
  1. python calibrate.py  ← 최초 1회, 카드 영역 설정
  2. python overlay.py    ← 게임 실행 중에 함께 실행
"""
import sys
import json
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

def _bundle_dir() -> Path:
    """PyInstaller 번들이면 _internal, 아니면 스크립트 위치"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent

def _exe_dir() -> Path:
    """실행 파일 옆 디렉토리 (deck.json 같은 쓰기 가능 파일용)"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent

_BUNDLE = _bundle_dir()
_EXE_DIR = _exe_dir()
_LOG_PATH = _EXE_DIR / "ocr_debug.log"

def _log(msg: str):
    line = f"{datetime.datetime.now().strftime('%H:%M:%S')} {msg}\n"
    _LOG_PATH.open("a", encoding="utf-8").write(line)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QFrame, QPushButton, QDialog, QButtonGroup, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QCursor

from engine import RecommendEngine, CardChoice
from capture import CaptureLoop, ScreenCapture, Region
from matcher import CardMatcher
import claude_ocr

TIER_COLORS  = {"S": "#e84057", "A": "#ff8c00", "B": "#2ecc71", "C": "#5b9bd5", "D": "#888", "F": "#555"}
ACTION_COLORS = {"pick": "#2ecc71", "skip": "#e84057"}
CARDS_PATH = _BUNDLE / "cards.json"
DECK_PATH  = _EXE_DIR / "deck.json"

STARTING_DECKS = {
    "IC": ["타격"] * 4 + ["수비"] * 4 + ["강타"],
    "SI": ["타격"] * 4 + ["수비"] * 4 + ["무력화", "생존자"],
    "DE": ["타격"] * 4 + ["수비"] * 4 + ["이중 시전", "파지직"],
    "NE": ["타격"] * 4 + ["수비"] * 4 + ["풀어놓기", "호위"],
    "RE": ["타격"] * 4 + ["수비"] * 4 + ["별똥별", "추앙"],
}
CHAR_NAMES = {"IC": "아이언클래드", "SI": "사일런트", "DE": "디펙트", "NE": "네크로바인더", "RE": "리젠트"}
CHAR_ICONS = {
    "IC": ("⚔", "#c0392b"),
    "SI": ("🗝", "#16a085"),
    "DE": ("⚡", "#2475b0"),
    "NE": ("☽", "#7d3c98"),
    "RE": ("♔", "#c8a84b"),
}

# 전체 창 스타일
WINDOW_BG  = "#0d0d1a"
PANEL_BG   = "#11111f"
HEADER_BG  = "#07070f"
DIVIDER    = "#1e1e32"
GOLD       = "#c8a84b"
TEXT_DIM   = "#6b7280"
TEXT_MAIN  = "#e8e0d0"


class Bridge(QObject):
    cards_ready    = pyqtSignal(list, str)   # cards, mode
    status_update  = pyqtSignal(str)
    screen_gone    = pyqtSignal()
    combat_ready   = pyqtSignal(dict, str)   # state, rec_text

bridge = Bridge()


def load_cards_json() -> dict:
    return json.loads(CARDS_PATH.read_text(encoding="utf-8"))


def save_card_tier(card_name: str, tier: str):
    data = load_cards_json()
    for char_cards in data["cards"].values():
        for card in char_cards:
            if card["n"] == card_name:
                card["tier"] = tier
                CARDS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return


# ── 새 게임 다이얼로그 ─────────────────────────────────────────────────────────
class NewGameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("새 게임 — 캐릭터 선택")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Dialog)
        self.setStyleSheet(f"background:{WINDOW_BG}; color:{TEXT_MAIN};")
        self.selected_char = None

        layout = QVBoxLayout(self)
        lbl = QLabel("캐릭터를 선택하세요")
        lbl.setStyleSheet(f"color:{GOLD};font-size:13px;font-weight:bold;padding:4px;")
        layout.addWidget(lbl)

        for char_key, char_name in CHAR_NAMES.items():
            icon, color = CHAR_ICONS.get(char_key, ("?", "#888"))
            btn = QPushButton(f"  {icon}  {char_name}")
            btn.setStyleSheet(
                f"QPushButton{{background:{PANEL_BG};color:{TEXT_MAIN};border:1px solid {DIVIDER};"
                f"border-left:3px solid {color};border-radius:4px;padding:8px;font-size:12px;text-align:left;}}"
                f"QPushButton:hover{{background:{color}22;border-left-color:{color};}}"
            )
            btn.clicked.connect(lambda _, k=char_key: self._pick(k))
            layout.addWidget(btn)

    def _pick(self, char_key: str):
        self.selected_char = char_key
        self.accept()


# ── 티어 수정 다이얼로그 ──────────────────────────────────────────────────────
class TierDialog(QDialog):
    def __init__(self, card_name: str, current_tier: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"티어 수정 — {card_name}")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Dialog)
        self.selected_tier = current_tier
        self.setStyleSheet(f"background:{WINDOW_BG}; color:{TEXT_MAIN};")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{card_name}</b> 티어:"))

        btn_row = QHBoxLayout()
        self._group = QButtonGroup(self)
        for tier in ["S", "A", "B", "C", "D", "F"]:
            btn = QPushButton(tier)
            btn.setCheckable(True)
            btn.setFixedSize(38, 32)
            color = TIER_COLORS.get(tier, "#fff")
            btn.setStyleSheet(
                f"QPushButton{{background:{PANEL_BG};color:{color};border:2px solid {color}33;"
                f"border-radius:4px;font-weight:bold;font-size:12px;}}"
                f"QPushButton:checked{{background:{color};color:#000;}}"
                f"QPushButton:hover{{background:{color}44;}}"
            )
            if tier == current_tier:
                btn.setChecked(True)
            btn.clicked.connect(lambda _, t=tier: setattr(self, "selected_tier", t))
            self._group.addButton(btn)
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        confirm = QPushButton("저장")
        confirm.setStyleSheet(
            f"background:{GOLD};color:#000;font-weight:bold;padding:7px;border-radius:4px;font-size:12px;"
        )
        confirm.clicked.connect(self.accept)
        layout.addWidget(confirm)


# ── 카드 위젯 (컴팩트 HDT 스타일) ────────────────────────────────────────────
class CardWidget(QFrame):
    picked       = pyqtSignal(str)
    skipped      = pyqtSignal()
    tier_changed = pyqtSignal(str, str)

    def __init__(self, rec, rank: int, mode: str = "reward"):
        super().__init__()
        self.rec   = rec
        self._mode = mode
        self.setObjectName("card")

        is_top  = rank == 0
        is_skip = rec.action == "skip" and is_top

        border_color = "#e8403344" if is_skip else (f"{GOLD}66" if is_top else DIVIDER)
        bg_color     = "#1a0808"   if is_skip else (f"#0f0f1e" if is_top else PANEL_BG)

        self.setStyleSheet(
            f"QFrame#card{{background:{bg_color};border:1px solid {border_color};"
            f"border-radius:6px;}}"
        )

        main = QVBoxLayout(self)
        main.setContentsMargins(8, 6, 8, 5)
        main.setSpacing(2)

        # ── 상단 행: [티어] 이름 ── 액션 ──
        top = QHBoxLayout()
        top.setSpacing(6)

        # 티어 뱃지
        tier = self._get_tier()
        color = TIER_COLORS.get(tier, "#888")
        tier_lbl = QLabel(tier)
        tier_lbl.setFixedSize(20, 20)
        tier_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tier_lbl.setStyleSheet(
            f"background:{color};color:#000;font-size:10px;font-weight:bold;"
            f"border-radius:3px;"
        )
        tier_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        tier_lbl.mousePressEvent = lambda _: self._edit_tier()
        top.addWidget(tier_lbl)

        # 순위 + 카드명
        rank_str = "★ " if is_top else f"{rank+1}. "
        name_lbl = QLabel(f"{rank_str}{rec.card_name}")
        name_lbl.setStyleSheet(
            f"color:{'#ffd700' if is_top else TEXT_MAIN};"
            f"font-size:{'13px' if is_top else '12px'};"
            f"font-weight:{'bold' if is_top else 'normal'};"
        )
        top.addWidget(name_lbl, stretch=1)

        # 액션 표시
        if is_skip:
            action_lbl = QLabel("SKIP")
            action_lbl.setStyleSheet("color:#e84057;font-size:10px;font-weight:bold;")
        else:
            action_lbl = QLabel("PICK" if rec.action == "pick" else "")
            action_lbl.setStyleSheet(f"color:{GOLD};font-size:10px;font-weight:bold;")
        top.addWidget(action_lbl)

        main.addLayout(top)

        # ── 이유 텍스트 (작고 흐릿하게) ──
        if rec.reason and not is_skip:
            reason_lbl = QLabel(rec.reason)
            reason_lbl.setWordWrap(True)
            reason_lbl.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;padding-left:26px;")
            main.addWidget(reason_lbl)
        elif is_skip:
            skip_lbl = QLabel("⚠ 모든 카드 가성비 낮음 — 스킵 권장")
            skip_lbl.setStyleSheet("color:#e84057;font-size:10px;padding-left:26px;")
            main.addWidget(skip_lbl)

        if rec.arch_name:
            arch_lbl = QLabel(f"▸ {rec.arch_name}")
            arch_lbl.setStyleSheet(f"color:#5dade2;font-size:10px;padding-left:26px;")
            main.addWidget(arch_lbl)

        # ── 버튼 행 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_row.addStretch()

        pick_label = "구매 ✓" if mode == "shop" else "뽑음 ✓"
        self._pick_btn = QPushButton(pick_label)
        self._pick_btn.setFixedHeight(20)
        self._pick_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:#2ecc71;border:1px solid #2ecc7166;"
            f"border-radius:3px;font-size:10px;padding:0 8px;}}"
            f"QPushButton:hover{{background:#2ecc7122;}}"
        )
        self._pick_btn.clicked.connect(self._on_pick_clicked)
        btn_row.addWidget(self._pick_btn)

        if is_top and mode == "reward":
            skip_btn = QPushButton("넘기기 →")
            skip_btn.setFixedHeight(20)
            skip_btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:#e84057;border:1px solid #e8405766;"
                f"border-radius:3px;font-size:10px;padding:0 8px;}}"
                f"QPushButton:hover{{background:#e8405722;}}"
            )
            skip_btn.clicked.connect(self.skipped)
            btn_row.addWidget(skip_btn)

        main.addLayout(btn_row)

    def _on_pick_clicked(self):
        done = "구매됨 ✓" if self._mode == "shop" else "추가됨 ✓"
        self._pick_btn.setText(done)
        self._pick_btn.setEnabled(False)
        self._pick_btn.setStyleSheet(
            "background:transparent;color:#555;border:1px solid #333;border-radius:3px;font-size:10px;padding:0 8px;"
        )
        self.picked.emit(self.rec.card_name)

    def _get_tier(self) -> str:
        data = load_cards_json()
        for char_cards in data["cards"].values():
            for card in char_cards:
                if card["n"] == self.rec.card_name:
                    return card.get("tier", "C")
        return "C"

    def _edit_tier(self):
        current = self._get_tier()
        dlg = TierDialog(self.rec.card_name, current, self)
        if dlg.exec():
            new_tier = dlg.selected_tier
            save_card_tier(self.rec.card_name, new_tier)
            self.tier_changed.emit(self.rec.card_name, new_tier)


# ── 빌드 패널 ────────────────────────────────────────────────────────────────
class ArchPanel(QFrame):
    build_selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{PANEL_BG};border-radius:6px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        hdr = QHBoxLayout()
        lbl = QLabel("목표 빌드")
        lbl.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;letter-spacing:1px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        auto_btn = QPushButton("자동")
        auto_btn.setFixedHeight(16)
        auto_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{TEXT_DIM};border:1px solid {DIVIDER};"
            f"border-radius:3px;font-size:9px;padding:0 5px;}}"
            f"QPushButton:hover{{color:{TEXT_MAIN};}}"
        )
        auto_btn.clicked.connect(lambda: self.build_selected.emit(""))
        hdr.addWidget(auto_btn)
        layout.addLayout(hdr)

        self._btn_container = QVBoxLayout()
        self._btn_container.setSpacing(2)
        layout.addLayout(self._btn_container)

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(f"color:{GOLD};font-size:11px;font-weight:bold;")
        layout.addWidget(self._stats_label)

        self._need_label = QLabel("")
        self._need_label.setWordWrap(True)
        self._need_label.setStyleSheet("color:#e84057;font-size:10px;")
        layout.addWidget(self._need_label)

        self._rec_label = QLabel("")
        self._rec_label.setWordWrap(True)
        self._rec_label.setStyleSheet("color:#2ecc71;font-size:10px;")
        layout.addWidget(self._rec_label)

    def set_builds(self, all_status: list[dict], selected_id: str | None):
        while self._btn_container.count():
            item = self._btn_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for s in all_status:
            arch     = s["arch"]
            arch_id  = arch.get("id", arch["name"])
            color    = arch.get("color", "#888")
            is_sel   = arch_id == selected_id
            pct      = s["pct"]
            btn = QPushButton(f"{arch['name']}  {pct}%")
            btn.setFixedHeight(20)
            btn.setStyleSheet(
                f"QPushButton{{background:{''+color+'22' if is_sel else 'transparent'};"
                f"color:{'white' if is_sel else TEXT_DIM};"
                f"border:{'1px solid '+color if is_sel else '1px solid transparent'};"
                f"border-radius:3px;font-size:10px;text-align:left;padding:0 6px;}}"
                f"QPushButton:hover{{color:white;background:{color}22;}}"
            )
            btn.clicked.connect(lambda _, aid=arch_id: self.build_selected.emit(aid))
            self._btn_container.addWidget(btn)

    def update_status(self, status: dict | None):
        if not status:
            self._stats_label.setText("덱을 초기화하면 빌드가 감지돼요")
            self._need_label.setText("")
            self._rec_label.setText("")
            return

        arch      = status["arch"]
        must_have = len(status["must_have"])
        must_need = status["must_need"]
        rec_need  = status["rec_need"]
        pct       = status["pct"]
        color     = arch.get("color", GOLD)

        self._stats_label.setText(
            f"필수 {must_have}/{len(arch.get('must', []))}  ·  완성도 {pct}%"
        )
        self._stats_label.setStyleSheet(f"color:{color};font-size:11px;font-weight:bold;")

        if must_need:
            self._need_label.setText("필수: " + "  ".join(must_need))
            self._need_label.setStyleSheet("color:#e84057;font-size:10px;")
        else:
            self._need_label.setText("✓ 필수 완성!")
            self._need_label.setStyleSheet("color:#2ecc71;font-size:10px;font-weight:bold;")

        self._rec_label.setText(
            "추천 미보유: " + "  ".join(rec_need[:4]) if rec_need else ""
        )


# ── 덱 편집 다이얼로그 ───────────────────────────────────────────────────────
class DeckEditDialog(QDialog):
    card_removed = pyqtSignal(str)

    def __init__(self, deck: list[str], suggestions: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("덱 편집")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Dialog)
        self.setStyleSheet(f"background:{WINDOW_BG};color:{TEXT_MAIN};")
        self.setFixedWidth(280)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        hdr = QLabel(f"덱 편집  ({len(deck)}장)  —  티어 뱃지를 클릭해 강화 우선도 확인")
        hdr.setStyleSheet(f"color:{GOLD};font-size:11px;font-weight:bold;")
        root.addWidget(hdr)

        # 강화 우선도 맵
        up_map = {name: (priority, reason) for name, priority, reason in suggestions}
        UP_COLOR = {"★★★ 필수": "#e84057", "★★ 권장": "#f39c12", "★ 보통": TEXT_DIM, "✕ 제거 고려": "#555"}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        scroll.setMaximumHeight(420)

        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        vbox = QVBoxLayout(inner)
        vbox.setSpacing(2)
        vbox.setContentsMargins(0, 0, 0, 0)

        # 덱 카드 집계 (중복 카드 count)
        from collections import Counter
        counts = Counter(deck)
        for name, cnt in sorted(counts.items()):
            priority, reason = up_map.get(name, ("★ 보통", ""))
            color = UP_COLOR.get(priority, TEXT_DIM)

            row = QHBoxLayout()
            row.setSpacing(4)

            # 우선도 뱃지
            badge = QLabel(priority.split()[0])   # 별 부분만
            badge.setFixedWidth(28)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"color:{color};font-size:10px;")
            badge.setToolTip(f"{priority} — {reason}")
            row.addWidget(badge)

            # 카드명 (+ 장수)
            cnt_str = f" ×{cnt}" if cnt > 1 else ""
            name_lbl = QLabel(f"{name}{cnt_str}")
            name_lbl.setStyleSheet(f"color:{TEXT_MAIN};font-size:11px;")
            row.addWidget(name_lbl, stretch=1)

            # 제거 버튼
            rm_btn = QPushButton("✕")
            rm_btn.setFixedSize(20, 20)
            rm_btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{TEXT_DIM};border:none;font-size:10px;}}"
                f"QPushButton:hover{{color:#e84057;}}"
            )
            rm_btn.clicked.connect(lambda _, n=name: self.card_removed.emit(n))
            row.addWidget(rm_btn)

            frame = QFrame()
            frame.setStyleSheet(f"background:{PANEL_BG};border-radius:4px;")
            frame.setLayout(row)
            frame.setFixedHeight(28)
            vbox.addWidget(frame)

        vbox.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # 강화 우선도 범례
        legend = QLabel("★★★ 필수  ★★ 권장  ★ 보통  ✕ 제거 고려")
        legend.setStyleSheet(f"color:{TEXT_DIM};font-size:9px;")
        root.addWidget(legend)

        close_btn = QPushButton("닫기")
        close_btn.setStyleSheet(
            f"QPushButton{{background:{PANEL_BG};color:{TEXT_MAIN};border:1px solid {DIVIDER};"
            f"border-radius:4px;padding:5px;font-size:11px;}}"
            f"QPushButton:hover{{background:{DIVIDER};}}"
        )
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn)


# ── 덱 패널 ──────────────────────────────────────────────────────────────────
class DeckPanel(QFrame):
    reset_clicked = pyqtSignal()
    edit_clicked  = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{PANEL_BG};border-radius:6px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        hdr = QHBoxLayout()
        self._title = QLabel("덱  0장")
        self._title.setStyleSheet(f"color:{GOLD};font-size:11px;font-weight:bold;")
        hdr.addWidget(self._title)
        hdr.addStretch()

        edit_btn = QPushButton("편집")
        edit_btn.setFixedHeight(18)
        edit_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{TEXT_DIM};border:1px solid {DIVIDER};"
            f"border-radius:3px;font-size:9px;padding:0 6px;}}"
            f"QPushButton:hover{{color:{TEXT_MAIN};}}"
        )
        edit_btn.clicked.connect(self.edit_clicked)
        hdr.addWidget(edit_btn)

        reset_btn = QPushButton("새 게임")
        reset_btn.setFixedHeight(18)
        reset_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:#e84057;border:1px solid #e8405766;"
            f"border-radius:3px;font-size:9px;padding:0 6px;}}"
            f"QPushButton:hover{{background:#e8405722;}}"
        )
        reset_btn.clicked.connect(self.reset_clicked)
        hdr.addWidget(reset_btn)
        layout.addLayout(hdr)

        self._deck_label = QLabel("")
        self._deck_label.setWordWrap(True)
        self._deck_label.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;")
        layout.addWidget(self._deck_label)

    def update_deck(self, deck: list[str]):
        self._title.setText(f"덱  {len(deck)}장")
        self._deck_label.setText("  ".join(deck) if deck else "비어있음")


# ── 전투 분석 패널 ───────────────────────────────────────────────────────────
class CombatPanel(QFrame):
    refresh_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"QFrame{{background:{PANEL_BG};border-radius:6px;}}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("⚔ 전투 분석")
        title.setStyleSheet("color:#e84057;font-size:11px;font-weight:bold;")
        hdr.addWidget(title)
        hdr.addStretch()
        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedSize(20, 20)
        refresh_btn.setToolTip("다시 분석")
        refresh_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{TEXT_DIM};border:1px solid {DIVIDER};"
            f"border-radius:3px;font-size:12px;}}"
            f"QPushButton:hover{{color:{TEXT_MAIN};}}"
        )
        refresh_btn.clicked.connect(self.refresh_clicked)
        hdr.addWidget(refresh_btn)
        layout.addLayout(hdr)

        self._state_label = QLabel("분석 대기 중...")
        self._state_label.setWordWrap(True)
        self._state_label.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;")
        layout.addWidget(self._state_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{DIVIDER};max-height:1px;")
        layout.addWidget(sep)

        rec_scroll = QScrollArea()
        rec_scroll.setWidgetResizable(True)
        rec_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rec_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        rec_scroll.setMaximumHeight(240)
        self._rec_label = QLabel("—")
        self._rec_label.setWordWrap(True)
        self._rec_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._rec_label.setStyleSheet(f"color:{TEXT_MAIN};font-size:10px;padding:2px;")
        rec_scroll.setWidget(self._rec_label)
        layout.addWidget(rec_scroll)

    def set_loading(self):
        self._state_label.setText("Gemini 분석 중...")
        self._state_label.setStyleSheet("color:#f39c12;font-size:10px;")
        self._rec_label.setText("...")

    def set_result(self, state: dict | None, rec: str):
        if state:
            energy = f"E:{state.get('energy')}/{state.get('energy_max')}"
            hp = f"HP:{state.get('player_hp')}/{state.get('player_hp_max')}"
            block = f"방어:{state.get('player_block', 0)}"
            enemy_strs = []
            for i, e in enumerate(state.get("enemies", [])):
                name = e.get("name") or f"적{i+1}"
                if e.get("intent") == "attack":
                    intent = f"⚔{e.get('intent_value', '?')}"
                else:
                    intent = str(e.get("intent", "?"))[:6]
                enemy_strs.append(f"{name} {e.get('hp')}/{e.get('hp_max')} ({intent})")
            self._state_label.setText(
                f"{energy}  {hp}  {block}\n" + "  ".join(enemy_strs)
            )
            self._state_label.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;")
        else:
            self._state_label.setText("화면 인식 실패")
            self._state_label.setStyleSheet("color:#e84057;font-size:10px;")
        self._rec_label.setText(rec)

    def set_error(self, msg: str):
        self._state_label.setText(f"오류: {msg}")
        self._state_label.setStyleSheet("color:#e84057;font-size:10px;")
        self._rec_label.setText("—")


# ── 메인 오버레이 창 ──────────────────────────────────────────────────────────
class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.engine      = RecommendEngine(str(CARDS_PATH))
        self.matcher     = CardMatcher(str(CARDS_PATH))
        self._screen     = ScreenCapture()   # Claude OCR 폴백용 캡처
        claude_ocr.init()                    # .api_key 파일 또는 환경변수 자동 로드
        self.current_deck: list[str] = self._load_deck()

        # Gemini 초기화
        self._gemini_ready = False
        try:
            import gemini_client
            import combat_recommender
            try:
                gemini_client.init()
            except FileNotFoundError:
                key = self._ask_api_key()
                if key:
                    gemini_client.init(api_key=key)
                    gemini_client.API_KEY_FILE.write_text(key)
            combat_recommender.set_engine(self.engine)
            self._gemini_ready = True
        except Exception as e:
            _log(f"[Gemini] 초기화 실패: {e}")

        self.setWindowTitle("STS2 Overlay")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint
        )
        self.setFixedWidth(260)
        self.setWindowOpacity(0.95)
        self.setStyleSheet(f"""
            QWidget {{ background: {WINDOW_BG}; color: {TEXT_MAIN}; }}
            QScrollArea {{ background: {WINDOW_BG}; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: {WINDOW_BG}; }}
            QScrollBar:vertical {{ width:8px; background:#1a1a2e; border-radius:4px; }}
            QScrollBar::handle:vertical {{ background:#4a4a70; border-radius:4px; min-height:24px; }}
            QScrollBar::handle:vertical:hover {{ background:#6a6a99; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 헤더 바 ──────────────────────────────────────────────────────────
        header_frame = QFrame()
        header_frame.setStyleSheet(f"background:{HEADER_BG};border-bottom:1px solid {DIVIDER};")
        header_frame.setFixedHeight(36)
        hdr = QHBoxLayout(header_frame)
        hdr.setContentsMargins(8, 0, 6, 0)
        hdr.setSpacing(6)

        # 캐릭터 아이콘
        self._char_icon = QLabel("⚔")
        self._char_icon.setFixedSize(22, 22)
        self._char_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._char_icon.setStyleSheet(
            f"background:#c0392b;color:#fff;font-size:11px;border-radius:11px;"
        )
        hdr.addWidget(self._char_icon)

        # 타이틀
        self._title_label = QLabel("STS2 추천")
        self._title_label.setStyleSheet(f"color:{GOLD};font-size:12px;font-weight:bold;")
        hdr.addWidget(self._title_label)

        # 접힌 상태 상태 표시 (대기/감지)
        self._dot_label = QLabel("● 대기")
        self._dot_label.setStyleSheet(f"color:{TEXT_DIM};font-size:9px;")
        self._dot_label.setVisible(True)
        hdr.addWidget(self._dot_label)
        hdr.addStretch()

        # 접기/펼치기 버튼
        self._toggle_btn = QPushButton("▾")
        self._toggle_btn.setFixedSize(24, 24)
        self._toggle_btn.setToolTip("패널 접기/펼치기")
        self._toggle_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{TEXT_DIM};border:none;font-size:13px;}}"
            f"QPushButton:hover{{color:{TEXT_MAIN};}}"
        )
        self._toggle_btn.clicked.connect(self._toggle_panels)
        hdr.addWidget(self._toggle_btn)

        # 카드 분석 버튼 (Gemini Vision)
        self._card_analysis_btn = QPushButton("★")
        self._card_analysis_btn.setFixedSize(24, 24)
        self._card_analysis_btn.setToolTip("카드 분석 (Gemini — 현재 화면 캡처)")
        self._card_analysis_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{GOLD};border:none;font-size:13px;}}"
            f"QPushButton:hover{{color:#ffd700;}}"
            f"QPushButton:disabled{{color:#555;}}"
        )
        self._card_analysis_btn.setEnabled(self._gemini_ready)
        self._card_analysis_btn.clicked.connect(self._trigger_card_analysis)
        hdr.addWidget(self._card_analysis_btn)

        # 캘리브레이션 버튼
        cal_btn = QPushButton("⚙")
        cal_btn.setFixedSize(24, 24)
        cal_btn.setToolTip("캘리브레이션")
        cal_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{TEXT_DIM};border:none;font-size:13px;}}"
            f"QPushButton:hover{{color:{TEXT_MAIN};}}"
        )
        cal_btn.clicked.connect(self._open_calibrate)
        hdr.addWidget(cal_btn)

        # 종료 버튼
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{TEXT_DIM};border:none;font-size:13px;}}"
            f"QPushButton:hover{{color:#e84057;}}"
        )
        close_btn.clicked.connect(QApplication.quit)
        hdr.addWidget(close_btn)

        root.addWidget(header_frame)

        # ── 상태 표시줄 ───────────────────────────────────────────────────────
        self._status_label = QLabel("게임 화면을 인식하는 중...")
        self._status_label.setStyleSheet(
            f"color:{TEXT_DIM};font-size:10px;padding:4px 10px;"
            f"background:{HEADER_BG};border-bottom:1px solid {DIVIDER};"
        )
        root.addWidget(self._status_label)

        # ── 전투 패널 (기본 숨김) ────────────────────────────────────────────────
        self._combat_wrap = QWidget()
        self._combat_wrap.setStyleSheet("background:transparent;")
        cw_layout = QVBoxLayout(self._combat_wrap)
        cw_layout.setContentsMargins(6, 2, 6, 2)
        self._combat_panel = CombatPanel()
        self._combat_panel.refresh_clicked.connect(self._trigger_card_analysis)
        cw_layout.addWidget(self._combat_panel)
        self._combat_wrap.setVisible(False)
        root.addWidget(self._combat_wrap)

        # ── 카드 목록 (스크롤) ────────────────────────────────────────────────
        self._card_scroll = QScrollArea()
        self._card_scroll.setWidgetResizable(True)
        self._card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._card_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        screen_h = QApplication.primaryScreen().geometry().height()
        self._card_scroll.setMaximumHeight(int(screen_h * 0.45))
        self._card_scroll.setVisible(False)

        _card_widget = QWidget()
        _card_widget.setStyleSheet("background:transparent;")
        self._card_container = QVBoxLayout(_card_widget)
        self._card_container.setSpacing(3)
        self._card_container.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._card_container.setContentsMargins(6, 6, 6, 6)
        self._card_scroll.setWidget(_card_widget)
        root.addWidget(self._card_scroll)

        # ── 빌드 패널 ─────────────────────────────────────────────────────────
        self._arch_wrap = QWidget()
        self._arch_wrap.setStyleSheet("background:transparent;")
        aw = QVBoxLayout(self._arch_wrap)
        aw.setContentsMargins(6, 4, 6, 2)
        self._arch_panel = ArchPanel()
        self._arch_panel.build_selected.connect(self._on_build_selected)
        aw.addWidget(self._arch_panel)
        root.addWidget(self._arch_wrap)

        # ── 덱 패널 ───────────────────────────────────────────────────────────
        self._deck_wrap = QWidget()
        self._deck_wrap.setStyleSheet("background:transparent;")
        dw = QVBoxLayout(self._deck_wrap)
        dw.setContentsMargins(6, 2, 6, 6)
        self._deck_panel = DeckPanel()
        self._deck_panel.reset_clicked.connect(self._reset_deck)
        self._deck_panel.edit_clicked.connect(self._open_deck_edit)
        self._deck_panel.update_deck(self.current_deck)
        dw.addWidget(self._deck_panel)
        root.addWidget(self._deck_wrap)

        self._selected_arch_id: str | None = None
        self._panels_visible = True
        self._current_mode: str | None = None
        self._last_valid_cards: list[str] = []  # 마지막으로 표시한 카드 목록
        self._refresh_arch()

        bridge.cards_ready.connect(self._on_cards)
        bridge.status_update.connect(self._on_status)
        bridge.screen_gone.connect(self._on_screen_gone)
        bridge.combat_ready.connect(self._on_combat_result)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 270, 60)
        self._start_capture_loop()
        # 시작 시 접힘 상태
        self._set_panels_visible(False)

    # ── 유틸 ──────────────────────────────────────────────────────────────────
    def _load_deck(self) -> list[str]:
        if DECK_PATH.exists():
            return json.loads(DECK_PATH.read_text(encoding="utf-8"))
        return []

    def _save_deck(self):
        DECK_PATH.write_text(json.dumps(self.current_deck, ensure_ascii=False), encoding="utf-8")

    def _update_char_icon(self):
        char = self.engine._current_char(self.current_deck)
        if char and char in CHAR_ICONS:
            icon, color = CHAR_ICONS[char]
            self._char_icon.setText(icon)
            self._char_icon.setStyleSheet(
                f"background:{color};color:#fff;font-size:11px;border-radius:11px;"
            )
        else:
            self._char_icon.setText("⚔")
            self._char_icon.setStyleSheet(
                f"background:#333;color:#aaa;font-size:11px;border-radius:11px;"
            )

    # ── 이벤트 핸들러 ─────────────────────────────────────────────────────────
    def _reset_deck(self):
        dlg = NewGameDialog(self)
        if dlg.exec() and dlg.selected_char:
            char = dlg.selected_char
            self.current_deck = STARTING_DECKS.get(char, []).copy()
            self._save_deck()
            self._deck_panel.update_deck(self.current_deck)
            self._refresh_arch()
            self._status_label.setText(f"{CHAR_NAMES[char]} 시작 덱")
            self.adjustSize()

    def _on_build_selected(self, arch_id: str):
        self._selected_arch_id = arch_id if arch_id else None
        self._refresh_arch()

    def _refresh_arch(self):
        all_status = self.engine.all_arch_status(self.current_deck)
        status     = self.engine.deck_status(self.current_deck, self._selected_arch_id)
        sel_id     = self._selected_arch_id or (status["arch"].get("id") if status else None)
        self._arch_panel.set_builds(all_status, sel_id)
        self._arch_panel.update_status(status)
        self._update_char_icon()
        self.adjustSize()

    def _on_pick(self, card_name: str):
        from PyQt6.QtCore import QTimer
        if card_name not in self.current_deck:
            self.current_deck.append(card_name)
            self._save_deck()
            self._deck_panel.update_deck(self.current_deck)
            self._refresh_arch()

        if self._current_mode == "shop":
            # 구매한 카드 제외하고 나머지 재평가
            remaining = [c for c in self._last_valid_cards if c != card_name]
            if remaining:
                recs = self.engine.recommend(self.current_deck, CardChoice(cards=remaining, mode="shop"))
                if any(r.action == "pick" for r in recs):
                    # 아직 살 카드 있음 → 업데이트
                    self._last_valid_cards = remaining
                    bridge.cards_ready.emit(remaining, "shop")
                    return
            # 살 카드 없음 → 대기
            QTimer.singleShot(800, self._on_screen_gone)
        else:
            # 보상: 뽑으면 대기
            QTimer.singleShot(1200, self._on_screen_gone)

    def _on_tier_changed(self, card_name: str, new_tier: str):
        self.engine = RecommendEngine(str(CARDS_PATH))
        self._status_label.setText(f"{card_name} → {new_tier} 저장됨")

    def _set_panels_visible(self, visible: bool):
        self._panels_visible = visible
        self._arch_wrap.setVisible(visible)
        self._deck_wrap.setVisible(visible)
        self._status_label.setVisible(visible)
        self._dot_label.setVisible(not visible)  # 접힐 때만 상태 도트 표시
        self._toggle_btn.setText("▾" if visible else "▸")
        self.adjustSize()

    def _toggle_panels(self):
        self._set_panels_visible(not self._panels_visible)

    def _on_screen_gone(self):
        self._current_mode = None
        self._last_valid_cards = []
        while self._card_container.count():
            item = self._card_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._card_scroll.setVisible(False)
        self._title_label.setText("STS2 추천")
        self._title_label.setStyleSheet(f"color:{GOLD};font-size:12px;font-weight:bold;")
        self._status_label.setText("카드 선택 대기 중...")
        self._dot_label.setStyleSheet(f"color:{TEXT_DIM};font-size:9px;")
        self._dot_label.setText("● 대기")
        self._set_panels_visible(False)

    def _start_capture_loop(self):
        self._claude_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="claude")
        self._loop = CaptureLoop(
            on_cards_detected=self._ocr_callback,
            on_screen_gone=lambda: bridge.screen_gone.emit(),
        )
        self._loop.start()

    def _ocr_callback(self, candidates_list: list[list[str]], mode: str = "reward"):
        self._current_mode = mode
        # 카드 화면 감지됨 → 도트 녹색으로
        bridge.status_update.emit("__dot_active__")

        char = self.engine._current_char(self.current_deck)
        results = self.matcher.match_many_candidates(candidates_list, char=char)

        label = "보상" if mode == "reward" else "상점"
        for i, (cands, (name, score)) in enumerate(zip(candidates_list, results)):
            _log(f"[{label}] 영역{i}: OCR={repr(cands[0] if cands else '')} -> {repr(name)} ({score})")

        valid = [name for name, _ in results if name is not None]
        preview = [cands[0] if cands else "" for cands in candidates_list]
        bridge.status_update.emit(f"[{label}] {' / '.join(t for t in preview if t)}")
        # 매칭된 카드가 이전과 동일하면 UI 재렌더링 스킵 (OCR 노이즈로 인한 깜박임 방지)
        if valid and valid != self._last_valid_cards:
            self._last_valid_cards = valid
            bridge.cards_ready.emit(valid, mode)

        # Claude 폴백: 별도 스레드에서 실행 (OCR 루프 블로킹 방지)
        if claude_ocr.is_enabled():
            failed = [
                i for i, (name, score) in enumerate(results)
                if (name is None or score < 75)
            ]
            if failed:
                regions = (
                    self._screen.config.card_regions if mode == "reward"
                    else self._screen.config.shop_card_regions
                )
                # 이미지 미리 캡처 (메인 캡처 루프와 충돌 방지)
                imgs = {}
                for i in failed:
                    if i < len(regions):
                        imgs[i] = self._screen.capture_region(regions[i])
                self._claude_executor.submit(
                    self._claude_fallback, imgs, results, char, mode, label
                )

    def _claude_fallback(self, imgs: dict, results: list, char, mode: str, label: str):
        from PIL import Image
        updated = False
        for i, img in imgs.items():
            h, w = img.shape[:2]
            crop = img[int(h*0.12):int(h*0.68), int(w*0.10):int(w*0.95)]
            img_pil = Image.fromarray(crop[:, :, :3][:, :, ::-1])
            if img_pil.width < 200:
                img_pil = img_pil.resize((img_pil.width * 2, img_pil.height * 2), Image.LANCZOS)
            img_pil.save(Path(__file__).parent / f"debug_claude_{i}.png")
            claude_text = claude_ocr.ocr_card_image(img_pil, char=char)
            _log(f"[Claude raw] 영역{i}: {repr(claude_text)}")
            if claude_text and not claude_text.startswith(("죄송", "모름", "알 수", "이미지", "제시")):
                fallback, fscore = self.matcher.match_best_from_candidates(
                    [claude_text], char=char)
                if fallback:
                    results[i] = (fallback, fscore)
                    _log(f"[Claude] 영역{i}: {repr(claude_text)} -> {fallback} ({fscore})")
                    updated = True

        if updated and self._current_mode == mode:
            valid = [name for name, _ in results if name is not None]
            if valid:
                self._last_valid_cards = valid  # Claude 수정 결과도 기준점 갱신
                bridge.cards_ready.emit(valid, mode)

    def _on_status(self, msg: str):
        if msg == "__dot_active__":
            self._dot_label.setStyleSheet("color:#2ecc71;font-size:9px;")
            self._dot_label.setText("● 감지")
            return
        self._status_label.setText(msg)

    def _on_cards(self, offered: list[str], mode: str = "reward"):
        while self._card_container.count():
            item = self._card_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if mode == "shop":
            self._title_label.setText("STS2 상점")
            self._title_label.setStyleSheet("color:#2ecc71;font-size:12px;font-weight:bold;")
        else:
            self._title_label.setText("STS2 추천")
            self._title_label.setStyleSheet(f"color:{GOLD};font-size:12px;font-weight:bold;")

        recs = self.engine.recommend(self.current_deck, CardChoice(cards=offered, mode=mode))
        for i, rec in enumerate(recs):
            w = CardWidget(rec, i, mode=mode)
            w.picked.connect(self._on_pick)
            w.skipped.connect(self._on_screen_gone)
            w.tier_changed.connect(self._on_tier_changed)
            self._card_container.addWidget(w)

        self._status_label.setText("상점 카드 감지됨" if mode == "shop" else "카드 선택 화면 감지됨")
        self._card_scroll.setVisible(True)
        # 카드 선택 화면 진입 시 패널 자동 펼치기
        self._set_panels_visible(True)

    def _open_deck_edit(self):
        suggestions = self.engine.upgrade_suggestions(self.current_deck)
        dlg = DeckEditDialog(self.current_deck, suggestions, self)
        dlg.card_removed.connect(self._on_card_removed)
        dlg.exec()

    def _on_card_removed(self, card_name: str):
        if card_name in self.current_deck:
            self.current_deck.remove(card_name)
            self._save_deck()
            self._deck_panel.update_deck(self.current_deck)
            self._refresh_arch()

    def _ask_api_key(self) -> str | None:
        from PyQt6.QtWidgets import QInputDialog, QLineEdit
        key, ok = QInputDialog.getText(
            self, "Gemini API 키 입력",
            "Google AI Studio에서 발급한 API 키를 입력하세요:",
            QLineEdit.EchoMode.Normal
        )
        return key.strip() if ok and key.strip() else None

    def _trigger_card_analysis(self):
        if not self._gemini_ready:
            self._status_label.setText("Gemini 키 없음 — .api_key 확인")
            return
        self._status_label.setText("Gemini 분석 중...")
        self._card_analysis_btn.setEnabled(False)
        import threading
        threading.Thread(target=self._card_analysis_worker, daemon=True).start()

    def _card_analysis_worker(self):
        try:
            import mss
            from PIL import Image
            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(mon)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            import card_parser
            result = card_parser.parse(img)
            if result and result.get("cards"):
                mode = result.get("mode") or "reward"
                bridge.cards_ready.emit(result["cards"], mode)
                bridge.status_update.emit(f"[Gemini] {' / '.join(result['cards'])}")
            else:
                bridge.status_update.emit("카드 선택 화면을 찾지 못했습니다")
        except Exception as e:
            bridge.status_update.emit(f"Gemini 오류: {e}")
        finally:
            bridge.combat_ready.emit({}, "")  # 버튼 재활성화용 신호 재활용

    def _on_combat_result(self, state: dict, rec: str):
        self._card_analysis_btn.setEnabled(True)

    def _open_calibrate(self):
        import subprocess
        subprocess.Popen([sys.executable, "calibrate.py"])

    def mousePressEvent(self, e):
        self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if hasattr(self, "_drag_pos") and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Malgun Gothic", 10))
    app.setQuitOnLastWindowClosed(True)

    window = OverlayWindow()
    window.show()

    sys.exit(app.exec())

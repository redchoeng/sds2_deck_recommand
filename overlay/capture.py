"""
스크린 캡처 + OCR 모듈
- 게임 창 캡처
- 카드 보상 화면 감지
- 카드명 OCR
"""
import json
import os
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import asyncio
import io

import ctypes
import mss
import numpy as np
from PIL import Image

try:
    import winrt.windows.media.ocr as _winrt_ocr
    import winrt.windows.graphics.imaging as _winrt_img
    import winrt.windows.storage.streams as _winrt_streams
    from winrt.windows.globalization import Language as _WinLanguage
    _WINRT_OK = True
except ImportError:
    _WINRT_OK = False

CONFIG_PATH = Path(__file__).parent / "capture_config.json"

# 포그라운드 창이 게임 또는 오버레이일 때만 OCR 실행
_ALLOWED_FOREGROUND = ("slay", "sts2 overlay")

def _foreground_window_title() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if n == 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value

def _is_game_foreground() -> bool:
    title = _foreground_window_title().lower()
    return any(hint in title for hint in _ALLOWED_FOREGROUND)

@dataclass
class Region:
    x: int
    y: int
    w: int
    h: int

    def to_mss(self) -> dict:
        return {"left": self.x, "top": self.y, "width": self.w, "height": self.h}

@dataclass
class CaptureConfig:
    card_regions: list[Region] = field(default_factory=list)
    detect_pixel: dict = field(default_factory=dict)
    shop_card_regions: list[Region] = field(default_factory=list)
    shop_detect_pixel: dict = field(default_factory=dict)
    poll_interval: float = 0.8

    def save(self):
        data = {
            "card_regions": [{"x":r.x,"y":r.y,"w":r.w,"h":r.h} for r in self.card_regions],
            "detect_pixel": self.detect_pixel,
            "shop_card_regions": [{"x":r.x,"y":r.y,"w":r.w,"h":r.h} for r in self.shop_card_regions],
            "shop_detect_pixel": self.shop_detect_pixel,
            "poll_interval": self.poll_interval,
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "CaptureConfig":
        if not CONFIG_PATH.exists():
            return cls()
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg = cls()
        cfg.card_regions = [Region(**r) for r in data.get("card_regions", [])]
        cfg.detect_pixel = data.get("detect_pixel", {})
        cfg.shop_card_regions = [Region(**r) for r in data.get("shop_card_regions", [])]
        cfg.shop_detect_pixel = data.get("shop_detect_pixel", {})
        cfg.poll_interval = data.get("poll_interval", 0.8)
        return cfg


class ScreenCapture:
    def __init__(self):
        self.config = CaptureConfig.load()
        self._winocr_engine = None
        self._reader = None          # easyocr fallback
        self._reader_lock = threading.Lock()
        # 전용 이벤트 루프 (asyncio.run() 반복 생성 오버헤드 제거)
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()

    def _get_winocr(self):
        if not _WINRT_OK:
            return None
        if self._winocr_engine is None:
            lang = _WinLanguage("ko")
            self._winocr_engine = _winrt_ocr.OcrEngine.try_create_from_language(lang)
        return self._winocr_engine

    def _get_easyocr(self):
        if self._reader is None:
            with self._reader_lock:
                if self._reader is None:
                    import easyocr
                    self._reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
        return self._reader

    def capture_region(self, region: Region) -> np.ndarray:
        with mss.mss() as sct:
            shot = sct.grab(region.to_mss())
            return np.array(shot)

    def capture_regions(self, regions: list[Region]) -> list[np.ndarray]:
        """여러 영역을 mss 컨텍스트 하나로 한 번에 캡처 (오버헤드 절감)"""
        with mss.mss() as sct:
            return [np.array(sct.grab(r.to_mss())) for r in regions]

    def capture_full(self) -> np.ndarray:
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])
            return np.array(shot)

    def _check_pixel(self, dp: dict) -> bool:
        if not dp:
            return False
        with mss.mss() as sct:
            region = {"left": dp["x"], "top": dp["y"], "width": 1, "height": 1}
            pixel = np.array(sct.grab(region))[0][0]
            r, g, b = int(pixel[2]), int(pixel[1]), int(pixel[0])
            tol = dp.get("tolerance", 20)
            return (abs(r-dp["r"])<=tol and abs(g-dp["g"])<=tol and abs(b-dp["b"])<=tol)

    def is_reward_screen_visible(self) -> bool:
        return self._check_pixel(self.config.detect_pixel)

    def is_shop_screen_visible(self) -> bool:
        return self._check_pixel(self.config.shop_detect_pixel)

    def screen_mode(self) -> str | None:
        """현재 화면 모드.
        반환값: 'reward' | 'shop' | '' (게임 포그라운드지만 카드 화면 없음) | None (게임 비포그라운드)
        None: 상태 보존, '': screen_gone 트리거
        """
        if not _is_game_foreground():
            return None  # 포그라운드 아님 → 상태 유지, OCR 스킵
        if self.is_reward_screen_visible():
            return "reward"
        if self.is_shop_screen_visible():
            return "shop"
        return ""  # 게임 포그라운드지만 카드 화면 없음

    async def _winocr_async(self, img_pil) -> str:
        """Windows OCR로 PIL 이미지 인식 → 텍스트"""
        engine = self._get_winocr()
        if engine is None:
            return ""
        buf = io.BytesIO()
        img_pil.convert("RGBA").save(buf, format="PNG")
        data = buf.getvalue()
        ras = _winrt_streams.InMemoryRandomAccessStream()
        writer = _winrt_streams.DataWriter(ras)
        writer.write_bytes(data)
        await writer.store_async()
        ras.seek(0)
        decoder = await _winrt_img.BitmapDecoder.create_async(ras)
        bitmap = await decoder.get_software_bitmap_async()
        if bitmap.bitmap_pixel_format != _winrt_img.BitmapPixelFormat.BGRA8:
            bitmap = _winrt_img.SoftwareBitmap.convert(bitmap, _winrt_img.BitmapPixelFormat.BGRA8)
        result = await engine.recognize_async(bitmap)
        lines = [line.text for line in result.lines]
        return " ".join(lines).strip()

    def _run_winocr(self, img_pil) -> str:
        """Windows OCR 동기 래퍼 (전용 루프 재사용)"""
        future = asyncio.run_coroutine_threadsafe(self._winocr_async(img_pil), self._loop)
        return future.result(timeout=5)

    def _run_easyocr(self, img_pil) -> str:
        """easyocr 폴백 (Windows OCR 실패 시)"""
        from PIL import ImageEnhance, ImageFilter
        img_pil = img_pil.resize((img_pil.width * 3, img_pil.height * 3), Image.LANCZOS)
        img_pil = img_pil.filter(ImageFilter.MedianFilter(3))
        img_pil = ImageEnhance.Contrast(img_pil).enhance(2.5)
        reader = self._get_easyocr()
        results = reader.readtext(np.array(img_pil), detail=1, paragraph=False)
        if not results:
            return ""
        filtered = [r for r in results if r[2] >= 0.25] or results
        filtered.sort(key=lambda r: r[0][0][0])
        return " ".join(r[1] for r in filtered).strip()

    def ocr_region_candidates(self, region: Region, img: np.ndarray | None = None) -> list[str]:
        """지정 영역 → OCR 후보 텍스트 리스트
        Windows OCR 우선, 실패 시 easyocr 폴백
        img: 사전 캡처된 이미지 (없으면 직접 캡처)
        """
        if img is None:
            img = self.capture_region(region)
        h, w = img.shape[:2]

        # 카드명 리본 크롭 (마나 배지 제외: w*0.20~, 이름 리본 h 20-65%)
        crop = img[int(h*0.20):int(h*0.65), int(w*0.20):int(w*0.95)]
        img_pil = Image.fromarray(crop[:, :, :3][:, :, ::-1])

        if _WINRT_OK:
            # 전략 1: Windows OCR 원본 (자체 전처리)
            t1 = self._run_winocr(img_pil)
            candidates = [t1] if t1 else []

            # 신뢰도 대신 텍스트 길이로 early exit
            if t1 and len(t1.replace(" ", "")) >= 2:
                return candidates

            # 전략 2: 밝기 마스킹 (하늘색 리본 흰 글씨)
            crop_rgb = img[int(h*0.10):int(h*0.55), int(w*0.15):int(w*0.85), :3][:, :, ::-1]
            gray = np.mean(crop_rgb, axis=2)
            mask = (gray > 200).astype(np.uint8) * 255
            binary = np.stack([mask, mask, mask], axis=2)
            t2 = self._run_winocr(Image.fromarray(binary.astype(np.uint8)))
            if t2:
                candidates.append(t2)
            return candidates
        else:
            # easyocr 폴백
            from PIL import ImageEnhance, ImageFilter
            p1 = img_pil.resize((img_pil.width * 3, img_pil.height * 3), Image.LANCZOS)
            p1 = p1.filter(ImageFilter.MedianFilter(3))
            p1 = ImageEnhance.Contrast(p1).enhance(2.5)
            t1 = self._run_easyocr(p1)
            return [t1] if t1 else []

    def ocr_region(self, region: Region) -> str:
        """지정 영역 OCR → 단일 텍스트 (candidates 중 첫 번째)"""
        cands = self.ocr_region_candidates(region)
        return cands[0] if cands else ""

    def ocr_all_cards(self, mode: str = "reward") -> list[str]:
        regions = self.config.card_regions if mode == "reward" else self.config.shop_card_regions
        if not regions:
            return []
        return [self.ocr_region(r) for r in regions]

    def ocr_all_cards_candidates(self, mode: str = "reward") -> list[list[str]]:
        """각 카드 영역별 OCR 후보 텍스트 목록 반환
        mss 컨텍스트 1회로 전체 캡처 후 순차 OCR (컨텍스트 오버헤드 절감)
        """
        regions = self.config.card_regions if mode == "reward" else self.config.shop_card_regions
        if not regions:
            return []
        imgs = self.capture_regions(regions)
        return [self.ocr_region_candidates(r, img) for r, img in zip(regions, imgs)]


class CaptureLoop:
    """백그라운드에서 반복 캡처 → 콜백 호출"""
    def __init__(self, on_cards_detected: Callable[[list[list[str]], str], None],
                 on_screen_gone: Callable[[], None] | None = None):
        self.capture = ScreenCapture()
        self.on_cards_detected = on_cards_detected
        self.on_screen_gone = on_screen_gone
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_cards: list[str] = []
        self._was_visible = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                mode = self.capture.screen_mode()
                if mode is None:
                    # 게임 비포그라운드 → 상태 보존, 아무 것도 안 함
                    pass
                elif mode:  # 'reward' or 'shop'
                    self._was_visible = True
                    # 후보 기반 OCR: 각 영역마다 전략별 결과 리스트
                    candidates_list = self.capture.ocr_all_cards_candidates(mode)
                    flat = [c[0] if c else "" for c in candidates_list]
                    if candidates_list and flat != self._last_cards:
                        self._last_cards = flat
                        self.on_cards_detected(candidates_list, mode)
                else:  # mode == '' → 게임 포그라운드지만 카드 화면 없음
                    if self._was_visible and self.on_screen_gone:
                        self.on_screen_gone()
                    self._was_visible = False
                    self._last_cards = []
            except Exception as e:
                print(f"Capture error: {e}")
            time.sleep(self.capture.config.poll_interval)


if __name__ == "__main__":
    # 테스트: 현재 화면 전체 캡처 후 저장
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sc = ScreenCapture()
    img = sc.capture_full()
    pil = Image.fromarray(img[:, :, :3][:, :, ::-1])
    pil.save("screenshot.png")
    print(f"스크린샷 저장됨: {pil.size}")

"""
Claude Vision API를 이용한 OCR 폴백
- Windows OCR + 퍼지 매칭으로도 인식 실패한 카드만 호출
- claude-haiku (가장 빠르고 저렴)
"""
import base64
import io
import os
from pathlib import Path

_client = None
_ENABLED = False


def init(api_key: str | None = None):
    """API 키 설정 후 활성화. api_key=None이면 환경변수 ANTHROPIC_API_KEY 사용."""
    global _client, _ENABLED
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        # overlay 폴더의 .api_key 파일에서 읽기
        key_file = Path(__file__).parent / ".api_key"
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
    if key:
        import anthropic
        _client = anthropic.Anthropic(api_key=key)
        _ENABLED = True
    return _ENABLED


def is_enabled() -> bool:
    return _ENABLED


_CHAR_KO = {"IC": "아이언클래드", "SI": "사일런트", "DE": "디펙트",
            "NE": "네크로바인더", "RE": "리젠트"}


def ocr_card_image(img_pil, char: str | None = None) -> str:
    """카드 이름 리본 이미지 → Claude Vision으로 카드명 추출"""
    if not _ENABLED or _client is None:
        return ""
    try:
        buf = io.BytesIO()
        img_pil.save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode()

        char_hint = ""
        if char and char in _CHAR_KO:
            char_hint = f"현재 캐릭터는 {_CHAR_KO[char]}입니다. "

        prompt = (
            "이 이미지는 Slay the Spire 2 게임의 카드 이름 리본입니다. "
            f"{char_hint}"
            "장식체 한글 폰트로 쓰인 카드 이름만 출력하세요. "
            "설명·영어·괄호 금지. 읽을 수 없으면 '모름'만 출력하세요."
        )

        msg = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = msg.content[0].text
        # 여러 줄 출력 시 한국어가 포함된 마지막 줄만 추출
        import re
        korean_lines = [
            l.strip().strip("*").strip()
            for l in raw.split("\n")
            if re.search(r"[가-힣]", l) and "(" not in l and len(l.strip()) <= 20
        ]
        result = korean_lines[-1] if korean_lines else raw.split("\n")[0].strip().strip("*").strip()
        return "" if result in ("모름", "") else result
    except Exception as e:
        print(f"[ClaudeOCR] 오류: {e}")
        return ""

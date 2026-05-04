"""
카드 선택 화면 파서 — Gemini Vision으로 제공 카드명 추출
"""
import json
from PIL import Image
import gemini_client

PROMPT = """
이 이미지는 슬레이 더 스파이어 2 화면입니다.
카드 보상 화면 또는 상점 화면인지 확인하고, 제공되는 카드명을 추출해주세요.

다음 JSON 형식으로만 출력하세요:
{
  "mode": "reward" 또는 "shop" 또는 null,
  "cards": ["카드명1", "카드명2", ...]
}

- 카드 선택/보상 화면: mode = "reward"
- 상점 화면: mode = "shop"
- 해당 화면이 아님: mode = null, cards = []
- 카드명은 한국어로 추출
- JSON만 출력, 설명 금지
"""


def parse(img: Image.Image) -> dict | None:
    """Returns {"mode": "reward"|"shop"|None, "cards": [...]} or None on failure"""
    raw = gemini_client.ask(PROMPT, img)
    text = raw
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        result = json.loads(text.strip())
        if not isinstance(result.get("cards"), list):
            return None
        return result
    except json.JSONDecodeError:
        return None

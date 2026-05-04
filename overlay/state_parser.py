import json
from PIL import Image
import gemini_client

PROMPT = """
이 이미지는 슬레이 더 스파이어 2 전투 화면입니다.
다음 정보를 JSON으로 추출해주세요. 보이지 않는 항목은 null로 표시하세요.

{
  "hand": ["카드명1", "카드명2", ...],
  "energy": 현재에너지,
  "energy_max": 최대에너지,
  "player_hp": 현재HP,
  "player_hp_max": 최대HP,
  "player_block": 현재방어도,
  "enemies": [
    {
      "name": "적이름 (모르면 null)",
      "hp": 현재HP,
      "hp_max": 최대HP,
      "intent": "attack|defend|buff|unknown",
      "intent_value": 공격이면데미지숫자_아니면null
    }
  ]
}

JSON만 출력하고 다른 설명은 하지 마세요.
"""


def parse(img: Image.Image) -> dict | None:
    raw = gemini_client.ask(PROMPT, img)
    text = raw
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


def format_state(state: dict) -> str:
    lines = []
    lines.append(f"에너지: {state.get('energy')}/{state.get('energy_max')}")
    lines.append(f"내 HP: {state.get('player_hp')}/{state.get('player_hp_max')}  방어도: {state.get('player_block')}")
    lines.append(f"손패: {state.get('hand')}")
    for i, e in enumerate(state.get("enemies", [])):
        if e.get("intent") == "attack":
            intent_str = f"공격 {e.get('intent_value', '?')}데미지"
        else:
            intent_str = e.get("intent", "unknown")
        name = e.get("name") or f"적{i+1}"
        lines.append(f"{name}: HP {e.get('hp')}/{e.get('hp_max')}  의도: {intent_str}")
    return "\n".join(lines)

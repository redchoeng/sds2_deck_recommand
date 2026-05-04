"""
전투 추천 모듈
- 화면 파싱: Gemini Vision (상태 JSON 추출만)
- 추천 로직: engine.py 알고리즘 기반 (Gemini 상상력 배제)
"""
from PIL import Image
import state_parser

_engine = None

TS = {"S": 50, "A": 35, "B": 20, "C": 8, "D": 0}

# 기본 카드 (가중치 하향)
_BASIC = {"타격", "수비", "강타", "무력화", "생존자", "이중 시전", "파지직",
          "풀어놓기", "호위", "별똥별", "추앙"}

# 방어류 카드 키워드 (적 공격 시 우선도 상향)
_BLOCK_KEYWORDS = {"수비", "바리케이드", "방어", "방패", "철갑", "냉정함"}


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def recommend(img: Image.Image, deck: list, arch_id: str = None) -> tuple:
    """Returns (state_dict | None, recommendation_str)"""
    state = state_parser.parse(img)
    if not state:
        return None, "화면 인식 실패 — 전투 화면인지 확인해주세요"

    rec_text = _generate_rec(state, deck, arch_id)
    return state, rec_text


def _generate_rec(state: dict, deck: list, arch_id: str) -> str:
    hand = state.get("hand") or []
    enemies = state.get("enemies") or []
    energy = state.get("energy") or 0
    block = state.get("player_block") or 0

    if not hand:
        return "손패 카드를 인식하지 못했습니다"

    # 적 공격 합산
    incoming = sum(
        (e.get("intent_value") or 0)
        for e in enemies if e.get("intent") == "attack"
    )
    need_block = max(0, incoming - block)

    # 카드별 점수 계산
    scored = []
    for card_name in hand:
        score, tags = _score_card_combat(card_name, deck, arch_id, need_block > 0)
        scored.append((card_name, score, tags))
    scored.sort(key=lambda x: x[1], reverse=True)

    lines = []

    # 전술 경고
    if need_block > 0:
        lines.append(f"⚠ 이번 턴 순 피해: {need_block}  → 방어 우선 고려")
        lines.append("")

    # 순위 출력
    lines.append("[추천 순서]")
    for i, (name, score, tags) in enumerate(scored):
        prefix = "1순위" if i == 0 else f"{i+1}순위"
        tag_str = "  " + "  ".join(f"[{t}]" for t in tags) if tags else ""
        lines.append(f"  {prefix}: {name}{tag_str}")

    # 에너지 부족 경고
    top_names = [name for name, _, _ in scored]
    if energy < len(hand):
        skippable = scored[energy:]  # 에너지 부족 시 후순위 카드
        if skippable:
            skip_names = [n for n, _, _ in skippable]
            lines.append(f"\n에너지 {energy} — 후순위 스킵 후보: {', '.join(skip_names)}")

    return "\n".join(lines)


def _score_card_combat(name: str, deck: list, arch_id: str, enemy_attacks: bool) -> tuple:
    """Returns (score, tag_list)"""
    tags = []

    if not _engine:
        return 0, ["엔진 없음"]

    char = _engine._current_char(deck)
    status = _engine.deck_status(deck, arch_id)
    arch = status["arch"] if status else {}
    must_cards = arch.get("must", [])
    rec_cards = arch.get("rec", [])
    arch_name = arch.get("name", "")

    meta = _engine._get_card_meta(name, char)
    if not meta:
        return 0, ["정보 없음"]

    tier = meta.get("tier", "C")
    score = TS.get(tier, 8)

    # 빌드 필수/추천
    if any(name == c for c in must_cards):
        score += 64
        tags.append(f"★필수 {arch_name}")
    elif any(name == c for c in rec_cards):
        score += 30
        tags.append(f"추천 {arch_name}")

    # 콤보 보너스
    combo = _engine._get_combo_matches(name, char, deck)
    if combo["full"]:
        score += 20
        tags.append("콤보 활성")
    elif combo["partial"]:
        score += 10

    # 범용 카드 (uni)
    uni = _engine._uni_by_char.get(char, [])
    if name in uni:
        score += 18
        tags.append("범용")

    # 기본 카드 하향
    if name in _BASIC:
        score -= 20

    # 방어 카드: 적 공격 시 우선도 상향
    if enemy_attacks and any(kw in name for kw in _BLOCK_KEYWORDS):
        score += 30
        tags.append("방어 필요")

    # 티어 표시 (태그 없을 때만)
    if not tags:
        tags.append(f"티어 {tier}")

    return score, tags

"""
STS2 카드 추천 엔진
- 현재 덱 + 제시된 카드 3장 → 어떤 카드를 뽑을지 (또는 스킵) 추천
"""
import json
from dataclasses import dataclass, field
from typing import Optional

TIER_SCORE = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1, "F": 0}

@dataclass
class Recommendation:
    card_name: str
    action: str          # "pick" | "skip"
    score: float
    reason: str
    arch_name: Optional[str] = None

@dataclass
class CardChoice:
    cards: list[str]     # 제시된 카드 이름 3장
    can_skip: bool = True


class RecommendEngine:
    def __init__(self, cards_path: str = "cards.json"):
        with open(cards_path, encoding="utf-8") as f:
            data = json.load(f)

        self.all_cards: dict[str, dict] = {}
        self.archs_by_char: dict[str, list] = data["archs"]

        # 전체 카드를 이름→데이터 딕셔너리로
        from collections import defaultdict
        name_to_chars: dict[str, set] = defaultdict(set)
        for char_key, card_list in data["cards"].items():
            for card in card_list:
                self.all_cards[card["n"]] = {**card, "char": char_key}
                name_to_chars[card["n"]].add(char_key)

        # 여러 캐릭터에 공유된 카드 (타격·수비 등) — 캐릭터 감지에서 제외
        self._shared_cards: set[str] = {n for n, chars in name_to_chars.items() if len(chars) > 1}

    def _get_card(self, name: str) -> Optional[dict]:
        return self.all_cards.get(name)

    def _current_char(self, deck: list[str]) -> Optional[str]:
        """덱에서 가장 많이 등장하는 캐릭터 키 반환
        - colorless/basic 카드 제외
        - 타격·수비처럼 여러 캐릭터에 공유된 카드 제외 (캐릭터 특정 불가)
        - 캐릭터 고유 시작 카드(강타·무력화 등)는 포함해서 시작 덱도 감지 가능
        """
        counts: dict[str, int] = {}
        for name in deck:
            if name in self._shared_cards:
                continue
            card = self._get_card(name)
            if not card:
                continue
            if card["char"] in ("colorless", "basic"):
                continue
            counts[card["char"]] = counts.get(card["char"], 0) + 1
        if not counts:
            return None
        return max(counts, key=lambda k: counts[k])

    def _best_arch(self, char: str, deck: list[str]) -> Optional[dict]:
        """현재 덱과 가장 맞는 빌드 아키텍처 반환"""
        archs = self.archs_by_char.get(char, [])
        if not archs:
            return None

        deck_set = set(deck)
        best_arch = None
        best_score = -1

        for arch in archs:
            must_cards = arch.get("must", [])
            rec_cards = arch.get("rec", [])
            must_hits = sum(1 for c in must_cards if c in deck_set)
            rec_hits = sum(1 for c in rec_cards if c in deck_set)
            score = must_hits * 3 + rec_hits
            if score > best_score:
                best_score = score
                best_arch = arch

        return best_arch

    def _score_card(self, card_name: str, char: str, arch: Optional[dict]) -> tuple[float, str]:
        """카드 하나의 점수와 이유 반환"""
        card = self._get_card(card_name)
        if not card:
            return 0.0, "알 수 없는 카드"

        tier = card.get("tier", "C")
        base = TIER_SCORE.get(tier, 2) * 10.0
        reason_parts = [f"티어 {tier}"]

        if arch:
            must_list = arch.get("must", [])
            rec_list = arch.get("rec", [])
            if card_name in must_list:
                base += 50
                reason_parts.append(f"{arch['name']} 필수 카드")
            elif card_name in rec_list:
                base += 25
                reason_parts.append(f"{arch['name']} 추천 카드")

        tip = card.get("tip", "")
        if tip:
            reason_parts.append(tip)

        return base, " / ".join(reason_parts)

    def all_arch_status(self, deck: list[str]) -> list[dict]:
        """현재 캐릭터의 모든 빌드 완성도 반환"""
        char = self._current_char(deck)
        if not char:
            return []
        archs = self.archs_by_char.get(char, [])
        deck_set = set(deck)
        result = []
        for arch in archs:
            must = arch.get("must", [])
            rec = arch.get("rec", [])
            must_have = [c for c in must if c in deck_set]
            must_need = [c for c in must if c not in deck_set]
            rec_have = [c for c in rec if c in deck_set]
            rec_need = [c for c in rec if c not in deck_set]
            total = len(must) + len(rec)
            have = len(must_have) + len(rec_have)
            pct = int(have / total * 100) if total else 100
            result.append({
                "arch": arch,
                "must_have": must_have,
                "must_need": must_need,
                "rec_have": rec_have,
                "rec_need": rec_need,
                "pct": pct,
            })
        return result

    def deck_status(self, deck: list[str], arch_id: Optional[str] = None) -> Optional[dict]:
        """특정 빌드(또는 최적 빌드)의 완성도 반환"""
        char = self._current_char(deck)
        if not char:
            return None
        all_status = self.all_arch_status(deck)
        if not all_status:
            return None
        if arch_id:
            for s in all_status:
                if s["arch"].get("id") == arch_id:
                    return s
        # 자동: 가장 높은 완성도
        return max(all_status, key=lambda s: s["pct"] * 10 + len(s["rec_have"]))

    def upgrade_suggestions(self, deck: list[str]) -> list[tuple[str, str, str]]:
        """덱에서 강화 우선순위 반환 → [(카드명, 우선도, 이유), ...]
        우선도: '★★★ 필수', '★★ 권장', '★ 보통', '✕ 제거 고려'
        """
        char  = self._current_char(deck)
        arch  = self._best_arch(char, deck) if char else None
        must_set = set(arch.get("must", [])) if arch else set()
        rec_set  = set(arch.get("rec",  [])) if arch else set()

        result: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for name in deck:
            if name in seen:
                continue
            seen.add(name)
            card = self._get_card(name)
            if not card:
                continue
            tier = card.get("tier", "C")
            if name in must_set:
                priority = "★★★ 필수"
                reason   = f"빌드 핵심 카드 (티어 {tier})"
            elif name in rec_set and tier in ("S", "A", "B"):
                priority = "★★ 권장"
                reason   = f"빌드 시너지 카드 (티어 {tier})"
            elif tier in ("S", "A"):
                priority = "★★ 권장"
                reason   = f"고티어 카드 (티어 {tier})"
            elif tier in ("D", "F"):
                priority = "✕ 제거 고려"
                reason   = f"저티어 카드 (티어 {tier}) — 교체 우선"
            else:
                priority = "★ 보통"
                reason   = f"티어 {tier}"
            result.append((name, priority, reason))

        # 우선도 순 정렬
        order = {"★★★ 필수": 0, "★★ 권장": 1, "★ 보통": 2, "✕ 제거 고려": 3}
        result.sort(key=lambda x: order.get(x[1], 9))
        return result

    def recommend(self, deck: list[str], choice: CardChoice) -> list[Recommendation]:
        """
        deck: 현재 내 덱 카드 이름 목록
        choice: 제시된 카드 3장
        반환: 각 카드에 대한 추천 (점수 높은 순)
        """
        char = self._current_char(deck)
        arch = self._best_arch(char, deck) if char else None

        results: list[Recommendation] = []
        for card_name in choice.cards:
            score, reason = self._score_card(card_name, char or "", arch)
            results.append(Recommendation(
                card_name=card_name,
                action="pick",
                score=score,
                reason=reason,
                arch_name=arch["name"] if arch else None,
            ))

        results.sort(key=lambda r: r.score, reverse=True)

        # 최고 점수가 너무 낮으면 스킵 권장
        SKIP_THRESHOLD = 25.0
        if results and results[0].score < SKIP_THRESHOLD:
            results[0].action = "skip"
            results[0].reason = "모든 카드 가성비 낮음 — 스킵 권장"
        else:
            results[0].action = "pick"

        return results


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    engine = RecommendEngine("cards.json")

    # 테스트: 아이언클래드 취약덱 방향 덱
    test_deck = ["타격", "타격", "수비", "수비", "제압", "포악함", "협박"]
    test_choice = CardChoice(cards=["떨림", "악랄함", "거상"])

    print(f"현재 덱: {test_deck}")
    print(f"제시 카드: {test_choice.cards}")
    print()

    recs = engine.recommend(test_deck, test_choice)
    for i, r in enumerate(recs):
        mark = ">>>" if i == 0 else "   "
        print(f"{mark} [{r.action.upper()}] {r.card_name} (점수:{r.score:.0f})")
        print(f"      이유: {r.reason}")
        if r.arch_name:
            print(f"      빌드: {r.arch_name}")
        print()

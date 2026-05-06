"""
STS2 카드 추천 엔진 — sts2-guide 원본 JS 로직 포팅
"""
import json
from dataclasses import dataclass
from typing import Optional

# TS: 원본 JS const TS={S:50,A:35,B:20,C:8,D:0}
TS = {"S": 50, "A": 35, "B": 20, "C": 8, "D": 0}

BUILD_POWER_BONUS = {
    "지옥검무": 90, "바리케이드": 48, "창의적인 인공지능": 44, "서브루틴": 34,
    "카운트다운": 42, "도망칠 수 없다": 42, "서류 폭풍": 38, "장막 관통자": 38,
    "잿빛 혼령": 34, "장송가": 32, "무덤지기": 32, "냉정함": 26, "조각모음": 26,
    "제압": 26, "포악함": 24, "파열": 30, "불타는조약": 30,
}

HIGH_POWER_SINGLES = {"불사", "지옥검무", "바리케이드", "창의적인 인공지능", "권역"}

START_DECKS = {
    "IC": ["타격","타격","타격","타격","타격","수비","수비","수비","수비","강타"],
    "SI": ["타격","타격","타격","타격","타격","수비","수비","수비","수비","수비","무력화","생존자"],
    "DE": ["타격","타격","타격","타격","수비","수비","수비","수비","파지직","이중 시전"],
    "NE": ["타격","타격","타격","타격","수비","수비","수비","수비","호위","풀어놓기"],
    "RE": ["타격","타격","타격","타격","수비","수비","수비","수비","별똥별","추앙"],
}

BASIC_CARDS = {
    "IC": ["타격","수비","강타"],
    "SI": ["타격","수비","무력화","생존자"],
    "DE": ["타격","수비","이중 시전","파지직"],
    "NE": ["타격","수비","풀어놓기","호위"],
    "RE": ["타격","수비","별똥별","추앙"],
}

SHOP_SLOT_COUNT = 5


def _normalize(name: str) -> str:
    return (name or "").replace(" ", "").strip()


def _same_name(a: str, b: str) -> bool:
    return _normalize(a) == _normalize(b)


def _list_has(lst: list, name: str) -> bool:
    return any(_same_name(x, name) for x in (lst or []))


def _deck_has(deck: list, name: str) -> bool:
    return any(_same_name(x, name) for x in deck)


def _count_matches(lst: list, deck: list) -> int:
    return sum(1 for n in (lst or []) if _deck_has(deck, n))


def _build_tier_bonus(tier: str) -> int:
    return {"S": 36, "A": 18, "B": 8, "C": 0, "D": -8}.get(tier, 0)


def _build_rarity_bonus(r: str) -> int:
    return {"레어": 22, "언커먼": 10, "커먼": 0, "시작": -28}.get(r, 0)


def _build_starter_factor(name: str, count: int, char: str) -> float:
    starters = START_DECKS.get(char, [])
    starter_cnt = sum(1 for c in starters if c == name)
    if not starter_cnt:
        return count
    starter_seen = min(count, starter_cnt)
    extra = max(0, count - starter_cnt)
    return starter_seen * 0.12 + extra * 1.0


@dataclass
class Recommendation:
    card_name: str
    action: str       # "pick" | "skip"
    score: float
    reason: str
    summary: str
    chips: list
    arch_name: Optional[str] = None


@dataclass
class CardChoice:
    cards: list[str]
    can_skip: bool = True
    mode: str = "reward"   # "reward" | "shop"


class RecommendEngine:
    def __init__(self, cards_path: str = "cards.json"):
        with open(cards_path, encoding="utf-8") as f:
            data = json.load(f)

        self._cards_by_char: dict[str, list] = data["cards"]
        self._archs_by_char: dict[str, list] = data["archs"]
        self._uni_by_char: dict[str, list] = data.get("uni", {})
        self._combos_by_char: dict[str, list] = data.get("combos", {})

        # 이름 → 카드 메타 (캐릭터별 우선)
        self._all_cards: dict[str, dict] = {}
        from collections import defaultdict
        name_to_chars: dict[str, set] = defaultdict(set)
        for char_key, card_list in data["cards"].items():
            for card in card_list:
                self._all_cards[card["n"]] = {**card, "char": char_key}
                name_to_chars[card["n"]].add(char_key)
        self._shared_cards: set[str] = {n for n, cs in name_to_chars.items() if len(cs) > 1}

    # ── 캐릭터 감지 ──────────────────────────────
    def _current_char(self, deck: list[str]) -> Optional[str]:
        counts: dict[str, int] = {}
        for name in deck:
            if name in self._shared_cards:
                continue
            card = self._get_card_meta(name)
            if not card or card.get("char") in ("colorless", "basic"):
                continue
            counts[card["char"]] = counts.get(card["char"], 0) + 1
        return max(counts, key=lambda k: counts[k]) if counts else None

    def _get_card_meta(self, name: str, char: Optional[str] = None) -> Optional[dict]:
        if char and char in self._cards_by_char:
            for c in self._cards_by_char[char]:
                if _same_name(c["n"], name):
                    return c
        return self._all_cards.get(name)

    # ── 게임 단계 감지 ────────────────────────────
    def _get_run_stage(self, deck: list[str], char: Optional[str], hist: list) -> dict:
        base = len(START_DECKS.get(char or "", [])) or 10
        added = max(0, len(deck) - base)
        shop_runs = sum(1 for h in hist if isinstance(h.get("f"), list) and len(h["f"]) == SHOP_SLOT_COUNT)
        picked = sum(1 for h in hist if h.get("p") and h["p"] != "스킵")
        score = added * 10 + shop_runs * 12 + picked * 4
        if added <= 4: score -= 10
        if added >= 10: score += 10
        if score < 45: stage = "early"
        elif score < 95: stage = "mid"
        else: stage = "late"
        return {"score": score, "added": added, "stage": stage}

    # ── 빌드 감지 ─────────────────────────────────
    def _detect_build_scores(self, deck: list[str], char: str, stage: str) -> list[dict]:
        archs = self._archs_by_char.get(char, [])
        counts: dict[str, int] = {}
        for n in deck:
            k = _normalize(n)
            counts[k] = counts.get(k, 0) + 1

        results = []
        for arch in archs:
            score = 0.0
            mh = rh = touched = anchor = 0
            names = list(dict.fromkeys((arch.get("must", []) + arch.get("rec", []))))
            for name in names:
                cnt = counts.get(_normalize(name), 0)
                if not cnt: continue
                card = self._get_card_meta(name, char) or {}
                in_must = _list_has(arch.get("must", []), name)
                factor = _build_starter_factor(name, cnt, char)
                if not factor: continue
                if in_must: mh += cnt
                else: rh += cnt
                touched += factor
                w = (72 if in_must else 30) + _build_tier_bonus(card.get("tier","C")) + \
                    _build_rarity_bonus(card.get("r","커먼")) + BUILD_POWER_BONUS.get(name, 0)
                if stage == "early":
                    if in_must: w += 10
                    if card.get("tier") == "S": w += 8
                elif stage == "mid":
                    if in_must: w += 4
                else:
                    if in_must: w += 8
                    if _list_has(arch.get("rec", []), name): w += 4
                if BUILD_POWER_BONUS.get(name, 0) >= 60: anchor += 1
                score += w * factor

            if mh > 0 and rh > 0: score += 18
            elif mh > 0: score += 10
            elif rh >= 2: score += 8
            score += touched * (5 if stage == "early" else 8)
            if stage == "early" and mh > 0: score += 12
            if stage == "early" and anchor > 0: score += 18 * anchor
            results.append({"id": arch.get("id",""), "score": score, "mh": mh, "rh": rh, "touched": touched, "anchor": anchor, "arch": arch})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def _detect_build(self, deck: list[str], char: str, stage: str) -> Optional[dict]:
        scored = self._detect_build_scores(deck, char, stage)
        if not scored: return None
        top = scored[0]
        min_score = 26 if stage == "early" else 34
        # must 카드 없으면 rec만으로 빌드 확정하지 않도록 임계값 상향
        if top["mh"] == 0:
            min_score += 60
        return top["arch"] if top["score"] >= min_score else None

    def _get_build_commit_level(self, arch: Optional[dict], deck: list[str]) -> int:
        if not arch: return 0
        must_count = _count_matches(arch.get("must", []), deck)
        rec_count = _count_matches(arch.get("rec", []), deck)
        return must_count * 2 + rec_count

    # ── 콤보 매칭 ─────────────────────────────────
    def _get_combo_matches(self, name: str, char: str, deck: list[str]) -> dict:
        combos = self._combos_by_char.get(char, [])
        full, partial = [], []
        for cb in combos:
            if not _list_has(cb.get("cards", []), name): continue
            others = [c for c in cb["cards"] if not _same_name(c, name)]
            have = [c for c in others if _deck_has(deck, c)]
            if len(have) == len(others):
                full.append({"cb": cb, "have": have})
            elif have:
                partial.append({"cb": cb, "have": have, "missing": [c for c in others if not _deck_has(deck, c)]})
        return {"full": full, "partial": partial}

    def _get_build_refs(self, name: str, char: str) -> list[dict]:
        return [a for a in self._archs_by_char.get(char, [])
                if _list_has(a.get("must", []), name) or _list_has(a.get("rec", []), name)]

    # ── 보스 보상 감지 ────────────────────────────
    def _is_boss_reward(self, choice: CardChoice) -> bool:
        if choice.mode != "reward" or len(choice.cards) < 3: return False
        return all((self._get_card_meta(n) or {}).get("r") == "레어" for n in choice.cards)

    # ── 카드 점수 계산 (원본 JS scoreCard 포팅) ──────
    def _score_card(self, name: str, deck: list[str], char: str,
                    active_arch: Optional[dict], stage: str,
                    is_boss_reward: bool, hist: list) -> dict:
        card = self._get_card_meta(name, char)
        if not card:
            return {"score": 0, "reasons": ["정보 없음"], "tier": "?", "warn": False,
                    "summary": "정보 없음", "chips": [["mid", "판단 보류"]]}

        s = TS.get(card.get("tier","C"), 0)
        reasons, chips = [], []
        warn = False
        dupes = sum(1 for c in deck if _same_name(c, name))
        total_cards = len(deck)
        early = stage == "early"
        mid   = stage == "mid"
        late  = stage == "late"

        combo = self._get_combo_matches(name, char, deck)
        build_refs = self._get_build_refs(name, char)
        must_refs = [a for a in build_refs if _list_has(a.get("must", []), name)]
        rec_refs  = [a for a in build_refs if _list_has(a.get("rec", []), name)]
        in_active = bool(active_arch and (_list_has(active_arch.get("must",[]), name) or
                                          _list_has(active_arch.get("rec",[]), name)))
        anchor_bonus = BUILD_POWER_BONUS.get(name, 0)
        build_commit = self._get_build_commit_level(active_arch, deck)
        uni = self._uni_by_char.get(char, [])

        # 캐릭터 특수 규칙: necro 영혼 폭풍
        soul_enablers = ["강탈","장송가","출몰","혼령 포획","강령회","분리"]
        if char == "NE" and _same_name(name, "영혼 폭풍"):
            if not any(_deck_has(deck, e) for e in soul_enablers):
                s -= 26; warn = True
                chips.append(["bad","엔진 없음"])
                reasons.append("영혼 생성 카드가 아직 없어 기대값이 낮음")

        # 활성 빌드 보너스
        if active_arch:
            if _list_has(active_arch.get("must", []), name):
                s += 64 if early else 74
                s += round(anchor_bonus * 0.24)
                s += {"S":18,"A":12,"B":6,"C":0,"D":-6}.get(card.get("tier","C"), 0)
                if build_commit >= 4: s += 16
                if is_boss_reward: s += 34
                chips.append(["good","필수"])
                reasons.append(f"{active_arch['name']}에 잘 맞습니다")
            elif _list_has(active_arch.get("rec", []), name):
                s += 30 if early else 40
                s += round(anchor_bonus * 0.18)
                s += {"S":16,"A":12,"B":6,"C":0,"D":-6}.get(card.get("tier","C"), 0)
                if build_commit >= 4: s += 22
                if build_commit >= 6: s += 14
                if is_boss_reward: s += 28
                chips.append(["good","시너지"])
                reasons.append(f"{active_arch['name']}에 잘 맞습니다")
            elif build_refs and not early:
                s -= 12; warn = True
                chips.append(["bad","오프빌드"])
                reasons.append("지금 방향과는 거리 있음")
        else:
            if must_refs:
                s += 26 + round(anchor_bonus * 0.14)
                chips.append(["mid","빌드 시동"])
                reasons.append(f"{must_refs[0]['name']} 빌드를 열 수 있음")
            elif rec_refs:
                s += 12 + round(anchor_bonus * 0.10)
                chips.append(["mid","빌드 후보"])
                reasons.append(f"{rec_refs[0]['name']} 후보 카드")

        # 콤보 보너스
        if combo["full"]:
            boost = 20 if in_active else 58
            s += boost
            chips.append(["good","지금 강함"])
            reasons.append("지금 뽑으면 바로 강해집니다")
        elif combo["partial"]:
            boost = 10 if in_active else 24
            s += boost
            chips.append(["mid","나중에 좋아짐"])
            reasons.append("후속 카드가 붙을수록 더 좋아집니다")

        # 범용 카드
        if _list_has(uni, name) and dupes == 0:
            s += 18
            chips.append(["mid","범용"])
            reasons.append("단독 성능이 좋은 카드")

        # 고점 카드
        if name in HIGH_POWER_SINGLES:
            s += 42
            if early: s += 16
            if is_boss_reward: s += 14
            chips.append(["mid","고점 카드"])
            reasons.append("카드 자체 체급이 높음")

        # 단계별 보정
        if early:
            if card.get("tier") in ("S","A") and dupes == 0:
                s += 10
                if not any(v in ["지금 강함","필수","고점 카드","빌드 시동"] for _,v in chips):
                    chips.append(["mid","초반 안정성"])
            if card.get("t") == "공격" and (card.get("c") if isinstance(card.get("c"), int) else 1) <= 1:
                s += 12
                if not any(v == "초반 안정성" for _,v in chips):
                    chips.append(["mid","전투력"])
            if card.get("t") == "파워" and not combo["full"] and not in_active and \
               card.get("tier") != "S" and name not in must_refs and name not in HIGH_POWER_SINGLES:
                s -= 14
                reasons.append("지금은 바로 강해지지 않음")
        elif mid:
            if card.get("t") == "파워" and in_active: s += 10
            if must_refs: s += 8
        elif late:
            if card.get("t") == "파워" and in_active: s += 12
            if combo["partial"]: s += 6

        # 보스 보상 빌드 확정 보너스
        if is_boss_reward and active_arch and build_commit >= 4:
            if in_active: s += 24
            elif card.get("tier") == "S": s -= 18
        if is_boss_reward and active_arch and _list_has(active_arch.get("must",[]) + active_arch.get("rec",[]), name):
            s += 18

        # 덱 오염 패널티
        basic_set = set(BASIC_CARDS.get(char, []))
        if total_cards >= 18 and not in_active and not combo["full"] and \
           card.get("tier") != "S" and not must_refs and name not in HIGH_POWER_SINGLES:
            s -= 18; warn = True
            chips.append(["bad","덱 오염"])
            reasons.append("덱이 커져서 회전이 느려짐")
        elif total_cards >= 15 and not in_active and not combo["full"] and \
             card.get("tier") == "B" and not must_refs and name not in HIGH_POWER_SINGLES:
            s -= 8
            reasons.append("지금은 우선순위 낮음")

        # 중복 패널티
        MULTI_OK = {"폼멜타격","촉진제","촉매","계산된 도박","곡예","서류 폭풍","반향"}
        if dupes >= 2:
            s -= 55; warn = True
            chips.append(["bad","중복"])
            reasons.append(f"이미 {dupes}장 보유")
        elif dupes == 1:
            if name in MULTI_OK:
                s += 6; reasons.append("2장째도 고려 가능")
            else:
                s -= 26; warn = True
                chips.append(["bad","중복"])
                reasons.append("이미 1장 있음")

        # D 티어 패널티
        if card.get("tier") == "D":
            s -= 25; warn = True
            chips.append(["bad","기본 카드"])
            reasons.append("기본 카드는 줄이는 편이 좋음")
        if name in basic_set and total_cards > 10:
            s -= 20; warn = True
            if not any("기본" in r for r in reasons):
                reasons.append("기본 카드는 교체 대상")

        if not reasons: reasons.append(card.get("tip") or "무난한 카드")
        reasons = reasons[:2]

        # 요약
        chip_labels = [v for _,v in chips]
        if "필수" in chip_labels:        summary = "이 카드 없으면 빌드가 완성되지 않습니다"
        elif "시너지" in chip_labels:    summary = "모일수록 빌드가 제대로 굴러가기 시작합니다"
        elif "지금 강함" in chip_labels: summary = "지금 뽑으면 바로 강해집니다"
        elif "빌드 시동" in chip_labels: summary = "지금 집으면 한 빌드 방향을 열기 좋음"
        elif "빌드 후보" in chip_labels: summary = "후속 카드가 붙으면 빌드 후보가 될 수 있음"
        elif "오프빌드" in chip_labels:  summary = "덱과는 다르지만 성능이 강함"
        elif "엔진 없음" in chip_labels: summary = "핵심 재료가 없어 지금은 기대값이 낮음"
        elif "전투력" in chip_labels or "초반 안정성" in chip_labels: summary = "초반 전투력 보강용으로 좋음"
        elif "덱 오염" in chip_labels:   summary = "지금은 넣을수록 덱이 무거워짐"
        else:                            summary = "지금 뽑아도 무난함"

        return {"score": s, "reasons": reasons, "tier": card.get("tier","?"),
                "warn": warn, "summary": summary, "chips": chips}

    # ── 공개 API ──────────────────────────────────
    def recommend(self, deck: list[str], choice: CardChoice, hist: list = None) -> list[Recommendation]:
        hist = hist or []
        char = self._current_char(deck)
        stage_info = self._get_run_stage(deck, char, hist)
        stage = stage_info["stage"]
        active_arch = self._detect_build(deck, char, stage) if char else None
        is_boss = self._is_boss_reward(choice)

        results = []
        for card_name in choice.cards:
            info = self._score_card(card_name, deck, char or "IC", active_arch, stage, is_boss, hist)
            in_build = active_arch and (
                _list_has(active_arch.get("must", []), card_name) or
                _list_has(active_arch.get("rec", []), card_name)
            )
            results.append(Recommendation(
                card_name=card_name,
                action="pick",
                score=info["score"],
                reason=" / ".join(info["reasons"]),
                summary=info["summary"],
                chips=info["chips"],
                arch_name=active_arch["name"] if in_build else None,
            ))

        results.sort(key=lambda r: r.score, reverse=True)

        # 스킵 판단
        SKIP_THRESHOLD = 15
        if results and results[0].score < SKIP_THRESHOLD:
            results[0].action = "skip"
            results[0].summary = "모든 카드 가성비 낮음 — 스킵 권장"
        else:
            results[0].action = "pick"

        return results

    def deck_status(self, deck: list[str], arch_id: Optional[str] = None, hist: list = None) -> Optional[dict]:
        hist = hist or []
        char = self._current_char(deck)
        if not char: return None
        stage_info = self._get_run_stage(deck, char, hist)
        stage = stage_info["stage"]
        all_scored = self._detect_build_scores(deck, char, stage)
        if not all_scored: return None

        deck_set = set(deck)
        def _build_status(entry: dict) -> dict:
            arch = entry["arch"]
            must = arch.get("must", [])
            rec  = arch.get("rec",  [])
            must_have = [c for c in must if c in deck_set]
            must_need = [c for c in must if c not in deck_set]
            rec_have  = [c for c in rec  if c in deck_set]
            rec_need  = [c for c in rec  if c not in deck_set]
            total = len(must) + len(rec)
            have  = len(must_have) + len(rec_have)
            pct   = int(have / total * 100) if total else 100
            return {"arch": arch, "score": entry["score"],
                    "must_have": must_have, "must_need": must_need,
                    "rec_have": rec_have, "rec_need": rec_need, "pct": pct}

        if arch_id:
            for e in all_scored:
                if e["id"] == arch_id:
                    return _build_status(e)
        min_score = 26 if stage == "early" else 34
        top = all_scored[0]
        if top["mh"] == 0:
            min_score += 60
        if top["score"] < min_score:
            return None
        return _build_status(top)

    def all_arch_status(self, deck: list[str], hist: list = None) -> list[dict]:
        hist = hist or []
        char = self._current_char(deck)
        if not char: return []
        stage_info = self._get_run_stage(deck, char, hist)
        stage = stage_info["stage"]
        all_scored = self._detect_build_scores(deck, char, stage)
        deck_set = set(deck)
        result = []
        for entry in all_scored:
            arch = entry["arch"]
            must = arch.get("must", [])
            rec  = arch.get("rec",  [])
            must_have = [c for c in must if c in deck_set]
            must_need = [c for c in must if c not in deck_set]
            rec_have  = [c for c in rec  if c in deck_set]
            rec_need  = [c for c in rec  if c not in deck_set]
            total = len(must) + len(rec)
            have  = len(must_have) + len(rec_have)
            pct   = int(have / total * 100) if total else 100
            result.append({"arch": arch, "score": entry["score"],
                           "must_have": must_have, "must_need": must_need,
                           "rec_have": rec_have, "rec_need": rec_need, "pct": pct})
        return result

    def upgrade_suggestions(self, deck: list[str], hist: list = None) -> list[tuple[str, str, str]]:
        hist = hist or []
        char = self._current_char(deck)
        stage_info = self._get_run_stage(deck, char, hist)
        stage = stage_info["stage"]
        active_arch = self._detect_build(deck, char, stage) if char else None
        must_set = set(active_arch.get("must", [])) if active_arch else set()
        rec_set  = set(active_arch.get("rec",  [])) if active_arch else set()

        result: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for name in deck:
            if name in seen: continue
            seen.add(name)
            card = self._get_card_meta(name, char)
            if not card: continue
            tier = card.get("tier", "C")
            if name in must_set:
                priority = "★★★ 필수"
                reason   = f"빌드 핵심 카드 (티어 {tier})"
            elif name in rec_set:
                priority = "★★ 권장"
                reason   = f"빌드 시너지 카드 (티어 {tier})"
            elif tier in ("S", "A"):
                priority = "★★ 권장"
                reason   = f"고티어 카드 (티어 {tier})"
            elif tier == "D":
                priority = "✕ 제거 고려"
                reason   = f"저티어 카드 — 소멸 우선"
            else:
                priority = "★ 보통"
                reason   = f"티어 {tier}"
            result.append((name, priority, reason))

        order = {"★★★ 필수": 0, "★★ 권장": 1, "★ 보통": 2, "✕ 제거 고려": 3}
        result.sort(key=lambda x: order.get(x[1], 9))
        return result


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    engine = RecommendEngine("cards.json")
    deck = ["타격","타격","수비","수비","강타","떨림","악랄함","협박","흘려보내기","파괴"]

    print("=== 덱 방향성 ===")
    status = engine.deck_status(deck)
    if status:
        arch = status["arch"]
        print(f"추천 빌드: {arch['name']} (빌드점수:{status['score']:.0f}, 완성도:{status['pct']}%)")
        print(f"보유 must: {status['must_have']}")
        print(f"필요 must: {status['must_need']}")
        print(f"보유 rec: {status['rec_have']}")
        print(f"필요 rec: {status['rec_need']}")
    else:
        print("빌드 감지 안 됨")

    print()
    print("=== 카드 선택 추천 ===")
    for cards, label in [
        (["어퍼컷", "포식", "수비"], "고티어+범용+저티어"),
        (["타격", "수비", "철의파동"], "저티어 3장"),
        (["해체", "박치기", "난타"], "rec 카드들"),
    ]:
        choice = CardChoice(cards=cards)
        recs = engine.recommend(deck, choice)
        print(f"[{label}]")
        for r in recs:
            mark = ">>>" if r.action in ("pick","skip") and r == recs[0] else "   "
            print(f"  {mark} [{r.action.upper()}] {r.card_name} (점수:{r.score}) — {r.summary}")
        print()

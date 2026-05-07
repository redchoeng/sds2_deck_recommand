"""
OCR 결과 → 카드명 퍼지 매칭
1단계: 공백 정규화 + token_sort_ratio (threshold=50)
2단계: 한글 자모 분해 + ratio (threshold=65) — 블랙홀처럼 모양이 비슷한 글자 오독 처리
캐릭터 필터: 현재 덱 캐릭터 + colorless/basic 카드만 후보로 제한 (오매칭 방지)
"""
import json
from rapidfuzz import process, fuzz

ONSET = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
VOWEL = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
CODA  = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"

# 전 캐릭터 공용 카드 키
_SHARED_CHARS = {"colorless", "basic"}

def _jamo(text: str) -> str:
    result = []
    for ch in text.replace(" ", ""):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            off = code - 0xAC00
            coda_i = off % 28
            result.append(ONSET[off // 28 // 21])
            result.append(VOWEL[(off // 28) % 21])
            if coda_i:
                result.append(CODA[coda_i])
        else:
            result.append(ch)
    return "".join(result)


class CardMatcher:
    def __init__(self, cards_path: str = "cards.json"):
        with open(cards_path, encoding="utf-8") as f:
            data = json.load(f)

        self.all_names: list[str] = []
        self._normalized: list[str] = []  # 공백 제거 (1단계 매칭용)
        self._jamo_list: list[str] = []   # 자모 분해 (2단계 매칭용)
        self._char_of: dict[str, str] = {}      # 카드명 → 기본 캐릭터 키
        self._chars_of: dict[str, set] = {}     # 카드명 → 등장 캐릭터 집합 (공유 카드 감지)

        for char_key, card_list in data["cards"].items():
            for card in card_list:
                name = card["n"]
                self._chars_of.setdefault(name, set()).add(char_key)
                if name not in self.all_names:
                    self.all_names.append(name)
                    self._normalized.append(name.replace(" ", ""))
                    self._jamo_list.append(_jamo(name))
                    self._char_of[name] = char_key

        # 여러 캐릭터에 등장하는 공유 카드 (타격·수비 등) — 모든 풀에 포함
        self._shared_names: set[str] = {
            n for n, chars in self._chars_of.items()
            if len(chars) > 1 or chars & _SHARED_CHARS
        }

        # 캐릭터별 인덱스 (해당 캐릭터 + colorless/basic + 공유 카드)
        self._char_indices: dict[str, list[int]] = {}
        all_chars = {c for c in self._char_of.values() if c not in _SHARED_CHARS}
        for char in all_chars:
            self._char_indices[char] = [
                i for i, name in enumerate(self.all_names)
                if self._char_of[name] in (char, *_SHARED_CHARS)
                or name in self._shared_names
            ]

    def _indices_for(self, char: str | None) -> list[int] | None:
        """캐릭터에 맞는 인덱스 목록. None이면 전체 사용."""
        if char and char in self._char_indices:
            return self._char_indices[char]
        return None

    def _match_with_indices(
        self, ocr_norm: str, indices: list[int] | None,
        threshold: int, jamo_threshold: int
    ) -> tuple[str | None, int]:
        """지정 인덱스 범위 내에서 매칭 → (카드명, 점수)"""
        if indices is not None:
            norm_pool = [self._normalized[i] for i in indices]
            jamo_pool = [self._jamo_list[i] for i in indices]
        else:
            norm_pool = self._normalized
            jamo_pool = self._jamo_list

        # 1단계
        r1 = process.extractOne(ocr_norm, norm_pool, scorer=fuzz.token_sort_ratio)
        if r1 and r1[1] >= threshold:
            idx = indices[r1[2]] if indices is not None else r1[2]
            return self.all_names[idx], r1[1]

        # 2단계 자모
        ocr_jamo = _jamo(ocr_norm)
        r2 = process.extractOne(ocr_jamo, jamo_pool, scorer=fuzz.ratio)
        if r2 and r2[1] >= jamo_threshold:
            idx = indices[r2[2]] if indices is not None else r2[2]
            return self.all_names[idx], r2[1]

        return None, 0

    @staticmethod
    def _strip_upgrade(text: str) -> str:
        """강화 카드 표시 제거: '타격+' → '타격', '타격+2' → '타격'"""
        import re
        return re.sub(r'\+\d*$', '', text.strip())

    def match(self, ocr_text: str, threshold: int = 50, jamo_threshold: int = 65,
              char: str | None = None) -> str | None:
        """OCR 텍스트를 카드명으로 매칭.
        char 지정 시 해당 캐릭터 + 공용 카드만 후보로 사용.
        """
        ocr_norm = self._strip_upgrade(ocr_text).replace(" ", "")
        if not ocr_norm:
            return None
        indices = self._indices_for(char)
        name, _ = self._match_with_indices(ocr_norm, indices, threshold, jamo_threshold)

        # 캐릭터 필터 후 미매칭 → 전체 카드로 재시도 (폴백)
        if name is None and indices is not None:
            name, _ = self._match_with_indices(ocr_norm, None, threshold, jamo_threshold)
        return name

    def match_best_from_candidates(
        self, candidates: list[str],
        threshold: int = 50, jamo_threshold: int = 65,
        char: str | None = None
    ) -> tuple[str | None, int]:
        """여러 OCR 후보 중 가장 잘 맞는 카드명 반환 → (카드명, 점수)"""
        indices = self._indices_for(char)
        best_name = None
        best_score = -1

        for text in candidates:
            if not text:
                continue
            norm = self._strip_upgrade(text).replace(" ", "")
            name, score = self._match_with_indices(norm, indices, threshold, jamo_threshold)
            if name and score > best_score:
                best_score = score
                best_name = name

        # 캐릭터 필터로 미매칭 → 전체 재시도
        if best_name is None and indices is not None:
            for text in candidates:
                if not text:
                    continue
                norm = self._strip_upgrade(text).replace(" ", "")
                name, score = self._match_with_indices(norm, None, threshold, jamo_threshold)
                if name and score > best_score:
                    best_score = score
                    best_name = name

        return best_name, best_score

    def match_many(self, ocr_texts: list[str], threshold: int = 50,
                   char: str | None = None) -> list[str | None]:
        return [self.match(t, threshold, char=char) for t in ocr_texts]

    def match_many_candidates(self, candidates_list: list[list[str]],
                              threshold: int = 50, char: str | None = None
                              ) -> list[tuple[str | None, int]]:
        """각 카드 영역의 후보 리스트를 매칭 → (카드명, 점수) 목록 반환"""
        return [self.match_best_from_candidates(cands, threshold, char=char)
                for cands in candidates_list]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    m = CardMatcher("cards.json")

    # 캐릭터 필터 테스트
    print("=== RE 필터 ===")
    tests_re = [("궤도", "RE"), ("별의 파동", "RE"), ("구르기", "RE"), ("타격", "RE")]
    for text, char in tests_re:
        print(f"  '{text}' (char={char}) -> '{m.match(text, char=char)}'")

    print("=== 전체 ===")
    for text, _ in tests_re:
        print(f"  '{text}' -> '{m.match(text)}'")

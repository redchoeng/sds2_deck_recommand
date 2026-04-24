# STS2 Overlay 작업 현황

## 프로젝트 구조

```
sds2/
├── overlay/               # 메인 오버레이 패키지
│   ├── overlay.py         # PyQt6 오버레이 UI (메인 진입점)
│   ├── engine.py          # 카드 추천 엔진 (티어/빌드 기반)
│   ├── capture.py         # 스크린 캡처 + OCR (mss + Windows OCR)
│   ├── calibrate.py       # 캘리브레이션 도구 (카드 영역 지정)
│   ├── matcher.py         # OCR 결과 → 카드명 퍼지 매칭 (rapidfuzz)
│   ├── capture_config.json# 저장된 캘리브레이션 설정
│   ├── cards.json         # 카드 DB (티어/빌드 정보 포함, 501장)
│   └── deck.json          # 현재 덱 상태 (런타임 저장)
├── cards.json             # 루트 카드 DB
├── extract_data.py        # 카드 데이터 추출 스크립트
└── mod/                   # 모드 파일
```

## 실행 방법

```bash
cd /c/Users/준호/Desktop/coding/sds2/overlay
source venv311/Scripts/activate

# 최초 캘리브레이션 (카드 영역 지정)
python calibrate.py

# 오버레이 실행
python overlay.py
# 또는 백그라운드 (창 없이)
pythonw overlay.py
```

## 구현된 기능 (전체)

### OCR 파이프라인
- [x] Windows OCR (WinRT/DirectML) — easyocr 대비 **20배 빠름**, AMD/Intel/NVIDIA GPU 자동 가속
  - 설치: `pip install winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Globalization winrt-Windows.Foundation`
  - easyocr는 Windows OCR 실패 시 자동 폴백으로만 사용
- [x] 다중 전략 OCR (`ocr_region_candidates`)
  - 전략 1: h*0.20~0.65, w*0.20~0.95 크롭 → Windows OCR (마나 배지 제외)
  - 전략 2: brightness>200 마스킹 (하늘색 리본 흰 글씨) — 전략 1 텍스트 짧을 때만 실행
  - 테스트 결과 3/3 정확 (별무리, 권위 행사, 전투의 성과)
- [x] mss 일괄 캡처 (`capture_regions`) — 카드 영역 전체를 컨텍스트 1회로 캡처
- [x] OCR 텍스트 박스 좌→우 정렬 후 전부 이어붙이기 (다단어 카드명 대응: "별의 파동" 등)
- [x] Windows OCR 전용 asyncio 이벤트 루프 (매 호출 루프 생성 오버헤드 제거)

### 카드명 매칭 (matcher.py)
- [x] 1단계: 공백 정규화 + token_sort_ratio (threshold=50)
- [x] 2단계: 한글 자모 분해 + ratio (threshold=65) — "불래호"→"블랙홀" 같은 유사 오독 복구
- [x] `match_best_from_candidates()` — 다중 OCR 후보 중 매칭 점수 최고 결과 선택
- [x] `match_many_candidates()` — 영역별 후보 리스트 배치 처리
- [x] **캐릭터 필터 매칭** — 현재 덱 캐릭터 + 공유 카드만 후보로 제한 (타 캐릭터 오매칭 방지)
  - 예: RE 덱이면 RE + colorless + 공유 카드(타격·수비 등)만 검색
  - 필터 후 미매칭 → 전체 카드로 자동 폴백

### 추천 엔진 (engine.py)
- [x] 티어 점수 + 빌드 아키텍처 기반 카드 추천
- [x] 캐릭터 자동 감지 (공유 카드 타격·수비 등 제외, 캐릭터 고유 시작 카드 포함)
- [x] 빌드 완성도 계산 — **필수(must) + 시너지(rec) 카드 모두 포함**
- [x] `upgrade_suggestions()` — 강화 우선도 (★★★ 필수 / ★★ 권장 / ★ 보통 / ✕ 제거 고려)

### UI (overlay.py)
- [x] STS2 + HDT 스타일 다크 테마 (WINDOW_BG #0d0d1a)
- [x] 창 폭 260px, 화면 우측 상단 자동 배치
- [x] 헤더: 캐릭터 아이콘(색상별) + 타이틀 + ⚙ + ✕ 종료
- [x] 보상/상점 화면 구분 (타이틀 색상, 버튼 라벨)
- [x] 카드 위젯: 티어 뱃지(클릭→수정) + 순위 + 카드명 + 액션 + 이유
- [x] QScrollArea (상점 7장도 스크롤)
- [x] 덱 패널: 현재 덱 카드 목록 + "편집" 버튼
- [x] 덱 편집 다이얼로그: 카드별 ✕ 제거 + 강화 우선도 뱃지

### 카드 DB (cards.json)
- [x] 총 501장 (RE 91장)
- [x] 추가된 카드: `일곱 개의 별` (RE, tier B)
- [x] 제거된 중복: `비밀기술` (공백 없는 버전) → `비밀 기술`로 통일

### 캘리브레이션 설정 현황 (capture_config.json)
- 보상 카드 영역: 3개
- 상점 카드 영역: 7개 (상단 5장 + 하단 2장)
- 감지 픽셀: 보상/상점 모두 설정됨

### Claude Vision API 폴백 (claude_ocr.py)
- [x] Windows OCR + 퍼지 매칭 후에도 미매칭 카드만 Claude API 호출
- [x] 모델: **claude-sonnet-4-6** (장식체 한글 폰트 인식률 우수 — haiku는 "감마 세례"→"카드 세레" 오독)
- [x] API 키 설정: overlay/.api_key 파일에 키 입력하거나 ANTHROPIC_API_KEY 환경변수
- [x] API 키 없으면 자동 비활성화 (오버레이 정상 동작)
- [x] **마나 배지 제외 크롭** — Claude용 w*0.22, Windows OCR용 w*0.20 시작 (왼쪽 마나 비용 배지 제거)
- [x] **포그라운드 창 감지** — 게임/오버레이 창이 아닌 앱 전환 시 OCR 자동 중단 (IDE·브라우저 오캡처 방지)
- [x] **필터 강화** — "죄송", "모름", "알 수", "이미지", "제시" 로 시작하는 설명형 답변 무시

## 다음 작업 후보
- [ ] Claude API 키 입력 및 폴백 테스트 (.api_key 파일)
- [ ] 누락 카드 계속 추가 (게임 중 인식 안 되는 카드 발견 시)
- [ ] 유물 추천 (cards.json에 relics 키 추가 필요, 위키 리서치 후 진행)
- [ ] 카드 가격 표시 (상점 — 별도 OCR 영역 필요)

## 디버그 스크립트

```bash
# 상점 OCR 테스트
python debug_shop.py

# 루프 상태 확인
python debug_loop.py

# 카드 영역 캡처 확인
python debug_capture.py
```

## 캐릭터 키 매핑
- `IC` = 아이언클래드
- `SI` = 사일런트
- `DE` = 디펙트
- `NE` = 네크로바인더
- `RE` = 리젠트

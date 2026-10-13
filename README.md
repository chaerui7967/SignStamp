# SignStamp

흰 종이에 쓴 서명 사진을 올리면 배경을 투명하게 만들어 보관하고,
PDF · Word · 한글 문서의 원하는 위치에 끌어다 놓아 삽입하는 Windows 데스크톱 프로그램.

## 실행

[uv](https://docs.astral.sh/uv/) 로 실행합니다.

```bash
uv sync                      # 최초 1회: .venv 생성 + 의존성 설치
uv run signstamp             # 실행 (콘솔 창 없이 GUI 로 뜸)
uv run python -m signstamp   # 콘솔 로그를 보면서 실행
```

- `pyproject.toml` 에 `python-preference = "only-managed"` 를 지정해 uv 가 받은 파이썬만 씁니다.
  Anaconda 파이썬을 기반으로 venv 를 만들면 PySide6 가 `DLL load failed` 로 실패하기 때문입니다.
- uv 없이 쓰려면 `pip install -r requirements.txt` 후 `python -m signstamp` 로도 실행됩니다.

- Word 문서(.docx/.doc/.rtf)는 **MS Word**, 한글 문서(.hwp/.hwpx)는 **한컴오피스 한글**이 설치돼 있어야 합니다.
- PDF 는 별도 프로그램 없이 동작합니다.

## 사용법

1. **서명 등록** — `+ 서명 등록 (사진)` → 서명 사진 선택
   - 사진에서 서명 부분을 드래그해 영역을 고르면 더 깔끔합니다.
   - 획이 끊기면 *배경 제거 강도*를 낮추고, 배경이 남으면 높이세요.
   - 서명 색: 검정 / 파랑 / 원본 색
   - 등록한 서명은 `%APPDATA%\SignStamp\signatures` 에 투명 PNG 로 저장됩니다.
2. **문서 열기** — `문서 열기` 또는 창에 파일을 끌어다 놓기
3. **서명 배치** — 왼쪽 서명을 더블클릭하면 현재 페이지에 들어갑니다.
   - 드래그: 이동 / 파란 모서리: 크기 조절 (Shift: 비율 무시)
   - 방향키: 미세 이동 (Shift+방향키: 크게) / Delete: 삭제 / 오른쪽 클릭: 복제
   - Ctrl+휠: 확대·축소, PgUp/PgDn: 페이지 이동
4. **저장** — `서명 넣어 저장` → 원본은 그대로 두고 `원본이름_서명.확장자` 로 저장됩니다.

## 동작 방식

| 형식 | 미리보기 | 삽입 |
|---|---|---|
| PDF | PyMuPDF 로 렌더링 | 페이지에 이미지 삽입 (회전된 페이지 지원) |
| Word | Word 로 PDF 변환 후 렌더링 | Word COM: 쪽 기준 위치의 "글 앞으로" 그림 |
| 한글 | 한글로 PDF 변환 후 렌더링 | 한글 COM: 종이 기준 위치의 "글 앞으로" 그림 |

Word/한글에 들어간 서명은 일반 그림 개체라서 해당 프로그램에서 다시 옮기거나 지울 수 있습니다.

배경 제거는 종이의 밝기 분포(그림자·조명 얼룩 포함)를 추정해 나눈 뒤, 종이보다 어두운 정도를
투명도로 바꾸는 방식이라 휴대폰으로 찍은 사진에서도 잘 동작합니다.

## 한글 보안 승인 창

한글 자동화는 기본적으로 파일을 열 때마다 "접근 허용" 창을 띄웁니다. 매번 뜨는 게 번거로우면
한컴에서 배포하는 보안 모듈(`FilePathCheckerModule.dll`)을 받아 레지스트리에 등록하세요.

```
HKEY_CURRENT_USER\Software\HNC\HwpAutomation\Modules
  FilePathCheckerModule = (dll 전체 경로)   ; 문자열 값
```

## 구조

```
signstamp/
  sigproc.py     서명 사진 → 투명 PNG
  library.py     서명 보관함
  documents.py   PDF 변환(미리보기) · PDF/Word/한글 삽입
  ui_register.py 서명 등록 창
  ui_main.py     메인 창 (페이지 뷰, 서명 배치)
```

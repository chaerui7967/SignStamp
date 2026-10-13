"""문서 미리보기(PDF 변환)와 서명 삽입.

- PDF        : PyMuPDF 로 직접 처리
- Word       : MS Word COM 자동화 (.docx/.doc/.rtf)
- 한글        : 한컴오피스 COM 자동화 (.hwp/.hwpx)

모든 좌표는 "화면에 보이는 페이지" 기준 pt(1/72 inch), 왼쪽 위가 원점이다.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz

PDF_EXT = {".pdf"}
WORD_EXT = {".docx", ".doc", ".rtf", ".odt"}
HWP_EXT = {".hwp", ".hwpx"}
SUPPORTED_EXT = PDF_EXT | WORD_EXT | HWP_EXT

PT_TO_HWPUNIT = 100  # 1pt = 100 HWPUNIT (1inch = 7200)


@dataclass
class Placement:
    sig_path: str
    page: int  # 0부터
    x: float
    y: float
    w: float
    h: float


def kind_of(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in WORD_EXT:
        return "word"
    if ext in HWP_EXT:
        return "hwp"
    raise ValueError(f"지원하지 않는 형식입니다: {ext}")


def default_output_path(src: str | Path) -> Path:
    src = Path(src)
    out = src.with_name(f"{src.stem}_서명{src.suffix}")
    i = 2
    while out.exists():
        out = src.with_name(f"{src.stem}_서명({i}){src.suffix}")
        i += 1
    return out


# ---------------------------------------------------------------- 미리보기

_tmpdir = Path(tempfile.mkdtemp(prefix="signstamp_"))


def make_preview_pdf(src: str | Path) -> Path:
    """미리보기용 PDF 경로를 반환 (PDF 는 원본 그대로, 그 외는 변환)."""
    src = Path(src).resolve()
    kind = kind_of(src)
    if kind == "pdf":
        return src
    out = _tmpdir / f"{src.stem}_{abs(hash(str(src)))}.pdf"
    if kind == "word":
        _word_to_pdf(src, out)
    else:
        _hwp_to_pdf(src, out)
    if not out.exists():
        raise RuntimeError("PDF 변환에 실패했습니다.")
    return out


# ---------------------------------------------------------------- 저장

def save_with_signatures(src: str | Path, out: str | Path, placements: list[Placement]) -> list[str]:
    """서명을 넣어 out 에 저장하고, 사용자에게 알릴 경고 목록을 반환."""
    src, out = Path(src).resolve(), Path(out).resolve()
    if src == out:
        raise ValueError("원본과 다른 파일 이름으로 저장해 주세요.")
    kind = kind_of(src)
    if kind == "pdf":
        _insert_pdf(src, out, placements)
        return []
    shutil.copyfile(src, out)
    try:
        if kind == "word":
            return _insert_word(out, placements)
        return _insert_hwp(out, placements)
    except Exception:
        out.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- PDF

def _insert_pdf(src: Path, out: Path, placements: list[Placement]) -> None:
    doc = fitz.open(src)
    try:
        for p in placements:
            page = doc[p.page]
            # 회전된 페이지면 보이는 좌표 → 내부 좌표로 변환하고 그림도 같이 돌린다
            rect = fitz.Rect(p.x, p.y, p.x + p.w, p.y + p.h) * page.derotation_matrix
            page.insert_image(rect, filename=p.sig_path, keep_proportion=False,
                              rotate=-page.rotation % 360, overlay=True)
        doc.save(out, garbage=3, deflate=True)
    finally:
        doc.close()


# ---------------------------------------------------------------- Word

def _repair_gen_py_cache() -> None:
    """pywin32 의 gen_py 캐시에 __init__.py 없이 남은 깨진 폴더가 있으면 지운다.
    (중간에 생성이 끊기면 생기며, 그대로 두면 COM 호출이 AttributeError 로 실패한다)"""
    import sys

    import win32com

    gen = Path(win32com.__gen_path__)
    if not gen.is_dir():
        return
    for d in gen.iterdir():
        if d.is_dir() and not (d / "__init__.py").exists():
            shutil.rmtree(d, ignore_errors=True)
            for name in [m for m in sys.modules if d.name in m]:
                del sys.modules[name]


def _new_com(progid: str):
    """새 COM 서버 인스턴스. 타입라이브러리 캐시 없이 동적 디스패치를 쓴다."""
    import pythoncom
    import pywintypes
    import win32com.client.dynamic

    _repair_gen_py_cache()
    pythoncom.CoInitialize()
    disp = pythoncom.CoCreateInstance(pywintypes.IID(progid), None,
                                      pythoncom.CLSCTX_LOCAL_SERVER, pythoncom.IID_IDispatch)
    return win32com.client.dynamic.Dispatch(disp)


def _word_app():
    word = _new_com("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    return word


def _word_open(word, path: Path, read_only: bool):
    return word.Documents.Open(str(path), ConfirmConversions=False, ReadOnly=read_only,
                               AddToRecentFiles=False, Visible=False)


def _word_to_pdf(src: Path, out: Path) -> None:
    word = _word_app()
    try:
        doc = _word_open(word, src, read_only=True)
        try:
            doc.ExportAsFixedFormat(OutputFileName=str(out), ExportFormat=17)  # wdExportFormatPDF
        finally:
            doc.Close(SaveChanges=0)
    finally:
        word.Quit()


wdActiveEndPageNumber, wdWithInTable = 3, 12
wdHorizontalPositionRelativeToPage, wdVerticalPositionRelativeToPage = 5, 6


def _word_page_anchor(doc, page_no: int):
    """page_no(1부터) 쪽에 그림을 고정할 앵커 Range.

    표 안에 고정하면 Word 2013+ 는 좌표를 셀 기준으로 계산하므로(LayoutInCell 을 끌 수 없음)
    같은 쪽에서 표 밖 문단을 우선 찾는다."""
    wdGoToPage, wdGoToAbsolute = 1, 1
    rng = doc.GoTo(What=wdGoToPage, Which=wdGoToAbsolute, Count=page_no)
    if not rng.Information(wdWithInTable):
        return rng
    para = rng.Paragraphs(1)
    for _ in range(500):
        para = para.Next()
        if para is None:
            break
        start = para.Range.Duplicate
        start.Collapse(1)  # wdCollapseStart
        if start.Information(wdActiveEndPageNumber) != page_no:
            break
        if not start.Information(wdWithInTable):
            return start
    return rng


def _word_cell_origin(anchor) -> tuple[float, float]:
    """앵커가 표 안이면 그림 좌표의 기준이 되는 셀 왼쪽 위의 쪽 기준 좌표(pt)."""
    if not anchor.Information(wdWithInTable):
        return 0.0, 0.0
    cell = anchor.Cells(1)
    start = cell.Range.Duplicate
    start.Collapse(1)
    x = start.Information(wdHorizontalPositionRelativeToPage) - cell.LeftPadding
    y = start.Information(wdVerticalPositionRelativeToPage) - cell.TopPadding
    return x, y


def _insert_word(path: Path, placements: list[Placement]) -> list[str]:
    word = _word_app()
    try:
        doc = _word_open(word, path, read_only=False)
        try:
            for p in placements:
                anchor = _word_page_anchor(doc, p.page + 1)
                ox, oy = _word_cell_origin(anchor)
                # 동적 디스패치에서는 Anchor 키워드 인자가 무시되므로 위치 인자로 넘긴다
                shp = doc.Shapes.AddPicture(p.sig_path, False, True, p.x, p.y, p.w, p.h, anchor)
                shp.WrapFormat.Type = 3            # wdWrapFront (글 앞에)
                shp.RelativeHorizontalPosition = 1  # wdRelativeHorizontalPositionPage
                shp.RelativeVerticalPosition = 1    # wdRelativeVerticalPositionPage
                shp.LockAspectRatio = 0
                shp.Width, shp.Height = p.w, p.h
                shp.Left, shp.Top = p.x - ox, p.y - oy
                shp.LockAnchor = True
            doc.Save()
        finally:
            doc.Close(SaveChanges=0)
    finally:
        word.Quit()
    return []


# ---------------------------------------------------------------- 한글

def _hwp_app():
    hwp = _new_com("HWPFrame.HwpObject")
    # 보안 모듈이 등록돼 있으면 파일 접근 승인 창이 뜨지 않는다 (README 참고)
    try:
        hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
    except Exception:
        pass
    try:
        hwp.XHwpWindows.Item(0).Visible = False
    except Exception:
        pass
    return hwp


def _hwp_open(hwp, path: Path) -> None:
    fmt = "HWPX" if path.suffix.lower() == ".hwpx" else "HWP"
    if not hwp.Open(str(path), fmt, "forceopen:true"):
        raise RuntimeError(f"한글에서 파일을 열 수 없습니다: {path}")


def _hwp_to_pdf(src: Path, out: Path) -> None:
    hwp = _hwp_app()
    try:
        _hwp_open(hwp, src)
        hwp.SaveAs(str(out), "PDF", "")
        hwp.Clear(1)
    finally:
        hwp.Quit()


def _hwp_goto_page(hwp, page_no: int) -> None:
    pset = hwp.HParameterSet.HGotoE
    hwp.HAction.GetDefault("Goto", pset.HSet)
    pset.HSet.SetItem("DialogResult", page_no)
    pset.SetSelectionIndex = 1  # 쪽 이동
    hwp.HAction.Execute("Goto", pset.HSet)


def _insert_hwp(path: Path, placements: list[Placement]) -> list[str]:
    hwp = _hwp_app()
    try:
        _hwp_open(hwp, path)
        for p in placements:
            _hwp_goto_page(hwp, p.page + 1)
            w_mm, h_mm = p.w * 25.4 / 72, p.h * 25.4 / 72
            # sizeoption=1: 지정한 크기(mm)로 삽입
            ctrl = hwp.InsertPicture(p.sig_path, True, 1, False, False, 0, w_mm, h_mm)
            if ctrl is None:
                raise RuntimeError("한글에 그림을 삽입하지 못했습니다.")
            props = ctrl.Properties
            props.SetItem("TreatAsChar", False)  # 글자처럼 취급 해제
            props.SetItem("TextWrap", 3)         # 글 앞으로
            props.SetItem("FlowWithText", False)
            props.SetItem("AllowOverlap", True)
            props.SetItem("HorzRelTo", 0)        # 종이 기준
            props.SetItem("VertRelTo", 0)
            props.SetItem("HorzAlign", 0)
            props.SetItem("VertAlign", 0)
            props.SetItem("HorzOffset", int(p.x * PT_TO_HWPUNIT))
            props.SetItem("VertOffset", int(p.y * PT_TO_HWPUNIT))
            ctrl.Properties = props
        hwp.Save(True)
        hwp.Clear(1)
    finally:
        hwp.Quit()
    return []

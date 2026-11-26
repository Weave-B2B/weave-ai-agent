"""Page quality gate used by the R2 baseline parser."""

SCAN_MAX_TEXT_CHARS = 20        # 공백 제외 PDF 텍스트 글자 수가 이보다 적으면 '텍스트 거의 없음'
                                # (Docling is_broken_text_layer의 _MIN_CHARS와 같은 값)
SCAN_MIN_IMAGE_COVERAGE = 0.50  # 이미지가 덮는 쪽 면적 비율이 이 이상이면 '쪽 대부분이 이미지'
# 깨진 텍스트 레이어 기준값은 docling.utils.text_quality 상수를 그대로 쓴다 (여기서 바꾸지 않음).
# ----------------------------------------------------------------------------


def _image_coverage(pdf_page):
    """pdfium으로 이미지 객체들이 덮는 면적 비율(겹침은 합산, 1.0 상한)."""
    import pypdfium2.raw as raw
    w, h = pdf_page.get_size()
    area = 0.0
    for obj in pdf_page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=5):
        l, b, r, t = obj.get_bounds()
        area += max(0.0, min(r, w) - max(l, 0.0)) * max(0.0, min(t, h) - max(b, 0.0))
    return min(area / (w * h), 1.0) if w * h else 0.0


def judge_page(page_no, pdf_cells, pdf_page):
    """한 쪽의 판정. pdf_cells: Docling이 읽은 그 쪽의 PDF 텍스트 셀(OCR 셀 제외)."""
    from docling.models.base_ocr_model import segregate_by_visibility
    from docling.utils.text_quality import is_broken_text_layer
    visible, _ = segregate_by_visibility(pdf_cells)
    text = '\n'.join(c.text for c in visible)
    chars = sum(not ch.isspace() for ch in text)
    coverage = _image_coverage(pdf_page)
    reasons = []
    if is_broken_text_layer(text):
        junk = sum(ch == '�' or (ord(ch) < 32 and not ch.isspace()) or 0xE000 <= ord(ch) <= 0xF8FF
                   for ch in text)
        reasons.append(f'text_layer_broken: 제어·대체·사설영역 문자 비율 {junk / max(chars, 1):.2f}')
    if chars < SCAN_MAX_TEXT_CHARS and coverage >= SCAN_MIN_IMAGE_COVERAGE:
        reasons.append(f'scan: 텍스트 {chars}자 < {SCAN_MAX_TEXT_CHARS}, 이미지 면적 {coverage:.2f} >= {SCAN_MIN_IMAGE_COVERAGE}')
    return {'page': page_no, 'reasons': reasons,
            'text_chars': chars, 'image_coverage': round(coverage, 4)}


import base64
import io
import re
import ddddocr
from PIL import Image, ImageEnhance, ImageFilter

_ocr_instance = None

def get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = ddddocr.DdddOcr(show_ad=False)
    return _ocr_instance


def clean_text(text: str) -> str:
    """تنظيف النص وإزالة أي رموز غير مسموحة"""
    return re.sub(r"[^a-zA-Z0-9]", "", text)


def solve_image_ocr(image_input) -> str:
    """
    حل صورة الكابتشا باستخدام OCR
    image_input: base64 string أو bytes أو PIL Image
    """
    if isinstance(image_input, str):
        if "," in image_input:
            image_input = image_input.split(",", 1)[1]
        img_bytes = base64.b64decode(image_input)
    elif isinstance(image_input, Image.Image):
        buf = io.BytesIO()
        image_input.save(buf, format="PNG")
        img_bytes = buf.getvalue()
    else:
        img_bytes = image_input

    ocr = get_ocr()

    # محاولة 1: التعرف المباشر
    result = ocr.classification(img_bytes)
    result = clean_text(result)
    if result:
        return result

    # محاولة 2: معالجة وتحسين التباين
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        enhancer = ImageEnhance.Contrast(img)
        img_enhanced = enhancer.enhance(2.0)
        buf = io.BytesIO()
        img_enhanced.save(buf, format="PNG")
        result2 = ocr.classification(buf.getvalue())
        result2 = clean_text(result2)
        if result2:
            return result2
    except Exception:
        pass

    return result

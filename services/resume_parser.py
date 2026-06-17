import io
import os
import re

import pdfplumber
import docx2txt


class ResumeParseError(Exception):
    """не удалось извлечь осмысленный текст из файла резюме."""


SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt")

MIN_TEXT_LENGTH = 50


def _normalize(text: str) -> str:
    """чистим текст"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    out: list[str] = []
    blank = 0
    for line in text.split("\n"):
        line = line.rstrip()
        if line:
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


def _extract_pdf(file_bytes: bytes) -> str:
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            if txt:
                parts.append(txt)
    return "\n".join(parts)


def _extract_docx(file_bytes: bytes) -> str:
    # docx2txt.process принимает path или file-like
    return docx2txt.process(io.BytesIO(file_bytes)) or ""


def _extract_txt(file_bytes: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".txt": _extract_txt,
}


def extract_resume_text(file_bytes: bytes, filename: str) -> str:
    """извлекает чистый текст из файла резюме"""
    if not file_bytes:
        raise ResumeParseError("пустой файл")

    ext = os.path.splitext(filename or "")[1].lower()
    extractor = _EXTRACTORS.get(ext)
    if extractor is None:
        raise ResumeParseError(
            f"неподдерживаемый формат '{ext or '?'}'. "
            f"Поддерживаются: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    try:
        raw = extractor(file_bytes)
    except Exception as e:
        raise ResumeParseError(f"не удалось прочитать файл ({ext}): {e}") from e

    text = _normalize(raw)

    if len(text) < MIN_TEXT_LENGTH:
        raise ResumeParseError(
            "в файле почти нет текста — возможно, это скан без текстового слоя. "
            "Пришлите текстовый PDF/DOCX."
        )

    return text


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+7|\b8|\b7)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
)


def extract_contacts(text: str) -> dict[str, str | None]:
    email_m = _EMAIL_RE.search(text)
    phone_m = _PHONE_RE.search(text)
    phone = re.sub(r"[^\d+]", "", phone_m.group(0)) if phone_m else None
    return {
        "email": email_m.group(0) if email_m else None,
        "phone": phone,
    }

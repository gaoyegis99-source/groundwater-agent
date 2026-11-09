from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams, LTTextBox
from pdfminer.pdfdocument import PDFDocument, PDFPasswordIncorrect
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser


@dataclass(frozen=True)
class PdfTextBlock:
    page_number: int
    source_index: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class PdfInspection:
    page_count: int
    empty_pages: list[int]
    blocks: list[PdfTextBlock]


def _contains_encryption_marker(handle: BinaryIO) -> bool:
    marker = b"/Encrypt"
    previous = b""
    while chunk := handle.read(1024 * 1024):
        if marker in previous + chunk:
            return True
        previous = chunk[-(len(marker) - 1) :]
    return False


def _document(path: Path) -> tuple[BinaryIO, PDFDocument]:
    handle = path.open("rb")
    if _contains_encryption_marker(handle):
        handle.close()
        raise ValueError("DOCUMENT_ENCRYPTED")
    handle.seek(0)
    try:
        document = PDFDocument(PDFParser(handle), password="")
    except PDFPasswordIncorrect as exc:
        handle.close()
        raise ValueError("DOCUMENT_ENCRYPTED") from exc
    except Exception as exc:
        handle.close()
        raise ValueError("DOCUMENT_INVALID") from exc
    if document.encryption is not None:
        handle.close()
        raise ValueError("DOCUMENT_ENCRYPTED")
    return handle, document


def parse_pdf(path: Path, maximum_pages: int | None = None) -> PdfInspection:
    handle, document = _document(path)
    try:
        pages = list(PDFPage.create_pages(document))
        if maximum_pages is not None and len(pages) > maximum_pages:
            raise ValueError("DOCUMENT_LIMIT_EXCEEDED")
        manager = PDFResourceManager()
        device = PDFPageAggregator(manager, laparams=LAParams(boxes_flow=None))
        interpreter = PDFPageInterpreter(manager, device)
        blocks: list[PdfTextBlock] = []
        empty_pages: list[int] = []
        for page_number, page in enumerate(pages, 1):
            interpreter.process_page(page)
            layout = device.get_result()
            page_height = float(layout.height)
            page_blocks: list[PdfTextBlock] = []
            for source_index, item in enumerate(layout):
                if not isinstance(item, LTTextBox):
                    continue
                text = item.get_text().strip()
                if not text:
                    continue
                page_blocks.append(
                    PdfTextBlock(
                        page_number=page_number,
                        source_index=source_index,
                        text=text,
                        x0=float(item.x0),
                        y0=page_height - float(item.y1),
                        x1=float(item.x1),
                        y1=page_height - float(item.y0),
                    )
                )
            if not page_blocks:
                empty_pages.append(page_number)
            blocks.extend(page_blocks)
        blocks.sort(key=lambda item: (item.page_number, item.y0, item.x0, item.source_index))
        return PdfInspection(page_count=len(pages), empty_pages=empty_pages, blocks=blocks)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("DOCUMENT_INVALID") from exc
    finally:
        handle.close()


def inspect_pdf(path: Path) -> PdfInspection:
    return parse_pdf(path)

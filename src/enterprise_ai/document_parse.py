"""Bounded parsers for inspectable teaching documents, not a hostile-file sandbox."""
import io
import re
import unicodedata
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 500_000
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
PARSER_REVISION = 'm08-parser-v2'


def _text(value):
    if '\x00' in value:
        raise ValueError('NUL in extracted text')
    return unicodedata.normalize('NFC', value.replace('\r\n', '\n').replace('\r', '\n')).strip()


def _markdown(text):
    blocks, lines, section, fence = [], [], '', None

    def flush():
        if lines:
            blocks.append({'text': '\n'.join(lines), 'section': section, 'page': None})
            lines.clear()

    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence:
            lines.append(line)
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*', line):
                fence = None
                flush()
        elif marker:
            flush(); fence = marker[1]; lines.append(line)
        elif match := re.match(r'^ {0,3}#{1,6}\s+(.+)$', line):
            flush(); section = match[1].strip(); lines.append(section); flush()
        elif not line.strip():
            flush()
        else:
            lines.append(line)
    flush()
    return blocks


def _check_docx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 2000 or sum(i.file_size for i in infos) > 20 * 1024 * 1024:
            raise ValueError('DOCX ZIP expansion limit')
        names = set()
        for info in infos:
            name = info.filename
            if (name in names or '\\' in name or ':' in name or name.startswith('/')
                    or '..' in PurePosixPath(name).parts or info.flag_bits & 1
                    or info.file_size / max(info.compress_size, 1) > 200
                    or 'vba' in name.lower() or 'embedding' in name.lower()):
                raise ValueError('unsupported or unsafe DOCX ZIP member')
            names.add(name)
            if name.endswith(('.xml', '.rels')):
                data = archive.read(name)
                if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                    raise ValueError('XML declarations are not supported')
                root = ET.fromstring(data)
                if name == '[Content_Types].xml' and b'macroEnabled' in data:
                    raise ValueError('macro document unsupported')
                if name.startswith('word/') and not name.endswith('.rels'):
                    unsupported = {'ins','del','moveFrom','moveTo','drawing','pict','object',
                                   'altChunk','sdt','fldSimple','fldChar','instrText',
                                   'footnoteReference','endnoteReference','sym','txbxContent'}
                    for el in root.iter():
                        if el.tag in {W+n for n in unsupported} | {M+'oMath',M+'oMathPara'}:
                            raise ValueError('unsupported DOCX content: ' + el.tag.split('}')[-1])
                        if el.tag == W+'tbl' and any(x.tag == W+'tbl' for x in list(el.iter())[1:]):
                            raise ValueError('nested DOCX tables unsupported')
                    if re.match(r'word/(header|footer|footnotes|endnotes)\d*\.xml$', name):
                        if any(el.text and el.text.strip() for el in root.iter(W+'t')):
                            raise ValueError('DOCX header/footer/notes text requires manual review')


def _docx(raw):
    _check_docx(raw)
    try:
        from docx import Document
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise ValueError('install requirements/m08-documents.txt for DOCX') from exc
    document = Document(io.BytesIO(raw))
    blocks, section = [], ''
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text
            if item.style and item.style.name.startswith(('Heading ', 'Title')):
                section = _text(text)
            texts = [text]
        else:
            texts = [' | '.join(cell.text for cell in row.cells) for row in item.rows]
        blocks.extend({'text': text, 'section': section, 'page': None} for text in texts if text.strip())
    return blocks


def _pdf(raw):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError('install requirements/m08-documents.txt for PDF') from exc
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 50:
        raise ValueError('encrypted PDF or page limit exceeded')
    blocks = []
    total = 0
    for n, page in enumerate(reader.pages, 1):
        text = _text(page.extract_text() or '')
        if not text:
            raise ValueError(f'PDF page {n} empty; inspect manually / OCR required')
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError('extracted text limit')
        blocks.append({'text': text, 'section': '', 'page': n})
    return blocks


def parse_document(raw: bytes, suffix: str) -> list[dict]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('document byte size must be 1..10MiB')
    try:
        if suffix.lower() == '.md':
            blocks = _markdown(_text(raw.decode('utf-8-sig')))
        elif suffix.lower() == '.pdf':
            blocks = _pdf(raw)
        elif suffix.lower() == '.docx':
            blocks = _docx(raw)
        else:
            raise ValueError('supported formats: .md, .pdf, .docx')
        result = []
        for block in blocks:
            text = _text(block['text'])
            if text:
                result.append({**block, 'text': text, 'block': len(result)+1})
        if not result or sum(len(b['text']) for b in result) > MAX_TEXT:
            raise ValueError('empty document or extracted text limit')
        return result
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'cannot parse {suffix}: {type(exc).__name__}') from exc

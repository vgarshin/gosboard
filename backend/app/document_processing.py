"""Bounded PDF/text extraction. Worker invocation isolates parsing from the API process."""
import json
from pathlib import Path
import sys
import subprocess
import zipfile
import xml.etree.ElementTree as ET

CHUNK_SPEC = 'page-recursive-1200-180-v1'
MAX_PAGES = 100
MAX_CHARS = 1_000_000
MAX_CHUNKS = 2000



DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
DOC_MIME = 'application/msword'
WORD_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def word_text(path, mime):
    if mime == DOC_MIME:
        try:
            result = subprocess.run(['antiword', '-m', 'UTF-8.txt', '-w', '0', str(path)],
                                    capture_output=True, timeout=15, check=True)
            if len(result.stdout) > 8 * 1024 * 1024:
                raise ValueError('В документе слишком много текста')
            return result.stdout.decode('utf-8')
        except subprocess.CalledProcessError as error:
            if b'text stream of this file is too small' in (error.stderr or b''):
                raise ValueError('Этот DOC слишком короткий для обработчика. Сохраните его как DOCX') from None
            raise ValueError('Не удалось прочитать DOC. Проверьте формат и снимите пароль') from None
        except (subprocess.SubprocessError, UnicodeError):
            raise ValueError('Не удалось прочитать DOC. Проверьте формат и снимите пароль') from None
        except FileNotFoundError:
            raise ValueError('Обработчик DOC недоступен на сервере') from None
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(item.file_size for item in entries) > 40 * 1024 * 1024:
                raise ValueError('Распакованный DOCX превышает допустимый размер')
            entry = archive.getinfo('word/document.xml')
            if entry.file_size > 8 * 1024 * 1024 or entry.flag_bits & 1:
                raise ValueError('Содержимое DOCX слишком большое или зашифровано')
            xml = archive.read(entry)
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise ValueError('Недопустимый XML в DOCX')
        root = ET.fromstring(xml)
        # Paragraphs in document order include table cells. No external relationships are fetched.
        paragraphs = []
        for paragraph in root.iter(WORD_NS + 'p'):
            parts = []
            for node in paragraph.iter():
                if node.tag == WORD_NS + 't': parts.append(node.text or '')
                elif node.tag == WORD_NS + 'tab': parts.append('\t')
                elif node.tag in {WORD_NS + 'br', WORD_NS + 'cr'}: parts.append('\n')
            if parts: paragraphs.append(''.join(parts))
        return '\n\n'.join(paragraphs)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError):
        raise ValueError('Не удалось прочитать DOCX. Проверьте формат и снимите пароль') from None


def extract(path, mime, chunk_size=1200, chunk_overlap=180):
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    if mime == 'application/pdf':
        from pypdf import PdfReader
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ValueError('PDF защищён паролем. Загрузите открытый документ')
        if len(reader.pages) > MAX_PAGES:
            raise ValueError('В документе больше 100 страниц')
        pages = [(i + 1, page.extract_text() or '') for i, page in enumerate(reader.pages)]
    elif mime in {DOC_MIME, DOCX_MIME}:
        pages = [(None, word_text(path, mime))]
    else:
        pages = [(None, Path(path).read_text(encoding='utf-8-sig'))]
    if sum(len(text) for _, text in pages) > MAX_CHARS:
        raise ValueError('В документе слишком много текста')
    if sum(len(text.strip()) for _, text in pages) < 30:
        raise ValueError('Текст не найден. Для сканированного PDF сначала нужен OCR')
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                                              add_start_index=True, separators=['\n\n', '\n', '. ', ' ', ''])
    chunks = []
    for page, text in pages:
        text = text.replace('\x00', '').strip()
        for item in splitter.create_documents([text]):
            if len(item.page_content.strip()) < 20:
                continue
            chunks.append({'page': page, 'position': item.metadata['start_index'], 'text': item.page_content})
            if len(chunks) > MAX_CHUNKS:
                raise ValueError('Документ содержит больше 2000 фрагментов')
    if not chunks:
        raise ValueError('Не удалось выделить текстовые фрагменты')
    return {'pages': len(pages), 'chunks': chunks, 'chunk_spec': f'page-recursive-{chunk_size}-{chunk_overlap}-v1'}


if __name__ == '__main__':
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    try:
        print(json.dumps(extract(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv)>3 else 1200, int(sys.argv[4]) if len(sys.argv)>4 else 180), ensure_ascii=False))
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, UnicodeError)) else 'Не удалось прочитать документ'
        print(json.dumps({'error': message[:240]}, ensure_ascii=False))
        raise SystemExit(1)

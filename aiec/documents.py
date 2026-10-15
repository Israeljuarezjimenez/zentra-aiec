"""Carga de fuentes explícitas; nunca se recorren conversaciones o credenciales."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PAGES = 200
MAX_TEXT_CHARS = 2_000_000
EXTENSIONS = {".md", ".txt", ".pdf"}


@dataclass(frozen=True)
class Document:
    source: str
    title: str
    pages: tuple[tuple[int | None, str], ...]
    version: str


def digest(value: str | bytes):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def read_document(path: Path, source: str):
    if path.is_symlink() or path.suffix.lower() not in EXTENSIONS:
        raise ValueError("Usa documentos .md, .txt o PDF con texto; no se permiten enlaces simbólicos.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{source}: el límite es 10 MB por archivo.")
    raw = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        try:
            from io import BytesIO
            reader = PdfReader(BytesIO(raw))
            if reader.is_encrypted or len(reader.pages) > MAX_PAGES:
                raise ValueError("El PDF debe estar sin contraseña y tener hasta 200 páginas.")
            pages, total_chars = [], 0
            for number, page in enumerate(reader.pages, 1):
                # Evita descomprimir flujos desproporcionados durante la extracción.
                content = page.get_contents()
                if content and len(content.get_data()) > 8 * 1024 * 1024:
                    raise ValueError("Una página es demasiado compleja; exporta el documento como texto.")
                text = page.extract_text() or ""
                total_chars += len(text)
                if total_chars > MAX_TEXT_CHARS:
                    raise ValueError("El PDF contiene demasiado texto; divídelo en archivos menores.")
                if not text.strip():
                    raise ValueError(f"La página {number} no tiene texto extraíble; aplica reconocimiento de texto antes de importarla.")
                pages.append((number, text))
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(f"No se pudo leer el PDF {source}. Exporta una copia con texto.") from error
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise ValueError(f"{source}: guarda el archivo con codificación UTF-8.") from None
        if "\x00" in text:
            raise ValueError(f"{source}: el archivo no es texto válido.")
        pages = [(None, text)]
    if not any(text.strip() for _, text in pages):
        raise ValueError(f"{source}: el documento está vacío.")
    if sum(len(text) for _, text in pages) > MAX_TEXT_CHARS:
        raise ValueError(f"{source}: demasiado texto; divide el documento en archivos menores.")
    return Document(source, path.stem, tuple(pages), digest(raw))


def catalog_document(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    sections = [
        f"Negocio: {data['business']}\nMoneda: {data['currency']}\nMercado: {data['market']}\nHorario: {data['hours']}",
    ]
    if data.get("commercial_context"):
        sections.append(f"Estado del catálogo: {data.get('catalog_status', 'sin especificar')}\n"
                        f"Versión: {data.get('version', 'sin especificar')}\n{data['commercial_context']}\n"
                        f"Precios: {data.get('pricing_policy', 'Consultar las fuentes específicas.')}")
    for service in data["services"]:
        price = service.get("price")
        price_note = service.get("price_note") or (str(price) if price is not None else "Sin precio definido; requiere cotización.")
        sections.append(
            f"Servicio: {service['name']}\n{service['description']}\n"
            f"Precio: {price_note}"
        )
    for reference in data.get("reference_prices", []):
        sections.append(
            f"Referencia externa {reference['id']}: {reference['name']}\n"
            f"Proveedor: {reference['provider']}; NO es una oferta de {data['business']}.\n"
            f"Cantidad: {reference['quantity']} {reference.get('unit', 'piezas')}. "
            f"Precio publicado: {'desde ' if reference.get('price_kind') == 'desde' else ''}${reference['price_mxn']:,.2f} MXN "
            f"{reference['tax']}. {reference['specifications']}\n"
            f"Fuente: {reference['url']}\nConsultada: {reference['verified_on']}. "
            "Vencimiento no publicado; confirmar disponibilidad con el proveedor."
        )
    sections.extend("Regla del negocio: " + rule for rule in data["rules"])
    text = "\n\n".join(sections)
    return Document("catalogo", "Catálogo y reglas del negocio", tuple((None, s) for s in sections), digest(text))


def collect_documents(folder: Path, catalog_path: Path):
    documents = [catalog_document(catalog_path)]
    if folder.exists():
        if folder.is_symlink():
            raise ValueError("La carpeta de conocimiento no puede ser un enlace simbólico.")
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.suffix.lower() not in EXTENSIONS or not path.is_file():
                continue
            if any((folder / part).is_symlink() for part in [relative, *relative.parents]):
                raise ValueError("No se indexan enlaces simbólicos.")
            documents.append(read_document(path, relative.as_posix()))
            if len(documents) > 200:
                raise ValueError("El límite local es de 200 documentos. Reduce la carpeta de conocimiento.")
    return documents


def chunk_document(document: Document, offsets, *, size=300, overlap=45):
    chunks = []
    for page, text in document.pages:
        # Respeta los párrafos cuando caben; las ventanas largas usan tokens reales del modelo.
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        blocks, pending = [], ""
        for paragraph in paragraphs:
            candidate = pending + "\n\n" + paragraph if pending else paragraph
            if pending and len(offsets(candidate)) > size:
                blocks.append(pending)
                pending = paragraph
            else:
                pending = candidate
        if pending:
            blocks.append(pending)
        for block in blocks:
            tokens = offsets(block)
            for start in range(0, len(tokens), size - overlap):
                end = min(start + size, len(tokens))
                content = block[tokens[start][0]:tokens[end - 1][1]].strip()
                if content:
                    chunk_id = digest(f"{document.source}:{document.version}:{page}:{len(chunks)}:{content}")[:32]
                    chunks.append({"id": chunk_id, "source": document.source, "title": document.title,
                                   "page": page, "text": content, "version": document.version})
                if end == len(tokens):
                    break
    return chunks

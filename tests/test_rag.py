from io import BytesIO
import asyncio
import json
from pathlib import Path
import re

import numpy as np
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from aiec.config import ROOT
from aiec.documents import Document, chunk_document, read_document
from aiec.rag import KnowledgeBase, SourceCitation
from aiec.mcp_server import create_mcp


class FakeEmbedder:
    """Dobles locales para probar integridad, no para medir calidad semántica."""
    def __init__(self):
        self.encoded = 0
        self.fail = False

    def prepare(self, **kwargs):
        pass

    def offsets(self, text):
        return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]

    def passages(self, texts):
        if self.fail:
            raise ValueError("Fallo de modelo simulado")
        self.encoded += len(texts)
        matrix = np.zeros((len(texts), 384), dtype="<f4")
        matrix[:, 0] = 1
        return matrix

    def query(self, text):
        return self.passages([text])[0]


class FakeReranker:
    def prepare(self, **kwargs):
        pass

    def score(self, query, texts):
        return np.array([5. if "ZENTRA42" in text and "ajena" not in query else -20. for text in texts])


@pytest.fixture
def knowledge(tmp_path):
    folder = tmp_path / "documents"
    folder.mkdir()
    (folder / "condiciones.md").write_text("# Condiciones ZENTRA42\n\nEl anticipo ZENTRA42 es del 40% del total cotizado.")
    kb = KnowledgeBase(folder, tmp_path / "store", embedder=FakeEmbedder(), reranker=FakeReranker())
    kb.index()
    return kb


def context(knowledge, ticket):
    return knowledge.search("anticipo ZENTRA42", ticket_id=ticket["_id"], revision=ticket["revision"])


def citation(result):
    hit = result["results"][0]
    return SourceCitation(chunk_id=hit["id"], quote="El anticipo ZENTRA42 es del 40% del total cotizado.")


def test_incremental_index_survives_restart_and_removes_deleted_sources(knowledge):
    count = knowledge.embedder.encoded
    result = knowledge.index()
    assert result["embedded"] == 0 and knowledge.embedder.encoded == count
    other = KnowledgeBase(knowledge.folder, knowledge.storage, embedder=FakeEmbedder(), reranker=FakeReranker())
    assert other.search("anticipo")["results"][0]["source"] == "condiciones.md"
    (knowledge.folder / "condiciones.md").unlink()
    assert knowledge.status()["stale"]
    with pytest.raises(ValueError, match="Actualiza"):
        knowledge.search("anticipo")
    other.index()
    assert knowledge.search("anticipo")["results"] == []


def test_context_is_bound_to_one_client_and_revision(knowledge, ticket):
    result = context(knowledge, ticket)
    refs = [citation(result)]
    verified = knowledge.validate_citations(result["context_id"], refs, ticket_id=ticket["_id"], revision=1)
    assert verified["citations"][0]["source"] == "condiciones.md"
    for identifier, revision in [("other-client", 1), (ticket["_id"], 2)]:
        with pytest.raises(ValueError, match="revisión actual"):
            knowledge.validate_citations(result["context_id"], refs, ticket_id=identifier, revision=revision)
    general = knowledge.search("anticipo")
    with pytest.raises(ValueError):
        knowledge.validate_citations(general["context_id"], refs, ticket_id=ticket["_id"], revision=1)


def test_sources_must_be_retrieved_literal_and_nonempty(knowledge, ticket):
    result = context(knowledge, ticket)
    for refs in [[], [SourceCitation(chunk_id="a" * 32, quote="Una fuente que no existe")],
                 [SourceCitation(chunk_id=result["results"][0]["id"], quote="El anticipo es del 99%")],
                 [SourceCitation(chunk_id=result["results"][0]["id"], quote=" " * 9)]]:
        with pytest.raises(ValueError):
            knowledge.validate_citations(result["context_id"], refs, ticket_id=ticket["_id"], revision=1)


def test_modified_knowledge_invalidates_context_and_blocks_old_approval(knowledge, service, ticket, analysis, telegram):
    service.knowledge = knowledge
    result = context(knowledge, ticket)
    draft = service.save_draft(ticket["_id"], 1, analysis, "Propuesta revisada.",
                               rag_context_id=result["context_id"], sources=[citation(result)])
    assert draft["analysis"]["knowledge"]["citations"][0]["quote"].startswith("El anticipo")
    (knowledge.folder / "condiciones.md").write_text("ZENTRA42: el anticipo cambió al 50%.")
    with pytest.raises(ValueError, match="cambió"):
        service.send_response(ticket["_id"], draft["draft"]["id"], 1, draft["draft"]["text"])
    assert telegram.calls == []
    with pytest.raises(ValueError):
        knowledge.validate_citations(result["context_id"], [citation(result)], ticket_id=ticket["_id"], revision=1)
    knowledge.index()
    with pytest.raises(ValueError):
        knowledge.validate_citations(result["context_id"], [citation(result)], ticket_id=ticket["_id"], revision=1)


def test_validated_sources_can_be_reviewed_and_sent(knowledge, service, ticket, analysis, telegram):
    service.knowledge = knowledge
    result = context(knowledge, ticket)
    draft = service.save_draft(ticket["_id"], 1, analysis, "Propuesta revisada por una persona.",
                               rag_context_id=result["context_id"], sources=[citation(result)])
    service.send_response(ticket["_id"], draft["draft"]["id"], 1, draft["draft"]["text"])
    assert len(telegram.calls) == 1
    assert "ZENTRA42" not in telegram.calls[0][1]  # Las referencias internas no se añaden al mensaje.


def test_no_evidence_requires_explicit_internal_note(knowledge, service, repo, ticket, analysis):
    service.knowledge = knowledge
    repo.receive(ticket["chat_id"], 2, "Lucía", "Consulta ajena al negocio")
    current = repo.get(ticket["_id"])
    result = service.knowledge_context(current)
    assert result["status"] == "sin_evidencia"
    analysis.missing_data = []
    with pytest.raises(ValueError, match="missing_data"):
        service.save_draft(ticket["_id"], 2, analysis, "Propuesta sin datos inventados.", rag_context_id=result["context_id"])
    analysis.missing_data = ["No hay información documental suficiente sobre este tema."]
    saved = service.save_draft(ticket["_id"], 2, analysis, "Propuesta sin datos inventados.", rag_context_id=result["context_id"])
    assert saved["analysis"]["knowledge"]["status"] == "sin_evidencia"


def test_embedding_failure_keeps_previous_index_atomic(knowledge):
    previous = knowledge.status()["generation"]
    (knowledge.folder / "condiciones.md").write_text("Nueva versión ZENTRA42")
    knowledge.embedder.fail = True
    with pytest.raises(ValueError):
        knowledge.index()
    assert knowledge.status()["generation"] == previous
    with knowledge.connect() as db:
        assert db.execute("SELECT count(*) FROM search").fetchone()[0] > 0


def test_queries_do_not_execute_fts_syntax_and_do_not_index_private_files(knowledge):
    (knowledge.folder / ".privado.txt").write_text("SECRETO_NO_INDEXAR ZENTRA42")
    (knowledge.folder.parent / "conversaciones.txt").write_text("OTRO_CLIENTE ZENTRA42")
    knowledge.index()
    result = knowledge.search('" OR *); DROP TABLE chunks; -- ZENTRA42')
    assert result["results"]
    assert all("SECRETO_NO_INDEXAR" not in r["text"] and "OTRO_CLIENTE" not in r["text"] for r in result["results"])


def test_symlinks_are_not_ingested(knowledge, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("No debe indexarse")
    (knowledge.folder / "link.txt").symlink_to(secret)
    with pytest.raises(ValueError, match="simbólicos"):
        knowledge.index()


def test_long_chunks_preserve_tail_and_overlap_without_model_truncation():
    text = " ".join(f"palabra{i}" for i in range(780))
    doc = Document("long.md", "Largo", ((None, text),), "version")
    chunks = chunk_document(doc, FakeEmbedder().offsets)
    assert len(chunks) == 3
    assert all(len(c["text"].split()) <= 300 for c in chunks)
    assert chunks[0]["text"].split()[-45:] == chunks[1]["text"].split()[:45]
    assert chunks[-1]["text"].endswith("palabra779")


def test_markdown_sections_keep_unrelated_business_rules_in_separate_chunks():
    text = '# Preparar un pedido\n\nVerificar medidas y material.\n\n## Presupuesto\n\nEl presupuesto de otro pedido no se traslada.\n\n## Entrega\n\nAceptar un mensaje no confirma producción.'
    doc = Document('solicitud.md', 'Solicitud', ((None, text),), 'version')
    chunks = chunk_document(doc, FakeEmbedder().offsets)
    assert len(chunks) == 3
    budget = next(c for c in chunks if 'no se traslada' in c['text'])
    assert budget['text'].startswith('## Presupuesto')
    assert 'Verificar medidas' not in budget['text'] and 'producción' not in budget['text']


def test_pdf_preserves_page_and_rejects_scans(tmp_path):
    writer = PdfWriter()
    for text in ["Primera pagina", "Segunda pagina"]:
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 20 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    path = tmp_path / "test.pdf"
    writer.write(path)
    document = read_document(path, "test.pdf")
    assert [p for p, _ in document.pages] == [1, 2]
    assert "Segunda" in document.pages[1][1]
    blank = PdfWriter()
    blank.add_blank_page(width=600, height=800)
    blank.write(path)
    with pytest.raises(ValueError, match="texto extraíble"):
        read_document(path, "test.pdf")


def csrf(client):
    client.get("/conocimiento")
    with client.session_transaction() as session:
        return session["csrf"]


def test_document_upload_validation_replacement_and_search(app, service, knowledge):
    service.knowledge = knowledge
    with app.test_client() as client:
        assert client.get("/conocimiento").status_code == 200
        assert client.post("/conocimiento/importar", data={}).status_code == 400
        token = csrf(client)
        assert client.post("/conocimiento/importar", data={"csrf": token, "document": (BytesIO(b"ZENTRA42 nueva politica"), "reglas.txt")}).status_code == 302
        assert (knowledge.folder / "reglas.txt").read_text().startswith("ZENTRA42")
        client.post("/conocimiento/importar", data={"csrf": token, "document": (BytesIO(b"NO REEMPLAZAR"), "reglas.txt")})
        assert "NO REEMPLAZAR" not in (knowledge.folder / "reglas.txt").read_text()
        client.post("/conocimiento/importar", data={"csrf": token, "replace": "si", "document": (BytesIO(b"ZENTRA42 reemplazada"), "reglas.txt")})
        assert "reemplazada" in (knowledge.folder / "reglas.txt").read_text()
        assert list((knowledge.storage / "previous").iterdir())
        client.post("/conocimiento/importar", data={"csrf": token, "document": (BytesIO(b"bad"), "script.py")})
        assert not (knowledge.folder / "script.py").exists()
        response = client.get("/conocimiento?consulta=ZENTRA42")
        assert response.status_code == 200 and b"reemplazada" in response.data


def test_mcp_sends_directly_with_bound_context_and_verified_citations(knowledge, service, ticket, analysis):
    service.knowledge = knowledge
    server = create_mcp(service)

    async def exercise():
        raw = await server.call_tool("obtener_solicitud", {"id_solicitud": ticket["_id"], "consulta_conocimiento": "ZENTRA42 anticipo"})
        # FastMCP devuelve contenido y estructura para los resultados tipados.
        structured = raw[1] if isinstance(raw, tuple) else raw
        if isinstance(structured, list):
            structured = json.loads(structured[0].text)
        retrieved = structured["knowledge_context"]
        assert retrieved["results"][0]["source"] == "condiciones.md"
        result = await server.call_tool("responder_cliente", {
            "id_solicitud": ticket["_id"], "revision": 1, "analisis": analysis.model_dump(),
            "respuesta": "Una propuesta para tu negocio.", "rag_context_id": retrieved["context_id"],
            "fuentes": [citation(retrieved).model_dump()],
        })
        assert result
        current = service.repo.get(ticket["_id"])
        assert current["status"] == "respondida"
        assert current["analysis"]["knowledge"]["citations"][0]["source"] == "condiciones.md"

    asyncio.run(exercise())


def test_oversized_upload_is_rejected_without_changing_index(app, service, knowledge):
    service.knowledge = knowledge
    generation = knowledge.status()["generation"]
    with app.test_client() as client:
        token = csrf(client)
        result = client.post("/conocimiento/importar", data={
            "csrf": token, "document": (BytesIO(b"a" * (11 * 1024 * 1024)), "large.txt"),
        })
        assert result.status_code == 413
        assert not (knowledge.folder / "large.txt").exists()
    assert knowledge.status()["generation"] == generation


def test_retrieval_configuration_changes_invalidate_previous_contexts_without_reembedding(knowledge, ticket):
    result=context(knowledge,ticket)
    old_generation=knowledge.status()['generation']
    changed=KnowledgeBase(knowledge.folder,knowledge.storage,embedder=FakeEmbedder(),reranker=FakeReranker(),min_similarity=.84)
    assert not changed.status()['ready']
    status=changed.index()
    assert status['embedded']==0 and status['generation'] != old_generation
    with pytest.raises(ValueError):
        changed.validate_citations(result['context_id'],[citation(result)],ticket_id=ticket['_id'],revision=1)

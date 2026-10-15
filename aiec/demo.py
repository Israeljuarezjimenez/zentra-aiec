"""Conversaciones de imprenta en memoria; ningún mensaje real sale de la demo."""
from .repository import Repository
from .service import Analysis, Service


def demo_service():
    try:
        import mongomock
    except ImportError:
        raise ValueError("Para instalar la demo y las pruebas, ejecuta uv sync.") from None
    repo = Repository(mongomock.MongoClient(tz_aware=True).imprenta_demo)
    service = Service(repo, demo=True)
    repo.receive(1001, 1, "Lucía · cafetería", "Necesito 1,000 volantes para mi cafetería, a color por los dos lados.")
    ticket_id, _ = repo.receive(1002, 2, "Mateo · restaurante", "Necesito 1,000 tarjetas de presentación. ¿Qué opciones hay?")
    repo.save_draft(ticket_id, 1, Analysis(
        service="offset", summary="Busca opciones para 1,000 tarjetas de presentación.",
        priority="media", priority_reason="No indica una fecha urgente.",
        evidence=["1,000 tarjetas de presentación"],
        missing_data=["Tarifa propia de San Martín no publicada."],
    ).model_dump(), "Podemos orientar tu proyecto hacia tarjetas convencionales o tarjetas con acabado mate. La tarifa propia de Impresos San Martín todavía no está cargada; no hay una cotización confirmada para este pedido.")
    ticket = repo.get(ticket_id)
    repo.begin_send(ticket_id, ticket["draft"]["id"], 1, ticket["draft"]["text"])
    repo.finish_send(ticket_id, repo.get(ticket_id)["attempt"]["id"], 900)
    repo.receive(1003, 3, "Ana · panadería", "Quiero una lona para el frente de mi panadería.")
    return service

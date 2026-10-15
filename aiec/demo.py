"""Ejemplos aislados en memoria. No simulan una conexión real con Claude."""
from .repository import Repository
from .service import Analysis, Service


def demo_service():
    try:
        import mongomock
    except ImportError:
        raise ValueError("Para instalar la demo y las pruebas, ejecuta uv sync.") from None
    repo = Repository(mongomock.MongoClient(tz_aware=True).aiec_demo)
    service = Service(repo, demo=True)
    repo.receive(1001, 1, "Lucía · tienda de ropa", "Tengo una tienda de ropa en Puebla. Quiero publicidad en Instagram y tengo $15,000 mensuales.")
    ticket_id, _ = repo.receive(1002, 2, "Mateo · restaurante", "Necesito una página web para mi restaurante esta semana. ¿Podrían ayudarme?")
    service.save_draft(ticket_id, 1, Analysis(
        service="diseno_web", summary="Solicita una página web para un restaurante esta semana.",
        priority="alta", priority_reason="Indica que necesita la página esta semana.",
        evidence=["esta semana"], missing_data=["presupuesto", "alcance de la página"],
    ), "¡Hola, Mateo! Te propongo una página sencilla con el menú, la ubicación y un botón de contacto. Como segunda opción, podemos añadir una sección de reservaciones. Recomiendo comenzar con la primera versión y ampliarla después. Tu fecha deseada es esta semana; el alcance y la fecha de entrega se definirían en la propuesta.")
    repo.receive(1003, 3, "Ana · estudio de yoga", "Quiero mejorar las publicaciones de mi estudio de yoga. ¿Qué información necesitan?")
    return service

import mongomock
import pytest

from aiec.repository import Repository
from aiec.service import Analysis, Service
from aiec.web import create_app


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.error = None
        self.before_result = None

    def send_message(self, chat_id, text):
        self.calls.append((chat_id, text))
        if self.before_result:
            self.before_result()
        if self.error:
            raise self.error
        return {"message_id": 900}


@pytest.fixture
def repo():
    return Repository(mongomock.MongoClient(tz_aware=True).tests)


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def service(repo, telegram):
    return Service(repo, telegram)


@pytest.fixture
def analysis():
    return Analysis(service="publicidad_redes", summary="Pide publicidad y declara un presupuesto.",
                    priority="media", priority_reason="No indicó una fecha de inicio.",
                    budget="$15,000 mensuales", evidence=["Quiero publicidad"],
                    missing_data=["fecha de inicio"])


@pytest.fixture
def ticket(repo):
    ticket_id, _ = repo.receive(8000000000001, 1, "Lucía", "Quiero publicidad. Tengo $15,000 mensuales.")
    return repo.get(ticket_id)


@pytest.fixture
def drafted(service, ticket, analysis):
    return service.save_draft(ticket["_id"], ticket["revision"], analysis, "¿Cuándo te gustaría comenzar?")


@pytest.fixture
def app(service):
    app = create_app(service, "test-" * 10)
    app.config["TESTING"] = True
    return app

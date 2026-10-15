import socket

import pytest
from werkzeug.serving import make_server

from aiec.config import ConfigurationError, Settings
from aiec.server import serve
from aiec.telegram import TelegramRejected


def test_occupied_port_does_not_start_telegram(service, monkeypatch):
    def must_not_connect(_telegram):
        pytest.fail("No debe consultar Telegram si no pudo reservar el puerto del panel.")
    monkeypatch.setattr("aiec.server.check_bot", must_not_connect)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        settings = Settings(port=occupied.getsockname()[1], secret_key="test-" * 10)
        with pytest.raises(ConfigurationError, match="receptor de Telegram no se inició"):
            serve(service, settings)


def test_failed_bot_verification_releases_panel_port(service, monkeypatch):
    servers = []
    def reserve_server(host, port, app, **kwargs):
        server = make_server(host, 0, app, **kwargs)
        servers.append(server)
        return server
    def reject_token(_telegram):
        raise TelegramRejected(401)
    monkeypatch.setattr("aiec.server.make_server", reserve_server)
    monkeypatch.setattr("aiec.server.check_bot", reject_token)
    with pytest.raises(TelegramRejected):
        serve(service, Settings(secret_key="test-" * 10))
    with socket.socket() as available:
        available.bind(("127.0.0.1", servers[0].server_port))

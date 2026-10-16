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
        with pytest.raises(ConfigurationError, match="No se iniciaron receptores de mensajes"):
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


def test_disabled_claude_starts_only_receiver_and_never_automation(service, repo, monkeypatch):
    from types import SimpleNamespace
    launched = []
    monkeypatch.setattr('aiec.server.make_server', lambda *_args, **_kwargs: SimpleNamespace(
        serve_forever=lambda: None, server_close=lambda: None))
    monkeypatch.setattr('aiec.server.check_bot', lambda _telegram: {'username':'TestBot'})
    class Thread:
        def __init__(self, *, name, **_kwargs):
            self.name = name
        def start(self):
            launched.append(self.name)
    monkeypatch.setattr('aiec.server.threading.Thread', Thread)
    serve(service, Settings(secret_key='test-' * 10))
    assert launched == ['telegram-poller']
    assert repo.automation_state()['status'] == 'desactivado'


def test_whatsapp_only_starts_isolated_receiver_and_inbox_without_telegram(service,monkeypatch):
    from types import SimpleNamespace
    created,launched,closed=[],[],[]
    def server(host,port,app,**kwargs):
        created.append((host,port,app,kwargs))
        return SimpleNamespace(serve_forever=lambda:None,server_close=lambda:closed.append(port))
    monkeypatch.setattr('aiec.server.make_server',server)
    def unexpected(_telegram):pytest.fail('WhatsApp no debe consultar Telegram.')
    monkeypatch.setattr('aiec.server.check_bot',unexpected)
    class Thread:
        def __init__(self,*,name,**kwargs):self.name=name
        def start(self):launched.append(self.name)
    monkeypatch.setattr('aiec.server.threading.Thread',Thread)
    service.telegram=None
    settings=Settings(telegram_enabled=False,whatsapp_enabled=True,
        whatsapp_access_token='unit-test-token',whatsapp_phone_number_id='111222333444',
        whatsapp_business_account_id='555666777888',whatsapp_app_secret='unit-test-app-secret',
        whatsapp_verify_token='unit-test-verification-token',secret_key='unit-test-'*8)
    serve(service,settings)
    assert [(host,port) for host,port,*_ in created]==[('127.0.0.1',5050),('127.0.0.1',5051)]
    assert set(launched)=={'whatsapp-webhook','whatsapp-inbox'}
    assert set(closed)=={5050,5051}
    assert 'request_handler' in created[1][3]


def test_missing_whatsapp_secret_releases_panel_port_without_starting_receivers(service,monkeypatch):
    from types import SimpleNamespace
    closed=[]
    monkeypatch.setattr('aiec.server.make_server',lambda *_args,**_kwargs:SimpleNamespace(server_close=lambda:closed.append(True)))
    settings=Settings(whatsapp_enabled=True,whatsapp_access_token='unit-test-token',
        whatsapp_phone_number_id='111222333444',whatsapp_business_account_id='555666777888')
    with pytest.raises(ConfigurationError,match='WHATSAPP_APP_SECRET'):
        serve(service,settings)
    assert closed==[True]

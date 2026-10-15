def post_payload(client, drafted):
    client.get(f'/solicitudes/{drafted["_id"]}')
    with client.session_transaction() as session:
        csrf = session["csrf"]
    return {"csrf": csrf, "revision": drafted["revision"], "draft_id": drafted["draft"]["id"],
            "text": "Respuesta revisada por una persona", "confirm": "si"}


def test_all_pages_and_filters_render(app, drafted):
    with app.test_client() as client:
        for path in ["/", "/guia", "/health", "/?estado=revision", f'/solicitudes/{drafted["_id"]}']:
            assert client.get(path).status_code == 200
        assert client.get("/?estado=inventado").status_code == 400
        assert client.get("/solicitudes/no-existe").status_code == 404


def test_csrf_and_explicit_confirmation_are_required(app, telegram, drafted):
    with app.test_client() as client:
        endpoint = f'/solicitudes/{drafted["_id"]}/enviar'
        assert client.post(endpoint, data={"text": "Hola"}).status_code == 400
        payload = post_payload(client, drafted)
        payload.pop("confirm")
        assert client.post(endpoint, data=payload).status_code == 400
    assert telegram.calls == []


def test_human_can_edit_and_send_once(app, repo, telegram, drafted):
    with app.test_client() as client:
        payload = post_payload(client, drafted)
        endpoint = f'/solicitudes/{drafted["_id"]}/enviar'
        assert client.post(endpoint, data=payload).status_code == 302
        assert client.post(endpoint, data=payload).status_code == 302
    assert telegram.calls == [(drafted["chat_id"], payload["text"])]
    assert repo.get(drafted["_id"])["status"] == "respondida"


def test_customer_html_is_escaped(app, repo):
    ticket_id, _ = repo.receive(123, 1, '<img src=x onerror=alert(1)>', '<script>alert("x")</script>')
    with app.test_client() as client:
        response = client.get(f"/solicitudes/{ticket_id}")
        assert b'<script>alert("x")</script>' not in response.data
        assert b'&lt;script&gt;' in response.data
        assert b'<img src=x' not in response.data


def test_panel_refuses_external_clients_and_unknown_hosts(app):
    with app.test_client() as client:
        assert client.get("/", environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code == 403
        assert client.get("/", headers={"Host": "example.org"}).status_code == 400


def test_close_form_rejects_stale_revision(app, repo, drafted):
    with app.test_client() as client:
        payload = post_payload(client, drafted)
        repo.receive(drafted["chat_id"], 2, "Lucía", "Un nuevo mensaje")
        client.post(f'/solicitudes/{drafted["_id"]}/cerrar', data=payload)
        assert repo.get(drafted["_id"])["status"] == "pendiente"

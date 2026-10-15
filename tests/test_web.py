def post_payload(client, drafted):
    client.get(f'/solicitudes/{drafted["_id"]}')
    with client.session_transaction() as session:
        csrf = session["csrf"]
    return {"csrf": csrf, "revision": drafted["revision"]}


def test_all_pages_and_filters_render(app, drafted):
    with app.test_client() as client:
        for path in ["/", "/guia", "/health", "/?estado=revision", f'/solicitudes/{drafted["_id"]}']:
            assert client.get(path).status_code == 200
        assert client.get("/?estado=inventado").status_code == 400
        assert client.get("/solicitudes/no-existe").status_code == 404


def test_panel_has_no_approval_flow_or_manual_send_endpoint(app, telegram, drafted):
    with app.test_client() as client:
        for path in ['/', '/guia', f'/solicitudes/{drafted["_id"]}']:
            html = client.get(path).get_data(as_text=True)
            for obsolete in ['Aprobar y enviar', 'Preparar en Claude', 'Copiar instrucción', 'Revisión humana', 'name="text"']:
                assert obsolete not in html
        payload = post_payload(client, drafted)
        assert client.post(f'/solicitudes/{drafted["_id"]}/enviar', data=payload).status_code == 404
    assert telegram.calls == []


def test_csrf_is_required_for_remaining_mutations(app, drafted):
    with app.test_client() as client:
        assert client.post(f'/solicitudes/{drafted["_id"]}/cerrar', data={'revision':1}).status_code == 400


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


def test_catalog_displays_sources_and_exact_external_configuration(app):
    with app.test_client() as client:
        response=client.get('/catalogo')
        assert response.status_code == 200
        html=response.get_data(as_text=True)
        assert 'Impresshop' in html and 'Gráficos Impresos' in html
        assert 'no son tarifas propias' in html
        assert '$999.00' in html and 'MXN más IVA' in html
        assert 'https://impresshop.mx/promociones/' in html


def test_live_panel_state_contains_no_customer_content(app, repo):
    repo.receive(123,1,'PRIVATE_CUSTOMER_NAME','PRIVATE_CUSTOMER_MESSAGE')
    with app.test_client() as client:
        data=client.get('/api/panel')
        assert data.status_code == 200
        assert 'PRIVATE_CUSTOMER' not in data.get_data(as_text=True)
        assert 'chat_id' not in data.get_data(as_text=True)
        first=data.json['version']
        repo.receive(123,2,'PRIVATE_CUSTOMER_NAME','Un nuevo mensaje')
        assert client.get('/api/panel').json['version'] != first

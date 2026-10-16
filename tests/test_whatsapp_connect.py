from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from filelock import FileLock

import httpx
import pytest

from aiec.config import ROOT, Settings
from aiec.whatsapp import WhatsAppRejected
from aiec.whatsapp_connect import connect, restart_application, verify_callback
from aiec.whatsapp_tunnel import ensure_tunnel, process_matches, read_state

CALLBACK='https://unit-test-aiec.trycloudflare.com/webhook/whatsapp'


@pytest.fixture
def settings():
    return Settings(mongodb_uri='mongodb://unit-test', secret_key='unit-test-'*8,
        telegram_enabled=False, whatsapp_enabled=True, whatsapp_access_token='unit-test-access',
        whatsapp_app_secret='unit-test-app-secret', whatsapp_verify_token='unit-test-verify-token',
        whatsapp_phone_number_id='111111111111', whatsapp_business_account_id='222222222222')


def meta_client(settings, *, bad_secret=False, wrong_final_app=False):
    calls=[];subscriptions_gets=0
    def handle(request):
        nonlocal subscriptions_gets
        calls.append(request)
        path=request.url.path
        if path.endswith('/app'):
            payload={'id':'333333333333'}
        elif path.endswith('/subscriptions'):
            if bad_secret:
                return httpx.Response(401,json={'error':{'code':190,'message':'PRIVATE_META_DETAILS'}})
            if request.method=='POST':payload={'success':True}
            else:
                subscriptions_gets+=1
                payload={'data':[{'object':'whatsapp_business_account','active':True,
                    'callback_url':CALLBACK if subscriptions_gets>1 else 'https://old.example/webhook',
                    'fields':[{'name':'messages'},{'name':'message_template_status_update'}]}]}
        elif path.endswith('/subscribed_apps'):
            payload={'success':True} if request.method=='POST' else {
                'data':[{'whatsapp_business_api_data':{'id':'999999999999' if wrong_final_app else '333333333333'}}]}
        elif path.endswith('/phone_numbers'):
            payload={'data':[{'id':settings.whatsapp_phone_number_id}]}
        elif path.endswith('/'+settings.whatsapp_phone_number_id):
            payload={'id':settings.whatsapp_phone_number_id,'verified_name':'Test Number'}
        else:pytest.fail('No se permite enviar mensajes ni acceder a rutas ajenas al registro.')
        return httpx.Response(200,json=payload)
    return httpx.Client(transport=httpx.MockTransport(handle)),calls


def test_registers_both_subscriptions_preserves_existing_fields_and_sends_no_messages(settings,tmp_path):
    client,calls=meta_client(settings);restarted=[];verified=[]
    result=connect(settings,tmp_path,client=client,tunnel=lambda *_args:{'callback_url':CALLBACK},
        restart=lambda *_args:restarted.append(True),verify=lambda *_args:verified.append(True))
    assert restarted==verified==[True]
    assert result['webhook_registered'] and result['application_subscribed']
    assert result['customer_messages_sent']==0
    app_post=next(r for r in calls if r.method=='POST' and r.url.path.endswith('/subscriptions'))
    data=parse_qs(app_post.content.decode())
    assert data['fields']==['message_template_status_update,messages']
    assert data['callback_url']==[CALLBACK]
    assert app_post.headers['authorization']=='Bearer 333333333333|unit-test-app-secret'
    waba_post=next(r for r in calls if r.method=='POST' and r.url.path.endswith('/subscribed_apps'))
    assert waba_post.headers['authorization']=='Bearer unit-test-access'
    assert all('unit-test-' not in str(r.url) for r in calls)
    assert all(not r.url.path.endswith('/messages') for r in calls)
    backup=next(tmp_path.glob('subscriptions-before-*.json'))
    assert backup.stat().st_mode & 0o777==0o600
    assert json.loads(backup.read_text())['data'][0]['callback_url']=='https://old.example/webhook'
    assert json.loads((tmp_path/'connection.json').read_text())['webhook_registered']


def test_invalid_app_secret_does_not_restart_a_working_application_or_change_meta(settings,tmp_path):
    client,calls=meta_client(settings,bad_secret=True)
    def unexpected(*_args):pytest.fail('No detener AIEC ni abrir túneles con un secreto rechazado.')
    with pytest.raises(WhatsAppRejected):
        connect(settings,tmp_path,client=client,tunnel=unexpected,restart=unexpected,verify=unexpected)
    assert all(r.method=='GET' for r in calls)
    assert not (tmp_path/'connection.json').exists()


def test_two_connectors_cannot_restart_or_register_the_same_application_at_once(settings,tmp_path):
    client,calls=meta_client(settings)
    with FileLock(str(tmp_path/'connect.lock')):
        with pytest.raises(ValueError,match='ya está en proceso'):
            connect(settings,tmp_path,client=client)
    assert calls==[]


def test_failed_public_verification_never_registers_a_broken_webhook(settings,tmp_path):
    client,calls=meta_client(settings)
    def failed(*_args):raise ValueError('Receptor inaccesible.')
    with pytest.raises(ValueError,match='inaccesible'):
        connect(settings,tmp_path,client=client,tunnel=lambda *_args:{'callback_url':CALLBACK},
            restart=lambda *_args:None,verify=failed)
    assert all(r.method=='GET' for r in calls)
    assert not (tmp_path/'connection.json').exists()


def test_account_mismatch_after_registration_is_not_reported_as_connected(settings,tmp_path):
    client,_=meta_client(settings,wrong_final_app=True)
    with pytest.raises(ValueError,match='ambas suscripciones'):
        connect(settings,tmp_path,client=client,tunnel=lambda *_args:{'callback_url':CALLBACK},
            restart=lambda *_args:None,verify=lambda *_args:None)
    assert not (tmp_path/'connection.json').exists()


def test_callback_verification_expects_exact_challenge_without_redirects(settings):
    def handle(request):
        assert request.url.params['hub.verify_token']==settings.whatsapp_verify_token
        return httpx.Response(200,text=request.url.params['hub.challenge'])
    with httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=False) as client:
        verify_callback(client,CALLBACK,settings.whatsapp_verify_token)


def test_restart_refuses_to_stop_an_unrelated_application(settings,tmp_path,monkeypatch):
    monkeypatch.setattr('aiec.whatsapp_connect.subprocess.run',lambda *_a,**_k:SimpleNamespace(stdout='987654\n'))
    monkeypatch.setattr('aiec.whatsapp_connect.subprocess.check_output',lambda args,**_k:
        'Python unrelated.py' if args[0]=='ps' else 'p987654\nn'+str(ROOT)+'\n')
    def unexpected(*_a,**_k):pytest.fail('No se permite detener o iniciar procesos ajenos.')
    monkeypatch.setattr('aiec.whatsapp_connect.os.kill',unexpected)
    monkeypatch.setattr('aiec.whatsapp_connect.subprocess.Popen',unexpected)
    with pytest.raises(ValueError,match='Otro programa'):
        restart_application(settings,tmp_path)


def test_tunnel_can_never_publish_the_private_panel(tmp_path):
    with pytest.raises(ValueError,match='nunca el panel'):
        ensure_tunnel(tmp_path,5050,5050)


@pytest.mark.parametrize('content',['[]','null','{"url":"https://foreign.example"}'])
def test_invalid_tunnel_state_is_ignored(tmp_path,content):
    (tmp_path/'tunnel.json').write_text(content)
    assert read_state(tmp_path)=={}


def test_tunnel_does_not_trust_a_reused_process_identifier(monkeypatch):
    monkeypatch.setattr('aiec.whatsapp_tunnel.subprocess.run',lambda *_a,**_k:
        SimpleNamespace(returncode=0,stdout='some-other-program'))
    assert not process_matches({'pid':987654,'port':5051,'command':'cloudflared expected'},5051)


def test_tunnel_creates_private_metadata_and_exposes_only_receiver_port(tmp_path,monkeypatch):
    observed=[]
    monkeypatch.setattr('aiec.whatsapp_tunnel.shutil.which',lambda _name:'/opt/test/cloudflared')
    class Process:
        pid=987654
        def __init__(self,command,**kwargs):
            observed.append(command)
            assert kwargs['start_new_session'] is True
            kwargs['stdout'].write(b'INF Your Tunnel: https://unit-test-aiec.trycloudflare.com\n')
            kwargs['stdout'].flush()
        def poll(self):return None
    monkeypatch.setattr('aiec.whatsapp_tunnel.subprocess.Popen',Process)
    result=ensure_tunnel(tmp_path,5051,5050)
    assert result['callback_url']==CALLBACK
    assert 'http://127.0.0.1:5051' in observed[0] and '5050' not in ' '.join(observed[0])
    assert (tmp_path/'tunnel.json').stat().st_mode & 0o777==0o600
    assert (tmp_path/'tunnel.log').stat().st_mode & 0o777==0o600


def test_running_tunnel_is_reused_without_changing_the_registered_address(tmp_path,monkeypatch):
    (tmp_path/'tunnel.json').write_text(json.dumps({'url':CALLBACK.removesuffix('/webhook/whatsapp'),
        'callback_url':CALLBACK,'pid':987654,'port':5051,'command':'expected'}))
    monkeypatch.setattr('aiec.whatsapp_tunnel.shutil.which',lambda _name:'/opt/test/cloudflared')
    monkeypatch.setattr('aiec.whatsapp_tunnel.process_matches',lambda *_args:True)
    def unexpected(*_args,**_kwargs):pytest.fail('Conservar la dirección ya creada para la demo.')
    monkeypatch.setattr('aiec.whatsapp_tunnel.subprocess.Popen',unexpected)
    assert ensure_tunnel(tmp_path,5051,5050)['reused']

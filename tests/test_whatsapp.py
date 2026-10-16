from dataclasses import replace
from datetime import timedelta
import hashlib
import hmac
import json

import httpx
from pymongo.errors import PyMongoError
import pytest

from aiec.config import ConfigurationError, Settings
from aiec.repository import now
from aiec.service import public_ticket
from aiec.telegram import DeliveryUncertain
from aiec.whatsapp import WhatsApp, WhatsAppRejected
from aiec.whatsapp_webhook import create_whatsapp_webhook, process_inbox
from aiec.web import create_app


PHONE = '111222333444'
ACCOUNT = '555666777888'
SENDER = '525512345678'
OUTBOUND = 'wamid.unit-test-outbound'


@pytest.fixture
def settings():
    return Settings(whatsapp_enabled=True, telegram_enabled=False,
        whatsapp_access_token='unit-test-meta-token', whatsapp_phone_number_id=PHONE,
        whatsapp_business_account_id=ACCOUNT, whatsapp_app_secret='unit-test-app-secret',
        whatsapp_verify_token='unit-test-verification-token')


def event(*, mid='wamid.unit-test-inbound', text='Quiero volantes. Tengo $15,000 para el pedido.', stamp=None):
    return {'object':'whatsapp_business_account', 'entry':[{'id':ACCOUNT, 'changes':[{
        'field':'messages', 'value':{'messaging_product':'whatsapp', 'metadata':{'phone_number_id':PHONE},
            'contacts':[{'wa_id':SENDER,'profile':{'name':'Cliente de prueba'}}],
            'messages':[{'from':SENDER,'id':mid,'timestamp':str(int((stamp or now()).timestamp())),
                         'type':'text','text':{'body':text}}]},
    }]}]}


def post(client, settings, data, *, secret=None):
    body = json.dumps(data, ensure_ascii=False).encode()
    signature = 'sha256='+hmac.new((secret or settings.whatsapp_app_secret).encode(),body,hashlib.sha256).hexdigest()
    return client.post('/webhook/whatsapp', data=body, content_type='application/json',
                       headers={'X-Hub-Signature-256':signature})


def connect(service, *, handler=None):
    calls=[]
    def send(request):
        calls.append(request)
        return handler(request) if handler else httpx.Response(200,json={'messages':[{'id':OUTBOUND}]})
    service.whatsapp=WhatsApp('unit-test-meta-token',PHONE,
        client=httpx.Client(transport=httpx.MockTransport(send),follow_redirects=False))
    return calls


def receive(service, settings, **kwargs):
    app=create_whatsapp_webhook(service,settings)
    with app.test_client() as client:
        assert post(client,settings,event(**kwargs)).status_code==200
    assert service.repo.process_whatsapp_inbox()
    return service.repo.get(service.repo.list()[0]['_id'])


def test_webhook_checks_verification_token_and_never_exposes_the_panel(service,settings):
    app=create_whatsapp_webhook(service,settings)
    with app.test_client() as client:
        assert client.get('/webhook/whatsapp',query_string={'hub.mode':'subscribe',
            'hub.verify_token':settings.whatsapp_verify_token,'hub.challenge':'12345'}).text=='12345'
        for supplied in ['wrong', 'áéíóú', '']:
            assert client.get('/webhook/whatsapp',query_string={'hub.mode':'subscribe',
                'hub.verify_token':supplied,'hub.challenge':'12345'}).status_code==403
        for path in ['/','/health','/catalogo','/negocio','/whatsapp']:
            assert client.get(path).status_code==404


def test_public_receiver_requires_a_separate_app_secret(service,settings):
    with pytest.raises(ConfigurationError,match='WHATSAPP_APP_SECRET'):
        create_whatsapp_webhook(service,replace(settings,whatsapp_app_secret=''))


def test_private_setup_displays_only_verification_token_and_refuses_remote_access(service,settings):
    service.settings=settings
    with create_app(service,'unit-test-private-panel-'*3).test_client() as client:
        result=client.get('/whatsapp')
        assert result.status_code==200
        assert settings.whatsapp_verify_token in result.text
        assert settings.whatsapp_access_token not in result.text and settings.whatsapp_app_secret not in result.text
        assert client.get('/whatsapp',environ_overrides={'REMOTE_ADDR':'203.0.113.1'}).status_code==403
        assert settings.whatsapp_verify_token not in client.get('/api/panel').text


def test_webhook_rejects_missing_forged_and_non_ascii_signatures(service,settings,repo):
    with create_whatsapp_webhook(service,settings).test_client() as client:
        assert client.post('/webhook/whatsapp',json=event()).status_code==401
        assert post(client,settings,event(),secret='other-secret').status_code==401
        assert client.post('/webhook/whatsapp',json=event(),headers={'X-Hub-Signature-256':'é'}).status_code==401
    assert repo.whatsapp_inbox.count_documents({})==0


def test_signed_duplicate_events_are_persisted_once_without_sending(service,settings,repo,telegram):
    with create_whatsapp_webhook(service,settings).test_client() as client:
        for _ in range(3):
            assert post(client,settings,event()).status_code==200
    assert repo.whatsapp_inbox.count_documents({})==1
    assert repo.process_whatsapp_inbox()
    assert not repo.process_whatsapp_inbox()
    t=repo.get(repo.list()[0]['_id'])
    assert t['revision']==1 and t['channel']=='whatsapp' and t['recipient_id']==SENDER
    assert t['messages'][0]['provider_message_id']=='wamid.unit-test-inbound'
    assert 'telegram_message_id' not in t['messages'][0]
    assert 'recipient_id' not in public_ticket(t,full=True)
    assert telegram.calls==[]


@pytest.mark.parametrize('different', ['account','number','type','future','no_text'])
def test_other_accounts_numbers_and_unsupported_events_are_ignored(service,settings,repo,different):
    data=event()
    value=data['entry'][0]['changes'][0]['value']
    if different=='account':data['entry'][0]['id']='another-account'
    if different=='number':value['metadata']['phone_number_id']='another-number'
    if different=='type':value['messages'][0]['type']='image'
    if different=='future':value['messages'][0]['timestamp']=str(int((now()+timedelta(days=1)).timestamp()))
    if different=='no_text':value['messages'][0]['text']={'body':''}
    with create_whatsapp_webhook(service,settings).test_client() as client:
        assert post(client,settings,data).status_code==200
    assert repo.whatsapp_inbox.count_documents({})==0


def test_database_failure_returns_retryable_status_without_leaking_details(service,settings,repo,monkeypatch):
    def fail(*_args,**_kwargs):raise PyMongoError('PRIVATE_DATABASE_DETAILS')
    monkeypatch.setattr(repo,'queue_whatsapp',fail)
    with create_whatsapp_webhook(service,settings).test_client() as client:
        result=post(client,settings,event())
    assert result.status_code==503
    assert 'PRIVATE_DATABASE_DETAILS' not in result.text


def test_whatsapp_and_telegram_customer_identifiers_never_collide(service,settings,repo):
    telegram_id,_=repo.receive(int(SENDER),1,'Otro cliente','Consulta por Telegram.')
    wa=receive(service,settings)
    assert wa['_id']!=telegram_id
    assert repo.tickets.count_documents({})==2


def test_duplicate_event_does_not_reappear_after_history_has_been_trimmed(service,settings,repo):
    t=receive(service,settings)
    for number in range(105):
        repo.receive(t['chat_id'],f'wamid.followup.{number}','Cliente','Consulta.',
                     channel='whatsapp',recipient_id=SENDER)
    revision=repo.get(t['_id'])['revision']
    with create_whatsapp_webhook(service,settings).test_client() as client:
        assert post(client,settings,event()).status_code==200
    assert not repo.process_whatsapp_inbox()
    assert repo.get(t['_id'])['revision']==revision


def test_inbox_retry_after_a_crash_does_not_append_the_message_twice(service,settings,repo,monkeypatch):
    with create_whatsapp_webhook(service,settings).test_client() as client:
        post(client,settings,event())
    update=repo.whatsapp_inbox.update_one
    def fail_after_receive(query,changes,*args,**kwargs):
        if changes.get('$set',{}).get('processed'):
            raise PyMongoError('Simulated crash before checkpoint')
        return update(query,changes,*args,**kwargs)
    monkeypatch.setattr(repo.whatsapp_inbox,'update_one',fail_after_receive)
    with pytest.raises(PyMongoError):repo.process_whatsapp_inbox()
    monkeypatch.setattr(repo.whatsapp_inbox,'update_one',update)
    update({}, {'$set':{'lease_until':now()-timedelta(seconds=1)}})
    assert repo.process_whatsapp_inbox()
    assert repo.get(repo.list()[0]['_id'])['revision']==1


def test_response_uses_stored_whatsapp_recipient_and_is_never_resent(service,settings,repo,telegram,analysis):
    calls=connect(service);t=receive(service,settings)
    text='Podemos orientarte con las opciones documentadas para tus volantes.'
    receipt=service.respond_to_customer(t['_id'],t['revision'],analysis,text)
    assert receipt['sent'] and receipt['channel']=='whatsapp'
    assert receipt['provider_message_id']==OUTBOUND and 'telegram_message_id' not in receipt
    assert receipt['provider_delivery_status']=='accepted'
    assert service.respond_to_customer(t['_id'],t['revision'],analysis,text)['already_sent']
    assert len(calls)==1 and telegram.calls==[]
    payload=json.loads(calls[0].content)
    assert payload=={'messaging_product':'whatsapp','recipient_type':'individual','to':SENDER,
                     'type':'text','text':{'preview_url':False,'body':text}}
    assert calls[0].headers['Authorization']=='Bearer unit-test-meta-token'
    assert 'unit-test-meta-token' not in str(calls[0].url)


def test_expired_service_window_sends_no_request_or_paid_template(service,settings,repo,analysis):
    calls=connect(service);t=receive(service,settings,stamp=now()-timedelta(hours=25))
    with pytest.raises(ValueError,match='ventana'):
        service.respond_to_customer(t['_id'],t['revision'],analysis,'Podemos orientar tu solicitud.')
    assert calls==[]
    assert repo.get(t['_id'])['delivery_state']=='rechazado'


@pytest.mark.parametrize('status,expected', [(400,WhatsAppRejected),(401,WhatsAppRejected),
                                           (429,WhatsAppRejected),(500,DeliveryUncertain),(503,DeliveryUncertain)])
def test_meta_errors_do_not_expose_tokens_or_provider_details(status,expected):
    wa=WhatsApp('unit-test-meta-token',PHONE,client=httpx.Client(transport=httpx.MockTransport(
        lambda _request:httpx.Response(status,json={'error':{'code':190,'message':'PRIVATE_META_DETAILS'}}))))
    with pytest.raises(expected) as error:
        wa.send_message(SENDER,'Texto.',last_customer_at=now())
    assert 'PRIVATE_META_DETAILS' not in str(error.value) and 'unit-test-meta-token' not in str(error.value)


@pytest.mark.parametrize('payload', [{'success':True}, {'messages':'invalid'},
    {'messages':['invalid']}, {'messages':[{'id':'invalid'}]}, {'messages':[{'id':123}]}])
def test_missing_or_invalid_message_id_is_uncertain_and_never_retried(service,settings,repo,analysis,payload):
    calls=connect(service,handler=lambda _request:httpx.Response(200,json=payload))
    t=receive(service,settings)
    with pytest.raises(DeliveryUncertain):
        service.respond_to_customer(t['_id'],t['revision'],analysis,'Podemos orientar tu solicitud.')
    assert len(calls)==1 and repo.get(t['_id'])['delivery_state']=='incierto'


def test_inbox_worker_survives_unexpected_errors_without_logging_private_data(service,repo,monkeypatch,caplog):
    class Stop:
        done=False
        def is_set(self):return self.done
        def wait(self,seconds):
            assert seconds==5
            self.done=True
    def fail():raise RuntimeError('PRIVATE_WEBHOOK_DETAILS')
    monkeypatch.setattr(repo,'process_whatsapp_inbox',fail)
    process_inbox(service,Stop())
    assert 'reintento' in caplog.text and 'PRIVATE_WEBHOOK_DETAILS' not in caplog.text


def test_status_before_api_ack_is_preserved_and_later_statuses_never_go_backwards(service,settings,repo,analysis):
    def send(_request):
        repo.whatsapp_status(OUTBOUND,'read',now())
        return httpx.Response(200,json={'messages':[{'id':OUTBOUND}]})
    connect(service,handler=send);t=receive(service,settings)
    result=service.respond_to_customer(t['_id'],t['revision'],analysis,'Podemos orientar tu solicitud.')
    assert result['provider_delivery_status']=='read'
    for status in ['sent','failed','delivered']:
        repo.whatsapp_status(OUTBOUND,status,now())
    assert repo.get(t['_id'])['attempt']['provider_delivery_status']=='read'


def test_delivery_receipt_does_not_create_a_customer_message(service,settings,repo,analysis):
    connect(service);t=receive(service,settings)
    service.respond_to_customer(t['_id'],t['revision'],analysis,'Podemos orientar tu solicitud.')
    data=event();value=data['entry'][0]['changes'][0]['value'];value.pop('messages')
    value['statuses']=[{'id':OUTBOUND,'status':'delivered','timestamp':str(int(now().timestamp()))}]
    with create_whatsapp_webhook(service,settings).test_client() as client:
        assert post(client,settings,data).status_code==200
    updated=repo.get(t['_id'])
    assert updated['attempt']['provider_delivery_status']=='delivered' and updated['revision']==1

from dotenv import dotenv_values
import pytest

from aiec.whatsapp_setup import configure


def test_setup_only_requests_missing_secret_and_preserves_existing_tokens_and_ai_mode(tmp_path,capsys):
    env=tmp_path/'.env'
    env.write_text('WHATSAPP_ACCESS_TOKEN=private-existing-access\nWHATSAPP_PHONE_NUMBER_ID=111222333444\n'
        'WHATSAPP_BUSINESS_ACCOUNT_ID=555666777888\nAUTO_REPLY_ENABLED=false\nTELEGRAM_ENABLED=true\n')
    prompts=[]
    def secret(prompt):
        prompts.append(prompt)
        return 'private-separate-app-secret'
    def unexpected(_prompt):pytest.fail('No se deben pedir de nuevo identificadores guardados.')
    configure(env,ask=unexpected,ask_secret=secret)
    values=dotenv_values(env)
    assert len(prompts)==1 and 'Secreto' in prompts[0]
    assert values['WHATSAPP_ACCESS_TOKEN']=='private-existing-access'
    assert values['WHATSAPP_ENABLED']=='true' and values['AUTO_REPLY_ENABLED']=='false'
    assert values['TELEGRAM_ENABLED']=='true' and len(values['WHATSAPP_VERIFY_TOKEN'])>=20
    assert env.stat().st_mode & 0o777==0o600
    output=capsys.readouterr().out
    assert 'private-existing-access' not in output and 'private-separate-app-secret' not in output


def test_invalid_configuration_does_not_replace_any_saved_credentials(tmp_path):
    env=tmp_path/'.env';before='WHATSAPP_ACCESS_TOKEN=private-existing-access\n'
    env.write_text(before)
    with pytest.raises(ValueError):
        configure(env,ask=lambda _prompt:'not-a-number',ask_secret=lambda _prompt:'bad')
    assert env.read_text()==before


def test_mcp_loads_a_newly_enabled_channel_without_restarting_claude(service,monkeypatch):
    from dataclasses import replace
    from aiec.config import Settings
    from aiec.whatsapp_setup import refresh_mcp_channel
    values={'WHATSAPP_ENABLED':'true','WHATSAPP_ACCESS_TOKEN':'unit-access',
        'WHATSAPP_PHONE_NUMBER_ID':'111222333444','WHATSAPP_BUSINESS_ACCOUNT_ID':'555666777888',
        'WHATSAPP_APP_SECRET':'unit-app-secret-123456','WHATSAPP_VERIFY_TOKEN':'unit-verification-token-123456',
        'WHATSAPP_GRAPH_VERSION':'v26.0'}
    monkeypatch.setattr('aiec.whatsapp_setup.dotenv_values',lambda _path:values)
    service.settings=Settings()
    refresh_mcp_channel(service)
    first=service.whatsapp
    assert first is not None and service.settings.whatsapp_enabled
    refresh_mcp_channel(service)
    assert service.whatsapp is first
    values['WHATSAPP_ENABLED']='false'
    refresh_mcp_channel(service)
    assert service.whatsapp is None
    first.client.close()


def test_invalid_new_credentials_do_not_enable_a_channel_or_modify_telegram(service,telegram,monkeypatch):
    from aiec.config import Settings
    from aiec.whatsapp_setup import refresh_mcp_channel
    service.settings=Settings()
    monkeypatch.setattr('aiec.whatsapp_setup.dotenv_values',lambda _path:{'WHATSAPP_ENABLED':'true'})
    refresh_mcp_channel(service)
    assert service.whatsapp is None and service.telegram is telegram and telegram.calls==[]

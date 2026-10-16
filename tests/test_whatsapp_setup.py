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

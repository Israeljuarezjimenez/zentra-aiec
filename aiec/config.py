from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str = field(default="", repr=False)
    bot_token: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    claude_model: str = "claude-haiku-5-5"
    auto_reply_enabled: bool = False
    telegram_enabled: bool = True
    whatsapp_enabled: bool = False
    whatsapp_access_token: str = field(default='', repr=False)
    whatsapp_app_secret: str = field(default='', repr=False)
    whatsapp_verify_token: str = field(default='', repr=False)
    whatsapp_phone_number_id: str = ''
    whatsapp_business_account_id: str = ''
    whatsapp_graph_version: str = 'v26.0'
    whatsapp_webhook_port: int = 5051
    database: str = "aiec"
    port: int = 5050

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env", override=False)
        try:
            port = int(os.getenv("PORT", "5050"))
        except ValueError:
            raise ConfigurationError("PORT debe ser un número, por ejemplo 5050.") from None
        if not 1024 <= port <= 65535:
            raise ConfigurationError("PORT debe estar entre 1024 y 65535.")
        automatic = os.getenv("AUTO_REPLY_ENABLED", "false").lower().strip()
        if automatic not in ("true", "false"):
            raise ConfigurationError("AUTO_REPLY_ENABLED debe ser true o false.")
        def flag(name, default):
            value = os.getenv(name, default).strip().lower()
            if value not in ('true', 'false'):
                raise ConfigurationError(f'{name} debe ser true o false.')
            return value == 'true'
        try:
            webhook_port = int(os.getenv('WHATSAPP_WEBHOOK_PORT', '5051'))
        except ValueError:
            raise ConfigurationError('WHATSAPP_WEBHOOK_PORT debe ser un número.') from None
        if not 1024 <= webhook_port <= 65535 or webhook_port == port:
            raise ConfigurationError('El puerto de WhatsApp debe estar entre 1024 y 65535 y ser distinto al del panel.')
        return cls(
            mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            secret_key=os.getenv("FLASK_SECRET_KEY", "").strip(),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-haiku-5-5").strip(),
            auto_reply_enabled=automatic == "true",
            telegram_enabled=flag('TELEGRAM_ENABLED', 'true'),
            whatsapp_enabled=flag('WHATSAPP_ENABLED', 'false'),
            whatsapp_access_token=os.getenv('WHATSAPP_ACCESS_TOKEN', '').strip(),
            whatsapp_app_secret=os.getenv('WHATSAPP_APP_SECRET', '').strip(),
            whatsapp_verify_token=os.getenv('WHATSAPP_VERIFY_TOKEN', '').strip(),
            whatsapp_phone_number_id=os.getenv('WHATSAPP_PHONE_NUMBER_ID', '').strip(),
            whatsapp_business_account_id=os.getenv('WHATSAPP_BUSINESS_ACCOUNT_ID', '').strip(),
            whatsapp_graph_version=os.getenv('WHATSAPP_GRAPH_VERSION', 'v26.0').strip(),
            whatsapp_webhook_port=webhook_port,
            database=os.getenv("MONGODB_DATABASE", "aiec").strip(),
            port=port,
        )

    def validate(self, *, telegram=False, web=False):
        if not self.mongodb_uri.startswith(("mongodb://", "mongodb+srv://")):
            raise ConfigurationError("Configura MONGODB_URI en .env con la conexión de Atlas.")
        if not self.database or any(c in self.database for c in '/\\."$ '):
            raise ConfigurationError("MONGODB_DATABASE debe ser un nombre sencillo, por ejemplo aiec.")
        if telegram:
            parts = self.bot_token.split(":", 1)
            if len(parts) != 2 or not parts[0].isdigit() or len(parts[1]) < 20:
                raise ConfigurationError("Configura TELEGRAM_BOT_TOKEN en .env con el token de BotFather.")
        if web and len(self.secret_key) < 32:
            raise ConfigurationError("Ejecuta python -m aiec setup para generar la clave del panel.")

    def validate_whatsapp(self, *, webhook=False):
        import re
        if not self.whatsapp_access_token or not self.whatsapp_phone_number_id.isdigit():
            raise ConfigurationError('Configura WHATSAPP_ACCESS_TOKEN y WHATSAPP_PHONE_NUMBER_ID en .env.')
        if not self.whatsapp_business_account_id.isdigit():
            raise ConfigurationError('Configura WHATSAPP_BUSINESS_ACCOUNT_ID en .env.')
        if not re.fullmatch(r'v\d+\.0', self.whatsapp_graph_version):
            raise ConfigurationError('WHATSAPP_GRAPH_VERSION debe tener un formato como v26.0.')
        if webhook and (len(self.whatsapp_app_secret) < 16 or len(self.whatsapp_verify_token) < 20):
            raise ConfigurationError('Falta WHATSAPP_APP_SECRET o WHATSAPP_VERIFY_TOKEN en .env para validar los avisos de Meta.')

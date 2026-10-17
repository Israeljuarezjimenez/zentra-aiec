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
        return cls(
            mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            secret_key=os.getenv("FLASK_SECRET_KEY", "").strip(),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-haiku-5-5").strip(),
            auto_reply_enabled=automatic == "true",
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

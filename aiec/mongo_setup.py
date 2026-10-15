"""Permite configurar la contraseña de Atlas desde la terminal, sin mostrarla."""
import getpass
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from dotenv import dotenv_values, set_key

from .config import ROOT


def with_password(uri: str, password: str) -> str:
    if not password:
        raise ValueError("La contraseña no puede estar vacía.")
    try:
        parts = urlsplit(uri)
        if parts.scheme not in ("mongodb", "mongodb+srv") or not parts.username or not parts.hostname:
            raise ValueError
    except ValueError:
        raise ValueError("Primero guarda la cadena de conexión de Atlas en MONGODB_URI dentro de .env.") from None
    # Reconstruir solo las credenciales; conservar servidor, base y opciones de Atlas.
    host = parts.netloc.rsplit("@", 1)[-1]
    credentials = quote(unquote(parts.username), safe="") + ":" + quote(password, safe="")
    return urlunsplit((parts.scheme, credentials + "@" + host, parts.path, parts.query, parts.fragment))


def main():
    env_file = ROOT / ".env"
    uri = dotenv_values(env_file).get("MONGODB_URI") or ""
    if not uri:
        print("Primero guarda la cadena de conexión de Atlas en MONGODB_URI dentro de .env.")
        return 1
    print("Configurar contraseña de MongoDB Atlas")
    print("Escribe la contraseña del usuario de la base de datos, no la de tu cuenta de Atlas.")
    print("Por privacidad no aparecerán caracteres mientras escribes o pegas. Al terminar pulsa Enter.")
    try:
        password = getpass.getpass("Contraseña de la base de datos: ")
        new_uri = with_password(uri, password)
        env_file.chmod(0o600)
        set_key(env_file, "MONGODB_URI", new_uri)
        env_file.chmod(0o600)
    except (EOFError, KeyboardInterrupt):
        print("\nConfiguración cancelada; el archivo no cambió.")
        return 1
    except ValueError as error:
        print(str(error))
        return 1
    print("Contraseña guardada en .env sin mostrarla. Comprobando las conexiones…")
    from .__main__ import main as run
    return run(["doctor"])


if __name__ == "__main__":
    raise SystemExit(main())

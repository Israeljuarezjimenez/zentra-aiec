"""Punto de entrada con ruta absoluta para Claude Desktop; stdout pertenece a MCP."""
from aiec.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["mcp"]))

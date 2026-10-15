import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from aiec.config import ROOT


def unpack(result):
    assert not result.isError, result
    return json.loads(result.content[0].text)


def test_real_stdio_protocol_reads_and_saves_but_exposes_no_send_tool():
    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "aiec", "mcp", "--demo"], cwd=str(ROOT))
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "consultar_catalogo", "listar_solicitudes", "obtener_solicitud", "guardar_borrador", "buscar_conocimiento"}
                business = unpack(await session.call_tool("consultar_catalogo", {}))
                assert len(business["services"]) == 3
                pending_result = await session.call_tool("listar_solicitudes", {"estado": "pendiente", "limite": 5})
                assert not pending_result.isError
                # Las listas se devuelven como un bloque de texto por elemento.
                structured = pending_result.structuredContent
                pending = structured["result"] if structured else [json.loads(c.text) for c in pending_result.content]
                ticket = unpack(await session.call_tool("obtener_solicitud", {"id_solicitud": pending[0]["id"]}))
                assert ticket["knowledge_context"]["status"] == "no_disponible"
                quote = ticket["messages"][-1]["text"]
                result = unpack(await session.call_tool("guardar_borrador", {
                    "id_solicitud": ticket["id"], "revision": ticket["revision"],
                    "analisis": {"service": "desconocido", "summary": "Solicitud por revisar.",
                                 "priority": "media", "priority_reason": "Requiere más datos.",
                                 "budget": None, "evidence": [quote], "missing_data": []},
                    "respuesta": "Gracias por escribir. ¿Cuándo te gustaría comenzar?",
                }))
                assert result["status"] == "revision"
                updated = unpack(await session.call_tool("obtener_solicitud", {"id_solicitud": ticket["id"]}))
                assert updated["draft"]["text"].startswith("Gracias")
                assert updated["delivery_state"] == "ninguno"
    asyncio.run(exercise())

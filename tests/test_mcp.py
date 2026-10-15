import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from aiec.config import ROOT


def unpack(result):
    assert not result.isError, result
    return json.loads(result.content[0].text)


def test_stdio_exposes_direct_reply_and_demo_prevents_real_delivery():
    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m','aiec','mcp','--demo'], cwd=str(ROOT))
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert {tool.name for tool in tools} == {
                    'consultar_catalogo','estado_sistema','listar_solicitudes','obtener_solicitud',
                    'responder_cliente','buscar_conocimiento'}
                send = next(t for t in tools if t.name == 'responder_cliente')
                assert send.annotations.readOnlyHint is False
                assert send.annotations.idempotentHint is True
                assert 'chat_id' not in send.inputSchema['properties']
                business = unpack(await session.call_tool('consultar_catalogo', {}))
                assert len(business['services']) == 9
                status = unpack(await session.call_tool('estado_sistema', {}))
                assert status['mode'] == 'demo' and not status['delivery_ready']
                pending_result = await session.call_tool('listar_solicitudes', {'estado':'pendiente','limite':5})
                pending = pending_result.structuredContent['result']
                ticket = unpack(await session.call_tool('obtener_solicitud', {'id_solicitud':pending[0]['id']}))
                result = await session.call_tool('responder_cliente', {
                    'id_solicitud':ticket['id'],'revision':ticket['revision'],
                    'analisis':{'service':'offset','summary':'Consulta de impresión.', 'priority':'media',
                        'priority_reason':'Sin fecha urgente.','evidence':[ticket['messages'][-1]['text']],
                        'missing_data':['Sin evidencia documental en esta demo.']},
                    'respuesta':'Podemos orientar tu consulta de impresión.',
                })
                assert result.isError
                updated = unpack(await session.call_tool('obtener_solicitud', {'id_solicitud':ticket['id']}))
                assert updated['status'] == 'pendiente' and updated['delivery_state'] == 'ninguno'
    asyncio.run(exercise())


def test_real_stdio_validates_rag_sends_directly_and_deduplicates(tmp_path):
    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=[str(ROOT/'tests/mcp_fixture_server.py'),str(tmp_path)],
            env={'PYTHONPATH':str(ROOT)},cwd=str(ROOT))
        async with stdio_client(parameters) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                await session.initialize()
                pending=await session.call_tool('listar_solicitudes',{})
                ticket_id=pending.structuredContent['result'][0]['id']
                ticket=unpack(await session.call_tool('obtener_solicitud',{
                    'id_solicitud':ticket_id,'consulta_conocimiento':'QA_PRINT_SOURCE medida cantidad acabado'}))
                hit=ticket['knowledge_context']['results'][0]
                payload={'id_solicitud':ticket_id,'revision':ticket['revision'],
                    'analisis':{'service':'offset','summary':'Solicitud de volantes.', 'priority':'media',
                        'priority_reason':'Sin urgencia explícita.', 'evidence':['Necesito volantes QA_PRINT_SOURCE'],
                        'missing_data':['Tarifa propia no cargada.']},
                    'respuesta':'Tu solicitud de volantes quedó registrada. El material y acabado se definirían para esa impresión.',
                    'rag_context_id':ticket['knowledge_context']['context_id'],
                    'fuentes':[{'chunk_id':hit['id'],'quote':hit['text']}]}
                delivered=unpack(await session.call_tool('responder_cliente',payload))
                assert delivered['sent'] and not delivered['already_sent']
                duplicate=unpack(await session.call_tool('responder_cliente',payload))
                assert duplicate['already_sent']
                assert duplicate['telegram_message_id'] == delivered['telegram_message_id'] == 901
                updated=unpack(await session.call_tool('obtener_solicitud',{'id_solicitud':ticket_id}))
                assert updated['status'] == 'respondida' and updated['draft'] is None
                assert len(updated['messages']) == 2
                assert updated['messages'][-1]['text'] == payload['respuesta']
                assert updated['analysis']['knowledge']['citations'][0]['source'] == 'impresion.md'
    asyncio.run(exercise())

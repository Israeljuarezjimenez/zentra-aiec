"""Servidor exclusivo para pruebas del protocolo: RAM, vectores simulados y transporte falso."""
from pathlib import Path
import re
import sys

import mongomock
import numpy as np

from aiec.mcp_server import create_mcp
from aiec.rag import KnowledgeBase
from aiec.repository import Repository
from aiec.service import Service


class Embedder:
    def prepare(self, **_kwargs):
        pass
    def offsets(self, text):
        return [(m.start(),m.end()) for m in re.finditer(r'\S+',text)]
    def passages(self, texts):
        vectors=np.zeros((len(texts),384),dtype='<f4'); vectors[:,0]=1
        return vectors
    def query(self, text):
        return self.passages([text])[0]


class Reranker:
    def prepare(self, **_kwargs):
        pass
    def score(self, query, texts):
        return np.array([5 if 'QA_PRINT_SOURCE' in text else -20 for text in texts])


class TelegramTransport:
    def __init__(self):
        self.sent=[]
    def send_message(self, chat_id, text):
        self.sent.append((chat_id,text))
        return {'message_id':900+len(self.sent)}


def main():
    root=Path(sys.argv[1])
    folder=root/'knowledge'; folder.mkdir()
    (folder/'impresion.md').write_text('# Preparación QA_PRINT_SOURCE\n\nLa medida, cantidad y acabado se registran para describir una solicitud de impresión QA_PRINT_SOURCE.')
    kb=KnowledgeBase(folder,root/'rag',embedder=Embedder(),reranker=Reranker()); kb.index()
    repo=Repository(mongomock.MongoClient(tz_aware=True).mcp_test)
    repo.receive(1001,1,'Cliente de prueba','Necesito volantes QA_PRINT_SOURCE para mi negocio.')
    if '--recoverable-database' in sys.argv:
        from pymongo.errors import ServerSelectionTimeoutError
        import aiec.rag
        import aiec.telegram
        from aiec.__main__ import main as run_cli

        def connect(_cls, _settings):
            with (root/'connection-attempts').open('a') as output:
                output.write('attempt\n')
            if not (root/'mongodb-ready').exists():
                raise ServerSelectionTimeoutError('PRIVATE_DATABASE_ERROR_DO_NOT_EXPOSE')
            return repo

        Repository.connect=classmethod(connect)
        aiec.rag.KnowledgeBase=lambda:kb
        aiec.telegram.Telegram=lambda _token:TelegramTransport()
        raise SystemExit(run_cli(['mcp']))
    create_mcp(Service(repo,TelegramTransport(),knowledge=kb)).run(transport='stdio')


if __name__ == '__main__':
    main()

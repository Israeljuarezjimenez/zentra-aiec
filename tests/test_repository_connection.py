from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from aiec.config import Settings
from aiec.repository import DeferredRepository, Repository


def test_deferred_connection_is_shared_by_simultaneous_tool_calls():
    connections=[]
    closed=[]
    target=SimpleNamespace(tickets=object(),database=SimpleNamespace(
        client=SimpleNamespace(close=lambda:closed.append(True))))
    def connect():
        connections.append(True)
        return target
    deferred=DeferredRepository(connect)
    deferred.close()
    assert not connections
    barrier=Barrier(8)
    def read(_index):
        barrier.wait()
        return deferred.tickets
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(read,range(8)))
    assert all(result is target.tickets for result in results)
    assert connections==[True] and deferred.initialized
    deferred.close()
    assert closed==[True] and not deferred.initialized


@pytest.mark.parametrize('fail_during',['ping','indexes'])
def test_failed_database_connection_closes_its_client(monkeypatch, fail_during):
    closed=[]
    def fail(*_args,**_kwargs):
        raise ServerSelectionTimeoutError('Private provider details')
    class Client:
        admin=SimpleNamespace(command=fail if fail_during=='ping' else lambda _command:None)
        def __getitem__(self,_name):
            return SimpleNamespace(tickets=SimpleNamespace(create_index=fail),state=object())
        def close(self):
            closed.append(True)
    monkeypatch.setattr('aiec.repository.MongoClient',lambda *_args,**_kwargs:Client())
    with pytest.raises(ServerSelectionTimeoutError):
        Repository.connect(Settings(mongodb_uri='mongodb://127.0.0.1:1/test'))
    assert closed==[True]

from concurrent.futures import ThreadPoolExecutor
from cxpe.sessions import SessionStore


def test_concurrent_updates_and_reads_are_complete(tmp_path):
    store=SessionStore(tmp_path)
    sid=store.create('PLAN').id
    def update(i):
        store.update_meta(sid, summary={str(i):i})
        for _ in range(4):assert store.read_meta(sid).id==sid
    with ThreadPoolExecutor(max_workers=8) as workers:list(workers.map(update,range(60)))
    assert store.read_meta(sid).summary=={str(i):i for i in range(60)}
    assert not list(store.path(sid).glob('*.tmp'))

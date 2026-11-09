"""Exercise SQLite initialization in real concurrent processes."""
import multiprocessing

from yank.model import Store


def initialize_queue(state_dir, gate, results):
    gate.wait(timeout=10)
    try:
        store = Store(state_dir)
        try:
            results.put((store.load(), store.db.execute('PRAGMA journal_mode').fetchone()[0]))
        finally:
            store.close()
    except Exception as error:
        results.put((type(error).__name__, str(error)))


def test_concurrent_fresh_queue_initialization(tmp_path):
    context = multiprocessing.get_context('spawn')
    for index in range(20):
        gate = context.Barrier(2)
        results = context.Queue()
        processes = [context.Process(target=initialize_queue,
            args=(tmp_path/str(index), gate, results)) for _ in range(2)]
        try:
            for process in processes:
                process.start()
            actual = [results.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(timeout=10)
                assert process.exitcode == 0
            assert actual == [({}, 'wal'), ({}, 'wal')], actual
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            results.close()
            results.join_thread()

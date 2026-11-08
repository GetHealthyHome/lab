from lab_toolbox import sync


class FakeSink:
    def __init__(self):
        self.sent, self.down = [], False

    def send(self, kind, payload):
        if self.down:
            raise ConnectionError("offline")
        self.sent.append((kind, payload))


def test_queue_holds_while_offline_then_drains_in_order(conn, tmp_path):
    photo = tmp_path / "gate.jpg"
    photo.write_bytes(b"jpg")
    sync.enqueue(conn, "photo", {"path": str(photo)})
    sync.enqueue(conn, "transaction", {"txn_id": "a"})
    sync.enqueue(conn, "transaction", {"txn_id": "b"})
    sink = FakeSink()

    sink.down = True
    assert sync.sync_once(conn, sink) == 0
    assert sync.pending(conn) == 3
    err = conn.execute("SELECT attempts, last_error FROM sync_queue ORDER BY id LIMIT 1").fetchone()
    assert tuple(err) == (1, "ConnectionError: offline")
    assert photo.exists()

    sink.down = False
    assert sync.sync_once(conn, sink) == 3
    assert [k for k, _ in sink.sent] == ["photo", "transaction", "transaction"]
    assert [p.get("txn_id") for _, p in sink.sent[1:]] == ["a", "b"]
    assert sync.pending(conn) == 0
    assert not photo.exists()  # local copy removed once Drive has it


def test_failure_mid_queue_keeps_order(conn):
    class FlakySink(FakeSink):
        failed = False

        def send(self, kind, payload):
            if payload["n"] == 2 and not self.failed:
                self.failed = True
                raise TimeoutError("slow")
            super().send(kind, payload)

    for n in range(1, 4):
        sync.enqueue(conn, "transaction", {"n": n})
    sink = FlakySink()
    assert sync.sync_once(conn, sink) == 1
    assert sync.sync_once(conn, sink) == 2
    assert [p["n"] for _, p in sink.sent] == [1, 2, 3]

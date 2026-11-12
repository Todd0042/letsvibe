"""The queue is first come, first served: the worker takes the oldest task, and the list shows that order."""
from letsvibe.storage.db import get_db_connection, get_queue_items, init_db
from letsvibe.web.worker import AuditQueueWorker


def _enqueue(db, slug):
    conn = get_db_connection(db)
    with conn:
        conn.execute("INSERT INTO audit_queue (target, owner, repo_name, full_slug, mode, status) VALUES (?, ?, ?, ?, 'blend', 'queued')",
                     (f"https://github.com/{slug}", slug.split("/")[0], slug.split("/")[1], slug))
    conn.close()


def test_oldest_task_runs_first_and_is_listed_first(tmp_path):
    db = tmp_path / "q.db"
    init_db(db)
    for slug in ("a/first", "b/second", "c/third"):
        _enqueue(db, slug)
    conn = get_db_connection(db)
    with conn:
        conn.execute("UPDATE queue_settings SET is_paused = 0, auto_process = 1 WHERE id = 1")
    conn.close()

    listed = [(i["full_slug"], i.get("queue_position")) for i in get_queue_items(db_path=db)]
    assert listed == [("a/first", 1), ("b/second", 2), ("c/third", 3)]

    task = AuditQueueWorker(db_path=db)._fetch_next_task()
    assert task["full_slug"] == "a/first"
    listed = [(i["full_slug"], i["status"], i.get("queue_position")) for i in get_queue_items(db_path=db)]
    assert listed == [("a/first", "running", None), ("b/second", "queued", 1), ("c/third", "queued", 2)]


def test_run_next_jumps_the_line_but_never_runs_in_parallel(tmp_path):
    db = tmp_path / "q.db"
    init_db(db)
    for slug in ("a/first", "b/second", "c/third"):
        _enqueue(db, slug)
    conn = get_db_connection(db)
    with conn:
        conn.execute("UPDATE queue_settings SET is_paused = 1 WHERE id = 1")
        third = conn.execute("SELECT id FROM audit_queue WHERE full_slug = 'c/third'").fetchone()["id"]
    conn.close()

    worker = AuditQueueWorker(db_path=db)
    assert worker._fetch_next_task() is None                 # paused: nothing runs on its own
    assert worker.process_task_now(third) is True             # no thread is started here
    assert [i["status"] for i in get_queue_items(db_path=db)] == ["queued"] * 3
    assert worker._fetch_next_task()["full_slug"] == "c/third"
    assert worker.process_task_now(third) is False            # already running

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


def _set(db, sql, *args):
    conn = get_db_connection(db)
    with conn:
        conn.execute(sql, args)
    conn.close()


def test_stop_pauses_queue_and_keeps_the_task_first_in_line(tmp_path):
    import pytest
    from letsvibe.web.worker import TaskStopped

    db = tmp_path / "q.db"
    init_db(db)
    for slug in ("a/first", "b/second", "c/third"):
        _enqueue(db, slug)
    _set(db, "UPDATE queue_settings SET is_paused = 0, auto_process = 1 WHERE id = 1")
    worker = AuditQueueWorker(db_path=db)
    third = [i for i in get_queue_items(db_path=db) if i["full_slug"] == "c/third"][0]["id"]
    assert worker.process_task_now(third)              # started via "Run next", so it is not the oldest
    task = worker._fetch_next_task()
    worker.current_task_info = {"id": task["id"]}       # as _process_task sets it

    assert worker.request_stop(task["id"]) is True
    assert get_queue_items(db_path=db)[0]["status"] == "running"   # still running until its next safe point
    with pytest.raises(TaskStopped):
        worker._update_task_progress(task["id"], "next step")       # the next safe point stops it
    worker._requeue_stopped(task["id"])

    listed = [(i["full_slug"], i["status"], i.get("queue_position")) for i in get_queue_items(db_path=db)]
    assert listed[:3] == [("c/third", "queued", 1), ("a/first", "queued", 2), ("b/second", "queued", 3)]
    assert worker._fetch_next_task() is None             # the queue was paused by the stop
    _set(db, "UPDATE queue_settings SET is_paused = 0 WHERE id = 1")    # admin presses Resume
    assert worker._fetch_next_task()["full_slug"] == "c/third"          # the stopped task goes first
    assert worker.request_stop(9999) is False


def test_stopping_a_leftover_running_task_requeues_it_at_once(tmp_path):
    db = tmp_path / "q.db"
    init_db(db)
    _enqueue(db, "a/orphan")
    _set(db, "UPDATE audit_queue SET status = 'running'")
    worker = AuditQueueWorker(db_path=db)                # nothing is actually running in this worker
    tid = get_queue_items(db_path=db)[0]["id"]
    assert worker.request_stop(tid) is True
    item = get_queue_items(db_path=db)[0]
    assert item["status"] == "queued" and item["queue_position"] == 1

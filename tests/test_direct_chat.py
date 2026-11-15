from pathlib import Path

from letsvibe.storage.db import (
    add_chat_message_tag,
    delete_user_account,
    export_user_data,
    get_admin_chat_thread,
    get_admin_chat_unread_count,
    get_user_chat_thread,
    get_user_chat_unread_count,
    init_db,
    list_admin_chat_messages_by_tag,
    list_admin_chat_tags,
    list_admin_chat_threads,
    remove_chat_message_tag,
    reply_admin_chat_thread,
    send_user_chat_message,
    upsert_user,
)


def test_direct_chat_and_tagging_lifecycle(tmp_path: Path):
    db_path = tmp_path / "chat_test.db"
    init_db(db_path)

    upsert_user(github_id="101", username="alice", avatar_url="https://example.com/a.png", db_path=db_path)
    upsert_user(github_id="202", username="bob", avatar_url="https://example.com/b.png", db_path=db_path)

    m1 = send_user_chat_message("101", "alice", None, "Found a bug on ESP32 dash", db_path=db_path)
    m2 = send_user_chat_message("202", "bob", None, "Another bug report here", db_path=db_path)

    assert get_admin_chat_unread_count(db_path=db_path) == 2

    threads = list_admin_chat_threads(sort="unread", db_path=db_path)
    assert len(threads) == 2

    # Opening Alice's thread marks her unread_admin = 0
    alice_thread = get_admin_chat_thread(threads[0]["id"], mark_read=True, db_path=db_path)
    assert alice_thread is not None
    assert get_admin_chat_unread_count(db_path=db_path) == 1

    # Admin replies to Alice -> increments Alice's unread_user
    reply = reply_admin_chat_thread(m1["thread_id"], "Todd0042", "Looking into it!", db_path=db_path)
    assert reply is not None
    assert get_user_chat_unread_count("101", db_path=db_path) == 1

    user_view = get_user_chat_thread("101", "alice", mark_read=True, db_path=db_path)
    assert len(user_view["messages"]) == 2
    assert get_user_chat_unread_count("101", db_path=db_path) == 0

    # Tagging & newest-first sorting
    assert add_chat_message_tag(m1["id"], "ESP32", db_path=db_path) == ["esp32"]
    assert add_chat_message_tag(m1["id"], "bug", db_path=db_path) == ["bug", "esp32"]
    assert add_chat_message_tag(m2["id"], "bug", db_path=db_path) == ["bug"]

    tag_counts = {t["tag"]: t["count"] for t in list_admin_chat_tags(db_path=db_path)}
    assert tag_counts == {"bug": 2, "esp32": 1}

    by_bug_newest = list_admin_chat_messages_by_tag("bug", sort="newest", db_path=db_path)
    assert [m["id"] for m in by_bug_newest] == [m2["id"], m1["id"]]

    assert remove_chat_message_tag(m1["id"], "bug", db_path=db_path) == ["esp32"]

    # Export & delete compliance
    exported = export_user_data("101", "alice", db_path=db_path)
    assert len(exported["direct_messages"]) == 2

    assert delete_user_account("101", "alice", db_path=db_path) is True
    assert get_user_chat_thread("101", "alice", db_path=db_path)["messages"] == []
    assert list_admin_chat_messages_by_tag("esp32", db_path=db_path) == []

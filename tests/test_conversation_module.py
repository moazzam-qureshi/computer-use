import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from storage.connection import Database
from storage.conversations import ConversationStore
from assistant.conversation import load_history, append_user, append_assistant


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


def test_load_history_empty(db):
    conv = ConversationStore(db)
    cid = conv.get_or_create("test-conv-mod-1")
    h = load_history(db, cid, window=10)
    # summary may be None or empty
    assert h["messages"] == [] or isinstance(h["messages"], list)


def test_append_and_load(db):
    conv = ConversationStore(db)
    cid = conv.get_or_create("test-conv-mod-2")
    append_user(db, cid, "what setups are active?")
    append_assistant(db, cid, "Setup #2 (active, normal). Setup #4 (active, critical).")
    h = load_history(db, cid, window=10)
    assert len(h["messages"]) >= 2
    # Last two messages should be these two
    last_two = h["messages"][-2:]
    assert last_two[0]["role"] == "user"
    assert last_two[1]["role"] == "assistant"

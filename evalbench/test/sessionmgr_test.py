import pytest
from util import get_SessionManager
import time


@pytest.fixture(scope="session")
def manager():
    print("creating sessionmanager")
    sesssionmanager = get_SessionManager()
    sesssionmanager.set_ttl(5)
    yield sesssionmanager
    print("\nshutting down sessionmanager")
    sesssionmanager.shutdown()


class TestSessionManager:

    def test_create_session(self, manager):
        session = manager.create_session(f"test_session")
        session_count = len(manager.get_sessions())
        assert session_count == 1

    def test_update_session(self, manager):
        session = manager.get_session(f"test_session")
        session["foo"] = "bar"
        session_count = len(manager.get_sessions())
        got_session = manager.get_session(f"test_session")
        assert session_count == 1
        assert got_session["foo"] == "bar"

    def test_dump_sessions(self, manager):
        for session in manager.get_sessions():
            assert isinstance(manager.get_session(session), dict)

    def test_delete_session(self, manager):
        session = manager.get_session(f"test_session")
        session_count = len(manager.get_sessions())
        assert session_count == 1
        manager.delete_session(f"test_session")
        session_count = len(manager.get_sessions())
        assert session_count == 0

    def test_expire_session(self, manager):
        session = manager.create_session(f"test_session_expire")
        session_count = len(manager.get_sessions())
        assert session_count == 1
        time.sleep(
            10
            # TODO: to mock out time.time instead() see: https://pypi.org/project/freezegun/ maybe
        )
        session_count = len(manager.get_sessions())
        assert session_count == 0

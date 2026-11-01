import tempfile
import unittest
from pathlib import Path

from chaoscode.sessions import SessionError, SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "project"
        self.root.mkdir()
        self.store = SessionStore(Path(self.temp_dir.name) / "sessions")

    def test_create_save_list_and_resume(self):
        session = self.store.new(self.root)
        session.messages.append({"role": "user", "content": "Fix a bug"})
        self.store.save(session)
        loaded = self.store.load(session.id, self.root)
        self.assertEqual(loaded.messages, session.messages)
        self.assertEqual(self.store.latest(self.root).id, session.id)
        self.assertEqual(len(self.store.list(self.root)), 1)

    def test_session_cannot_be_resumed_in_another_project(self):
        session = self.store.new(self.root)
        other = Path(self.temp_dir.name) / "other"
        other.mkdir()
        with self.assertRaises(SessionError):
            self.store.load(session.id, other)

    def test_remove_session(self):
        session = self.store.new(self.root)
        self.store.delete(session.id)
        self.assertEqual(self.store.list(self.root), [])
        with self.assertRaises(SessionError):
            self.store.delete(session.id)


if __name__ == "__main__":
    unittest.main()

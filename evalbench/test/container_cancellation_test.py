import unittest

from container.cancellation import cancel_session, cancellation_scope


class TestCancellation(unittest.TestCase):

    def test_cancelling_a_session_sets_its_tokens(self):
        with cancellation_scope("s1") as a, cancellation_scope("s1") as b:
            self.assertEqual(cancel_session("s1"), 2)
            self.assertTrue(a.is_set() and b.is_set())

    def test_other_sessions_are_untouched(self):
        with cancellation_scope("s1") as a, cancellation_scope("s2") as b:
            cancel_session("s1")
            self.assertTrue(a.is_set())
            self.assertFalse(b.is_set())

    def test_cancelling_after_the_scope_ends_is_a_noop(self):
        with cancellation_scope("s1") as token:
            pass
        self.assertEqual(cancel_session("s1"), 0)
        self.assertFalse(token.is_set())

    def test_unknown_or_empty_session_is_a_noop(self):
        self.assertEqual(cancel_session("nope"), 0)
        self.assertEqual(cancel_session(None), 0)


if __name__ == "__main__":
    unittest.main()

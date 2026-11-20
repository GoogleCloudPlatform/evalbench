"""Tests for the bind host of the eval server."""

import unittest

from util import bindhost


class ResolveBindHostTest(unittest.TestCase):

    def test_localhost_flag_binds_the_loopback_interface(self):
        host = bindhost.resolve_bind_host(
            localhost=True, cloud_run=False, env_host=None)
        self.assertEqual(host, "127.0.0.1")
        self.assertTrue(bindhost.is_loopback(host))

    def test_alts_server_binds_all_interfaces(self):
        host = bindhost.resolve_bind_host(
            localhost=False, cloud_run=False, env_host=None)
        self.assertEqual(host, "[::]")

    def test_cloud_run_binds_all_interfaces(self):
        for localhost in (False, True):
            host = bindhost.resolve_bind_host(
                localhost=localhost, cloud_run=True, env_host=None)
            self.assertEqual(host, "[::]")

    def test_evalbench_host_has_priority(self):
        host = bindhost.resolve_bind_host(
            localhost=True, cloud_run=False, env_host="0.0.0.0")
        self.assertEqual(host, "0.0.0.0")

    def test_empty_evalbench_host_is_ignored(self):
        host = bindhost.resolve_bind_host(
            localhost=True, cloud_run=False, env_host="")
        self.assertEqual(host, "127.0.0.1")


class IsLoopbackTest(unittest.TestCase):

    def test_loopback_hosts(self):
        for host in ("127.0.0.1", "127.0.0.2", "localhost", "LOCALHOST",
                     "::1", "[::1]"):
            self.assertTrue(bindhost.is_loopback(host), host)

    def test_other_hosts(self):
        for host in ("[::]", "::", "0.0.0.0", "10.0.0.5", "example.com", ""):
            self.assertFalse(bindhost.is_loopback(host), host)


if __name__ == "__main__":
    unittest.main()

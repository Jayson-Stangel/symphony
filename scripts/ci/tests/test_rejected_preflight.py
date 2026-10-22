"""Intentional hosted diagnostic; never merge this source into dev."""
import unittest

class RejectedPreflight(unittest.TestCase):
    def test_early_portable_failure(self):
        self.fail('JAY-695 intentional portable preflight rejection; image jobs must not start')

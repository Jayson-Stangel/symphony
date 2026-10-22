"""Never-merge diagnostic: delay is solely an observable cancellation window."""
import time
import unittest

class CancellationWindow(unittest.TestCase):
    def test_bounded_portable_rejection(self):
        time.sleep(20)
        self.fail('JAY-695 diagnostic 20-second cancellation window; never build images')

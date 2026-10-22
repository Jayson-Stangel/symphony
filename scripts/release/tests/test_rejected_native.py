"""Intentional hosted diagnostic; never merge this source into dev."""
import platform
import unittest

class RejectedNative(unittest.TestCase):
    def test_native_qualification_failure(self):
        self.fail('JAY-695 intentional native-only rejection on ' + platform.system() + '/' + platform.machine())

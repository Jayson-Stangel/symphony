"""Own temporary files only; never acquire the actual host-capacity slot."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mac_runner_capacity import CapacityBusy, LocalSessionLease, Refusal

ONE='8f54445f-c9a6-4bb8-8f61-6c08ff574101'
TWO='df1234ab-1234-4234-8234-123456789abc'
def clean(session):
    return dict(session_id=session, cleanup_complete=True, vm_stopped=True, vm_deleted=True,
        runner_deregistered=True, foreground_reaped=True, permit_removed=True, settings_restored=True)

class Capacity(unittest.TestCase):
    def test_second_owner_is_busy_and_does_not_touch_healthy_session(self):
        with tempfile.TemporaryDirectory() as folder:
            first=LocalSessionLease(Path(folder).resolve()/'slot',ONE)
            before=first.path.read_bytes()
            with self.assertRaises(CapacityBusy):LocalSessionLease(first.directory,TWO)
            self.assertEqual(first.path.read_bytes(),before)
            first.complete(clean(ONE));first.close()
            second=LocalSessionLease(Path(folder).resolve()/'slot',TWO)
            second.complete(clean(TWO));second.close()

    def test_crashed_or_unclean_owner_requires_reconciliation_not_another_guest(self):
        with tempfile.TemporaryDirectory() as folder:
            first=LocalSessionLease(Path(folder).resolve()/'slot',ONE);first.close()
            with self.assertRaises(Refusal):LocalSessionLease(Path(folder).resolve()/'slot',TWO)

    def test_incomplete_foreign_or_uncertain_cleanup_cannot_release(self):
        with tempfile.TemporaryDirectory() as folder:
            lease=LocalSessionLease(Path(folder).resolve()/'slot',ONE)
            for proof in (clean(TWO), dict(clean(ONE),permit_removed=False),dict(clean(ONE),foreground_reaped=None)):
                with self.assertRaises(Refusal):lease.complete(proof)
            self.assertIs(json.loads(lease.path.read_text())['cleanup_complete'],False)
            lease.complete(clean(ONE));lease.close()

    def test_redirected_or_nonprivate_state_refuses_before_lock_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();(root/'real').mkdir();(root/'redirect').symlink_to(root/'real')
            with self.assertRaises(Refusal):LocalSessionLease(root/'redirect',ONE)
            (root/'open').mkdir(mode=0o755)
            with self.assertRaises(Refusal):LocalSessionLease(root/'open',ONE)
            self.assertFalse((root/'real/foreground-session.lock').exists())

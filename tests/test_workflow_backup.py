from pathlib import Path
import json
import tempfile
import unittest

from enterprise_ai.workflow_graph import create_lab, WorkflowLab, WorkflowBusy
from enterprise_ai.workflow_store import FixtureActor
from enterprise_ai.workflow_backup import backup_lab, restore_lab


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lab = create_lab(self.root / 'lab')
        self.employee = FixtureActor(1, 2, 1, 'employee')
        self.manager = FixtureActor(1, 4, 4, 'manager')
        self.lab.start(self.employee, 'leave-1', 'key-1', 'leave', {'days': 3})

    def test_restore_pending_then_approve_once(self):
        backup_lab(self.root / 'lab', self.root / 'backup')
        result = restore_lab(self.root / 'backup', self.root / 'restored')
        self.assertEqual(result['lab_id'], self.lab.lab_id)
        lab = WorkflowLab(self.root / 'restored')
        self.assertTrue(lab.inspect(self.employee, 'leave-1')['waiting_for_approval'])
        for _ in range(2):
            lab.decide(self.manager, 'leave-1', True)
        self.assertEqual(lab.store.effect_count(self.manager), 1)
        self.assertEqual(self.lab.store.effect_count(self.manager), 0)

    def test_existing_targets_and_busy_source_rejected(self):
        with self.assertRaises(FileExistsError):
            backup_lab(self.root / 'lab', self.root / 'lab')
        with self.lab._locked(), self.assertRaises(WorkflowBusy):
            backup_lab(self.root / 'lab', self.root / 'busy-backup')
        self.assertFalse((self.root / 'busy-backup').exists())
        backup_lab(self.root / 'lab', self.root / 'backup')
        with self.assertRaises(FileExistsError):
            restore_lab(self.root / 'backup', self.root / 'lab')

    def test_corrupt_or_traversal_manifest_rejected_before_restore(self):
        backup_lab(self.root / 'lab', self.root / 'backup')
        with (self.root / 'backup/business.sqlite').open('ab') as stream:
            stream.write(b'corrupt')
        with self.assertRaises(ValueError):
            restore_lab(self.root / 'backup', self.root / 'bad')
        self.assertFalse((self.root / 'bad').exists())
        path = self.root / 'backup/manifest.json'
        obj = json.loads(path.read_text())
        obj['files']['../outside'] = '0'*64
        path.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):
            restore_lab(self.root / 'backup', self.root / 'bad2')

    def test_wrong_lab_identity_rejected(self):
        backup_lab(self.root / 'lab', self.root / 'backup')
        path = self.root / 'backup/manifest.json'
        obj = json.loads(path.read_text())
        obj['lab_id'] = '0'*32
        path.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):
            restore_lab(self.root / 'backup', self.root / 'bad')

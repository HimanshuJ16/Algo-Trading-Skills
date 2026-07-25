import unittest
from pitr_backup_tester import PitrBackupTester, Config

class TestPitrBackupTester(unittest.TestCase):
    def test_init(self):
        obj = PitrBackupTester(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = PitrBackupTester()
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()

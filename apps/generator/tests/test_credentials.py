"""Publishing tokens are kept in Windows Credential Manager (a private file elsewhere)."""
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from bidoc_generator import credentials


class Credentials(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        os.environ["BIDOC_HOME"] = str(self.home)
        self.addCleanup(os.environ.pop, "BIDOC_HOME", None)
        self.url = f"https://library-{uuid.uuid4().hex[:8]}.example.com"
        self.addCleanup(credentials.delete_token, self.url)

    def test_round_trip_and_delete(self):
        self.assertIsNone(credentials.load_token(self.url))
        credentials.save_token(self.url, "bidocpt_1234567890abcdef_secret")
        self.assertEqual(credentials.load_token(self.url + "/"), "bidocpt_1234567890abcdef_secret")
        credentials.save_token(self.url, "bidocpt_1234567890abcdef_newer")
        self.assertEqual(credentials.load_token(self.url), "bidocpt_1234567890abcdef_newer")
        credentials.delete_token(self.url)
        self.assertIsNone(credentials.load_token(self.url))
        credentials.delete_token(self.url)                      # deleting twice is fine

    def test_store_location(self):
        credentials.save_token(self.url, "bidocpt_x")
        stored = self.home / "credentials.json"
        if sys.platform == "win32":
            self.assertEqual(credentials.STORE, "Windows Credential Manager")
            self.assertFalse(stored.exists())                    # nothing on disk in the generator home
        else:
            self.assertEqual(oct(stored.stat().st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()

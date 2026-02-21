# -*- coding: utf-8 -*-
import unittest
import asyncio
from src.modules.email_manager import EmailManager

class TestEmailManager(unittest.TestCase):
    def test_init(self):
        config = {
            "EMAIL_IMAP_SERVER": "imap.test.com",
            "EMAIL_USER": "test@test.com",
            "EMAIL_PASS": "pass"
        }
        manager = EmailManager(config)
        self.assertEqual(manager.imap_server, "imap.test.com")
        self.assertEqual(manager.user, "test@test.com")

if __name__ == "__main__":
    unittest.main()

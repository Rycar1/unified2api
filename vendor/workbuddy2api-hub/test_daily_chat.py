import concurrent.futures
import time
import unittest
from unittest.mock import patch

from wb_accounts import Account


class DailyChatTest(unittest.TestCase):
    def test_scheduler_and_manual_trigger_only_send_once_per_day(self):
        account = Account({"uid": "international", "realm": "intl"})
        calls = []

        def send_once():
            calls.append(True)
            time.sleep(0.05)
            account.last_daily_chat = time.strftime("%Y-%m-%d %H:%M:%S")
            return {"ok": True, "msg": "done"}

        with patch.object(account, "_daily_chat_once", send_once):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: account.daily_chat(), range(2)))

        self.assertEqual(len(calls), 1)
        self.assertTrue(all(result["ok"] for result in results))
        self.assertTrue(any(result.get("already") for result in results))

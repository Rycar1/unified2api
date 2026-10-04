import copy
import unittest

import wb_proxy as proxy


class WorkBuddyParameters(unittest.TestCase):
    def test_off_disables_thinking_with_accepted_upstream_value(self):
        for field in ("reasoning_effort", "reasoningEffort"):
            payload = {"model": "deepseek-v4.1-flash", field: "off",
                       "messages": [{"role": "user", "content": "OK"}]}
            original = copy.deepcopy(payload)
            result = proxy.build_upstream_body(payload)
            self.assertEqual(result["reasoning_effort"], "none")
            self.assertEqual(result["thinking"], {"type": "disabled"})
            self.assertNotIn("reasoningEffort", result)
            self.assertEqual(payload, original)

    def test_explicit_high_is_preserved(self):
        result = proxy.build_upstream_body({"model": "deepseek-v4.1-flash",
                                          "reasoning_effort": "high", "messages": []})
        self.assertEqual(result["reasoning_effort"], "high")

    def test_diagnostics_contain_no_message_or_tool_content(self):
        private = "private-content-that-must-not-be-stored"
        row = proxy.request_parameter_metadata({"reasoning_effort": private,
            "messages": [{"role": "user", "content": private}],
            "tools": [{"name": private}], "api_key": private})
        self.assertNotIn(private, str(row))
        self.assertEqual(row["messages"], 1)
        self.assertEqual(row["reasoning_effort"], "other")


if __name__ == "__main__":
    unittest.main()

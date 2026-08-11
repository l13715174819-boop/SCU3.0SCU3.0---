# -*- coding: utf-8 -*-
"""
tests/test_content_filter.py — 内容过滤测试（原则五）
=====================================================
测试输出必经内容过滤，防止D层数据泄漏。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from guard.content_filter import ContentFilter, filter_output


class TestContentFilterBasic(unittest.TestCase):
    """基础过滤测试"""

    def setUp(self):
        self.cf = ContentFilter()

    def test_empty_text(self):
        """空文本不报错"""
        filtered, warnings = self.cf.filter("")
        self.assertEqual(filtered, "")
        self.assertEqual(warnings, [])

    def test_normal_text_passes(self):
        """正常文本不脱敏"""
        text = "你好，我是标准计算单元2"
        filtered, warnings = self.cf.filter(text)
        self.assertEqual(filtered, text)
        self.assertEqual(warnings, [])


class TestAPIKeyFiltering(unittest.TestCase):
    """API密钥脱敏测试"""

    def setUp(self):
        self.cf = ContentFilter()

    def test_openai_key_redacted(self):
        """OpenAI API key脱敏"""
        text = "key: sk-abcdefghijklmnopqrstuvwxyz123456"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", filtered)

    def test_deepseek_key_redacted(self):
        """DeepSeek API key脱敏"""
        text = "deepseek-abcdefghijklmnopqrstuvwxyz123456"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)

    def test_aws_key_redacted(self):
        """AWS密钥脱敏"""
        text = "AKIAIOSFODNN7EXAMPLE"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)

    def test_github_pat_redacted(self):
        """GitHub PAT脱敏"""
        text = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)


class TestPasswordFiltering(unittest.TestCase):
    """密码脱敏测试"""

    def setUp(self):
        self.cf = ContentFilter()

    def test_password_redacted(self):
        """password脱敏"""
        text = "password=secret123"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)
        self.assertNotIn("secret123", filtered)

    def test_passwd_redacted(self):
        """passwd脱敏"""
        text = "passwd=mypass456"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)


class TestDLayerFieldFiltering(unittest.TestCase):
    """D层字段脱敏测试"""

    def setUp(self):
        self.cf = ContentFilter()

    def test_balance_field_redacted(self):
        """_balance字段脱敏"""
        text = "当前_balance is 100"
        filtered, warnings = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)
        self.assertGreater(len(warnings), 0)

    def test_history_field_redacted(self):
        """_history字段脱敏"""
        text = "_history has 500 records"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)

    def test_hash_chain_redacted(self):
        """hash_chain脱敏"""
        text = "hash_chain=abc123def456"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)

    def test_balance_value_redacted(self):
        """余额数值脱敏"""
        text = "balance=999.5"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)


class TestNetworkFiltering(unittest.TestCase):
    """网络地址脱敏测试"""

    def setUp(self):
        self.cf = ContentFilter()

    def test_internal_ip_redacted(self):
        """内网IP脱敏"""
        text = "server at 10.0.0.1"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED_INTERNAL_IP]", filtered)
        self.assertNotIn("10.0.0.1", filtered)

    def test_private_ip_redacted(self):
        """私有IP脱敏"""
        text = "db at 192.168.1.100"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED_INTERNAL_IP]", filtered)
        self.assertNotIn("192.168.1.100", filtered)

    def test_mongodb_uri_redacted(self):
        """MongoDB连接串脱敏"""
        text = "mongodb://user:pass@host:27017/db"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED]", filtered)


class TestPIIFiltering(unittest.TestCase):
    """PII（个人身份信息）脱敏测试"""

    def setUp(self):
        self.cf = ContentFilter()

    def test_phone_redacted(self):
        """手机号脱敏"""
        text = "电话 13812345678"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED_PHONE]", filtered)
        self.assertNotIn("13812345678", filtered)

    def test_emp_id_redacted(self):
        """员工ID脱敏"""
        text = "EMP123456 提交了请求"
        filtered, _ = self.cf.filter(text)
        self.assertIn("[REDACTED_EMP_ID]", filtered)
        self.assertNotIn("EMP123456", filtered)


class TestDLayerAllowedFields(unittest.TestCase):
    """D层白名单字段测试"""

    def setUp(self):
        self.cf = ContentFilter()

    def test_allowed_fields(self):
        """白名单字段允许输出"""
        for field in ["A1", "A2", "A3", "A4", "D", "M", "W1", "W2"]:
            self.assertTrue(self.cf.is_d_field_allowed(field),
                            f"{field} 应在白名单中")

    def test_forbidden_fields_not_allowed(self):
        """禁止字段不在白名单"""
        for field in ["_balance", "_history", "_hash_chain"]:
            self.assertFalse(self.cf.is_d_field_allowed(field))


class TestFilterStatus(unittest.TestCase):
    """过滤状态测试"""

    def test_status_after_filtering(self):
        """过滤后状态正确"""
        cf = ContentFilter()
        cf.filter("password=secret")
        status = cf.get_status()
        self.assertGreater(status["total_filters"], 0)
        self.assertGreater(status["total_warnings"], 0)
        self.assertGreater(status["patterns_count"], 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)

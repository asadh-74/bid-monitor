import os
import unittest
from unittest.mock import patch
from app.runtime_config import collectors_enabled
from app.collect import validate_environment


class RuntimeTests(unittest.TestCase):
    def test_dashboard_default_disables_scraping(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(collectors_enabled())

    def test_old_interval_cannot_enable_scraping(self):
        with patch.dict(os.environ, {"SCRAPE_INTERVAL_HOURS": "12"}, clear=True):
            self.assertFalse(collectors_enabled())

    def test_local_scraping_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"ENABLE_LOCAL_SCRAPING": "true"}, clear=True):
            self.assertTrue(collectors_enabled())

    def test_missing_worker_secrets_report_names_only(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "GOOGLE_SHEETS_ID"):
                validate_environment()

    def test_invalid_json_does_not_echo_secret(self):
        required = {key: "SECRET_VALUE" for key in (
            "GOOGLE_SHEETS_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "TOTALBIDDATA_USERNAME",
            "TOTALBIDDATA_PASSWORD", "MYVENDORLINK_EMAIL", "MYVENDORLINK_PASSWORD")}
        with patch.dict(os.environ, required, clear=True):
            with self.assertRaises(ValueError) as error:
                validate_environment()
            self.assertNotIn("SECRET_VALUE", str(error.exception))

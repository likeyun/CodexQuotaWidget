from __future__ import annotations

import base64
import concurrent.futures
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quota_widget.backend import (
    Credentials, UsageError, UsageService, auth_file_path, normalize_usage,
    read_credentials, start_api, _https_proxy, _request_usage,
)


def fixture():
    return {
        "plan_type": "plus",
        "rate_limit": {
            "primary_window": {"used_percent": 32, "limit_window_seconds": 18000, "reset_at": 2000000000},
            "secondary_window": {"used_percent": 71, "limit_window_seconds": 604800, "reset_at": 2000200000},
        },
        "credits": {"has_credits": False, "balance": "0"},
    }


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "auth.json"

    def tearDown(self):
        self.temp.cleanup()
        self.environment.stop()

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_codex_token_file_is_read_without_modification(self):
        self.write({"tokens": {"access_token": "private-value", "account_id": "account-test"}})
        before = self.path.read_bytes()
        self.assertEqual(read_credentials(self.path), Credentials("private-value", "account-test"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn("private-value", repr(read_credentials(self.path)))

    def test_environment_token_and_jwt_account_hint(self):
        payload = base64.urlsafe_b64encode(json.dumps({
            "https://api.openai.com/auth": {"chatgpt_account_id": "hint"}
        }).encode()).decode().rstrip("=")
        token = f"header.{payload}.signature"
        with patch.dict(os.environ, {"CHATGPT_ACCESS_TOKEN": "Bearer " + token}):
            self.assertEqual(read_credentials().account_id, "hint")
            self.assertEqual(read_credentials().access_token, token)

    def test_explicit_file_and_environment_path_precedence(self):
        with patch.dict(os.environ, {"CODEX_AUTH_FILE": str(self.path), "CODEX_HOME": "elsewhere"}):
            self.assertEqual(auth_file_path(), self.path)
            self.assertEqual(auth_file_path("specific"), Path("specific"))
        with patch.dict(os.environ, {"CODEX_HOME": str(self.path.parent)}):
            self.assertEqual(auth_file_path(), self.path)

    def test_api_key_login_and_corrupt_file_report_safe_errors(self):
        self.write({"OPENAI_API_KEY": "should-never-leak"})
        with self.assertRaises(UsageError) as raised:
            read_credentials(self.path)
        self.assertEqual(raised.exception.code, "chatgpt_login_required")
        self.assertNotIn("should-never-leak", str(raised.exception))
        self.path.write_text('{"secret": "do-not-echo", bad}', encoding="utf-8")
        with self.assertRaises(UsageError) as raised:
            read_credentials(self.path)
        self.assertEqual(raised.exception.code, "auth_invalid")
        self.assertNotIn("do-not-echo", str(raised.exception))

    def test_missing_account_and_header_injection_are_rejected(self):
        self.write({"tokens": {"access_token": "token"}})
        with self.assertRaises(UsageError) as raised:
            read_credentials(self.path)
        self.assertEqual(raised.exception.code, "auth_account_missing")
        self.write({"tokens": {"access_token": "token\r\nInjected: yes", "account_id": "account"}})
        with self.assertRaises(UsageError) as raised:
            read_credentials(self.path)
        self.assertEqual(raised.exception.code, "auth_invalid")


class NormalizationTests(unittest.TestCase):
    def test_usage_remaining_reset_and_raw(self):
        payload = fixture()
        result = normalize_usage(payload, now=1999996400)
        primary = result["windows"]["primary"]
        self.assertEqual(primary["remaining_percent"], 68)
        self.assertEqual(primary["reset_after_seconds"], 3600)
        self.assertEqual(primary["reset_at_beijing"], "2033-05-18 11:33:20")
        self.assertEqual(result["raw"], payload)
        self.assertEqual(result["plan_type"], "plus")

    def test_unknown_fields_never_become_full_allowance(self):
        result = normalize_usage({"rate_limit": {"primary_window": {"used_percent": "nan"}}}, now=100)
        primary = result["windows"]["primary"]
        self.assertIsNone(primary["remaining_percent"])
        self.assertIsNone(primary["reset_at"])
        self.assertIsNone(result["windows"]["secondary"])
        for value in (None, True, "broken", float("inf")):
            result = normalize_usage({"rate_limit": {"primary_window": {"used_percent": value}}})
            self.assertIsNone(result["windows"]["primary"]["remaining_percent"])
            json.dumps(result, allow_nan=False)

    def test_relative_reset_and_percent_bounds(self):
        result = normalize_usage({"rate_limit": {"primary_window": {
            "used_percent": 130, "reset_after_seconds": 90,
        }}}, now=100)
        self.assertEqual(result["windows"]["primary"]["remaining_percent"], 0)
        self.assertEqual(result["windows"]["primary"]["reset_at"], 190)

    def test_raw_token_fields_and_echoes_are_redacted(self):
        result = normalize_usage({"plan_type": "plus", "access_token": "abc", "nested": {
            "refresh_token": "other", "echo": "Bearer private-token",
        }}, now=100, access_token="private-token")
        encoded = json.dumps(result)
        self.assertNotIn("private-token", encoded)
        self.assertNotIn('"abc"', encoded)
        self.assertNotIn('"other"', encoded)


class RequestTests(unittest.TestCase):
    def request(self, status=200, body=None, error=None):
        if body is None:
            body = json.dumps(fixture()).encode()
        self.mock_get = unittest.mock.Mock(
            return_value=SimpleNamespace(status_code=status, content=body), side_effect=error,
        )
        module = SimpleNamespace(requests=SimpleNamespace(get=self.mock_get))
        with patch.dict("sys.modules", {"curl_cffi": module}):
            with patch("quota_widget.backend._https_proxy", return_value="http://127.0.0.1:10808"):
                return _request_usage(Credentials("fake-secret", "account"))

    def test_transport_uses_system_proxy_and_never_follows_redirect(self):
        self.assertEqual(self.request()["plan_type"], "plus")
        kwargs = self.mock_get.call_args.kwargs
        self.assertEqual(kwargs["proxy"], "http://127.0.0.1:10808")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 15)

    def test_status_and_transport_errors_are_safe(self):
        for status, code in ((401, "unauthorized"), (403, "forbidden"), (429, "rate_limited"), (302, "upstream_error")):
            with self.subTest(status=status), self.assertRaises(UsageError) as raised:
                self.request(status=status, body=b"fake-secret")
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("fake-secret", str(raised.exception))
        with self.assertRaises(UsageError) as raised:
            self.request(error=RuntimeError("Authorization: Bearer fake-secret"))
        self.assertEqual(raised.exception.code, "network_error")
        self.assertNotIn("fake-secret", str(raised.exception))

    def test_invalid_json_and_nonfinite_numbers_are_rejected(self):
        for body in (b"<html>not JSON</html>", b'{"plan_type":"plus","n":NaN}', b'{"plan_type":"plus","n":1e999}'):
            with self.subTest(body=body), self.assertRaises(UsageError) as raised:
                self.request(body=body)
            self.assertEqual(raised.exception.code, "upstream_invalid")

    def test_windows_proxy_detection_and_bypass(self):
        with patch("quota_widget.backend.urllib.request.getproxies", return_value={"https": "http://local-proxy:10808"}):
            with patch("quota_widget.backend.urllib.request.proxy_bypass", return_value=False):
                self.assertEqual(_https_proxy(), "http://local-proxy:10808")
            with patch("quota_widget.backend.urllib.request.proxy_bypass", return_value=True):
                self.assertEqual(_https_proxy(), "")


class CacheTests(unittest.TestCase):
    def test_cache_forced_refresh_throttle_and_reread_credentials(self):
        service = UsageService()
        with patch("quota_widget.backend.read_credentials", return_value=Credentials("fake", "account")) as auth:
            with patch("quota_widget.backend._request_usage", return_value=fixture()) as request:
                with patch("quota_widget.backend.time.monotonic", return_value=100):
                    first = service.get_usage()
                    first["raw"]["plan_type"] = "mutated"
                    self.assertEqual(service.get_usage(force=True)["plan_type"], "plus")
                with patch("quota_widget.backend.time.monotonic", return_value=111):
                    service.get_usage(force=True)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(auth.call_count, 2)

    def test_last_success_is_preserved_on_failed_refresh(self):
        service = UsageService()
        with patch("quota_widget.backend.read_credentials", return_value=Credentials("fake", "account")):
            with patch("quota_widget.backend._request_usage", side_effect=[fixture(), UsageError("unauthorized", "请重新登录")]):
                with patch("quota_widget.backend.time.monotonic", return_value=100):
                    original = service.get_usage()
                with patch("quota_widget.backend.time.monotonic", return_value=200):
                    stale = service.get_usage()
        self.assertFalse(stale["ok"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["windows"], original["windows"])
        self.assertEqual(stale["fetched_at"], original["fetched_at"])
        self.assertEqual(stale["error"]["code"], "unauthorized")

    def test_initial_failure_has_no_fabricated_usage(self):
        with patch("quota_widget.backend.read_credentials", side_effect=UsageError("auth_missing", "未登录")):
            result = UsageService().get_usage()
        self.assertFalse(result["ok"])
        self.assertFalse(result["stale"])
        self.assertIsNone(result["fetched_at"])
        self.assertEqual(result["windows"], {"primary": None, "secondary": None})

    def test_parallel_requests_only_fetch_once(self):
        service = UsageService()
        counter = []

        def request(credentials):
            counter.append(1)
            time.sleep(0.025)
            return fixture()

        with patch("quota_widget.backend.read_credentials", return_value=Credentials("fake", "account")):
            with patch("quota_widget.backend._request_usage", side_effect=request):
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    results = list(executor.map(lambda _: service.get_usage(), range(8)))
        self.assertEqual(len(counter), 1)
        self.assertTrue(all(result["ok"] for result in results))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = UsageService()
        self.service.get_usage = unittest.mock.Mock(return_value=normalize_usage(fixture()))
        self.server, self.thread = start_api(self.service, port=0)
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, headers=None, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        status, response_headers = response.status, dict(response.getheaders())
        data = json.loads(response.read())
        connection.close()
        return status, response_headers, data

    def test_health_and_usage_are_json_and_no_cors(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.request("/api/health")[0], 200)
        status, headers, result = self.request("/api/usage?force=true")
        self.assertEqual(status, 200)
        self.assertEqual(result["windows"]["primary"]["remaining_percent"], 68)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.service.get_usage.assert_called_once_with()

    def test_nonlocal_host_and_cross_origin_are_blocked(self):
        for headers in (
            {"Host": f"attacker.example:{self.port}"},
            {"Origin": "https://attacker.example"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            status, _, result = self.request("/api/usage", headers)
            self.assertEqual(status, 403)
            self.assertFalse(result["ok"])
        self.service.get_usage.assert_not_called()

    def test_loopback_origin_and_method_handling(self):
        status, _, _ = self.request("/api/usage", {"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/usage", method="POST")[0], 405)
        self.assertEqual(self.request("/does-not-exist")[0], 404)

    def test_second_listener_cannot_claim_same_port(self):
        with self.assertRaises(OSError):
            start_api(UsageService(), port=self.port)


if __name__ == "__main__":
    unittest.main()

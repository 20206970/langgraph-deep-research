"""Transient provider errors must back off before retry instead of hammering the same rate window."""

from src.graph.research import _retry_backoff_seconds


def test_rate_limit_error_backs_off_exponentially():
    error = "Error code: 429 - {'error': {'code': '1302', 'message': '您的账户已达到速率限制，请您控制请求频率'}}"

    assert _retry_backoff_seconds(error, 1) == 5.0
    assert _retry_backoff_seconds(error, 2) == 15.0
    assert _retry_backoff_seconds(error, 3) == 45.0


def test_backoff_is_capped():
    assert _retry_backoff_seconds("429 too many requests", 9) == 45.0


def test_network_errors_are_treated_as_transient():
    assert _retry_backoff_seconds("Connection error: timed out", 1) == 5.0


def test_parse_and_auth_errors_do_not_wait():
    assert _retry_backoff_seconds("Invalid JSON: missing 'summary' key", 1) == 0.0
    assert _retry_backoff_seconds("Error code: 401 - invalid api key", 1) == 0.0
    assert _retry_backoff_seconds("", 1) == 0.0

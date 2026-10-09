import socket
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from server.custom_endpoint import resolve_custom_endpoint, validate_custom_endpoint


def dns_answers(*addresses):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, 443, 0, 0) if ":" in address else (address, 443),
        )
        for address in addresses
    ]


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: dns_answers("8.8.8.8"))


def test_normalizes_public_endpoint_and_returns_addresses(monkeypatch):
    calls = []

    def resolve(*args, **kwargs):
        calls.append(args)
        return dns_answers("8.8.8.8", "2606:4700:4700::1111", "8.8.8.8")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    endpoint, addresses = resolve_custom_endpoint("https://API.Example.COM:8443/v1/")
    assert endpoint == "https://api.example.com:8443/v1"
    assert addresses == ["8.8.8.8", "2606:4700:4700::1111"]
    assert calls[0][0:2] == ("api.example.com", 8443)
    assert validate_custom_endpoint("https://api.example.com/v1/") == "https://api.example.com/v1"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.example.com/v1",
        "ftp://api.example.com/v1",
        "https://",
        "https://name:password@api.example.com/v1",
        "https://name@api.example.com/v1",
        "https://api.example.com/v1?key=value",
        "https://api.example.com/v1?",
        "https://api.example.com/v1#fragment",
        "https://api.example.com/v1#",
        " https://api.example.com/v1",
        "https://api.example.com/space here",
        "https://api.example.com/\npath",
        "https://api.example.com\\@localhost/v1",
        "https://api.example.com:0/v1",
        "https://api.example.com:65536/v1",
        "https://%31%32%37.0.0.1/v1",
        "https://localhost/v1",
        "https://localhost./v1",
        "https://app.localhost/v1",
        "https://host.local/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.1/v1",
        "https://172.16.0.1/v1",
        "https://192.168.0.1/v1",
        "https://169.254.169.254/v1",
        "https://100.64.0.1/v1",
        "https://0.0.0.0/v1",
        "https://192.0.2.1/v1",
        "https://224.0.0.1/v1",
        "https://240.0.0.1/v1",
        "https://[::1]/v1",
        "https://[fc00::1]/v1",
        "https://[fe80::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "https://[::ffff:8.8.8.8]/v1",
        "https://[64:ff9b::7f00:1]/v1",
        "https://[2002:7f00:0001::1]/v1",
        "https://[fe80::1%25eth0]/v1",
        "https://[::]/v1",
        "https://[ff02::1]/v1",
        "https://[2001:db8::1]/v1",
        "https://api.example.com/" + "a" * 2048,
    ],
)
def test_rejects_invalid_or_nonpublic_url(endpoint):
    with pytest.raises(HTTPException) as error:
        validate_custom_endpoint(endpoint)
    assert error.value.status_code == 422
    assert "password" not in error.value.detail


@pytest.mark.parametrize("private_address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fc00::2"])
def test_rejects_any_private_dns_answer(monkeypatch, private_address):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: dns_answers("8.8.8.8", private_address)
    )
    with pytest.raises(HTTPException):
        resolve_custom_endpoint("https://api.example.com/v1")


def test_rejects_empty_dns_results(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [])
    with pytest.raises(HTTPException):
        resolve_custom_endpoint("https://api.example.com/v1")


def test_dns_failure_has_safe_error(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("sensitive resolver internals")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(HTTPException) as error:
        resolve_custom_endpoint("https://api.example.com/v1")
    assert "sensitive" not in error.value.detail


def test_public_ip_literals_do_not_use_dns(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("IP literals must bypass DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    assert resolve_custom_endpoint("https://8.8.8.8/v1") == ("https://8.8.8.8/v1", ["8.8.8.8"])
    assert resolve_custom_endpoint("https://[2606:4700:4700::1111]/v1") == (
        "https://[2606:4700:4700::1111]/v1",
        ["2606:4700:4700::1111"],
    )


def test_ipv4_numeric_alias_is_rejected_when_dns_returns_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: dns_answers("127.0.0.1"))
    with pytest.raises(HTTPException):
        resolve_custom_endpoint("https://2130706433/v1")


def test_custom_migration_preserves_existing_rows():
    migration = Path(__file__).parents[1] / "server/migrations/004_custom_connections.sql"
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE TABLE model_connections (id TEXT PRIMARY KEY)")
        database.execute("INSERT INTO model_connections VALUES ('existing')")
        database.commit()
        database.executescript(migration.read_text(encoding="utf-8"))
        row = database.execute(
            "SELECT id,base_url,model_id,model_name,supports_image FROM model_connections"
        ).fetchone()
        assert row == ("existing", "", "", "", 0)
        assert database.execute("PRAGMA user_version").fetchone()[0] == 4

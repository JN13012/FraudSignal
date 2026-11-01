import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail any test that tries to reach the network."""

    def refuse(*args, **kwargs):
        raise RuntimeError("Network access is forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

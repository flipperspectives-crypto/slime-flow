"""HTTP tests against a real threaded server on a random port."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import FakeClock
from slimeflow import server
from slimeflow.agent_guard import AgentGuard
from slimeflow.billing import Billing

ADMIN = "test-admin-token"


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "guard", AgentGuard(clock=FakeClock()))
    monkeypatch.setattr(server, "billing", Billing(path=tmp_path / "b.json", enabled=False))
    server.configure(admin_token=ADMIN)
    httpd = server.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    server.configure(admin_token=None)


def call(base, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"{}")


def report(base, agent, kind, confirmed=False, **extra):
    body = {"agent_id": agent, "kind": kind, "user_confirmed": confirmed, **extra}
    return call(base, "POST", "/agents/report", body)


def test_second_unconfirmed_delete_freezes_over_http(srv):
    s1, _, r1 = report(srv, "bot", "delete", tool="rm", detail="1")
    s2, _, r2 = report(srv, "bot", "delete", tool="rm", detail="2")
    assert (s1, r1["allowed"], r1["anomaly"]) == (200, True, 0.38)
    assert (s2, r2["allowed"], r2["quarantined"]) == (200, False, True)
    _, _, gate = call(srv, "GET", "/agents/bot/check")
    assert gate["allowed"] is False


def test_release_requires_admin_token(srv):
    report(srv, "bot", "delete", detail="1")
    report(srv, "bot", "delete", detail="2")
    status, _, body = call(srv, "POST", "/agents/bot/release", raw=b"",
                           headers={"Content-Type": "text/plain", "Origin": "https://evil.example"})
    assert status == 403
    status, _, _ = call(srv, "POST", "/agents/bot/release", {}, headers={"X-Slime-Admin": "wrong"})
    assert status == 403
    assert call(srv, "GET", "/agents/bot/check")[2]["allowed"] is False
    status, _, body = call(srv, "POST", "/agents/bot/release", {"by": "lauren"},
                           headers={"X-Slime-Admin": ADMIN})
    assert status == 200 and body["released"] is True
    assert call(srv, "GET", "/agents/bot/check")[2]["allowed"] is True


def test_admin_refused_when_no_token_configured(srv):
    server.configure(admin_token=None)
    status, _, _ = call(srv, "POST", "/agents/bot/quarantine", {}, headers={"X-Slime-Admin": ""})
    assert status == 403


def test_manual_quarantine_requires_admin(srv):
    assert call(srv, "POST", "/agents/bot/quarantine", {"reason": "x"})[0] == 403
    status, _, body = call(srv, "POST", "/agents/bot/quarantine", {"reason": "x"},
                           headers={"X-Slime-Admin": ADMIN})
    assert status == 200 and body["quarantined"] is True


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_user_confirmed_must_be_json_bool(srv, value):
    status, _, body = call(srv, "POST", "/agents/report",
                           {"agent_id": "bot", "kind": "delete", "user_confirmed": value})
    assert status == 400
    assert "user_confirmed" in body["error"]


def test_kind_must_be_string(srv):
    status, _, _ = call(srv, "POST", "/agents/report", {"agent_id": "bot", "kind": 5})
    assert status == 400


def test_cors_only_on_sim_endpoints(srv):
    _, h_status, _ = call(srv, "GET", "/status")
    _, h_agents, _ = call(srv, "GET", "/agents")
    assert h_status.get("Access-Control-Allow-Origin") == "*"
    assert "Access-Control-Allow-Origin" not in h_agents


def test_secrets_not_served_back(srv):
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    report(srv, "bot", "network", confirmed=True, detail=f"Bearer {token}")
    _, _, body = call(srv, "GET", "/agents")
    assert token not in json.dumps(body)


def test_percent_encoded_agent_id(srv):
    report(srv, "my bot", "delete", detail="1")
    report(srv, "my bot", "delete", detail="2")
    _, _, gate = call(srv, "GET", "/agents/my%20bot/check")
    assert gate["allowed"] is False


def raw_request(base, payload: bytes) -> bytes:
    host, port = base.replace("http://", "").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(payload)
        chunks = []
        while True:
            try:
                data = s.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def test_bad_content_length_gets_400(srv):
    resp = raw_request(srv, b"POST /agents/report HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n")
    assert resp.startswith(b"HTTP/1.0 400")


def test_oversized_body_gets_413(srv):
    resp = raw_request(srv, b"POST /agents/report HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999\r\n\r\n")
    assert resp.startswith(b"HTTP/1.0 413")


def test_slow_client_does_not_block_others(srv):
    host, port = srv.replace("http://", "").split(":")
    stalled = socket.create_connection((host, int(port)))
    stalled.sendall(b"POST /agents/report HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{")
    try:
        t0 = time.time()
        status, _, body = call(srv, "GET", "/ping")
        assert status == 200 and body == {"status": "ok"}
        assert time.time() - t0 < 2
    finally:
        stalled.close()


def test_billing_endpoints_need_admin_and_finite_amounts(srv, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "billing", Billing(path=tmp_path / "paid.json", enabled=True))
    assert call(srv, "POST", "/billing/create_key", {"initial_usd": 1e9})[0] == 403
    status, _, key = call(srv, "POST", "/billing/create_key", {"initial_usd": 0.002},
                          headers={"X-Slime-Admin": ADMIN})
    assert status == 200
    secret = key["secret"]
    bad = call(srv, "POST", "/billing/topup", raw=b'{"amount_usd": NaN}',
               headers={"X-Slime-Key": secret, "X-Slime-Admin": ADMIN})
    assert bad[0] == 400
    assert call(srv, "POST", "/billing/topup", {"amount_usd": 5}, headers={"X-Slime-Key": secret})[0] == 403
    # no key -> 402 on report
    assert report(srv, "bot", "message")[0] == 402
    ok = call(srv, "POST", "/agents/report", {"agent_id": "bot", "kind": "message"},
              headers={"X-Slime-Key": secret})
    assert ok[0] == 200 and ok[2]["billing"]["charged_usd"] == 0.001


def test_unknown_path_is_json_404(srv):
    status, headers, body = call(srv, "GET", "/nope")
    assert status == 404 and body == {"error": "not found"}
    assert headers["Content-Type"] == "application/json"


def test_frame_is_normalized(srv):
    call(srv, "GET", "/reset")
    for _ in range(3):
        _, _, frame = call(srv, "GET", "/frame")
    assert len(frame["grid"]) == frame["w"] * frame["h"]
    assert all(0.0 <= v <= 1.0 for v in frame["grid"])
    assert all(0.0 <= v <= 1.0 for v in frame["rogue_grid"])

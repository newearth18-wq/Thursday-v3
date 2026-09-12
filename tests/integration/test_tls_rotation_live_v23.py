"""A node follows a real rotation, over a real TLS handshake (§117, ADR 0071).

ADR 0071 shipped with this named in §23 as the thing it had not done:

> *Not exercised:* a real node reconnecting through a real TLS handshake to a core that has
> actually rotated.

Each piece was tested — the statement against RSA, ECDSA and Ed25519 keys, the endpoint
against the running application, the node's decision against a forged chain — but the loop
that joins them ran with the fetch replaced, and a mechanism whose parts all pass separately
is exactly the kind that fails at the seams.

So this runs the real thing: **uvicorn serving the real FastAPI application over TLS**, stopped
and restarted with a certificate on a different key — which is what a rotation *is* — and the
node's own `run_forever` meeting it with no help. The pin check, the mismatch, the fetch and
the reconnection all happen on sockets.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn
from thursday_api.app import create_app
from thursday_devices.fake import FakeAdapter
from thursday_devices.node.executor import NodeExecutor
from thursday_security.pinning import PinMismatch

from apps.node.__main__ import CoreRefused, NodeClient, NodeIdentity
from apps.server.__main__ import sign_handover
from tests.integration.test_pinning import make_certificate


class TlsCore:
    """The real application, served over TLS, restartable with a different certificate.

    A thread rather than a task: `uvicorn.Server.run` makes its own event loop, and the test's
    node has to be driving the one in this thread while the core answers on that one — which
    is how the two actually run.
    """

    def __init__(self, app, port: int, certfile: Path, keyfile: Path) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                ssl_certfile=str(certfile),
                ssl_keyfile=str(keyfile),
                log_level="error",
                # The container is injected directly, as every other API test does it.
                lifespan="off",
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> TlsCore:
        self._thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("the core did not start listening")

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=20)


def free_port() -> int:
    """A port to bind twice: once before the rotation and once after.

    Chosen by binding and releasing, so there is a window in which something else could take
    it. Accepted, because the alternative — handing the second server the first one's socket —
    would stop this being two separate servers, which is the thing the test is about.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def rotation(tmp_path, settings, container):
    """A core on one key, a certificate on another, and a node pinned to the first."""
    app = create_app(settings, container=container)
    app.state.container = container

    old_cert, old_key, old_pin, _ = make_certificate(tmp_path, "retiring")
    new_cert, new_key, new_pin, _ = make_certificate(tmp_path, "incoming")
    port = free_port()
    core_url = f"wss://127.0.0.1:{port}/api/v1/device"

    identity = NodeIdentity(tmp_path / "node.json")
    identity.record_pairing(
        device_id=str(uuid.uuid4()),
        fingerprint=identity.fingerprint,
        core=core_url,
        pin=old_pin,
    )
    client = NodeClient(
        core_url=core_url,
        name="Office-PC",
        identity=identity,
        executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
        token="shared-enrolment-token",
    )
    return {
        "app": app,
        "port": port,
        "client": client,
        "identity": identity,
        "old": (old_cert, old_key, old_pin),
        "new": (new_cert, new_key, new_pin),
        "path": tmp_path / "node.json",
    }


async def reached_the_hello(client: NodeClient) -> None:
    """Open a real session and require that it got past the pin.

    `CoreRefused` is the success condition, which reads oddly and is exactly right: this node
    is not registered with this core, so a refusal at the HELLO means the TLS handshake was
    accepted, `check_peer` was satisfied, and the node sent its identity. Asserting on a
    later, unrelated failure is how a pin check silently stops running.
    """
    with pytest.raises(CoreRefused):
        await client._session()


# ------------------------------------------------------------------------ the real rotation


async def test_a_node_follows_a_real_rotation_with_nobody_helping_it(rotation, settings):
    """The claim §23 could not make, end to end and unassisted.

    `run_forever` is what runs on a real machine, so it is what runs here: nothing in this
    test calls `_follow_handover`, and nothing replaces the fetch.
    """
    client, identity = rotation["client"], rotation["identity"]
    _, old_key, old_pin = rotation["old"]
    new_cert, new_key, new_pin = rotation["new"]

    with TlsCore(rotation["app"], rotation["port"], rotation["old"][0], old_key):
        await reached_the_hello(client)

    # The rotation itself: the same core, the same port, a certificate on a different key.
    assert (
        sign_handover(
            retiring_key=old_key,
            incoming_cert=new_cert,
            compromised=False,
            data_dir=settings.data_dir,
        )
        == 0
    )

    with TlsCore(rotation["app"], rotation["port"], new_cert, new_key):
        loop = asyncio.create_task(client.run_forever())
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                if NodeIdentity(rotation["path"]).core_pin.value == new_pin:
                    break
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)

        assert identity.core_pin.value == new_pin, "the node never followed the hand-over"
        assert identity.core_pin.value != old_pin

        # And the new pin is real rather than merely written down: another handshake, checked
        # against it. A node that re-pinned to a key the core is not serving would stop here.
        await reached_the_hello(client)


async def test_the_mismatch_this_recovers_from_is_a_real_handshake_failing(rotation):
    """Without the hand-over published, a rotated core is still refused — on a real socket.

    The negative half of the test above. If this passed for the wrong reason — a connection
    error, a closed port — the recovery test would prove nothing, so the mismatch is asserted
    by type rather than by "something went wrong".
    """
    client = rotation["client"]
    _, old_key, _ = rotation["old"]
    new_cert, new_key, _ = rotation["new"]

    with TlsCore(rotation["app"], rotation["port"], rotation["old"][0], old_key):
        await reached_the_hello(client)

    with (
        TlsCore(rotation["app"], rotation["port"], new_cert, new_key),
        pytest.raises(PinMismatch),
    ):
        await client._session()


async def test_the_chain_is_fetched_over_the_rotated_core_itself(rotation, settings):
    """The fetch that recovers the connection goes to the host the node cannot yet trust.

    That is the awkward-looking part of ADR 0071 and the part that has to work: an
    unauthenticated request, over a certificate chain nothing validated, to a core presenting
    a key this node refuses. The signature is what makes it safe, so the transport is allowed
    to be exactly as untrusted as it is.
    """
    from apps.node.__main__ import api_base, published_handovers

    client = rotation["client"]
    _, old_key, old_pin = rotation["old"]
    new_cert, new_key, new_pin = rotation["new"]
    sign_handover(
        retiring_key=old_key,
        incoming_cert=new_cert,
        compromised=False,
        data_dir=settings.data_dir,
    )

    with TlsCore(rotation["app"], rotation["port"], new_cert, new_key):
        chain = await asyncio.to_thread(published_handovers, api_base(client.core_url))

    assert [link.retiring_pin for link in chain] == [old_pin]
    assert [link.next_pin for link in chain] == [new_pin]


# --------------------------------------------------------------- what the loop does with it

# The tests above prove the mechanism on sockets and take three seconds to do it. These drive
# `run_forever`'s branches directly, where the question is only which path a failure takes.


def instrumented(client: NodeClient, outcomes: list):
    """Give `run_forever` a scripted sequence of session outcomes, and watch where each goes.

    `_back_off` is replaced rather than timed: the assertion is about which branch a failure
    reaches, and sleeping two seconds to observe that would be measuring the clock instead.
    """
    backed_off: list[Exception] = []
    attempts: list[int] = []

    async def session() -> None:
        attempts.append(1)
        outcome = outcomes[min(len(attempts) - 1, len(outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        raise asyncio.CancelledError

    async def back_off(exc: Exception, delay: float) -> float:
        backed_off.append(exc)
        raise asyncio.CancelledError

    client._session = session
    client._back_off = back_off
    return attempts, backed_off


async def run_until_stopped(client: NodeClient) -> None:
    with pytest.raises(asyncio.CancelledError):
        await client.run_forever()


async def test_a_mismatch_that_was_followed_reconnects_at_once(rotation, monkeypatch):
    """Following a hand-over is not a failure, so it must not be paid for with a backoff.

    A node that re-pinned correctly and then waited two seconds — then four, then eight — to
    use the key it just verified would make an ordinary certificate rotation look like an
    outage.
    """
    client = rotation["client"]
    attempts, backed_off = instrumented(client, [PinMismatch("different key"), None])
    monkeypatch.setattr(client, "_follow_handover", lambda: _async(True))

    await run_until_stopped(client)

    assert len(attempts) == 2, "the node did not retry immediately after following"
    assert backed_off == []


async def test_a_mismatch_that_could_not_be_followed_backs_off(rotation, monkeypatch):
    """The case that stays dangerous. No signed path to the key being presented means this is
    what pinning is for, and the node waits rather than trying harder."""
    client = rotation["client"]
    mismatch = PinMismatch("different key")
    _, backed_off = instrumented(client, [mismatch])
    monkeypatch.setattr(client, "_follow_handover", lambda: _async(False))

    await run_until_stopped(client)

    assert backed_off == [mismatch]
    assert client.last_error == "different key"


async def test_a_refused_hello_backs_off_instead_of_taking_the_node_down(rotation):
    """It used to leave `run_forever` altogether.

    `main` gathers this loop with the node's diagnostics server, so an exception escaping here
    ended the process — including `GET /health`, whose whole job is to report `last_error`,
    which is to say the one place the owner could have learned *why* the node was refused.
    The refusal destroyed the explanation for the refusal.

    A refusal is an ordinary state of the world: a pairing code nobody has confirmed yet, an
    identity that was revoked, a core still starting up. The repo's own rule for close codes
    already said where it belongs — expiry after a healthy session reconnects at once, and
    everything else backs off.
    """
    client = rotation["client"]
    refusal = CoreRefused("core refused the connection: device authentication failed")
    _, backed_off = instrumented(client, [refusal])

    await run_until_stopped(client)

    assert backed_off == [refusal]


async def test_a_defect_inside_a_session_still_crashes_loudly(rotation):
    """Which is why the refusal got its own exception type rather than a caught `RuntimeError`.

    Catching the broad one would have swallowed programming errors into a two-second retry
    loop, where they would look like a flaky network for as long as anybody cared to watch.
    """
    client = rotation["client"]
    _, backed_off = instrumented(client, [RuntimeError("attribute error, really")])

    with pytest.raises(RuntimeError, match="attribute error"):
        await client.run_forever()

    assert backed_off == []


async def _async(value):
    return value

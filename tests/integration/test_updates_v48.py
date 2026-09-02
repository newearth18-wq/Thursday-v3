"""Software update (§120, Sprint 48).

An updater replaces the code that enforces every other rule, so these tests are written from
the attacker's side: given control of the manifest, of the network, or of what a model says,
what can be made to run?

The specification's rule — *never execute an arbitrary update URL supplied by a model* —
is the thing being proven, and the interesting half is that it is proven **structurally**.
There is no parameter anywhere in this component that a URL arrives in, so the test that
matters most is an inspection of the signatures rather than an attempt to exploit them.
"""

from __future__ import annotations

import hashlib
import inspect
import json

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_core.updates import (
    LocalReleaseSource,
    PinnedHttpReleaseSource,
    Release,
    UpdateError,
    UpdateService,
    parse_version,
    signing_payload,
)
from thursday_security.keys import generate_keypair

BASE = "https://updates.thursday.test/stable"
ARTIFACT = b"a new build of thursday, pretend this is a tarball"
DIGEST = hashlib.sha256(ARTIFACT).hexdigest()


@pytest.fixture
def keypair():
    return generate_keypair()


def release_row(private, *, version="0.3.0", url=None, sha256=None, **over) -> dict:
    url = url or f"{BASE}/thursday-{version}.tar.gz"
    sha256 = sha256 or DIGEST
    row = {
        "version": version,
        "url": url,
        "sha256": sha256,
        "signature": private.sign(signing_payload(version=version, sha256=sha256, url=url)),
        "notes": "faster and fewer bugs",
    }
    row.update(over)
    return row


def service(tmp_path, keypair, rows, *, current="0.2.0", base=BASE, install=None) -> UpdateService:
    _, public = keypair
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"releases": rows}))
    return UpdateService(
        current_version=current,
        source=LocalReleaseSource(path=manifest, base_url=base),
        signing_key=public.encoded,
        install=install if install is not None else (lambda release, artifact: None),
    )


# --------------------------------------------------------------------------- §120


def test_no_function_here_accepts_a_url():
    """§120, proven the only way it can be: by the shape of the API.

    A rule enforced by a check is a rule somebody can forget to call. A rule enforced by
    there being no parameter is one nobody can reach. `apply` takes a `Release` obtained
    from the configured source and the bytes it hashes to — never a location.
    """
    for method in (UpdateService.apply, UpdateService.verify, UpdateService.check):
        parameters = set(inspect.signature(method).parameters)
        assert not parameters & {"url", "uri", "location", "source_url", "download_url"}, method


def test_the_channel_is_configuration_and_has_no_setter():
    """Where this deployment looks for updates is set before the process starts. There is no
    method that changes it, so nothing running — a model, a document, an API caller — can."""
    setters = [
        name
        for name in dir(UpdateService)
        if not name.startswith("_")
        and any(word in name for word in ("set_", "channel", "configure", "source"))
    ]
    assert setters == []


def test_a_manifest_cannot_redirect_the_download_off_the_pinned_host(tmp_path, keypair):
    """The check that is easy to skip, and the one that matters most.

    Pinning where the *manifest* comes from and then trusting the URLs inside it hands the
    download to whoever controls the manifest — and a correct signature over "fetch this from
    somewhere else" is a correct signature. This release is signed properly and still refused.
    """
    private, _ = keypair
    evil = release_row(private, url="https://attacker.example/thursday-0.3.0.tar.gz")
    updater = service(tmp_path, keypair, [evil])

    with pytest.raises(UpdateError, match="outside this deployment's update channel"):
        updater.latest()


def test_a_prefix_that_merely_starts_the_same_is_not_inside_the_channel(tmp_path, keypair):
    """`https://updates.thursday.test.attacker.example/...` starts with the base string and is
    a different host. The trailing separator is what makes the check mean what it looks like."""
    private, _ = keypair
    row = release_row(private, url=f"{BASE}.attacker.example/thursday-0.3.0.tar.gz")
    with pytest.raises(UpdateError, match="outside"):
        service(tmp_path, keypair, [row]).latest()


def test_an_offline_deployment_will_not_fetch_from_the_internet(tmp_path, keypair):
    """An air-gapped install that downloads from the internet is not air-gapped."""
    private, _ = keypair
    row = release_row(private, url="https://updates.thursday.test/stable/x.tar.gz")
    updater = service(tmp_path, keypair, [row], base="")
    with pytest.raises(UpdateError, match="no configured update host"):
        updater.latest()


def test_an_http_channel_is_refused_outright():
    """The signature would still be checked, but plain HTTP lets anyone on the path serve an
    older signed release, and a downgrade is a real attack."""
    with pytest.raises(UpdateError, match="must be https"):
        PinnedHttpReleaseSource(base_url="http://updates.thursday.test/stable")


# --------------------------------------------------------------------------- verification


def test_an_unsigned_release_is_refused(tmp_path, keypair):
    private, _ = keypair
    row = release_row(private)
    row["signature"] = ""
    updater = service(tmp_path, keypair, [row])
    with pytest.raises(UpdateError, match="not signed by the key"):
        updater.verify(updater.latest(), ARTIFACT)


def test_a_release_signed_by_the_wrong_key_is_refused(tmp_path, keypair):
    other, _ = generate_keypair()
    updater = service(tmp_path, keypair, [release_row(other)])
    with pytest.raises(UpdateError, match="not signed by the key"):
        updater.verify(updater.latest(), ARTIFACT)


def test_bytes_that_do_not_match_the_manifest_are_refused(tmp_path, keypair):
    private, _ = keypair
    updater = service(tmp_path, keypair, [release_row(private)])
    with pytest.raises(UpdateError, match="not the one the manifest describes"):
        updater.verify(updater.latest(), b"different bytes entirely")


def test_a_signature_cannot_be_moved_between_releases(tmp_path, keypair):
    """Signed over the version, the digest and the URL together. Signing the digest alone
    would let a signature be lifted onto another release."""
    private, _ = keypair
    genuine = release_row(private, version="0.3.0")
    forged = release_row(private, version="0.4.0")
    forged["signature"] = genuine["signature"]

    updater = service(tmp_path, keypair, [forged])
    with pytest.raises(UpdateError, match="not signed by the key"):
        updater.verify(updater.latest(), ARTIFACT)


def test_a_deployment_with_no_signing_key_refuses_everything(tmp_path, keypair):
    """Fail closed. Guessing that an unconfigured key means "accept whatever arrives" is how
    an updater becomes the attack."""
    private, _ = keypair
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"releases": [release_row(private)]}))
    updater = UpdateService(
        current_version="0.2.0",
        source=LocalReleaseSource(path=manifest, base_url=BASE),
        signing_key="",
    )
    with pytest.raises(UpdateError, match="no release signing key"):
        updater.verify(updater.latest(), ARTIFACT)


# --------------------------------------------------------------------------- applying


def test_applying_needs_an_explicit_confirmation(tmp_path, keypair):
    private, _ = keypair
    updater = service(tmp_path, keypair, [release_row(private)])
    with pytest.raises(UpdateError, match="explicit approval"):
        updater.apply(updater.latest(), ARTIFACT)


def test_an_update_is_never_something_thursday_does_to_itself():
    """ADR 0028: a repair may restore a capability, never widen one, and installing new code
    is the widest change there is."""
    from thursday_core.recovery import NEVER_AUTOMATIC, SelfRecovery, is_self_repairable

    assert "install_component" in NEVER_AUTOMATIC
    assert not is_self_repairable("install_component")
    with pytest.raises(PermissionError):
        SelfRecovery().register("install_component", lambda: None)


def test_a_downgrade_is_refused_unless_it_is_asked_for(tmp_path, keypair):
    """A signed *old* release stays correctly signed for ever, so the version with the bug
    that was fixed is always available to an attacker who can serve a manifest."""
    private, _ = keypair
    old = Release(
        version="0.1.0",
        url=f"{BASE}/thursday-0.1.0.tar.gz",
        sha256=DIGEST,
        signature=private.sign(
            signing_payload(version="0.1.0", sha256=DIGEST, url=f"{BASE}/thursday-0.1.0.tar.gz")
        ),
    )
    updater = service(tmp_path, keypair, [release_row(private)], current="0.2.0")

    with pytest.raises(UpdateError, match="older than the installed"):
        updater.apply(old, ARTIFACT, confirm=True)

    # Rolling back is a real thing the owner may want, and it has to be said out loud.
    assert updater.apply(old, ARTIFACT, confirm=True, allow_downgrade=True)["version"] == "0.1.0"


def test_a_backup_is_taken_before_the_code_is_replaced(tmp_path, keypair):
    private, _ = keypair
    taken: list[str] = []

    class Backups:
        def create(self, path, note=""):
            taken.append(str(path))

    updater = service(tmp_path, keypair, [release_row(private)])
    updater._backups = Backups()
    updater.apply(updater.latest(), ARTIFACT, confirm=True)
    assert taken and "pre-update-0.3.0" in taken[0]


def test_an_update_does_not_proceed_when_the_backup_fails(tmp_path, keypair):
    """The update that goes wrong is the one you cannot undo."""
    private, _ = keypair

    class Backups:
        def create(self, path, note=""):
            raise OSError("disk full")

    updater = service(tmp_path, keypair, [release_row(private)])
    updater._backups = Backups()
    with pytest.raises(UpdateError, match="without a backup"):
        updater.apply(updater.latest(), ARTIFACT, confirm=True)
    assert updater.current == "0.2.0"


def test_a_failed_install_leaves_the_version_alone_and_is_recorded(tmp_path, keypair):
    private, _ = keypair

    def broken(release, artifact):
        raise RuntimeError("the installer exploded")

    updater = service(tmp_path, keypair, [release_row(private)], install=broken)
    with pytest.raises(UpdateError, match="failed and was not applied"):
        updater.apply(updater.latest(), ARTIFACT, confirm=True)

    assert updater.current == "0.2.0"
    assert updater.history()[-1]["ok"] is False


def test_a_build_with_no_installer_says_so_rather_than_reporting_success(tmp_path, keypair):
    private, _ = keypair
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"releases": [release_row(private)]}))
    _, public = keypair
    updater = UpdateService(
        current_version="0.2.0",
        source=LocalReleaseSource(path=manifest, base_url=BASE),
        signing_key=public.encoded,
    )
    with pytest.raises(UpdateError, match="no installer wired up"):
        updater.apply(updater.latest(), ARTIFACT, confirm=True)


# --------------------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("older", "newer"),
    [("0.2.9", "0.2.10"), ("0.9.0", "0.10.0"), ("1.0.0", "1.0.1"), ("0.2.0", "1.0.0")],
)
def test_versions_compare_numerically_not_alphabetically(older, newer):
    """Alphabetically, 0.2.10 is older than 0.2.9 — which turns a security release into one
    the updater declines to install."""
    assert parse_version(older) < parse_version(newer)


def test_a_version_that_is_not_a_version_is_refused():
    with pytest.raises(UpdateError, match="not a version number"):
        parse_version("latest")


def test_the_newest_release_is_chosen_not_the_last_listed(tmp_path, keypair):
    private, _ = keypair
    rows = [release_row(private, version=v) for v in ("0.3.0", "0.10.0", "0.4.0")]
    assert service(tmp_path, keypair, rows).latest().version == "0.10.0"


def test_a_malformed_entry_is_skipped_rather_than_taking_the_check_down(tmp_path, keypair):
    private, _ = keypair
    rows = [{"version": "0.9.0"}, release_row(private, version="0.3.0")]
    assert service(tmp_path, keypair, rows).latest().version == "0.3.0"


def test_a_check_reports_a_broken_channel_instead_of_raising(tmp_path, keypair):
    """Checking for updates is background work. It must not turn a network problem into a
    failure the owner sees as Thursday being broken."""
    _, public = keypair
    updater = UpdateService(
        current_version="0.2.0",
        source=LocalReleaseSource(path=tmp_path / "absent.json", base_url=BASE),
        signing_key=public.encoded,
    )
    state = updater.check()
    assert state.available is False
    assert "no update manifest" in state.problem


# --------------------------------------------------------------------------- the API


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


async def test_the_update_endpoints_take_no_url(client):
    """The API surface, checked the same way as the service: §120 as a schema property."""
    schema = client._transport.app.openapi()["paths"]
    for path in ("/api/v1/updates", "/api/v1/updates/apply"):
        for operation in schema[path].values():
            names = {p["name"] for p in operation.get("parameters", [])}
            assert not names & {"url", "uri", "source", "channel"}, path
            assert "requestBody" not in operation, f"{path} should take no body"


async def test_checking_for_updates_reports_rather_than_raising(client):
    body = (await client.get("/api/v1/updates")).json()
    assert body["current"]
    assert body["available"] is False
    assert "no update channel is configured" in body["problem"]


async def test_applying_an_update_needs_the_owner(client, container, tmp_path, keypair):
    """`system.update` is SYSTEM-level and ASK_ALWAYS, and an override cannot make it AUTO."""
    private, public = keypair
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"releases": [release_row(private, version="9.9.9")]}))
    container.updates = UpdateService(
        current_version="0.2.0",
        source=LocalReleaseSource(path=manifest, base_url=BASE),
        signing_key=public.encoded,
    )

    response = await client.post("/api/v1/updates/apply")
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["decision"] == "ASK_ALWAYS"
    assert detail["installing"]["version"] == "9.9.9"

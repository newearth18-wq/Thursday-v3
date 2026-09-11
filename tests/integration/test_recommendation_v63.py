"""Hardware detection into a recommendation (EASY INSTALL) — Sprint 63.

*"ผู้ใช้ไม่ต้องเลือก Model เอง"*. Thursday inspects the machine and proposes; the owner sees
FAST / BALANCED / SMART / PRIVATE, a download size and a disk requirement — never
`llama3.1:8b-instruct-q4_K_M`.

Three ways a recommendation engine makes things worse than none, and a test for each:

  · recommending a download that will not fit, which the owner discovers after the progress bar
  · reading RAM as capability, so a 32 GB laptop with no GPU is offered what a 16 GB machine
    with a 4090 runs
  · letting PRIVATE fall back to cloud on a weak machine — a breach rather than a
    disappointment, and the one that would be invisible because the answers still arrive
"""

from __future__ import annotations

import pytest
from thursday_core.recommend import (
    CLASSES,
    DISK_HEADROOM,
    RESERVED_RAM,
    AIPreset,
    ModelClass,
    recommend,
)
from thursday_shared.compute import GIB, ComputeProfile


def machine(ram_gb: int, vram_gb: int = 0, disk_gb: int = 200) -> ComputeProfile:
    return ComputeProfile(
        ram_bytes=ram_gb * GIB,
        vram_bytes=vram_gb * GIB,
        gpu_name="NVIDIA RTX" if vram_gb else "",
        disk_free_bytes=disk_gb * GIB,
        cpu_cores=8,
    )


def chosen(profile: ComputeProfile, **kw) -> str:
    result = recommend(profile, **kw)
    return result.model_class.key if result.model_class else "cloud"


# --------------------------------------------------------------------------- the worked examples


def test_the_requirements_own_examples():
    """§"Installer ตรวจ ... จากนั้นเลือก Local AI ที่เหมาะสม" spells out four cases. These are
    them, at the preset that maxes the machine out."""
    assert chosen(machine(8), preset=AIPreset.SMART) == "lightweight"
    assert chosen(machine(16, 6), preset=AIPreset.SMART) == "medium"
    assert chosen(machine(32, 12), preset=AIPreset.SMART) == "large"
    assert chosen(machine(4), preset=AIPreset.SMART) == "cloud"


def test_balanced_does_not_max_the_machine_out():
    """BALANCED is "แนะนำ", not "as much as this box can take". A recommended default that
    consumes every spare gigabyte is one the owner turns off after the first time their game
    stutters."""
    assert chosen(machine(32, 12), preset=AIPreset.BALANCED) == "medium"
    assert chosen(machine(32, 12), preset=AIPreset.SMART) == "large"


# --------------------------------------------------------------------------- fitting


def test_a_download_that_will_not_fit_is_never_proposed():
    """The failure the owner finds after the progress bar."""
    roomy = machine(32, 12, disk_gb=500)
    cramped = machine(32, 12, disk_gb=8)

    assert chosen(roomy, preset=AIPreset.SMART) == "large"
    assert chosen(cramped, preset=AIPreset.SMART) == "lightweight"


def test_disk_headroom_is_left_behind():
    """A full disk is a broken machine, and the owner remembers which program filled it."""
    klass = CLASSES[0]
    exactly_enough = machine(16, disk_gb=0)
    assert klass.fits(exactly_enough, free_disk=klass.download_bytes + DISK_HEADROOM)
    assert not klass.fits(exactly_enough, free_disk=klass.download_bytes + DISK_HEADROOM - 1)


def test_the_operating_system_gets_its_memory_back():
    """A machine with 8 GB does not have 8 GB to give a model. Recommending as though it did
    is how the first real question swaps to disk and takes a minute to answer."""
    klass = ModelClass(
        key="probe",
        label_en="x",
        label_th="x",
        min_ram_bytes=8 * GIB,
        min_vram_bytes=0,
        download_bytes=GIB,
    )
    assert not klass.fits(machine(8 + 3))
    assert klass.fits(machine(8 + RESERVED_RAM // GIB))


def test_ram_is_not_capability():
    """A 32 GB laptop with no discrete GPU cannot usefully run what a 16 GB machine with a
    4090 runs. VRAM gates the larger classes — which is why `has_gpu` keys on VRAM rather
    than on a GPU's name (Sprint 54)."""
    laptop = machine(32, vram_gb=0)
    workstation = machine(16, vram_gb=8)

    assert chosen(laptop, preset=AIPreset.SMART) == "lightweight"
    assert chosen(workstation, preset=AIPreset.SMART) == "medium"


def test_an_integrated_gpu_is_not_a_gpu():
    integrated = ComputeProfile(
        ram_bytes=16 * GIB, vram_bytes=0, gpu_name="Intel UHD Graphics", disk_free_bytes=200 * GIB
    )
    assert integrated.has_gpu is False
    assert chosen(integrated, preset=AIPreset.SMART) == "lightweight"


# --------------------------------------------------------------------------- PRIVATE


def test_private_never_becomes_cloud_however_weak_the_machine():
    """The rule that gives the word its meaning.

    Every other preset may reach for a cloud model when the hardware disappoints. This one may
    not — an owner who chose PRIVATE and got cloud inference has had a privacy decision
    silently reversed, and the answers still arrive, so nothing looks wrong.
    """
    for ram in (2, 4, 8, 16, 32):
        result = recommend(machine(ram), preset=AIPreset.PRIVATE)
        assert result.uses_cloud is False, f"{ram} GB machine reached for the cloud"


def test_private_on_a_machine_that_cannot_run_anything_says_so():
    """Rather than quietly using the cloud it was told not to. An honest "no" beats an
    answer from somewhere the owner excluded."""
    result = recommend(machine(2, disk_gb=2), preset=AIPreset.PRIVATE)

    assert result.runs_locally is False
    assert result.uses_cloud is False
    assert any("ส่วนตัว" in r for r in result.reasons)


def test_private_on_a_weak_machine_warns_that_it_will_be_slow():
    """It works and it is worse. Saying so is the difference between a managed expectation
    and a complaint."""
    result = recommend(machine(8), preset=AIPreset.PRIVATE)

    assert result.model_class.key == "lightweight"
    assert result.uses_cloud is False
    assert any("ช้า" in r for r in result.reasons)


def test_the_other_presets_may_use_cloud_when_local_is_not_enough():
    result = recommend(machine(4), preset=AIPreset.BALANCED)
    assert result.runs_locally is False
    assert result.uses_cloud is True


def test_no_cloud_configured_is_stated_rather_than_implied():
    """§38's rule about not failing silently, at setup time. "Thursday will use the cloud"
    is false when no cloud is configured, and the owner needs to know before they finish."""
    result = recommend(machine(4), preset=AIPreset.BALANCED, cloud_available=False)

    assert result.uses_cloud is False
    assert any("ยังไม่ได้ตั้งค่า" in r for r in result.reasons)


# --------------------------------------------------------------------------- what the owner sees


def test_the_summary_names_no_model():
    """§"ไม่ต้องเห็นชื่อ Model เว้นแต่เปิด Advanced Mode". A leaked model name here is the
    technical detail the whole requirement is about removing."""
    for preset in AIPreset:
        for profile in (machine(4), machine(8), machine(16, 6), machine(32, 12)):
            line = recommend(profile, preset=preset).summary()
            lowered = line.lower()
            for jargon in ("llama", "qwen", "ollama", "gguf", "q4", "b-instruct", ":8b"):
                assert jargon not in lowered, f"{line!r} leaks {jargon}"


def test_the_summary_states_a_size_because_a_download_is_about_to_happen():
    """§39: the model, its size, its source and its disk cost are shown before anything is
    fetched. This is the size."""
    result = recommend(machine(16, 6))
    assert "GB" in result.summary()
    assert result.download_bytes > 0
    assert result.disk_required_bytes > result.download_bytes


def test_being_offered_the_small_one_comes_with_the_reason():
    """An owner told "we picked the small one" deserves to know why — otherwise the only way
    to find out is to buy a graphics card and see if the answer changes."""
    result = recommend(machine(16, vram_gb=0), preset=AIPreset.SMART)

    assert result.limits, "no reason was given for the downgrade"
    assert any("การ์ดจอ" in limit or "แรม" in limit for limit in result.limits)


def test_a_cramped_disk_is_named_as_the_reason_rather_than_the_gpu():
    """Checked in the order a person would ask. Telling somebody with a 4090 that their
    graphics card is the problem, when the disk is full, sends them shopping."""
    result = recommend(machine(32, 12, disk_gb=8), preset=AIPreset.SMART)
    assert any("ดิสก์" in limit for limit in result.limits)


@pytest.mark.parametrize("preset", list(AIPreset))
def test_every_preset_produces_something_renderable(preset):
    """No preset may return a shape the setup screen cannot draw — on any machine, including
    the one running this test."""
    for profile in (machine(2, disk_gb=1), machine(8), machine(64, 24, disk_gb=1000)):
        result = recommend(profile, preset=preset)
        assert result.summary()
        assert isinstance(result.runs_locally, bool)
        assert result.download_bytes >= 0


def test_this_machine_gets_a_recommendation_from_its_own_hardware():
    """End to end against the real probe from Sprint 54, on whatever this container is —
    headless, no GPU, and the recommendation should say so rather than crash."""
    from thursday_models.local_manager import LocalModelManager

    profile = LocalModelManager().profile()
    result = recommend(profile)

    assert result.summary()
    assert result.uses_cloud or result.runs_locally, "neither local nor cloud was proposed"


# --------------------------------------------------------------------------- through the app


@pytest.fixture
def client(settings, container):
    from fastapi.testclient import TestClient
    from thursday_api.app import create_app

    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as http:
        yield http


def test_the_setup_screen_gets_a_sentence_a_size_and_no_model_name(client):
    """STEP 5 of the wizard, as the UI would call it."""
    body = client.get("/api/v1/setup/recommendation").json()

    assert body["preset"] == "BALANCED"
    assert body["summary"]
    assert "advanced" not in body, "a normal user was handed the internals"
    assert "model_class" not in body


def test_advanced_mode_is_the_only_way_to_see_the_internals(client):
    """§"Power user can enable Developer Options". Off by default, and the field that leaks
    is always the debugging one somebody left in."""
    plain = client.get("/api/v1/setup/recommendation").json()
    advanced = client.get("/api/v1/setup/recommendation", params={"advanced": True}).json()

    assert "advanced" not in plain
    assert "detected" in advanced["advanced"]
    assert "ram_bytes" in advanced["advanced"]["detected"]


@pytest.mark.parametrize("preset", ["FAST", "BALANCED", "SMART", "PRIVATE", "private"])
def test_every_preset_is_accepted_case_insensitively(client, preset):
    assert client.get("/api/v1/setup/recommendation", params={"preset": preset}).status_code == 200


def test_an_unknown_preset_is_refused_rather_than_defaulted(client):
    """Defaulting a misspelt preset would silently configure something the owner did not
    choose — and the one most likely to be misspelt is PRIVATE."""
    refused = client.get("/api/v1/setup/recommendation", params={"preset": "PRIVAT"})
    assert refused.status_code == 422


def test_asking_for_a_recommendation_installs_nothing(client, container):
    """It proposes. §39 puts a download behind a screen showing size and source, and this
    endpoint is what fills that screen in — not what acts on it."""
    before = container.model_registry.health()
    client.get("/api/v1/setup/recommendation", params={"preset": "SMART"})
    assert container.model_registry.health() == before

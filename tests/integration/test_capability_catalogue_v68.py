"""What Thursday can do here, checked against here (ADAPTIVE ONBOARDING) — Sprint 68.

§52 gives the rule this sprint exists to keep: *"Tutor obtains capabilities from same registry
as Thursday Core. Therefore tutorial always reflects real installation."* Everything below is
some form of that — a tutor that keeps its own list of features is a brochure, and the day it
disagrees with the machine is the day a beginner is told to try something impossible (§12).

The second rule is Sprint 65's, applied to features: a description written for whoever wired
the code never substitutes for one written for a person.
"""

from __future__ import annotations

import inspect

from thursday_core import catalogue
from thursday_core.catalogue import (
    AREA_TITLES,
    FEATURES,
    FEATURES_BY_KEY,
    Area,
    Availability,
    areas,
    from_agents,
    status,
    summary_line,
    unavailable_reason,
)
from thursday_core.plain import leaks

# ------------------------------------------------------------ availability is derived


def test_no_feature_carries_a_stored_availability_flag():
    """The structural version of §52. A `Feature` has a `probe`, not an `enabled` — so
    there is no field that could be right when written and wrong when read."""
    fields = set(inspect.signature(catalogue.Feature).parameters)
    for forbidden in ("enabled", "available", "installed", "supported"):
        assert forbidden not in fields


def test_a_feature_needing_a_device_is_unavailable_until_one_connects(container):
    assert not container.hub.online()
    assert status(container, "open_app").usable is False


async def test_and_becomes_available_the_moment_one_does(container, office_pc):
    """The same catalogue, the same call, a different answer — because the machine changed
    and nothing in the tutor was edited."""
    assert container.hub.online()
    assert status(container, "open_app").usable is True


async def test_availability_tracks_the_hub_the_hub_itself_enforces_with(container, office_pc):
    """`_any_device_supports` walks the same `capabilities.supports()` the hub refuses on,
    so the tutor cannot believe in a capability the hub would decline."""
    assert catalogue._any_device_supports(container, "app.open") is True
    assert catalogue._any_device_supports(container, "camera") is False


def test_a_probe_that_raises_reports_unavailable_rather_than_breaking_the_screen(container):
    """A catalogue that raises is a Learning Center that will not open. Unknown resolves to
    unavailable, which errs toward not teaching something rather than teaching a ghost."""

    def boom(_c):
        raise RuntimeError("nope")

    broken = catalogue.Feature(key="x", area=Area.BASICS, title="t", summary="s", probe=boom)
    result = broken.availability(container)
    assert result.state is Availability.UNAVAILABLE
    assert leaks(result.reason) == []


# ------------------------------------------------------------------ §12 alternatives


def test_a_missing_camera_offers_the_phone_instead_of_a_dead_end(container):
    """§12 by name. "เครื่องนี้ยังไม่พบกล้อง" is where somebody stops; the second clause is
    where they carry on."""
    row = status(container, "vision")
    assert row.usable is False
    assert row.availability.state is Availability.NEEDS_HARDWARE
    assert "กล้อง" in row.availability.reason
    assert "มือถือ" in row.availability.alternative

    combined = unavailable_reason(container, "vision")
    assert row.availability.reason in combined
    assert row.availability.alternative in combined


def test_every_unavailable_feature_says_why_in_words_a_person_can_act_on(container):
    for row in catalogue.catalogue(container):
        if row.usable:
            continue
        assert row.availability.reason, f"{row.feature.key} is unavailable and does not say why"
        assert leaks(row.availability.reason) == [], row.feature.key
        assert leaks(row.availability.alternative) == [], row.feature.key


# --------------------------------------------------------------- §33 "what can you do"


def test_the_answer_names_a_handful_of_areas_not_a_hundred_features(container):
    """ "ห้ามตอบเป็นรายการ 100 รายการ". The shape of the answer is the requirement."""
    grouped = areas(container)
    assert 1 <= len(grouped) <= 8
    assert "ด้าน" in summary_line(container)


def test_the_summary_counts_areas_rather_than_features(container):
    grouped = areas(container)
    assert str(len(grouped)) in summary_line(container)
    total_features = sum(len(a["features"]) for a in grouped)
    assert total_features > len(grouped)  # otherwise the distinction is untested


def test_what_can_you_do_lists_only_what_can_actually_be_done_here(container):
    """The answer to "what can you do" is what Thursday *can* do. Padding it with things
    this machine cannot is how a first conversation becomes a list of disappointments."""
    listed = {title for area in areas(container) for title in area["features"]}
    assert FEATURES_BY_KEY["open_app"].title not in listed  # no device in this container
    assert FEATURES_BY_KEY["conversation"].title in listed


async def test_the_answer_grows_when_the_machine_does(container, office_pc):
    assert FEATURES_BY_KEY["open_app"].title in {
        title for area in areas(container) for title in area["features"]
    }


def test_areas_come_back_in_a_stable_order(container):
    assert [a["area"] for a in areas(container)] == [a["area"] for a in areas(container)]


def test_every_area_has_a_thai_title(container):
    for area in Area:
        assert AREA_TITLES[area]
    for row in areas(container):
        assert row["title"] == AREA_TITLES[Area(row["area"])]


# ------------------------------------------------------- §61 self-documenting agents


def test_an_agent_that_describes_itself_for_a_person_is_taught(container):
    """The extension point §61 asks for: a new agent needs one sentence, not an edit here."""
    described = {row["name"] for row in from_agents(container)}
    assert "research" in described
    assert "file" in described


def test_an_agent_with_no_user_facing_sentence_is_silent_rather_than_leaking_one(container):
    """Sprint 65's rule. `AgentSpec.description` is written for whoever wired the agent —
    "Finds and cross-checks information from memory, the vault and the web" is fine in a
    code review and wrong in front of somebody who has never heard of a vault."""
    specs = {spec.name: spec for spec in container.agents.specs()}
    undescribed = [name for name, spec in specs.items() if not spec.user_description]
    assert undescribed, "this test needs at least one agent that has not been given a sentence"

    described = {row["name"] for row in from_agents(container)}
    for name in undescribed:
        assert name not in described

    developer_text = {spec.description for spec in specs.values()}
    for row in from_agents(container):
        assert row["summary"] not in developer_text


def test_nothing_an_agent_says_about_itself_leaks_an_internal(container):
    for row in from_agents(container):
        assert leaks(row["summary"]) == [], row["name"]
        assert leaks(row["safety"]) == [], row["name"]


# ------------------------------------------------------------------- the catalogue itself


def test_every_feature_is_described_in_the_owners_language():
    for feature in FEATURES:
        assert feature.title and feature.summary
        assert leaks(feature.title) == [], feature.key
        assert leaks(feature.summary) == [], feature.key
        for example in feature.examples:
            assert leaks(example) == [], feature.key


def test_feature_keys_are_unique():
    assert len(FEATURES_BY_KEY) == len(FEATURES)


def test_stopping_is_taught_at_the_shallowest_depth():
    """§56 puts "หยุดทั้งหมด" among the first lessons, and the reason is not politeness:
    knowing how to stop something is what makes it safe to try anything else."""
    assert FEATURES_BY_KEY["stop_everything"].depth == 1


def test_the_risky_features_carry_a_safety_note():
    for key in ("vision", "gesture", "automation"):
        assert FEATURES_BY_KEY[key].safety_notes, key


def test_an_unknown_feature_key_returns_nothing_rather_than_inventing_one(container):
    assert status(container, "teleportation") is None
    assert unavailable_reason(container, "teleportation") == ""


def test_a_rendered_feature_never_carries_a_probe_or_anything_internal(container):
    for row in catalogue.catalogue(container):
        rendered = row.render()
        assert "probe" not in rendered
        assert "depth" not in rendered
        assert leaks(str(rendered)) == [], row.feature.key

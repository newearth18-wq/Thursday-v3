"""The onboarding acceptance tests, over HTTP (ADAPTIVE ONBOARDING §65, §66, §67).

The spec writes three of these itself, and they are the ones worth running because they judge
the whole thing rather than any part of it:

    §65  A fresh install. The owner knows nothing. Thursday must guide them through speaking
         to it, opening an app, understanding permission, and learning how to stop — without
         any external documentation.

    §66  The owner searches files three times. Thursday may offer to teach the shortcut. If
         they decline, it does not keep asking.

    §67  The tutor never exposes secrets, hidden prompts, private reasoning, credentials or
         memory it was not authorised to show.

Everything goes through the API a real client would use, against a real container with a real
device node attached, because §65's whole claim is about somebody who has nothing else.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_core.learning import Familiarity
from thursday_core.plain import leaks
from thursday_core.tips import USES_BEFORE_UPGRADE

API = "/api/v1"


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


# ============================================================== §65 the first-run journey


async def test_a_new_owner_is_guided_through_the_four_things_that_matter(
    client, container, office_pc
):
    """§65, start to finish, with nothing but the API.

    The four the spec names: speaking to Thursday, opening an app, understanding the
    permission concept, and knowing how to stop. Each is checked by the machine rather than
    by the tutorial claiming it happened.
    """
    # ---------------------------------------------------------------- they know nothing
    opened = (await client.get(f"{API}/learn")).json()
    assert opened["summary"]
    assert opened["next"] is not None, "a fresh install must have somewhere to start"
    assert opened["progress"]["used"] == []

    # ------------------------------------------------- 1. speaking to Thursday
    first = opened["next"]["id"]
    assert first == "say-something"

    started = (await client.post(f"{API}/learn/{first}/start")).json()
    assert started["next"]["show"]
    assert started["next"]["try"], "a beginner needs the words, not a description of them"

    # They say it, and a real turn comes back through the real conversation endpoint.
    turn = await client.post(f"{API}/conversations", json={"text": started["next"]["try"]})
    assert turn.status_code == 200

    said = await client.post(
        f"{API}/learn/{first}/attempt", json={"reply": turn.json().get("reply", "ok")}
    )
    assert said.json()["passed"] is True
    assert said.json()["done"] is True

    # ------------------------------------------------- 2. how to stop (taught before acting)
    stop = (await client.get(f"{API}/learn")).json()["next"]["id"]
    assert stop == "how-to-stop", "stopping is taught before anything that touches the machine"

    shown = (await client.post(f"{API}/learn/{stop}/start")).json()
    assert "หยุด" in shown["next"]["show"]
    acknowledged = await client.post(f"{API}/learn/{stop}/attempt", json={"read": True})
    assert acknowledged.json()["passed"] is True

    # And the stop control genuinely works, which is the part that makes the rest safe.
    halted = await client.post(f"{API}/emergency/stop", json={"scope": "all"})
    assert halted.status_code == 200
    assert halted.json()["actions"], "stopping must actually do something"
    await client.post(f"{API}/emergency/release")

    # Stopping everything disconnects every node (§69), so the next lesson genuinely cannot
    # run until something reconnects — which the Learning Center reports rather than hides.
    # A real node re-establishes its own session; here the fixture's session is re-registered
    # to stand in for that. This detour is not incidental: it is the acceptance test noticing
    # that "learn how to stop" has a real consequence, and that the tutor stays truthful
    # through it.
    blocked = (await client.get(f"{API}/learn")).json()
    blocked_app = next(
        lesson
        for stage in blocked["path"]
        for lesson in stage["lessons"]
        if lesson["id"] == "open-an-app"
    )
    assert blocked_app["available"] is False
    assert blocked_app["reason"]

    await container.hub.register(office_pc, location_context="office")

    # ------------------------------------------------- 3. opening an app, verified
    app_lesson = (await client.get(f"{API}/learn")).json()["next"]["id"]
    assert app_lesson == "open-an-app"
    await client.post(f"{API}/learn/{app_lesson}/start")

    result = await client.post(f"{API}/conversations", json={"text": "Thursday เปิด notepad"})
    body = result.json()
    assert body["verified"] is True, "the lesson is only honest if the app really opened"

    done = await client.post(
        f"{API}/learn/{app_lesson}/attempt", json={"ok": True, "verified": body["verified"]}
    )
    assert done.json()["passed"] is True
    # §4: the closing line widens from the one command to the shape of commands.
    assert "Chrome" in done.json()["message"] or "โปรแกรมอื่น" in done.json()["message"]

    # ------------------------------------------------- 4. the permission concept
    why = (await client.get(f"{API}/learn/why/file.delete")).json()
    assert why["why"]
    assert "ถาม" in why["why"], "they should learn that some things are always asked about"
    assert "เปลี่ยน" in why["why"], "and that the rule is theirs to change"

    # They can meet an approval prompt with nothing at stake (§23).
    rehearsal = (await client.get(f"{API}/learn/practice/file.delete")).json()
    assert rehearsal["happened"] is False
    assert rehearsal["prompt"]

    # ------------------------------------------------- and the record reflects it
    progress = (await client.get(f"{API}/learn")).json()["progress"]
    assert "conversation" in progress["used"]
    assert "open_app" in progress["used"]
    assert "say-something" in progress["tutorials_completed"]


async def test_none_of_the_first_run_requires_reading_anything_outside_thursday(client):
    """§65's actual bar: "without reading external documentation." Every lesson has to carry
    the words to try, and no screen may send the owner to a manual."""
    body = (await client.get(f"{API}/learn")).json()
    text = str(body)
    for elsewhere in ("readme", "docs/", "documentation", "wiki", "github"):
        assert elsewhere not in text.lower()

    for stage in body["path"]:
        for lesson in stage["lessons"]:
            if not lesson["available"]:
                continue
            started = (await client.post(f"{API}/learn/{lesson['id']}/start")).json()
            assert started["next"]["show"], lesson["id"]


async def test_the_learning_centre_is_not_called_documentation(client):
    """§10: "ไม่ใช้คำว่า Documentation เป็นหน้าหลัก"."""
    body = (await client.get(f"{API}/learn")).json()
    assert "documentation" not in str(body).lower()
    assert body["areas"], "it opens on what Thursday can do, not on a table of contents"


# ============================================================ §66 contextual teaching


async def test_three_file_searches_earns_an_offer_and_a_no_ends_it(client, container):
    """§66, in the spec's own numbers.

    "User searches files 3 times. Thursday may suggest … If dismissed: do not repeatedly
    ask."
    """
    for _ in range(USES_BEFORE_UPGRADE):
        container.learning.used("file_search")

    tip = container.tips.after(container, capability="file_search")
    assert tip is not None, "three searches is when the offer becomes welcome rather than random"
    assert "ไฟล์" in tip.text

    # They say no.
    dismissed = await client.post(
        f"{API}/learn/tips/dismiss", params={"capability": tip.capability}
    )
    assert dismissed.status_code == 200

    # And it does not come back, however much more they search.

    from thursday_core.tips import COOLDOWN

    for i in range(10):
        container.learning.used("file_search")
        again = container.tips.after(
            container,
            capability="file_search",
            now=tip
            and __import__("datetime").datetime.now(__import__("datetime").UTC)
            + COOLDOWN * (i + 2),
        )
        assert again is None, "a dismissed offer must not return"


async def test_one_search_does_not_earn_an_offer(client, container):
    """The other side of the same rule. Offering the advanced move to somebody who has done
    the basic one once is getting ahead of them."""
    container.learning.used("file_search")
    assert container.tips.after(container, capability="file_search") is None


async def test_a_tip_never_interrupts_and_always_follows_work(container):
    """§41. `after()` is the only way a tip is produced, and it takes the capability that
    just finished — there is no method that offers a tip out of nowhere."""
    import inspect

    assert not hasattr(container.tips, "poll")
    assert not hasattr(container.tips, "tick")
    assert "capability" in inspect.signature(container.tips.after).parameters


async def test_turning_teaching_off_stops_everything_unprompted(client, container):
    """§39, over HTTP, including that the setting is honoured immediately."""
    response = await client.post(f"{API}/learn/teaching", params={"frequency": "off"})
    assert response.json()["unprompted"] is False

    for _ in range(USES_BEFORE_UPGRADE * 3):
        container.learning.used("file_search")
    assert container.tips.after(container, capability="file_search") is None


async def test_the_owner_can_ask_to_be_taught_even_with_teaching_off(client, container):
    """ "Only when asked" has to still answer when asked, or the setting is just OFF with
    another name. Asking is not Thursday speaking up."""
    await client.post(f"{API}/learn/teaching", params={"frequency": "on_request"})
    body = (await client.get(f"{API}/learn")).json()
    assert body["next"] is not None
    assert body["areas"]


# ==================================================================== §67 tutor privacy


async def test_the_tutor_never_exposes_what_thursday_remembers(client, container):
    """§67. A real secret-shaped memory exists; nothing any teaching surface returns
    contains it."""
    from thursday_shared.enums import MemoryLayer, MemorySource
    from thursday_shared.models import MemoryWrite

    secret = "รหัสผ่าน wifi บ้านคือ hunter2-abcdef"
    await container.memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content=secret, source=MemorySource.USER)
    )

    surfaces = [
        await client.get(f"{API}/learn"),
        await client.get(f"{API}/learn/features"),
        await client.get(f"{API}/learn/practice/file.delete"),
        await client.get(f"{API}/learn/why/email.send"),
    ]
    for response in surfaces:
        text = response.text
        assert "hunter2" not in text
        assert secret not in text


async def test_no_teaching_surface_exposes_a_prompt_or_an_internal(client):
    """The other half of §67: hidden prompts and private reasoning. `system_prompt` is a
    real field on every agent spec, and a features endpoint that dumps specs would leak
    every one of them."""
    for path in ("/learn", "/learn/features", "/learn/practice/email.send"):
        text = (await client.get(f"{API}{path}")).text
        assert "system_prompt" not in text
        assert "You explain what Thursday can do" not in text
        assert leaks(text) == [], path


async def test_no_teaching_surface_leaks_a_credential_or_a_path(client, settings):
    for path in ("/learn", "/learn/features"):
        text = (await client.get(f"{API}{path}")).text
        assert str(settings.data_dir) not in text
        assert "sqlite" not in text.lower()
        assert "secret" not in text.lower()


# ======================================================================= §38 resetting


async def test_resetting_tips_keeps_what_the_owner_actually_did(client, container):
    """ "Show beginner tips again" restores offers. It must not rewrite their history."""
    container.learning.used("file_search")
    container.learning.used("file_search")
    container.tips.dismiss("skills")

    await client.post(f"{API}/learn/reset", params={"scope": "tips"})

    assert container.learning.entry("skills").dismissed is False
    assert container.learning.knows("file_search") is Familiarity.LEARNED


async def test_restart_introduction_forgets_everything(client, container):
    container.learning.used("file_search")
    container.learning.start("say-something")

    await client.post(f"{API}/learn/reset", params={"scope": "all"})

    body = (await client.get(f"{API}/learn")).json()
    assert body["progress"]["used"] == []
    assert body["next"]["id"] == "say-something"


async def test_an_unknown_reset_scope_is_refused_rather_than_guessed(client):
    """A reset that silently does the wrong thing is worse than one that fails: the owner
    believes their tutorial progress is gone and it is not."""
    response = await client.post(f"{API}/learn/reset", params={"scope": "everything"})
    assert response.status_code == 422


# ================================================================= §68 the final experience


async def test_i_do_not_know_how_to_use_this(client, container):
    """§68's whole scene: somebody says they cannot use it, and ends up having opened
    Chrome and understood the shape of the thing."""
    body = (await client.get(f"{API}/learn")).json()
    assert body["next"]["name"]

    lesson = body["next"]["id"]
    started = (await client.post(f"{API}/learn/{lesson}/start")).json()
    assert started["next"]["try"], "the answer to 'ใช้ไม่เป็น' is words to say, not a menu"
    assert leaks(started["message"]) == []

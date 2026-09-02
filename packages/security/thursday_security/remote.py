"""The remote-command gate (§9.4, V8).

A *remote command* is one whose origin device is not its target device. The distinction is
not academic. When the owner says "open Chrome" to the machine in front of them, they can
see the result; when they say it to a machine in another building, they cannot, and neither
can anyone else who happens to be holding their phone.

V8 makes remote commands easy — conversational focus means a command need not even name the
machine it lands on (`thursday_core.focus`). That is the feature, and it is exactly why this
file has to exist. A convenience that quietly widens what an unattended device can reach is
not a convenience.

Five conditions, from the spec, and each one is a distinct failure being prevented:

============================  ===========================================================
Condition                     What goes wrong without it
============================  ===========================================================
Known origin                  a command from nowhere in particular drives a real machine
Trusted origin                a shared tablet in the kitchen commands the server
Encrypted link                the instruction is readable, and worse, writable, in transit
Permission check              unchanged and still applies — this gate only *adds*
Audit entry                   "who told my PC to do that, and from where" has no answer
============================  ===========================================================

There is a sixth rule, and it lives somewhere else on purpose: a consequential action does
not stay automatic just because a policy would allow it locally. Distance removes the
owner's ability to notice and intervene, which is what made "auto" acceptable to begin with.
That escalation is the permission engine's — `PermissionEngine.decide`, rule 4b — because
approvals are its job and a second component deciding the same question is how the two come
to disagree. This file owns only the list of actions the rule applies to.

So: this gate never *grants* anything. It can only refuse, and whatever it allows still
passes the permission engine exactly as a local action does (ADR 0011).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.actions import canonical, prefixes
from thursday_shared.enums import PermissionLevel, TrustLevel
from thursday_shared.models import DeviceSummary

log = get_logger(__name__)

#: The lowest trust a device may hold and still send instructions to another machine.
MIN_ORIGIN_TRUST = TrustLevel.TRUSTED

#: Actions that need an explicit approval when driven from another machine *even though*
#: they do not modify the target. Prefix-matched (ADR 0007), so a new verb under one of
#: these is covered without anyone remembering to add it.
#:
#: This list is deliberately short, because the main rule is not a list at all: **a remote
#: action that modifies the target needs confirming**, derived from the level the action
#: already declares (see `needs_confirmation_when_remote`). A hand-maintained enumeration
#: was tried first and was wrong within the hour — it named `file.delete` and `file.move`
#: and quietly missed `file.copy`, `file.rename`, `file.create`, `clipboard.write` and
#: `app.close`, every one of which writes to a machine the owner cannot see. A rule that
#: has to be remembered is a rule that will be forgotten.
#:
#: What stays here is the other direction: things below MODIFY that still deserve a
#: question. Starting a process is `OPEN`, and starting a process on someone else's machine
#: is not the same as opening a window on your own.
CONFIRM_WHEN_REMOTE: frozenset[str] = frozenset(
    {
        "system.process.start",
        "app.install",
        "browser.submit",
        "payment",
        "purchase",
        "email.send",
        "message.send",
        "credential",
        "vault",
        "security",
        "permission",
        "approval",
    }
)

#: A remote action at or above this level is confirmed regardless of the list above.
#: MODIFY is the line because it is where an action stops being a question and starts being
#: a change — and a change the owner cannot see is the thing this whole file is about.
CONFIRM_WHEN_REMOTE_LEVEL = PermissionLevel.MODIFY


@dataclass(frozen=True)
class RemoteVerdict:
    """The outcome of the gate. ``allowed`` false means the action does not happen."""

    allowed: bool
    reason: str
    #: True when the action is remote at all. False for a local command, where this gate
    #: has nothing to say and the ordinary path applies unchanged.
    remote: bool = False

    def __bool__(self) -> bool:
        return self.allowed


def needs_confirmation_when_remote(
    action: str, *, level: PermissionLevel = PermissionLevel.READ
) -> bool:
    """Whether this action, driven from another machine, has to be confirmed.

    Two ways to qualify, and the first is the one that carries the weight: the action
    modifies the target. The named list only adds things below that line.
    """
    if level >= CONFIRM_WHEN_REMOTE_LEVEL:
        return True
    name = canonical(action)
    return any(prefix in CONFIRM_WHEN_REMOTE for prefix in prefixes(name))


class RemoteCommandGate:
    """Decides whether an instruction may cross from one machine to another."""

    def __init__(self, *, min_origin_trust: TrustLevel = MIN_ORIGIN_TRUST) -> None:
        self._min_trust = min_origin_trust

    def check(
        self,
        *,
        action: str,
        origin: DeviceSummary | None,
        target: DeviceSummary,
        origin_device_id: UUID | None = None,
    ) -> RemoteVerdict:
        """Gate one action.

        ``origin`` is the device the instruction came from, or None when it did not come
        from a device at all — a scheduled automation, or the API called directly. That case
        is *local*: there is no second machine involved, so there is nothing for this gate
        to protect. What it must not do is treat "no origin" as "trusted origin".
        """
        # A quarantined machine is not a valid target for anything, local or remote. This
        # is checked first because it is the one rule that does not depend on where the
        # instruction came from.
        if target.trust_level <= TrustLevel.UNTRUSTED:
            return self._refuse(
                f"{target.name} is marked untrusted and will not be sent commands",
                action=action,
                target=target,
                origin=origin,
                remote=True,
            )

        if origin_device_id is None or (origin is not None and origin.id == target.id):
            # Same machine, or no machine. Not a remote command; the ordinary permission
            # path is the whole of the decision.
            return RemoteVerdict(True, "not a remote command", remote=False)

        if origin is None:
            # An origin id that names no device the hub knows. The instruction claims to
            # come from somewhere, and that somewhere cannot be identified — which is worse
            # than claiming nothing, not better.
            return self._refuse(
                "the device this came from is not one I recognise",
                action=action,
                target=target,
                origin=None,
                remote=True,
            )

        if origin.trust_level < self._min_trust:
            return self._refuse(
                f"{origin.name} is not trusted to control other machines",
                action=action,
                target=target,
                origin=origin,
                remote=True,
            )

        if not origin.encrypted or not target.encrypted:
            unprotected = origin.name if not origin.encrypted else target.name
            return self._refuse(
                f"the link to {unprotected} is not encrypted, so I will not relay commands over it",
                action=action,
                target=target,
                origin=origin,
                remote=True,
            )

        # Everything past here is allowed *by this gate*. Whether it also needs the owner's
        # explicit approval is the permission engine's call, not this one — see
        # `PermissionEngine.decide`, rule 4b, which escalates a remote consequential action
        # to ASK_ALWAYS. Two components deciding the same question is how they come to
        # disagree.
        log.info("remote_command_allowed", action=action, origin=origin.name, target=target.name)
        return RemoteVerdict(
            True, f"{origin.name} is trusted to command {target.name}", remote=True
        )

    def _refuse(
        self,
        reason: str,
        *,
        action: str,
        target: DeviceSummary,
        origin: DeviceSummary | None,
        remote: bool,
    ) -> RemoteVerdict:
        log.warning(
            "remote_command_refused",
            action=action,
            origin=origin.name if origin else None,
            target=target.name,
            reason=reason,
        )
        return RemoteVerdict(False, reason, remote=remote)

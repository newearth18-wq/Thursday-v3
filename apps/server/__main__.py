"""Thursday Core server.

python -m apps.server
python -m apps.server --sign-handover --retiring-key K --incoming-cert C --planned
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from thursday_api.app import create_app
from thursday_api.routers.devices import HANDOVER_FILE
from thursday_core.config import get_settings
from thursday_security.handover import HandOver, HandOverRefused, sign
from thursday_security.pinning import spki_pin


def sign_handover(
    *, retiring_key: Path, incoming_cert: Path, compromised: bool, data_dir: Path
) -> int:
    """Publish a signed hand-over from the retiring TLS key to the incoming one (ADR 0071).

    Run by the operator on the host, because **Thursday does not hold the core's TLS key**.
    It does not generate it, store it, install it or rotate it: the core is served behind
    whatever terminates TLS for it, and that key is the operator's. What this does is take a
    rotation that has already been decided and turn it into something every node can verify
    against the pin it took at pairing — so nobody has to walk to each machine.

    Nothing here reaches the network. The key is read, a statement is signed, a file is
    written; the rotation itself happens wherever certificates are installed.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    try:
        pem = retiring_key.read_text(encoding="utf-8")
        certificate = x509.load_pem_x509_certificate(incoming_cert.read_bytes())
    except (OSError, ValueError) as exc:
        print(f"could not read the key or certificate: {exc}")
        return 1

    next_pin = spki_pin(certificate.public_bytes(serialization.Encoding.DER))

    try:
        link = sign(pem, next_pin=next_pin, compromised=compromised)
    except HandOverRefused as exc:
        print(f"\n{exc}\n")
        return 1
    except TypeError:
        # `load_pem_private_key(password=None)` raises this for an encrypted key. Worth its
        # own message: "TypeError" is not a sentence anybody can act on.
        print(f"{retiring_key} is encrypted; decrypt it before signing a hand-over")
        return 1
    except ValueError as exc:
        print(f"{retiring_key} is not a private key this can sign with: {exc}")
        return 1

    path = data_dir / HANDOVER_FILE
    existing: list[HandOver] = []
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8")).get("handovers", [])
            existing = [HandOver.from_dict(item) for item in stored]
        except (OSError, ValueError, HandOverRefused) as exc:
            print(f"{path} exists but will not read, so nothing was written: {exc}")
            return 1

    # A second hand-over from the same key forks the chain, and `follow` takes the first
    # branch it finds — so half the nodes would end up pinned to an abandoned key and stay
    # off the network. Refused here, where somebody can still fix it, rather than on the
    # nodes, where the symptom is "some machines never came back".
    if any(link.retiring_pin == prior.retiring_pin for prior in existing):
        print(
            f"a hand-over from this key is already published ({link.retiring_pin[:8]}…). "
            "Signing a second one would fork the chain and leave some nodes following the "
            "branch that was abandoned. Remove the old entry deliberately if it was wrong."
        )
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    chain = [*existing, link]
    path.write_text(
        json.dumps(
            {
                "handovers": [item.to_dict() for item in chain],
                "written_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"\n  published      {path}\n"
        f"  retiring key   {link.retiring_pin}\n"
        f"  incoming key   {next_pin}\n"
        f"  chain length   {len(chain)}\n\n"
        "Nodes follow this the next time they fail to connect, which is the first time they\n"
        "meet the new certificate. Nothing needs restarting and nobody needs to re-pair.\n"
    )
    return 0


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="thursday-server", description="Thursday Core API")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--sign-handover",
        action="store_true",
        help="publish a signed TLS hand-over so paired nodes follow the core's new key, and exit",
    )
    parser.add_argument("--retiring-key", type=Path, help="PEM private key of the retiring cert")
    parser.add_argument("--incoming-cert", type=Path, help="PEM certificate the core will serve")
    honesty = parser.add_mutually_exclusive_group()
    honesty.add_argument(
        "--planned",
        action="store_true",
        help="the retiring key is not believed to be in anyone else's hands",
    )
    honesty.add_argument(
        "--compromised",
        action="store_true",
        help="the retiring key may have been copied (a hand-over cannot help; this will refuse)",
    )
    args = parser.parse_args()

    if args.sign_handover:
        if not args.retiring_key or not args.incoming_cert:
            parser.error("--sign-handover needs both --retiring-key and --incoming-cert")
        # Required rather than defaulted, and the reason is the whole design. A hand-over is
        # only worth anything while the retiring key is still trustworthy; defaulting to
        # "planned" would answer the one question that decides that on the operator's behalf,
        # silently, in the direction that produces a file.
        if not (args.planned or args.compromised):
            parser.error(
                "say whether the retiring key was compromised: --planned or --compromised. "
                "A hand-over is signed by the retiring key, so it is only trustworthy while "
                "that key is."
            )
        raise SystemExit(
            sign_handover(
                retiring_key=args.retiring_key,
                incoming_cert=args.incoming_cert,
                compromised=args.compromised,
                data_dir=settings.data_dir,
            )
        )

    uvicorn.run(
        "apps.server.__main__:app" if args.reload else create_app(settings),
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
        factory=False,
    )


app = create_app()

if __name__ == "__main__":
    main()

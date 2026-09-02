# 18. Vision (V6)

    camera or screen → sample → detect → OCR → barcode → analyse → answer

## The camera is off

That is the default, the state the system returns to, and the claim that matters more than
the rest of this document put together. Four properties keep it true without anyone having
to remember it — see [ADR 0020](architecture/decisions/0020-the-camera-is-off.md):

| Property | Why |
|---|---|
| A grant needs a **reason** | A grant nobody can describe later is one nobody can audit |
| Grants are **narrow and expiring** | "Look at this book" must not become "watch the room" |
| The indicator is **derived** from the capture state | A separately-computed light can disagree with reality |
| An idle camera **closes itself** | The failure mode of consent is a grant nobody withdrew |

`VisionService` checks a grant and never creates one. A component that can grant itself
camera access has no permission model, only a habit of asking forgiveness.

```
GET    /api/v1/vision                what the camera is doing, and what has been seen
GET    /api/v1/vision/camera/log     "when was my camera on?" — answerable by the owner
POST   /api/v1/vision/camera/grant   ?reason=… &seconds=… &max_captures=…
POST   /api/v1/vision/camera/off     "ปิดกล้อง" — plain, no model in the path
GET    /api/v1/vision/objects        sightings, never claims about the present
DELETE /api/v1/vision/objects        wipe what was seen (§68)
```

## A stream never leaves the machine

108,000 frames an hour is not something anyone can meaningfully consent to. What travels is
a single frame a **local** detector already flagged — three gates, then a hard rate cap
([ADR 0021](architecture/decisions/0021-a-stream-never-leaves-the-machine.md)):

1. **Interval** — never more often than `min_interval_s`
2. **Change** — a frame that looks like the last one is not news
3. **Interest** — a local detector found something worth escalating

Events carry labels and counts, never pixels.

## Reading a frame

Detection, OCR and barcode scanning each run independently, and a failure in one degrades
the reading rather than losing it: someone holding a book up gets "I can read the cover but
could not identify the object" rather than an error.

`SceneReading.primary` weights confidence by area — a tiny, certainly-identified pen in the
corner is rarely what someone pointing a camera is asking about.

An empty reading is marked `uncertain`, which is a third outcome distinct from success and
failure. "I cannot tell what this is" is a valid answer and must not be phrased as one.

## Resolving "this"

"Thursday ตรงนี้ผิดอะไร" carries no information in the word "ตรงนี้". `VisualReferenceResolver`
fuses the signals, strongest first — the ordering runs from *what the owner did
deliberately* to *what happens to be nearby*:

| Signal | Confidence |
|---|---|
| An explicit selection | 0.95 |
| Pointing at it | 0.88 |
| Named in the request | 0.70 |
| The only candidate | 0.75 |
| The most prominent thing | ≤ 0.44 — deliberately below the floor |

The last one alone becomes a question rather than an action. Acting on a guess about *which
thing* the owner means is worse than asking, because the wrong target is often irreversible.

An unconfident resolution is annotated with its own reasoning, because that is the case
where the owner needs to be able to say "no, the other one".

## Spatial memory

Sightings, never claims about the present. Each carries label, object type, camera, place,
position, confidence and time; `objects()` assembles the per-object view with `first_seen`
and `last_seen`. Every answer says how old it is — "last seen three days ago" and "last
seen a minute ago" are structurally the same sentence and completely different answers.

Detector labels are English; the owner asks in Thai. `LABEL_ALIASES` bridges them, without
which "หนังสืออยู่ไหน" would never match a sighting labelled `book` — every spatial question
from this system's primary user, failing silently.

A "where is X" with no sighting falls back to memory. Answering "I have never seen it"
while holding a note that says where it lives would be absurd.

## Not built yet

Real camera capture (`opencv-python`), a local detector (`ultralytics`), OCR (`pytesseract`)
and barcode decoding (`pyzbar`) — the adapters are written but unexercised without the
packages and hardware. Screen element enumeration through platform accessibility APIs is
also unbuilt; `ScreenReader` takes elements as an argument today.

`thursday_vision.fake` ships a camera, screen, and scripted or failing providers, so the
whole pipeline is exercisable with no hardware. The V6 acceptance test runs against those.

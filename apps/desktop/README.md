# Thursday Desktop

Tauri 2 + React 18 + TypeScript + Vite + Tailwind. The window is a client of the local
API like any other; nothing here decides anything (PART 64).

## What it is

A conversation over a mind — not a dashboard. The machinery is Thursday's business; the
owner sees one assistant, and can watch it think.

```
 ● THURSDAY                                              02:20:07
 ว่างอยู่ พร้อมรับงาน                                  วันศุกร์ที่ 4 ก.ย.
                        ╭───────────────╮
                   ╭────┤    ◯ ◯ ◯      ├────╮              DEVICES
  WORKING   1     │     │   ((  ●  ))   │     │            Office-PC ●
  ▬▬▬▬▬            │    ╰───────┬───────╯    │
  WAITING   0       ╰────────────┼───────────╯
  ▬                       กำลังจัดการไฟล์
  FAULTS    0        ●
  ▬                  Office-PC

           you>  ช่วยหาไฟล์งบประมาณล่าสุดใน Downloads
           Thursday>  ···

  tasks devices memory permissions   [ Say what you need…      ] [stop]
```

The core breathes, the rings turn, and every dot around it is something Thursday actually
has right now: a machine that is connected, a job that is running, a question waiting on
the owner, an open task. There are no decorative nodes. When Thursday is idle the graph is
one point of light, and that is the honest picture.

## And when you are working somewhere else

A second window — transparent, always on top, and ignoring the mouse entirely — holding a
small robot that walks along the bottom of the screen. It runs when Thursday is busy, sits
down when everything is stopped, turns to face you and raises a hand when something is
waiting on your answer, and says what it is doing in a bubble over its head.

"Somewhere else" means one thing and nothing more: Thursday's own window is not the one in
front. There is no idle timer, no input hook, no list of what you are running and no camera
(ADR 0055). Opening Thursday puts the robot away.

It is a second window onto the *same* expression, not a second opinion about it — the
gait is the only thing this window decides.

## And on a phone

The same shell, compiled for Android instead of started fresh for it — Tauri 2 runs the
same Rust core and the same web frontend inside a native Activity. What is different is
what it connects to: a phone is a screen onto a Thursday running on the owner's PC or home
hub, never the machine it runs on (Sprint 84, ADR 0057). There is no sidecar, no tray, and
no avatar window — Android has no equivalent for a second, always-on-top OS window.

The first time there is nothing to connect to, or an address that used to work stops
answering, `ServerConnect.tsx` asks where Thursday is. It is not shown because the app
detected it is on Android — nothing here asks what platform it is running on — it is
shown because `useRealtime` counted several real connection failures in a row with
nothing left to try. Desktop practically never sees it, because Sprint 83's sidecar makes
the local backend answer before the window is even shown.

## Rules this UI does not break

- **One voice.** Sub-agents never address the owner (§96). The agent strip is a status
  line, not a second conversation, and it is collapsed by default. If the owner has to
  watch it to understand what is happening, the design has failed.
- **Unverified looks different from done.** Every reply carries `verified`; a dispatched
  action whose effect could not be observed is rendered with its own banner, never as a
  success (§76, PART 5.1).
- **Context before buttons.** An approval shows what will happen, to what, on which
  machine, and what refusing costs — above the choices, never behind a disclosure.
- **"Always allow" is only offered when it exists.** `scopes_offered` decides; for the
  ASK_ALWAYS set the dialog shows one-time answers only, because a standing grant is not
  something the engine would honour (ADR 0008).
- **A permission control that would not stick is not shown.** `can_relax` decides. A
  setting that saves and silently reverts teaches the owner something false about their
  own machine.
- **Stop is never buried.** Escape closes a drawer, or interrupts the turn; the tray's
  *Stop everything* posts to the API from Rust, so it works when the webview does not.
- **No class name reaches the screen.** A running job is labelled with the allowlisted
  phrase from `plain.activity` — "กำลังค้นข้อมูล" — never with the agent that produced it
  (Sprint 65). `AgentStatus.name` exists to tell two concurrent jobs apart and is never
  rendered; `graph.test.ts` and `AgentStrip.test.tsx` assert it.
- **The client has no opinion about how Thursday feels.** `expression` arrives derived from
  the server (ADR 0054) and is rendered, not computed. `lib/mood.ts` holds colours and
  motion and has nowhere to put a word, which is what keeps this window and the avatar from
  showing different faces at the same moment.
- **The graph is state, not decoration.** Every node is a real entity. A layout that
  wandered off-screen or produced `NaN` would look fine until the tenth node, so the
  simulation is a plain function and `graph.test.ts` runs it two thousand frames.
- **The avatar appears because Thursday is not in front, not because of anything it
  learned about you.** Window focus, and nothing else (ADR 0055).
- **Nothing here asks what platform it is running on.** The connect screen is reached by
  counting real connection failures, the same way on a phone or a desktop alike (ADR
  0057) — never by sniffing the OS to decide whether to show it.

## Layout

```
src/
  App.tsx                  one window: the HUD, the conversation over it, drawers
  Avatar.tsx               the transparent window: a robot that walks about
  main.tsx                 entry point
  index.css                Tailwind layers
  hooks/useRealtime.ts     the single WebSocket, reconnect backoff, needsSetup (Sprint 84)
  hooks/useMind.ts         what Thursday consists of — pushed and polled halves
  lib/avatar.ts            gaits, faces and the walking, as plain functions
  lib/api.ts               the REST surface, typed
  lib/graph.ts             the nodes, and the layout, as plain functions
  lib/mood.ts              how each mood is drawn: colour and motion, never words
  lib/plain.ts             the one fallback phrase this side needs
  lib/origin.ts            where the API lives — dev proxy, packaged app, or a stored
                            override (Sprint 84) for a build with no local default at all
  lib/types.ts             the contracts this UI renders
  components/
    BrainGraph.tsx         Sprint 81 — the core, the rings, and one node per real thing
    Hud.tsx                Sprint 81 — the readable half: clock, meters, devices, activity
    Robot.tsx              Sprint 82 — one drawing, posed by the step cycle
    ServerConnect.tsx      Sprint 84 — "where is Thursday?", reached by connection failure
    Conversation.tsx       PART 64 — the interface, floating over the graph
    AgentStrip.tsx         PART 66 — collapsed status line
    ApprovalDialog.tsx     PART 38/70 — context, then buttons
    TaskPanel.tsx          PART 67 — progress, pause, cancel
    DevicePanel.tsx        PART 68 — machines and what each will allow
    MemoryPanel.tsx        PART 69 — search, inspect, confirm, forget
    PermissionPanel.tsx    PART 70 — approval modes, standing grants, autonomy
src-tauri/
  src/lib.rs               everything the app does — desktop-only pieces #[cfg(desktop)]
  src/main.rs              three lines: calls lib.rs's run() (Sprint 84 — mobile needs a lib)
  src/sidecar.rs           Sprint 83, desktop only — spawn the bundled backend, poll, stop
  tauri.conf.json          the main window (ships hidden), CSP, externalBin, macOSPrivateApi
  icons/                   generated with `npx tauri icon`; the build needs them
  binaries/                gitignored — built per-platform by scripts/build_sidecar.sh
  gen/android/             gitignored — generated by `tauri android init` (Sprint 84)
```

## Wiring

| Concern | Where |
|---|---|
| Conversation, streaming, expression | `WS /api/v1/realtime` |
| What Thursday is doing and how it goes | `expression` frames, and `GET /api/v1/expression` for the first paint |
| Approvals | `approval.required` in, `POST /approvals/{id}/approve` out |
| Tasks, devices, memory, policies | `GET /api/v1/…`, polled while a drawer is open |
| Emergency stop (§69) | `POST /api/v1/emergency/stop` — a plain call, bypassing the model |

`lib/origin.ts` is the one place that knows the difference between development and a
packaged build: in dev, Vite proxies `/api` (so a relative URL is correct and there is no
CORS); packaged, the page is served from `tauri://localhost`, where a relative URL would
resolve to the bundle. Override with `VITE_THURSDAY_API` or `THURSDAY_API_URL` at build
time, or — on any Tauri build, including Android — at runtime, through the address a
person types into `ServerConnect.tsx`, stored in `localStorage` and preferred over
everything else.

## Running it

In development, the API must be up first — this app renders it, it does not host it.

```bash
# terminal 1
./scripts/dev.sh

# terminal 2
cd apps/desktop
npm install
npm run dev          # browser, against the dev proxy on :1420
npm run tauri dev    # the real window
```

A **packaged desktop** build is the opposite: it starts its own backend and shows the
window only once that backend answers `/api/v1/health` (Sprint 83, ADR 0056) — see
[`installer/`](../../installer/) at the repo root for the sidecar that makes that true, and
`src-tauri/src/sidecar.rs` for the Rust half that spawns and health-polls it.
`sidecar::should_spawn` is what tells the two modes apart (a dev build, or `THURSDAY_API_URL`
being set), so nothing above changes: `tauri dev` behaves exactly as it always has.

**Android** has neither of those — no sidecar, no dev proxy of its own. It needs an Android
SDK and NDK this environment did not have; `.github/workflows/ci.yml`'s `android` job is
what actually builds it. Locally, with both installed:

```bash
cd apps/desktop
npx tauri android init    # generates src-tauri/gen/android, gitignored
npx tauri android dev     # a device or emulator, pointed at whatever ServerConnect gives it
```

`npm run typecheck`, `npm run test` and `npm run build` are what CI runs, plus
`cargo fmt --check` and `cargo clippy -- -D warnings` in `src-tauri`. All five are worth
running before a commit: `tsc` and the bundler resolve paths independently, so one can pass
while the other fails; the tests are where the decisions about what a person is shown are
actually checked; and until Sprint 82 nothing compiled the Rust, which is how the shell came
to reference icons that were not in the repository. Once `bundle.externalBin` was set
(Sprint 83), even `cargo check` needs the sidecar built first —
`bash scripts/build_sidecar.sh` from the repo root, or see `installer/README.md`.

The avatar can be worked on in a browser at `http://localhost:1420/#avatar`. In the packaged
app it is opened by Tauri and marked with an injected flag instead, so nothing depends on a
URL fragment surviving three platforms.

## Not built yet

Voice capture, the embedded device node, and the screen-context channel. The window speaks
to the API over HTTP and WebSocket only; when the node moves in-process, it will speak
TNP/1 from the Rust side without changing anything in `src/`.

The Android build itself has never run: no SDK, NDK or emulator existed in the environment
it was built in, so `tauri android init` has never produced `gen/android`, and no `.apk` has
been installed on a device. See ADR 0057 for what was verified instead (a clean desktop
rebuild after the `lib.rs`/`main.rs` split) and CI's `android` job for the first thing that
attempts a real build. There is also no avatar on Android — Sprint 82's second OS window has
no equivalent in Android's one-Activity-per-app model.

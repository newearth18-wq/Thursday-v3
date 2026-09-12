/**
 * What a surface may offer (§38, §64, V20).
 *
 * The Android build has existed since Sprint 87 and ships **this** frontend: CI cross-compiles
 * `apps/desktop` into an `.apk` on every commit (ADR 0057 — a phone is a screen, not a machine
 * Thursday runs on). What it has never had is a layout for a phone, so what the owner got was
 * a desktop window at 400px: a nav pinned to `bottom-6 left-8`, a 21rem drawer over a 900px
 * canvas. `apps/mobile/README.md` meanwhile described a Flutter app that does not exist.
 *
 * So this is not a new client. It is the same one, told which surface it is on.
 *
 * ## The rule that is not about layout
 *
 * **A phone may approve. It may not grant standing permission.**
 *
 * "Approve once" answers a question the owner can see described in front of them. "Always
 * allow" is a durable grant of authority that outlives the moment, and the moment is the
 * problem: a phone approval happens away from the desk, usually in a hurry, often on a device
 * that is easier to lose, lend or read over the shoulder than the machine the action will run
 * on. It is the decision most likely to be regretted and least likely to be revisited.
 *
 * This is the same reasoning ADR 0008 already applied to the *action* — an ASK_ALWAYS action
 * offers only a one-time answer, because remembering it would defeat the point of asking. Here
 * it applies to the *surface*.
 *
 * **It is a discipline in this client, not a boundary the core enforces**, and saying so
 * matters. The server cannot tell which surface a request came from without trusting a header
 * the client sets, and a header the client sets is not a security control — inventing one and
 * calling it security would be worse than this honest limit. What this protects against is the
 * owner in a hurry, which is the realistic failure. It is not a defence against a stolen phone:
 * for that, revoke the device (§80).
 */

/**
 * Below this, the desktop layout stops being usable rather than merely cramped: the drawer is
 * 21rem and the canvas the workflow builder draws on is 900 units wide.
 */
export const PHONE_MAX_WIDTH = 640;

export type Surface = "desktop" | "phone";

export function surfaceFor(width: number): Surface {
  return width <= PHONE_MAX_WIDTH ? "phone" : "desktop";
}

/**
 * Which approval scopes this surface may offer, narrowed from what the server said.
 *
 * Never widens: a surface cannot offer a scope the engine did not, and `always` disappears on
 * a phone even when the engine would have accepted it.
 */
export function scopesFor(offered: string[] | undefined, surface: Surface): string[] {
  const fromServer = offered?.length ? offered : ["once"];
  if (surface === "phone") return fromServer.filter((scope) => scope !== "always");
  return fromServer;
}

export function mayGrantStanding(offered: string[] | undefined, surface: Surface): boolean {
  return scopesFor(offered, surface).includes("always");
}

/**
 * Why "always allow" is missing, in the owner's words.
 *
 * Shown rather than left to be noticed: a control that is silently absent teaches nothing, and
 * the owner is entitled to know the rule rather than to conclude the app is broken.
 */
export const NO_STANDING_ON_PHONE =
  "จากมือถืออนุมัติได้ครั้งเดียวเท่านั้น — การให้สิทธิ์ถาวรควรตัดสินใจตอนอยู่หน้าเครื่อง";

/**
 * Which machine an action would run on.
 *
 * At a desk the owner is *at* the machine; on a phone they are not, and "delete these files"
 * without "on Office-PC" is a different question. An unknown device is said out loud rather
 * than rendered as a dash, which reads as "nothing" when it means "not stated".
 */
export function machineFor(deviceName: string | null | undefined): string {
  return deviceName?.trim() || "ไม่ได้ระบุเครื่อง";
}

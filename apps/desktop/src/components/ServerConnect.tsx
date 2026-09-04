import { useState } from "react";
import { serverOverride, setServerOverride } from "@/lib/origin";

/**
 * "Where is Thursday?" (Sprint 84, ADR 0057).
 *
 * A phone has no backend of its own to default to — Thursday runs on the owner's PC or
 * home hub, and this is the one screen that asks where. It is reached by a real, observed
 * signal (`useRealtime`'s `needsSetup`: several connection attempts have failed in a row),
 * never by asking what platform the app is running on. That also means it can appear on
 * desktop, if a sidecar somehow died after its own health check passed — pre-filling
 * whatever address was already stored is what makes that not a dead end.
 *
 * Deliberately plain: a phone number pad, one field, one button. Nothing here is styled to
 * match the HUD, because a person reading an IP address off a router's label is not in the
 * mood the HUD is designed for.
 */
export function ServerConnect() {
  const existing = serverOverride();
  const [address, setAddress] = useState(existing?.replace(/^https?:\/\//, "") ?? "");
  const [submitted, setSubmitted] = useState(false);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = address.trim();
    if (!trimmed) return;
    setServerOverride(trimmed);
    setSubmitted(true);
    // A reload rather than threading a "reconnect with this URL" action through the
    // socket hook: `API_ORIGIN`/`WS_ORIGIN` are computed once, at module load, from
    // whatever `origin.ts` finds in storage — the simplest way to make a freshly stored
    // address take effect everywhere that reads them is to let the module load again.
    window.location.reload();
  };

  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center gap-6 bg-ink-void px-8 text-center">
      <div className="max-w-sm space-y-2">
        <h1 className="text-lg font-medium text-slate-100">
          {existing ? "Thursday isn't answering" : "Where is Thursday?"}
        </h1>
        <p className="text-sm text-slate-500">
          {existing
            ? `${address || existing} didn't respond. Check the address below, or the machine it's running on.`
            : "Thursday runs on your computer, not on this phone. Enter the address it's" +
              " reachable at — the same one you'd type into a browser on that machine."}
        </p>
      </div>

      <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-3">
        <input
          value={address}
          onChange={(event) => setAddress(event.target.value)}
          placeholder="192.168.1.42:8000"
          autoFocus
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          className="rounded-xl border border-white/10 bg-ink-900/70 px-4 py-3 text-center
                     text-sm text-slate-100 outline-none placeholder:text-slate-600
                     focus:border-thursday/40 focus:ring-1 focus:ring-thursday/30"
        />
        <button
          type="submit"
          disabled={address.trim() === "" || submitted}
          className="rounded-xl bg-thursday/80 px-4 py-3 text-sm text-white
                     hover:bg-thursday disabled:opacity-30"
        >
          {submitted ? "Connecting…" : "Connect"}
        </button>
      </form>

      <p className="max-w-sm text-xs text-slate-700">
        Thursday's desktop app shows this address in its tray menu, or ask whoever set it up.
      </p>
    </div>
  );
}

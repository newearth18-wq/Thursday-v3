/**
 * The few plain-language constants this side of the socket needs.
 *
 * Sprint 65's phrases are produced on the server, where the allowlist lives, and arrive
 * already written. Only the fallback is duplicated here, because the client has to draw
 * *something* in the gap between an event with no phrase and the next one — and the same
 * rule applies: vague and always true beats an internal name.
 *
 * Kept to one string on purpose. A second copy of the phrase table would be a second
 * allowlist to forget to update, which is how the leak gets back in.
 */

/** `thursday_core.plain.WORKING`. "Working on it." */
export const WORKING = "กำลังทำงาน";

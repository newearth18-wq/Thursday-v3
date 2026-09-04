import { useEffect, useRef } from "react";
import { build, step, type Live, type MindNode } from "@/lib/graph";
import { APPEARANCE, withAlpha } from "@/lib/mood";
import type { Expression } from "@/lib/types";

/**
 * Thursday, drawn as a mind (Sprint 81).
 *
 * A core that breathes, rings that turn, and one node for every real thing Thursday
 * currently has — the Obsidian graph the owner asked for, where every dot is something
 * rather than something decorative. Colour and motion come from the derived expression, so
 * this component decides nothing about how Thursday feels; it only draws it.
 *
 * Canvas rather than DOM because there are three hundred draws a second here and a node is
 * a glow rather than a box. Everything that has to be *read* — the activity line, the
 * counts, the conversation — stays in the DOM, where text is crisp and selectable.
 */
export function BrainGraph({
  live,
  expression,
  connected,
}: {
  live: Live;
  expression: Expression;
  connected: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const nodes = useRef<MindNode[]>([]);
  // Read inside the animation loop rather than closed over, so a new expression does not
  // restart the loop and reset the clock — which would make the rings visibly stutter every
  // time anything changed.
  const latest = useRef({ live, expression, connected });
  latest.current = { live, expression, connected };

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext("2d");
    // No 2d context: an old webview, or a test environment. Draw nothing rather than throw
    // — the readable half of the interface is all in the DOM and still works.
    if (!element || !context) return;

    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    let frame = 0;
    let previous = performance.now();
    let clock = 0;

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      element.width = element.clientWidth * ratio;
      element.height = element.clientHeight * ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    const draw = (now: number) => {
      const dt = Math.min((now - previous) / 16.667, 3);
      previous = now;
      if (!still) clock += dt;

      const { live: current, expression: mood, connected: online } = latest.current;
      const look = APPEARANCE[mood.mood];
      // A disconnected Thursday is drawn dim rather than differently: the shape stays so
      // the owner can see it is the same thing, unreachable.
      const alpha = online ? 1 : 0.35;

      nodes.current = step(build(nodes.current, current), still ? 0 : dt);

      const width = element.clientWidth;
      const height = element.clientHeight;
      const cx = width / 2;
      const cy = height / 2;
      // One scale for the whole picture, so the graph fills a laptop and a monitor alike.
      const scale = Math.min(width, height) / 1000;

      context.clearRect(0, 0, width, height);

      // ---------------------------------------------------------------- the ambient glow
      const halo = context.createRadialGradient(cx, cy, 0, cx, cy, Math.min(width, height) * 0.55);
      halo.addColorStop(0, withAlpha(look.colour, 0.16 * alpha));
      halo.addColorStop(0.45, withAlpha(look.colour, 0.05 * alpha));
      halo.addColorStop(1, "#00000000");
      context.fillStyle = halo;
      context.fillRect(0, 0, width, height);

      const at = (node: MindNode) => ({ x: cx + node.x * scale, y: cy + node.y * scale });

      // -------------------------------------------------------------------- the synapses
      context.lineWidth = 1;
      for (const node of nodes.current) {
        if (node.kind === "core") continue;
        const point = at(node);
        const line = context.createLinearGradient(cx, cy, point.x, point.y);
        line.addColorStop(0, withAlpha(look.colour, 0.5 * alpha));
        line.addColorStop(1, withAlpha(look.colour, 0.06 * alpha));
        context.strokeStyle = line;
        context.beginPath();
        context.moveTo(cx, cy);
        context.lineTo(point.x, point.y);
        context.stroke();

        // A pulse travelling out along the link, one per node, offset by its position so
        // they do not march in step. Only while something is actually happening.
        if (mood.running > 0 && !still) {
          const phase = ((clock * 0.006 + node.x * 0.002) % 1 + 1) % 1;
          context.beginPath();
          context.arc(
            cx + (point.x - cx) * phase,
            cy + (point.y - cy) * phase,
            1.6,
            0,
            Math.PI * 2,
          );
          context.fillStyle = withAlpha(look.colour, (1 - phase) * 0.8 * alpha);
          context.fill();
        }
      }

      // ------------------------------------------------------------------------ the rings
      const base = 165 * scale;
      const rings: Array<[number, number, number, number]> = [
        // radius, arc length, direction, thickness
        [base * 1.35, 1.7, 1, 1.4],
        [base * 1.62, 2.6, -1, 1],
        [base * 1.95, 0.9, 1, 2.2],
      ];
      rings.forEach(([radius, sweep, direction, thickness], index) => {
        const turn = ((clock / 60 / look.spin) * Math.PI * 2 * direction) + index * 1.1;
        context.beginPath();
        context.arc(cx, cy, radius, turn, turn + sweep);
        context.strokeStyle = withAlpha(look.colour, 0.55 * alpha);
        context.lineWidth = thickness;
        context.stroke();

        // The faint full circle behind each arc, so the ring reads as an orbit rather than
        // as a stray stroke.
        context.beginPath();
        context.arc(cx, cy, radius, 0, Math.PI * 2);
        context.strokeStyle = withAlpha(look.colour, 0.07 * alpha);
        context.lineWidth = 1;
        context.stroke();
      });

      // Graduations on the outer orbit. Fixed, not turning: something has to stay still
      // or the whole picture reads as spinning rather than as a thing with parts.
      const ticks = 72;
      for (let i = 0; i < ticks; i += 1) {
        const angle = (i / ticks) * Math.PI * 2;
        const inner = base * 2.25;
        const outer = inner + (i % 6 === 0 ? 9 : 4);
        context.beginPath();
        context.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
        context.lineTo(cx + Math.cos(angle) * outer, cy + Math.sin(angle) * outer);
        context.strokeStyle = withAlpha(look.colour, (i % 6 === 0 ? 0.3 : 0.13) * alpha);
        context.lineWidth = 1;
        context.stroke();
      }

      // ------------------------------------------------------------------------- the core
      const breath = 1 + Math.sin(clock / 40) * look.breath * (0.5 + mood.intensity);
      const radius = base * 0.62 * breath;

      // The bloom around it, well outside the body: this is most of what makes the core
      // read as light rather than as a filled circle.
      const bloom = context.createRadialGradient(cx, cy, radius * 0.5, cx, cy, radius * 2.6);
      bloom.addColorStop(0, withAlpha(look.colour, 0.4 * alpha));
      bloom.addColorStop(0.5, withAlpha(look.colour, 0.1 * alpha));
      bloom.addColorStop(1, "#00000000");
      context.beginPath();
      context.arc(cx, cy, radius * 2.6, 0, Math.PI * 2);
      context.fillStyle = bloom;
      context.fill();

      const body = context.createRadialGradient(cx, cy, radius * 0.05, cx, cy, radius);
      body.addColorStop(0, withAlpha("#ffffff", 0.95 * alpha));
      body.addColorStop(0.28, withAlpha(look.colour, 0.95 * alpha));
      body.addColorStop(0.75, withAlpha(look.colour, 0.5 * alpha));
      body.addColorStop(1, withAlpha(look.colour, 0.04 * alpha));
      context.beginPath();
      context.arc(cx, cy, radius, 0, Math.PI * 2);
      context.fillStyle = body;
      context.fill();

      // Meridians, turning the other way to the rings. They are what make a flat disc read
      // as a sphere with something moving inside it.
      for (let i = 0; i < 7; i += 1) {
        const spread = Math.cos(clock / 150 + (i * Math.PI) / 7);
        context.beginPath();
        context.ellipse(cx, cy, Math.abs(radius * spread * 0.92), radius * 0.92, 0, 0, Math.PI * 2);
        context.strokeStyle = withAlpha("#ffffff", 0.11 * alpha);
        context.lineWidth = 0.7;
        context.stroke();
      }

      // The rim, brightest where the shell is edge-on.
      context.beginPath();
      context.arc(cx, cy, radius * 0.94, 0, Math.PI * 2);
      context.strokeStyle = withAlpha("#ffffff", 0.22 * alpha);
      context.lineWidth = 1;
      context.stroke();

      // ------------------------------------------------------------------------ the nodes
      context.textAlign = "center";
      context.textBaseline = "middle";
      for (const node of nodes.current) {
        if (node.kind === "core") continue;
        const point = at(node);
        const size = node.kind === "work" ? 5 : node.kind === "waiting" ? 4.5 : 3.5;

        const glow = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, size * 5);
        glow.addColorStop(0, withAlpha(look.colour, 0.55 * alpha));
        glow.addColorStop(1, "#00000000");
        context.fillStyle = glow;
        context.beginPath();
        context.arc(point.x, point.y, size * 5, 0, Math.PI * 2);
        context.fill();

        context.beginPath();
        context.arc(point.x, point.y, size, 0, Math.PI * 2);
        context.fillStyle = withAlpha("#e2e8f0", 0.92 * alpha);
        context.fill();

        context.font = "11px Inter, system-ui, sans-serif";
        context.fillStyle = withAlpha("#cbd5e1", 0.72 * alpha);
        // Long labels are cut rather than wrapped: a task title is a sentence, and a
        // paragraph floating in a constellation is unreadable at any size.
        const label = node.label.length > 26 ? `${node.label.slice(0, 25)}…` : node.label;
        context.fillText(label, point.x, point.y + size + 11);
      }

      frame = requestAnimationFrame(draw);
    };

    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={canvas} className="absolute inset-0 h-full w-full" aria-hidden="true" />;
}

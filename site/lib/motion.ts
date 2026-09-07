/**
 * The motion system, as numbers.
 *
 * Two curves, four durations, one stagger rule. Everything animated on the site — CSS
 * entrances, motion values, the canvas — takes its timing from here or from the matching
 * custom properties in globals.css, so the whole site decelerates the same way.
 *
 *  - EXIT_IN  (0.22, 1, 0.36, 1): a fast start and a long settle. Used for everything that
 *    enters or moves in response to the reader. It reads as weight, not bounce.
 *  - LINEAR-ish scrubbing: scroll-driven values are mapped linearly and left to the reader's
 *    hand. Easing a scrubbed value makes it lag the scroll, which reads as broken.
 *
 * Durations are quantized so nothing sits between steps:
 *    tap 120ms · quick 240ms · settle 600ms · reveal 900ms
 * Staggers are 40ms per item, capped at 12 items, so a long list never makes the reader
 * wait for its tail.
 */
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
export const EASE_IN_OUT = [0.65, 0, 0.35, 1] as const;

export const DURATION = {
  tap: 0.12,
  quick: 0.24,
  settle: 0.6,
  reveal: 0.9,
} as const;

export const STAGGER = 0.04;
export const STAGGER_CAP = 12;

export function staggerDelay(index: number, step = STAGGER): number {
  return Math.min(index, STAGGER_CAP) * step;
}

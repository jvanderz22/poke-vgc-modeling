// Left is left. Every position on the battle page is as the viewer sees the TV: the field draws
// it this way, the stepper numbers its options this way, and both ask this file. The journal
// still records field slots (`{side, slot}`), so this is the one place a screen position and a
// slot meet, and the one place to check against footage.
//
// Today it is the identity on both sides: the opponent's slot 0 is drawn on the left. If the
// cartridge turns out to mirror the opponent's side, `slotAt` is the only thing that changes.

export type Side = "p1" | "p2";
export type Pos = { side: Side; slot: number };

/** The two rows of the field, top first: the opponent's (or P2's) across the top, as on the TV. */
export const ROWS: readonly [Side, Side] = ["p2", "p1"];

/** The field slot drawn `x` places from the left (0 or 1) in `side`'s row. */
export function slotAt(_side: Side, x: number): number {
  return x;
}

/** Where a slot is drawn, as a word. */
export function across(side: Side, slot: number): "left" | "right" {
  return slotAt(side, 0) === slot ? "left" : "right";
}

/** Every slot on the field in reading order: the top row left to right, then the bottom row. */
export function screenOrder(): Pos[] {
  return ROWS.flatMap((side) => [0, 1].map((x) => ({ side, slot: slotAt(side, x) })));
}

// A watched video is laid out differently: the two players side by side, all four plates in one
// line along the bottom. The left player's run outward-in from the left edge (slot 0 first) and
// the right player's mirror them, slot 0 on the right edge (video-findings, "The right side's
// plates run the other way").

/** `side`'s two field slots as the video draws them, left to right, with `left` the player on
 *  the left of the screen. */
export function videoSlots(side: Side, left: Side): [number, number] {
  return side === left ? [0, 1] : [1, 0];
}

/** Where a slot is drawn on the video, as a word. */
export function videoAcross(side: Side, slot: number, left: Side): "left" | "right" {
  return videoSlots(side, left)[0] === slot ? "left" : "right";
}

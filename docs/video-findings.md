# Video findings: what a battle broadcast shows

_Written 2026-10-14._ The screen survey of [PLAN-video](PLAN-video.md) step 1: what a battle video
puts on screen, where, and for how long, measured on the footage below. A snapshot, like the other
findings documents. Standing facts it turns up go in [format-facts](format-facts.md).

## The footage

Two games from one source: the Louisville Regional Championships broadcast on YouTube (Swiss round
5 game 1, and round 6 game 1). Both were recorded with macOS screen recording of the YouTube player,
not the watch page's recorder, so they have no `.json` of video times. They are 2342×1324 at about
60 fps, 12.6 and 14.0 minutes long, H.264 in QuickTime (`data/video/`, gitignored).

**One source is one layout.** Everything below holds for both games, and nothing yet says another
broadcast looks the same. Positions are fractions of the frame, which is the 16:9 player.

## What is on screen, in order

1. **The two sheets, one at a time,** about 75 s each, over the player's camera. The layout is the
   one `vgc.video.sheet` was built on: a name bar, then six cards in a 2×3 grid. The grid is on the
   right for one player and on the left for the other, which the position-free reader handles. Each
   sheet slides in over about a second, so a frame mid-slide reads partly.
2. Desk shots, then the game's "VS" screen.
3. **The battle,** in the game's own view of one player, with the broadcast's overlay round it.
4. **"GAME WINNER" and the winner's name,** over the camera.

## The battle screen

| What | Where (x, y) | Notes |
| --- | --- | --- |
| Player names, flags, records | header, (0.22–0.74, 0.10–0.13) | Left name is the left side's player |
| Field: weather or terrain, turns left | (0.35, 0.05) and (0.58, 0.05) | "Rain 3/5". With two up, the slot alternates between them |
| A side's screen, turns left | beside that side, (0.63–0.89, 0.72) for the right | "Light Screen 1/5" |
| **Message** | left-aligned at x 0.15, y 0.73–0.79 | White text straight over the scene, no box behind it |
| Decision marker | centred, (0.40–0.60, 0.80–0.84) | "Trainers are choosing actions" |
| Timers | (0.06–0.12, 0.82–0.86) left, (0.87–0.93) right | Labelled with a name or just "Trainer" |
| **Nameplates: species** | y 0.88–0.91; x 0.02, 0.18 left, 0.67, 0.83 right | |
| **HP, as a percentage in text** | y 0.92–0.97, under each name | "38%" |
| Ability and item banners | beside the owner's side, mid-height | "Indeedee's Rocky Helmet", "Archaludon's Stamina" |
| Each team's six | columns down both edges | Icons, greyed when fainted |

**HP is printed as a number.** PLAN-video decision 4 assumed HP would be measured off bar pixels,
and kept the damage channel off until that was shown to be within a point. A printed percentage is
the same number Showdown's log carries, so the channel's 1-point slack fits it as it stands. What
still has to be measured is how often OCR misreads the digits (G-hp), not pixel error.

**When it shows:** all four nameplates and HPs are up at each decision point ("Trainers are choosing
actions"). During a move they are hidden, and only the Pokémon whose HP changed has its plate shown.
Its number counts down to the new value over about a second (100 → 82 → 75 → 66 → 60), then holds.
The value to read is the settled one.

**The right side's plates run the other way.** The left plates are in the order the leads were sent
out (L0 the first); the right ones are reversed (R1 the first). Three games of three: Louisville
r5 and r6 game 1, and JET7oW_RiLI game 2 (2026-10-16). So the plates at the first decision point
give the leads, slots included, when a send-out message was missed: one that reading too slowly
missed had left that game 2 unstarted, with nothing after it read (`assemble._lead_from_plates`).

## Messages

Each message is up for 1.5–2 s, with gaps of about 0.25–2 s between them. At 4 frames a second
every message in a 45-second stretch was caught on at least four frames. Kinds seen:

- Moves: "Gardevoir used Hyper Voice!", "The opposing Archaludon used Snarl!"
- Effectiveness and crits: "It's super effective on the opposing Grimmsnarl!", "It's extremely
  effective on Sneasler!" (Champions' wording for 4×), "A critical hit!"
- Stats: "Gardevoir and Indeedee's Sp. Atk fell!" (one line for both), "The opposing Archaludon's
  Defense rose!"
- Switching: "Go! Indeedee!", "Indeedee, come back!", "The Trainer sent out Archaludon the Great
  Friend!", "The Trainer sent out Raichu and Grimmsnarl the Treasure Hunter!" (two at once)
- Faints: "Gardevoir fainted!"
- Mega Evolution: "The opposing Raichu's Raichunite X is reacting to Trainer's Omni Ring!", then
  "The opposing Raichu has Mega Evolved into Mega Raichu!"
- Field: "The battlefield got weird!" (Psychic Terrain)
- Other: "Gardevoir protected itself!", "Indeedee became the center of attention!", "Indeedee is
  ready to help Gardevoir!", "The opposing Raichu was damaged by the recoil!"

Round 6, read once the reader existed, added more: "The opposing Rotom went back to Trainer!"
(Volt Switch), "It started to rain!" and "The rain stopped.", "A sandstorm kicked up!", "Pelipper
is buffeted by the sandstorm!", "Gardevoir avoided the attack!", "The opposing Tyranitar knocked off
Pelipper's Damp Rock!", "Sneasler returned its stats to normal using its White Herb!", "The opposing
Indeedee had its energy drained!", "The opposing Archaludon absorbed electricity!" (Electro Shot
charging), and a forfeit: "The battle has ended due to a forfeit." then "The Trainer lost!". From
the left player's view the right one is "the Trainer". `said.CONFIRMED` lists what footage has
shown; every other phrasing the reader knows is the main series' and reads provisionally.

Watched in production (2026-10-15, Enrnsque Grimaldo vs Alex Madara): "The opposing Kingambit's
Attack rose sharply!" (Defiant; the reader had the words the wrong way round), "Floette is radiating
a fairy aura!" (Mega Floette's Fairy Aura, as a message rather than a banner), and "Floette is
paralyzed, so it may be unable to move!" (Champions' wording, not the main series' "is paralyzed! It
may be unable to move!"). OCR also read a plate's "88%" as "%88", which left Struggle Bug's chip on
Incineroar unread.

Watched in production (2026-10-16, at 7:19:06 of JET7oW_RiLI): "Floette drank down all the matcha
that Sinistcha made!" (Hospitality, on Sinistcha's arrival). The ability announces as a message,
not a banner, and names its host. The still plates put Floette's HP right at the next decision
point anyway, but as an unexplained heal, and the switch-in question went unanswered.

Watched in production (2026-10-16, at 7:34:58 of JET7oW_RiLI, Louisville round 8): a forfeit read
"The battle has ended due to a forfeit." then "Alex™ lost!", not "The Trainer lost!". The right
player is called whatever their send-outs called them ("Alex™ sent out …"), and that in-game name
needn't match the broadcast's ("Alex Madara"). So the reader keeps the name from the send-outs and
matches the loser against it, ignoring the ™ (OCR may read it as "TM").

**The broadcast can cut away mid-turn.** In round 6 nothing was on screen for about 30 seconds at
the end of a turn, and the opposing Sneasler fainted unseen (sandstorm, from 21%). The next thing
shown was its replacement. The reader works such a faint out from the next decision point's plates
and logs it for review.

**Some events have no message.** Rocky Helmet's damage and Leftovers' healing showed as a banner and
an HP change only. A reader that follows only the message line misses them, so banners and HP are
read too.

## Names

- **"The opposing" marks the right side.** The game is shown from the left player's side, so
  messages name the left side's Pokémon bare and the right side's as "the opposing …".
- **Nameplates show the base species:** "Rotom" for Rotom-Heat, "Basculegion" for Basculegion-F,
  "Indeedee" for Indeedee-F. They resolve through the two sheets: each side has one of each.
- **A send-out may carry a title:** "Archaludon the Great Friend", "Grimmsnarl the Treasure Hunter".
  The title follows the species name.
- No nicknames in either game.
- **Player names don't tie a side to a sheet.** The broadcast's names (on the sheets and the
  winner banner) needn't match the in-game trainer names, and the watch page gives the first sheet
  it reads to player 1 whichever side that player is on. So the reader works out which sheet is on
  the left from the Pokémon: the first message only one way round can read (the leads, usually).
  Both games read identically with their sheets given either way round. A side with nothing left
  ends the game from the board; the banner's name is used only where the board can't say (a
  forfeit by banner), and a name matching neither player leaves the winner unsaid.

## OCR on these frames

On six full-resolution frames, RapidOCR read every message, nameplate, HP percentage, timer and
field line exactly. Over a 45-second stretch at 4 fps, a message read garbled on one frame in
four at most ("Sp. At fel!" between correct reads), and a counting HP number read wrong mid-count
("%78%"). Both are settle problems: a reading counts once it is the same over consecutive frames.

**Speed is the problem.** The full-width bottom band (30% of the frame) took 1.9 s per frame on
this Mac. That is too slow for 4 fps live, and 1.8 hours for one game offline. The reader has to
OCR only the regions above, scaled down, and only when a region has changed.

## Turns

Nothing on screen counts turns. "Trainers are choosing actions" marks each decision point, so a turn
is the stretch from one to the next. What fainted Pokémon are replaced by comes after "fainted!",
with a send-out message.

## Reading live (step 4)

_Added 2026-10-14._ Both games were run through the live path (`vgc.video.reading`) the way the
watch page sends them: one frame every 0.25 s of video, scaled to 1200 wide and JPEG-compressed at
0.85. Each live journal was compared with the offline read of the same game.

**The first frame of new text is often mid-fade.** It read as garbage ("ie eds see ss es Sdo t"
for "Light Screen made the opposing side stronger against special moves!"). The frames after it
differed from it by less than the change threshold, so the garbage was kept and the message lost.
The screen layer now reads a region again on the frame after any change. That costs one more OCR
per change, and it recovered messages the offline read had also lost: misses fell from 11 to 1
(round 5) and from 6 to 2 (round 6). New reads included a terrain, a crit, Sandstorm starting,
two Sp. Atk drops, a drain heal, and two of Golisopod's Protects failing. The
footage confirms the failed Protects ("But it failed!" is on screen at 13:29).

**A frame wider than the reader's 1200 was read at its own size.** `scale` only shrank frames
more than 10% wider, so a 1280 frame was read at a geometry nothing was tuned on. Frames are now
always brought to 1200, and the page sends 1200.

**OCR noise cut a move off from its damage.** A one-character fragment between "used Rock Slide!"
and the plate showing its damage closed the move, so the damage had nothing to belong to, and the
next decision point's plates didn't show that Pokémon. A fragment of noise no longer ends a move.

**A decision point is closed by the clock live.** Offline, a turn is written when the next event
arrives; live, that is after the players have chosen. The live reader closes a decision point
1.75 s after its marker, once its plates have settled.

Differences left between the live and offline reads, other than the above: one HP read a point
apart (Leftovers to 65 or 66; G-hp will say which), and one hit whose plate showed no number
until the next decision point, so its damage is right but written as unexplained.

**Speed on this Mac** (round 6, nothing else running): 124 ms a frame at the median, 309 ms at
p90, 516 ms at p99, 731 ms at most, so 4 frames a second about keeps up. Two games read at once
roughly doubled it. The page sends a frame only once the last is answered, so a
slower host reads fewer frames; the page warns below 2 a second. The Fly machine
(shared-cpu-4x) read about one frame in 4 s ([below](#on-the-deployed-machine-2026-10-15)).

## Served (2026-10-15)

The first use on the deployed app ended in "Failed to fetch": the server was killed for memory
(1.85 GB of a 2 GB machine) while reading sheets, and every open request died with it. Two
causes together. The page's sheet loop marked a read in flight only after encoding the frame,
which outlasts its 250 ms tick on a large capture, so several frames went at once, again after
each answer. And the sheet reader's detector took a full-size capture whole: 670 MB at its peak
for one 2342×1324 frame. The loop now claims its slot first and sends at most 1600 wide; the
detector caps the long side at 1280 (366 MB, faster, and it read two moves the full size had left
blank, both confirmed in the footage); and the server turns away a sheet read when one is running
and one waiting (429). Idle, the server holds 70 MB.

## In the browser (2026-10-15)

Watching made the video stutter and the browser heavy. The capture was the cost, not the reading.
Measured on a probe page in Chrome 141 at 2× (a 900 px box, cropped like the player), CPU of the
whole browser over 12 s, median of three, 23 points of it the probe page itself:

| Capture | Frames | CPU |
|---|---|---|
| none | | 23% |
| 30 fps, no size limit (as it was) | 3150×1770 | 94% |
| 8 fps, no size limit | 3150×1770 | 77% |
| 8 fps, at most 1600 across | 1600×900 | 40% |
| 30 fps at most 1600, lowered to 8 after | 1600×900 | 52% (41–76) |

With no size limit Chrome renders the captured tab at 3.5× (`screenPixelRatio`) to capture the
crop sharply, where the screen is 2×: the player, video and all, drawn at three times the pixels. A
limit asked for when the capture starts applies to the cropped player, not the tab, and keeps its
shape (width and height both limited; width alone gave 1422×800). Limits applied after it starts
only drop and shrink frames: the render stays at 3.5×, and a frame rate can't be raised past the
one it started with, so the capture can't switch to 30 for a clip.

Taking a frame on the page (draw to a canvas, `toBlob`) held its thread for a median 16–34 ms,
up to 87, several times a second. In a worker it holds the page for under 3 ms and left no task
over 50 ms.

## On the deployed machine (2026-10-15)

Watching on the deployed app read about one frame every 4 s ("the video skipped ahead" every
3.5–4 s in the review log). The Fly machine (shared-cpu-4x) was throttled: 373 s after a restart,
`/proc/stat` showed each core about 52 s running the app, 96 s stolen by the host and 227 s idle.
The CPU was the web process (194 CPU-s); the three solver processes had about 0.15 s each.

Much of it was onnxruntime's thread pool, which by default takes a thread per core and spins them
while they wait. CPU per frame of the battle reader, on 60 s of round 6 (232–292 s, the "choosing
actions" screen and a turn) at 4 fps on an x86 laptop (i7-9750H):

| onnxruntime | CPU a frame (median) | Wall a frame (median) |
|---|---|---|
| default (a thread per core, spinning) | 2250 ms | 417 ms |
| spinning off | 1147 ms | 332 ms |
| 2 threads, spinning off | 763 ms | 428 ms |
| 1 thread, spinning off | 704 ms | 701 ms |

Both readers now use 2 threads with spinning off (`vgc.video.ocr`). Whether the deployed machine
then keeps up is not yet measured.

The same run found the change check barely skipping: OCR ran on 238 of the 239 frames. The
"choosing actions" screen is animated (trainers, Pokémon, lights), and the banner regions saw their
white pixels change on 84–90% of frames with no banner up.

The check now looks only at text-like pixels: near-white in thin strokes (gone after a 5×5
opening) with something dark nearby (`screen._text`). Over 5 minutes of round 6 (200–500 s, 1,197
frames, 2 threads, three runs side by side):

| Change check | OCR calls | CPU | Events against the white check |
|---|---|---|---|
| white pixels (as it was) | 4,185 | 1,472 s | 141 |
| text-like pixels | 2,277 | 1,086 s | 138 matched; 3 lost and 2 added, all misreads of the sheet cards and scoreboard |
| text-like, and a region under 0.2% text-like skips OCR | 1,987 | 1,009 s | lost "A critical hit!", and the small field region flickered empty, re-reporting the field 11 times |

The third is not used.

With both changes deployed, a watch read cleanly for about 5 minutes and then dropped frames again.
From the restart to the end of that watch the machine used 314 CPU-s, nearly all in the 6 minutes of
reading: about 0.85 of a core, several times a shared-cpu-4x's baseline. It ran on burst credit until
that ran out. Two other cuts were tried on 5 minutes of round 6 and not used: skipping OCR on banner
regions with almost no text-like pixels (44 fewer calls than the text-like check, within noise), and
finding the lines from the text-like mask in place of the detector (8% less CPU, but it split
two-line messages, flickered the field and lost plates: 31 events lost, 22 added).

## Read on the page's GPU (2026-10-16)

So the page reads the frames itself. RapidOCR's pipeline is ported to TypeScript
(`frontend/src/ocr`): the same model files, served by the app (`GET /api/video/ocr`), run by
onnxruntime-web on WebGPU in the frame worker, with the server's change check, layout and threshold.
The page sends each frame with the lines of the regions it read; the server interprets them and runs
no OCR. Without WebGPU, or until the models load, the server reads the frames as before.

**Speed.** Single-threaded WASM is too slow (the page can't have threads: the YouTube player rules out
cross-origin isolation); WebGPU is not. On this laptop's Radeon Pro 555X, in Chrome 155:

| | WebGPU | WASM |
|---|---|---|
| detector, the message strip (256×960, padded as RapidOCR pads it) | 52 ms | 404 ms |
| detector, a plate (96×192) | 12 ms | 57 ms |
| recogniser, three lines | 55 ms | 446 ms |
| recogniser, four wide lines | 91 ms | 815 ms |

Over 60 s of round 5 (420–480 s, 241 frames at 4 fps), the whole of a frame's reading on the page
(change check and OCR) took 93 ms at the median, 246 at p90, 623 at most (the first frame, which
reads all nine regions); 132 ms at the median on a frame that read something, 23 on one that didn't.
The change check itself had first taken 102 ms a frame, a min/max filter comparing every pixel of
every window; counting from running sums made it 23.

**Reads.** `scripts/analysis/browser_ocr_parity.py` runs the port in Node against RapidOCR, two
readers side by side on the same crops. Over 60 s each of round 6 (200–260 s) and round 5
(420–480 s), 757 crops: the same lines in 692 (91%), the same boxes within 3 px in 735. The
differences mostly favour the port: plates read "100%" where RapidOCR read "1000%", the sheet cards
"Sp. Atk" for ".Ak", and RapidOCR's stray "." and "e" and "无房" are gone. Events: 37 of 39 the same
in round 6 (the other two the sheet cards' banner junk, now with "Sp. Atk" in it), 23 of 24 in round
5. The one only RapidOCR settled was its own misread: an HP read "%L" and "%64" became a plate with no
HP, where the port read "77%" and "79%". On the GPU in Chrome, the round 5 window's lines put through
the server's reader gave the same 22 of those 23, from the same 327 regions read.

**Download.** The models are 31 MB and onnxruntime-web's WASM 27 MB (6.7 MB compressed), once; the
models' URLs carry their hash and are cached for good. Loading them onto the GPU took 2.5–2.7 s.

## A capture that stops (2026-10-16)

Watched in production (Carson Confer vs Paul Chua, game 1, omC2XtMO8yk): the Pelipper sent out at
the end of turn 3 (2349.8 s) was never written, and nor was anything else until 2372.9: not its
Drizzle, the rain, or turn 4's decision point. Every frame was answered, at a normal rate, and the
review log had nothing for the stretch. Read offline, on the same frames, by the server's OCR and by
the page's (`rapid.ts`, in Node), the switch, the answer and the turn were all written. Holding one
picture from 2336 to 2373 while the times ran on gave exactly the production journal: nothing
until the first fresh frame, whose decision point wrote the Earth Power that had been waiting since
2331.8. So the picture had stopped and the player's clock had not. A hidden tab or a covered window
isn't painted, and its capture stops with it.

The broadcast itself never holds that still: over 300 s of the game (2070–2372 s, 4 frames a
second), no picture was the same to the pixel for more than one frame step. The page now hashes each
battle frame, holds back one unchanged for 1.5 s of playing video, and takes the video back to where
it stopped once it moves.

A second pass, rewound to 2340, read nothing either: the reader skipped every frame before where it
had got to (2372.9). A rewind to more than 2.5 s past the last thing written is now read again from
the journal. A message is up for 2 s at most, so nothing written is read twice.

## Decision gate (step 1)

This source passes: the message line, the nameplates and the HP numbers are all readable. Step 2
builds the reader for this layout as its first profile.

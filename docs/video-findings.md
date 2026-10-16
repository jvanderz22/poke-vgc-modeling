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
(shared-cpu-4x) is not measured.

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

## Decision gate (step 1)

This source passes: the message line, the nameplates and the HP numbers are all readable. Step 2
builds the reader for this layout as its first profile.

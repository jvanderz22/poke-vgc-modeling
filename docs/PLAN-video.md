# Video mode: live WP over a battle video — Plan

_Written 2026-09-29._ A sketch of what it takes to point the app at a video of an open-sheet
battle, give it the time the battle starts, and have win probability follow the battle as it plays.
It sits beside [PLAN-v3.md](PLAN-v3.md) and depends on its step 4 (Observer mode). Nothing here
is built yet.

---

## The goal

Paste a video link, the timestamp where the battle starts, and the two open team sheets. The video
plays in the page. As it plays, the app reads the battle off the screen and shows P(P1 wins), a
board and a WP line, keeping pace with playback. It runs on a host reachable from anywhere, not
only on this Mac.

**In scope:**
- open-sheet battles, watched from outside (Observer mode: both sheets known, neither spread known);
- Champions cartridge footage: a player's screen, a stream or an event broadcast;
- one battle per session, followed from the start timestamp.

**Out of scope for now:**
- closed sheets;
- Showdown battles, which have a replay log and don't need a video;
- reading the sheets off the video (step 6);
- move advice, and the solver (see decision 5).

**Two things named "observer".** In this plan, *Observer mode* means PLAN-v3 step 4: the Battle
page watching someone else's open-sheet game. It is not `vgc.data.observe.Observer`, the Showdown
protocol adapter. A cartridge video has no protocol stream, so the video reader feeds the **tap
adapter** (`vgc.battle.entry`) in the spectator perspective, exactly as a person tapping along would.

---

## What it reuses

| Piece | Where | What video mode gets from it |
| --- | --- | --- |
| The journal | `vgc.battle.entry` | The reader only has to emit entries. Derivation (Intimidate, Defiant, …) and the rest come for free. The state is a pure function of `(setup, journal)` |
| Replay by prefix | `entry.replay(…, upto=)` | Seeking backward costs nothing: truncate to the entries read before that video time |
| Questions with "not sure" | `entry.Question` | The reader answers only from what the text shows (for example, switch-in announcement order). Otherwise it answers `null` |
| Spectator WP | `wp-v1f-idp5`, `in_battle_pass` | The served open-sheet model was gated on spectator rows, so no new model and no re-pin |
| Belief channels | `vgc.belief.*`, `web/live.py` | Speed read and set belief work as on the Battle page. Damage and bulk need a re-gate (decision 4) |
| Endgames stepper | `web/endgames.py`, `EndgamesPanel` | Board rendering and the WP scrubber are the starting point for the overlay |

On open sheets, nothing hidden is something the model reads. Spreads are `None` in every
training row, and the sheets fill item, ability, moves and nature. So the 24-particle average
collapses to a single evaluation. A step costs two ONNX calls, one per orientation.

---

## Shape of the system

```
Browser                                          Host
───────                                          ────
YouTube iframe (IFrame API: currentTime)
  │ tab capture, cropped to the player
  ▼
frames @ ~4 fps + video time  ── WebSocket ──▶  frame reader (layout profile)
                                                  ├─ text box   → OCR when settled
                                                  ├─ HP bars    → % when settled
                                                  └─ nameplates → who is in each slot
                                                        ▼
                                                  event grammar (closed vocabulary:
                                                  the 12 sheets' species, moves,
                                                  abilities, items, nicknames)
                                                        ▼
                                                  journal entries, each stamped
                                                  with the video time it was read
                                                        ▼
                                                  entry.replay (spectator) → WP (P1)
                                                        │
overlay: WP line, board,       ◀── WebSocket ──────────┘
"state incomplete" banner
shown at playback time
```

**Every entry carries its video time.** The overlay shows the WP of the journal prefix at or before
the current playback time, not whatever arrived last. That keeps the display honest when the reader
lags slightly, and it is what makes seeking backward work.

---

## Decisions

### 1. Frames come from tab capture, not from the host downloading the video

| | Tab capture in the browser (recommended) | Host downloads the video (yt-dlp + ffmpeg) |
| --- | --- | --- |
| Input | Link + timestamp; the page plays it | Link + timestamp |
| YouTube terms | Nothing is downloaded | Downloading is against them |
| From a cloud host | Works: the viewer's browser does the fetching | YouTube routinely bot-checks datacenter IPs ("confirm you're not a bot") |
| Sync with what you watch | Exact: frames are what is on screen | A second copy, which has to be synced to the player |
| Other sources (Twitch, a local file) | Anything that plays in a tab | Per source |
| Cost | Chrome/Edge only (Region Capture); one "share this tab" prompt per session | Works in any browser, no prompt |

Mechanics:
- `getDisplayMedia({preferCurrentTab: true})`, then `CropTarget.fromElement(player)` and
  `track.cropTo(…)`, so only the player's pixels are captured.
- Frames are downscaled to a fixed size (1280×720), sent as JPEG and tagged with
  `player.getCurrentTime()`.
- The overlay must sit **outside** the player element, or it ends up in the capture.
- The page needs HTTPS (screen capture needs a secure context). `localhost` counts as secure for
  development.
- **Fallback when a video disables embedding:** share the tab it is playing in. Time then comes from
  the wall clock since the start, and seeking is not supported.

**Fixtures come from the same pipeline.** A *record* button runs `MediaRecorder` on the captured
track and saves the result as `.webm`, plus a JSON file of video times. The offline reader (step 2)
reads those files, so tests exercise the same frames the live path sees.

### 2. Reading runs on the host

Reading on the host means one Python implementation, tested offline against fixtures and run live
unchanged. The browser only captures and sends. Bandwidth is about 1–2 Mbps up at 4 fps.

OCR runs only when a region has **changed and then settled** (the same for N frames), so its cost
tracks the number of messages, not the frame rate.

The alternative, reading in the browser with tesseract.js and canvas maths, lightens the host but
gives two code paths to keep in parity. Revisit only if host CPU becomes the limit.

### 3. Unsure means dropped, never guessed

The same rule as `entry.py`: *"a belief that quietly excludes the truth … is worse than one that
stayed wide."*
- OCR output is matched against a **closed vocabulary**: the 12 sheets' names, their 48 moves,
  12 abilities and 12 items, and the fixed message templates. A read commits only if it wins by a
  margin.
- Below the margin, the reader drops the line and counts it as missed. It never picks the nearest
  name.
- Questions are answered from what the screen showed. For example, the order in which the text box
  announced two Intimidates *is* the switch-in order, so that answer is observed, not assumed.
  Anything else is answered `null`.
- Missed events surface on screen as a count ("3 lines unread since 04:12"). They never silently
  become part of the state.

### 4. HP read from pixels, and the damage and bulk channels

The damage channel allows **one percentage point** of slack
([`belief/damage.py`](../src/vgc/belief/damage.py) line 274). That matches Showdown's public
percent. A bar read from a 720p capture may not be that accurate, and a read that is 2 points off
can rule out the true spread without anyone noticing. That is exactly the soundness failure
principle 4 warns about. A new input source is a new regime for a channel.

- Damage and bulk are **off** for video battles until step 3 measures HP-read error.
- They come back on either when reads are within ±1 point on ≥99% of settled reads, or with the
  slack widened to the measured error and the channel re-gated under it.
- The WP model reads HP only as a feature, so it tolerates this noise far better than the belief
  channels. Its tolerance is measured separately (gate G-wp).

### 5. No solver on the host

A 1v1 solve takes seconds to hours (PLAN-v3 step 6), and a small host can't carry one while also
reading frames. Video mode shows the model's number only. Step 4's page says the solver is
unavailable here rather than hiding the row. Revisit after PLAN-v3 step 6.

### 6. Hosting: a tunnel with an access gate, not hand-written auth

The app is single-user today: an unauthenticated API, a library on disk, and `web-app.md` warns
against `--host 0.0.0.0`. That doesn't change. Access is gated in front of the app:
- **First: Cloudflare Tunnel + Cloudflare Access from this Mac** (free). This gives HTTPS, a
  login in front, and reach from anywhere. It needs no new infrastructure, and the models,
  battles and library stay where they are.
- **If the Mac can't stay on:** the same Docker image on a small VM behind the same tunnel. The
  Oracle Cloud always-free ARM tier costs $0 and fits the budget; `onnxruntime` and Node both have
  aarch64 builds.

Tailscale is the alternative if only your own devices need it.

---

## Steps, in order

### 0. Observer mode (PLAN-v3 step 4, items 1 and 4)

The prerequisite. Video mode is Observer mode with a screen reader doing the tapping.
- `setup_state` takes `perspective: "spectator"` and two sheets, with `source: "sheet"` on both
  sides and nothing exact. HP is a percentage on both sides.
- WP is P(P1 wins), and the page says P1/P2.
- A third mode button on the Battle page. The form takes two sheets.

Items 2–3 (Speed and the solver with two unknown spreads) are not needed for WP. The Speed read
works with pairs deferred until they land.

**Done when:** a battle tapped in by hand in Observer mode gives the same WP trajectory as the same
battle's Showdown replay through `Observer`, for a held-out open-sheet replay transcribed by hand.
That is the parity check the video reader will be measured against.

_Size: 2–4 days._

### 1. Screen survey → `docs/video-findings.md`

I have not seen what Champions footage looks like in the sources you will use, and everything
below depends on it. Take ~10 battles from those sources and record:

1. **Layouts.** Full-frame cartridge, or broadcast overlays that scale or crop the game feed.
   Where the text box, nameplates and HP bars sit, as fractions of the game area. Facecams and
   overlays that cover any of them.
2. **Messages.** The complete template list: moves, switches, abilities, items, weather, terrain,
   faints, Mega Evolution, end of battle. Whether text types out or appears whole. How long each
   stays up (this sets the frame rate).
3. **HP.** Bar only, or numbers too, for each side. How long a drain animation takes. Bar length
   in pixels at 720p, which bounds read error before any measurement.
4. **Turn boundaries.** What on screen marks a new turn: a move menu, a timer, a turn counter, or
   nothing, in which case turns are inferred from the message sequence.
5. **Names.** Nicknames or species in messages, and whether the sheets carry the nicknames.
6. **Sides.** Which side is "near", and whether that holds within a set or across a broadcast.

**Decision gate:** if the text box is unreadable in a source (covered, too small, too brief), that
source is out of scope for v1. The survey names which sources are in.

_Size: 1–2 days._

### 2. The offline reader

`vgc video read clip.webm --times clip.json --start 12:34 --sheets p1.txt p2.txt` → a journal of
stamped entries.

- **Layout profiles** from the survey. A profile is detected from the first settled frame by
  template-matching the text box frame. A manual crop box (drag once, saved per channel) is the
  fallback.
- **Message reader:** change detection, settle, OCR (Tesseract or PaddleOCR; pick on the survey's
  frames), then the closed-vocabulary grammar (decision 3).
- **HP reader:** the bar's fill fraction per active slot, read after the bar settles and paired
  with the move or residual message that caused the change → a `damage` or `heal` entry with `pct`.
- **Battle anchor:** from the start timestamp, the first send-out messages give the leads. Each
  side's two leads match exactly one sheet, which assigns P1/P2 to near/far. The page shows the
  assignment for one-tap confirmation.
- **Determinism:** the same frames give a byte-identical journal, golden-tested like
  `test_observe_golden.py`. Unit tests on single frames cover each message template and HP reads at
  known fractions.

_Size: 1–2 weeks. Most of it is the grammar and the settle logic._

### 3. Fixtures and the parity gate

- **Fixtures:** 20 battles from at least 5 sources, recorded with the capture pipeline. Each is also
  transcribed by hand in Observer mode (step 0) with video times: the ground-truth journal.
- **Scoring:** align the read journal to the hand one by order and video time, then apply the gates
  below. Count battles and sources as units, not entries (principle 5).
- **What the power allows:** 0 wrong entries out of ~800 (20 battles × ~40) bounds the rate below
  ~0.4% (rule of three), and only if the entries are spread across sources. That is enough to catch
  a systematic misread, not to certify a rare one. `video-findings.md` says so beside the result.

_Size: 3–4 days, most of it transcription._

### 4. The live session

- **`POST /api/watch`** takes `{video, start, sheets}` and creates an Observer battle with a
  `source: "video"` flag. **`WS /api/watch/{id}`** carries frames in and
  `{entries, wp, missed, banner}` out.
- **Page `/watch`:**
  - the embedded player cued to `start`;
  - a *Start reading* button that asks to share the tab;
  - a WP line and board outside the player;
  - the P1/P2 confirmation;
  - the gate banner as elsewhere.
- **Seeking:**
  - **Backward** truncates the displayed prefix by video time. The reader keeps its journal and
    doesn't re-read.
  - **Forward past the read point** leaves a gap. Show "state incomplete from mm:ss" and offer to
    rewind to the gap. Resynchronising from on-screen state is step 6.
  - **Pause:** frames stop changing, so nothing is read.
- **Latency target:** WP on screen ≤ 2 s (p95) after the message that changed it has settled.

_Size: 1 week._

### 5. Hosting

- A Docker image containing:
  - Python with `.[web]` (no torch);
  - Node, the pinned Showdown build (for sheet validation) and the calc sidecar;
  - the served models and `served.json`;
  - `data/battles` on a volume.
- Cloudflare Tunnel + Access in front (decision 6). `vgc web` stays bound to `127.0.0.1` inside
  the container or host; the tunnel is the only way in.
- Check that the image runs the same WP on ARM as on the Mac: one fixture's trajectory, bit for bit
  or within 1e-6.

_Size: 2–3 days._

### 6. Later, if v1 earns it

- Read the two sheets off the video when the source shows them. Paste stays the fallback.
- Resynchronise after a forward seek from what is on screen: actives, HP bars and fainted markers.
- Damage and bulk back on, once decision 4's measurement passes.
- More sources as they come up, each with a layout profile and a few fixtures.

---

## Gates

A video battle shows its WP as calibrated only when all of these pass on the fixture set. Until
then the banner says so, as for any model that misses a gate (principle 10).

| Gate | Pass |
| --- | --- |
| **G-read** | No committed entry is wrong for kinds that change who is on the field (`lead`, `switch`, `faint`, `mega`). Wrong `move` entries ≤ 1%. Missed entries are reported, not gated |
| **G-hp** | Settled HP reads within ±1 point on ≥99% of reads. Failing this keeps damage and bulk off; it doesn't block WP |
| **G-wp** | \|WP(video journal) − WP(hand journal)\| ≤ 0.03 on ≥95% of decision points, clustered by battle. The worst battle is reported |
| **G-latency** | p95 ≤ 2 s from settled message to WP on screen, measured on the host |

---

## Risks

| Risk | Handling |
| --- | --- |
| Sources differ more than a few layout profiles cover | The survey limits v1 to sources that pass. The manual crop covers the rest |
| OCR misreads names under compression | Closed vocabulary with a margin; misses are dropped and counted, never guessed |
| Pixel HP too coarse for the damage channel | Off until G-hp passes (decision 4). WP is gated on its own |
| Region Capture stays Chromium-only | Acceptable for a personal tool. The fallback is sharing a whole tab |
| Turn boundaries not visible on screen | Infer from the message sequence. G-read catches it if that goes wrong |
| Host can't keep up at 4 fps | OCR runs on change only. Drop to 2 fps if the survey shows messages stay up ≥ 1 s |

---

## Open questions

1. **Which sources?** The channels or events you will actually watch decide the survey and the
   fixtures.
2. **Sheets:** paste them (the v1 assumption), or are they shown on screen reliably enough that
   reading them should move ahead of step 6?
3. **Hosting:** is "external" reach from anywhere to a service on this Mac (the tunnel), or does it
   need to run with the Mac off (a VM)?

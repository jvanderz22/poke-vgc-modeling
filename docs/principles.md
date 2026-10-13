# Principles

The living list of how this project works. The plan documents carry copies of these as they stood
when each plan was written ([PLAN-v4](PLAN-v4.md), [PLAN-v5](PLAN-v5.md)); those copies are a
record and are not updated. This file is.

What is true of the format itself (what a sheet hides, how spreads are built, turn order) is in
[format-facts](format-facts.md).

## Standing principles

These come from measurements or explicit decisions. Each one has been broken at least once.

1. **Compute what can be computed. Learn only where a measurement shows learning pays.** Never
   build on a simulator-derived quantity that hasn't been checked against human outcomes.
2. **A WP model must use revealed set information.** A set-blind model is not served, however
   good its log loss.
3. **Never put an opponent's spread into a model input.** It is `None` in every training row.
   A solver output computed under the imputed spread (the stack in PLAN-v5 step 4, a learned leaf
   in step 3a) is allowed when it is computed the same way in training and serving. The spread
   itself never reaches the model, and the number is gated like any other.
4. **Every number is a property of the regime it was measured in:** open team sheets (OTS) or
   team preview only (TPO). A channel that was 0% wrong under OTS was 4.3% wrong under TPO.
5. **Count independent units, not rows.** The unit is the Bo3 series or player pair. Anything
   drawn from human data is drawn by group.
6. **State the power before the threshold.** "Beats X" means the whole interval does. A test
   that could not have failed returns *undecided*, not pass.
7. **A gate may not condition on the future.**
8. **Fit anything post-hoc on rows the model never trained on,** drawn the way the gate draws.
9. **Test a mechanism on a model that reads its inputs.** Check that the features and the
   predictions moved before believing a null result.
10. **A failing gate is recorded on the model card, and the app does not present that number as
    calibrated.**
11. **The engine leads the page only where it beat the model on held-out human games,** per state
    kind and per regime, and the model stays on screen underneath.
12. **A live answer lands within about 5 seconds, planning included.**

## Practices

1. **Measure what a data source buys before scaling it.** An ablation costs an hour.
2. **Check the corpus population before trusting a metric on it:** rating, concentration, how
   games end.
3. **Assert on the environment you're paying for.**
4. **Check what a format actually reveals before calling it full information.** See
   [format-facts](format-facts.md#what-a-sheet-reveals).
5. **Before concluding "no signal", show the predictor was measured.**
6. **Tag every artifact with regulation, snapshot version and featurizer version.**
7. **Land solver changes together and re-solve the benchmark once.**
8. **CPU-hours are worth more than GPU-hours here.** The budget is $0–5 total: Kaggle before paid,
   no auto-refill. See [cloud-compute](cloud-compute.md). The deployed app is the one standing
   cost: it stops when idle and sits under a hard spend cap.
9. **A split by which side is p1 is not a split.** A 2v1 and a 1v2 are one state; judge them
   pooled.
10. **Fit a calibration where the answer is made.**
11. **Before blaming the predictor for a null, score the best predictor available on the same
    test.** Phase 6 ran twice before its ceiling was asked about (PLAN-v5 step 1).
12. **A Kaggle merge replays against the code that was packed.** Note the commit when packing, and
    merge from it.
13. **Check that the corpus is current before a power-limited gate.** The M-C cache stopped at
    2026-09-20 while every gate in PLAN-v4 step 4 ran short of power (PLAN-v5 step 0).
14. **Ask whether a test asks the question the product needs.** Phase 6 tests matchups across
    teams; Phase 10 needs brings within one matchup (PLAN-v5 step 3d).

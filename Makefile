# The data and model pipeline, as targets that skip work whose output is already current.
#
#   make data          scrape → pool → self-play → extract → manifest → featurize
#   make models        the three WP models, calibrated and evaluated against each other
#   make sweep         the same models, but trained on a free Kaggle GPU (see scripts/cloud/)
#   make collect       fetch + evaluate a Kaggle run that finished without us watching
#   make gates         print every model's gate verdicts
#   make test          the suite, in both venvs
#
# Override anything on the command line:  make data REG=reg_md BATTLES=120000
#
# Deliberately NOT a target: `vgc data freeze`. The held-out split is frozen once per regulation
# and re-rolling it silently invalidates every evaluation ever made, so it stays a typed command.
# See docs/regulation-change.md.

REG         ?= reg_mc
DATASET     ?= wp-v1
MANIFEST    ?= wp-v1-train
PAGES       ?= 150
BATTLES     ?= 60000
PER_PAIR    ?= 20
USAGE_ALPHA ?= 0.7
SEED        ?= 7
WORKERS     ?= 8
POOL_TAG    ?= full
EPOCHS      ?= 12

VENV     := .venv/bin
VGC      := $(VENV)/vgc
RUN_ID   := gen-heuristic-heuristic-s$(SEED)-p$(shell expr $(BATTLES) / $(PER_PAIR))x$(PER_PAIR)

# The Bo3 format id comes from the regulation config, so REG=reg_md works without editing this.
FORMAT    := $(shell $(VENV)/python -c "from vgc.regulation import load_regulation; print(load_regulation('$(REG)').showdown_format)" 2>/dev/null)

REPLAYS   := data/replays/.stamp-$(REG)
SELFPLAY  := data/selfplay/$(RUN_ID)/battles.jsonl.gz
SNAPSHOTS := data/snapshots/$(REG)/selfplay/$(RUN_ID)/train.jsonl.gz
HUMAN     := data/snapshots/$(REG)/human/$(FORMAT)bo3/train.jsonl.gz
MANIFILE  := data/snapshots/$(REG)/manifests/$(MANIFEST).json
FEATURES  := data/features/$(REG)/$(DATASET)/info.json

.PHONY: data models sweep sim-validity collect gates test clean-derived help
.DEFAULT_GOAL := help

help:
	@sed -n '1,15p' Makefile | sed 's/^# \{0,1\}//'

data: $(FEATURES)

# --- corpus -----------------------------------------------------------------------------------

$(REPLAYS):
	$(VGC) meta scrape --regulation $(REG) --format both --pages $(PAGES)
	@mkdir -p $(dir $@) && touch $@

pool: $(REPLAYS)
	$(VGC) meta pool --regulation $(REG) --tag $(POOL_TAG)

# Self-play is the expensive step (~26 min for 60k on 8 cores), so it is keyed on the run id:
# changing SEED, BATTLES or PER_PAIR produces a new run rather than overwriting one. The replay
# prerequisite is order-only (`|`): a fresh scrape must not silently invalidate a 26-minute run
# that is keyed by seed anyway. Human extraction below takes 90s, so it is a normal prerequisite.
$(SELFPLAY): | $(REPLAYS)
	$(VGC) data generate --regulation $(REG) --n $(BATTLES) --per-pair $(PER_PAIR) \
	    --usage-alpha $(USAGE_ALPHA) --workers $(WORKERS) --seed $(SEED)

$(SNAPSHOTS): $(SELFPLAY)
	$(VGC) data extract --regulation $(REG) --run data/selfplay/$(RUN_ID) --workers $(WORKERS)

$(HUMAN): $(REPLAYS)
	$(VGC) data human --regulation $(REG) --format both

# The manifest re-reads every record and refuses to be written if it touches held-out data or
# mixes snapshot versions. That check is the point of the step, so it always runs.
$(MANIFILE): $(SNAPSHOTS) $(HUMAN)
	$(VGC) data manifest --regulation $(REG) --name $(MANIFEST) $(SNAPSHOTS) $(HUMAN)

$(FEATURES): $(MANIFILE)
	$(VGC) wp featurize --regulation $(REG) --manifest $(MANIFEST) --name $(DATASET) --workers $(WORKERS)

# --- models -----------------------------------------------------------------------------------

# Baselines first: they are what the set encoder has to beat, and they must be fit on the same
# rows, so they are rebuilt whenever the dataset is.
models: $(FEATURES)
	$(VGC) wp train --regulation $(REG) --kind logistic --dataset $(DATASET)
	$(VGC) wp train --regulation $(REG) --kind gbt      --dataset $(DATASET)
	$(VGC) wp train --regulation $(REG) --kind set      --dataset $(DATASET) --epochs $(EPOCHS) \
	    --threads $(WORKERS) -- --d 64 --layers 2 --dropout 0.3 --weight-decay 0.1 \
	    --id-dropout 0.5 --id-dropout-preview 0.0 --human-weight 4 --bs 512 --lr 2e-4
	$(MAKE) evaluate VERSION=$(DATASET)-set

evaluate:
	$(VGC) wp calibrate --regulation $(REG) --version $(VERSION) --dataset $(DATASET)
	$(VGC) wp eval      --regulation $(REG) --version $(VERSION) --dataset $(DATASET) \
	    --baseline $(DATASET)-gbt --baseline $(DATASET)-logistic --baseline constant

# Does heuristic self-play predict real human results? Not about any model — it is the standing
# check that the simulator measures the game rather than the bot, and it re-runs whenever the
# policy changes. ~50k battles, about 40 minutes on this laptop.
sim-validity:
	$(VGC) sim validate --regulation $(REG) --workers $(WORKERS)

sweep: $(FEATURES)
	KAGGLE=$(VENV)/kaggle VGC=$(VGC) bash scripts/cloud/kaggle_sweep.sh $(REG) $(DATASET)

# Fetch and evaluate a kernel that has already run — after a timeout, or after walking away from
# one. The kernel is Kaggle's; losing the local watcher does not lose the models.
collect:
	KAGGLE=$(VENV)/kaggle VGC=$(VGC) bash scripts/cloud/kaggle_sweep.sh $(REG) $(DATASET) --collect

gates:
	@$(VGC) wp registry

# The browsable set behind the web app's Endgames page: held-out human games the in-battle model
# called at 90%+ before they ended, and how often that call was right. Reads every cached replay,
# so about a minute.
endgames:
	$(VGC) wp endgames --regulation $(REG)

# --- checks -----------------------------------------------------------------------------------

test:
	$(VENV)/pytest -q
	PYTHONPATH=src .venv-train/bin/python -m pytest -q tests/test_wp.py

parity:
	$(VGC) data parity --regulation $(REG) --n 300 --seed 11

# Everything regenerable from git + the pool + the seeds. Leaves replays (slow to re-scrape,
# and polite not to), the team pool and the frozen split alone.
clean-derived:
	rm -rf data/features/$(REG) data/snapshots/$(REG)/selfplay data/snapshots/$(REG)/human

# --- web app ------------------------------------------------------------------------------------

frontend/node_modules:
	npm --prefix frontend install

# The React source in frontend/ compiles into src/vgc/web/static, which `vgc web` serves.
web-build: frontend/node_modules
	npm --prefix frontend run build

web: web-build
	$(VGC) web

# Hot reload: vite on :5173 proxies /api to the Python app, so run `vgc web` alongside it.
web-dev: frontend/node_modules
	npm --prefix frontend run dev

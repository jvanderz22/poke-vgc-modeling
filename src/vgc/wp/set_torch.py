"""Set-encoder WP model: training and ONNX export. Runs in `.venv-train` (torch, numpy 1.26):
it imports nothing from vgc except this file and reads only `.npz` + JSON.

    PYTHONPATH=src .venv-train/bin/python -m vgc.wp.set_torch --data data/features/reg_mc/wp-v1 --out <dir>

It runs on CPU or on a GPU (`--device auto` picks cuda when present); see docs/cloud-compute.md.
The only inputs are the dataset directory and flags, so a cloud run needs just that directory,
this file, torch and numpy. The ONNX export is always written from a CPU copy, so the artifact
is identical wherever it was trained.

Architecture: one token per Pokémon (6 mine, 6 theirs) plus one global token. Each Pokémon
token is built from embeddings (species, current forme, item, ability, mean of known moves) and
its numeric features. There are L pre-norm self-attention blocks with no positional encoding,
so the model is invariant to order within a side, and a side flag in the features tells the
two sides apart. Heads: WP (from the global token and each side's mean token) and bring (per
Pokémon). Loss: BCE(WP) + λ·BCE(bring on the tokens that have a target). The WP logit is
temperature-scaled on the validation set, and the temperature is baked into the export.

Snapshots within a battle share one outcome, so a model with enough capacity memorises battles
instead of learning positions (train loss falls, validation loss rises). Defences: the dataset is
thinned (`features.train_orientations`), the model is small, and `--id-dropout` hides a Pokémon's
identity at random so the generic numbers have to carry the prediction.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

UNK = 1
# features.KINDS = ("preview", "bring", "turn", "switch"); 0 and 1 are the pre-battle rows, where
# the teams are the only input there is.
KIND_BRING = 1


class Block(nn.Module):
    def __init__(self, d: int, heads: int, dropout: float):
        super().__init__()
        self.heads, self.dh = heads, d // heads
        self.n1, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d)
        self.qkv, self.proj = nn.Linear(d, 3 * d), nn.Linear(d, d)
        self.ff = nn.Sequential(nn.Linear(d, 2 * d), nn.GELU(), nn.Linear(2 * d, d))
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, t, d = x.shape
        q, k, v = self.qkv(self.n1(x)).reshape(b, t, 3, self.heads, self.dh).permute(2, 0, 3, 1, 4)
        att = torch.softmax(q @ k.transpose(-1, -2) / math.sqrt(self.dh), dim=-1)
        x = x + self.drop(self.proj((att @ v).transpose(1, 2).reshape(b, t, d)))
        return x + self.drop(self.ff(self.n2(x)))


class SetWP(nn.Module):
    def __init__(self, sizes: dict, n_num: int, n_glob: int, d: int = 128, layers: int = 3, heads: int = 4,
                 dropout: float = 0.1):
        super().__init__()
        self.species = nn.Embedding(sizes["species"], 48, padding_idx=0)
        self.item = nn.Embedding(sizes["items"], 24, padding_idx=0)
        self.ability = nn.Embedding(sizes["abilities"], 24, padding_idx=0)
        self.move = nn.Embedding(sizes["moves"], 24, padding_idx=0)
        self.tok = nn.Sequential(nn.Linear(48 * 2 + 24 * 3 + n_num, d), nn.GELU(), nn.Linear(d, d))
        self.glob = nn.Sequential(nn.Linear(n_glob, d), nn.GELU(), nn.Linear(d, d))
        self.blocks = nn.ModuleList(Block(d, heads, dropout) for _ in range(layers))
        self.norm = nn.LayerNorm(d)
        self.wp = nn.Sequential(nn.Linear(3 * d, d), nn.GELU(), nn.Linear(d, 1))
        self.bring = nn.Sequential(nn.Linear(d, d // 2), nn.GELU(), nn.Linear(d // 2, 1))
        self.register_buffer("temperature", torch.ones(()))

    def forward(self, cat: torch.Tensor, num: torch.Tensor, glob: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        cat = cat.long()
        mv = cat[..., 4:8]
        known = (mv > UNK).float().unsqueeze(-1)
        moves = (self.move(mv) * known).sum(-2) / known.sum(-2).clamp(min=1.0)
        tok = torch.cat([self.species(cat[..., 0]), self.species(cat[..., 1]), self.item(cat[..., 2]),
                         self.ability(cat[..., 3]), moves, num], dim=-1)
        x = torch.cat([self.glob(glob).unsqueeze(1), self.tok(tok)], dim=1)  # [B, 13, d]
        for blk in self.blocks:
            x = blk(x)
        x = self.norm(x)
        pooled = torch.cat([x[:, 0], x[:, 1:7].mean(1), x[:, 7:13].mean(1)], dim=-1)
        wp_logit = self.wp(pooled).squeeze(-1) / self.temperature
        bring_logit = self.bring(x[:, 1:]).squeeze(-1)
        return wp_logit, bring_logit


def _load(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as z:
        return {k: z[k] for k in ("cat", "num", "glob", "y", "bring", "source", "kind")}


def _tensors(d: dict[str, np.ndarray], human_weight: float = 1.0) -> list[torch.Tensor]:
    # num stays float16 in memory (it's the bulk of the data) and is cast per batch.
    # Human rows are a few percent of the data but they are the distribution we are judged on,
    # so they can be weighted up; the model also gets a human/self-play flag in `glob`.
    w = np.where(d["source"] == 1, human_weight, 1.0).astype(np.float32)
    return [torch.from_numpy(d["cat"].astype(np.int16)), torch.from_numpy(d["num"].astype(np.float16)),
            torch.from_numpy(d["glob"]), torch.from_numpy(d["y"]), torch.from_numpy(d["bring"]),
            torch.from_numpy(w), torch.from_numpy(d["source"].astype(np.int64)),
            torch.from_numpy(d["kind"].astype(np.int64))]


def _batch(data: list[torch.Tensor], idx, id_dropout: float = 0.0, device: str = "cpu",
           id_dropout_preview: float | None = None) -> list[torch.Tensor]:
    cat, num, glob, y, bring, w = (t[idx].to(device, non_blocking=True) for t in data[:6])
    cat = cat.long()
    rate = max(id_dropout, id_dropout_preview or 0.0)
    if rate > 0:
        # Hide a Pokémon's identity (species/forme/item/ability/moves → UNK) at random, so the
        # model can't memorise "this exact team pairing lost" and has to use the generic numbers
        # (base stats, types, move summary) that describe the position.
        rates = torch.full(cat.shape[:2], id_dropout, device=cat.device)
        if id_dropout_preview is not None and len(data) > 7:
            # At preview and bring there is no board yet — identity is the whole input, so hiding
            # it there teaches nothing and costs the matchup signal the preview gate measures.
            # Turn/switch rows still need heavy masking to stop pairing memorisation.
            is_preview = (data[7][idx].to(device) <= KIND_BRING).unsqueeze(-1)
            rates = torch.where(is_preview, torch.full_like(rates, id_dropout_preview), rates)
        hide = torch.rand(cat.shape[:2], device=cat.device) < rates
        cat = torch.where(hide.unsqueeze(-1), torch.full_like(cat, UNK), cat)
    return [cat, num.float(), glob, y, bring, w]


def _losses(model: SetWP, batch: list[torch.Tensor], bring_weight: float) -> tuple[torch.Tensor, torch.Tensor]:
    cat, num, glob, y, bring, w = batch
    wp_logit, bring_logit = model(cat, num, glob)
    wp_loss = (nn.functional.binary_cross_entropy_with_logits(wp_logit, y, reduction="none") * w).sum() / w.sum()
    mask = bring >= 0
    if mask.any():
        bw = w.unsqueeze(1).expand_as(bring)[mask]
        br = nn.functional.binary_cross_entropy_with_logits(bring_logit[mask], bring[mask], reduction="none")
        br_loss = (br * bw).sum() / bw.sum()
    else:
        br_loss = wp_loss * 0
    return wp_loss, wp_loss + bring_weight * br_loss


@torch.no_grad()
def _eval(model: SetWP, data: list[torch.Tensor], bs: int = 4096, device: str = "cpu") -> tuple[dict, np.ndarray]:
    """Validation log loss overall and on human rows alone. Validation is mostly self-play, so a
    model trained to fit human play scores worse overall by construction — when human rows are
    weighted up, they are also what the model is selected on."""
    model.eval()
    logits = []
    for i in range(0, len(data[0]), bs):
        logits.append(model(*_batch(data, slice(i, i + bs), device=device)[:3])[0])
    lg = torch.cat(logits).cpu()
    bce = nn.functional.binary_cross_entropy_with_logits
    out = {"all": bce(lg, data[3]).item()}
    human = data[6] == 1
    if human.any():
        out["human"] = bce(lg[human], data[3][human]).item()
        out["human_rows"] = int(human.sum())
    return out, lg.numpy()


def _fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    best = (1e9, 1.0)
    for t in np.exp(np.linspace(math.log(0.5), math.log(3.0), 121)):
        p = 1 / (1 + np.exp(-logits / t))
        loss = -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))
        best = min(best, (loss, float(t)))
    return best[1]


def train(data_dir: Path, out: Path, epochs: int = 20, bs: int = 512, lr: float = 3e-4, d: int = 128, layers: int = 3,
          heads: int = 4, dropout: float = 0.1, bring_weight: float = 0.3, seed: int = 0, patience: int = 2,
          threads: int = 6, weight_decay: float = 0.05, id_dropout: float = 0.0, device: str = "auto",
          human_weight: float = 1.0, id_dropout_preview: float | None = None) -> dict:
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.set_num_threads(threads)
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    info = json.loads((data_dir / "info.json").read_text())
    vocab = json.loads((data_dir / "vocab.json").read_text())
    sizes = {k: len(vocab[k]) + 3 for k in ("species", "items", "abilities", "moves")}
    tr, va = _tensors(_load(data_dir / "train.npz"), human_weight), _tensors(_load(data_dir / "val.npz"))
    hp = {"d": d, "layers": layers, "heads": heads, "dropout": dropout}
    model = SetWP(sizes, info["n_num"], info["n_glob"], **hp).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    steps = epochs * math.ceil(len(tr[0]) / bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps, pct_start=0.1)
    out.mkdir(parents=True, exist_ok=True)
    history, best, bad = [], (1e9, -1), 0
    t0 = time.perf_counter()
    for ep in range(epochs):
        model.train()
        perm = torch.randperm(len(tr[0]))
        total = 0.0
        for i in range(0, len(perm), bs):
            idx = perm[i : i + bs]
            wp_loss, loss = _losses(model, _batch(tr, idx, id_dropout, device, id_dropout_preview), bring_weight)
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            total += wp_loss.item() * len(idx)
        val, _ = _eval(model, va, device=device)
        val_loss = val.get("human", val["all"]) if human_weight > 1 else val["all"]
        history.append({"epoch": ep + 1, "train_wp_logloss": round(total / len(perm), 5),
                        "val_wp_logloss": round(val["all"], 5),
                        "val_wp_logloss_human": round(val["human"], 5) if "human" in val else None,
                        "selected_on": round(val_loss, 5), "seconds": round(time.perf_counter() - t0, 1)})
        print(json.dumps(history[-1]), flush=True)
        if val_loss < best[0] - 1e-4:
            best, bad = (val_loss, ep + 1), 0
            torch.save(model.state_dict(), out / "best.pt")
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(torch.load(out / "best.pt", map_location=device))
    val, logits = _eval(model, va, device=device)
    temp = _fit_temperature(logits, va[3].numpy())
    model.temperature.fill_(temp)
    val_t, _ = _eval(model, va, device=device)
    model.eval().to("cpu")  # export from CPU: the artifact must not depend on where it trained
    n = 2
    torch.onnx.export(
        model, tuple(_batch(tr, slice(0, n))[:3]), str(out / "model.onnx"),
        input_names=["cat", "num", "glob"], output_names=["wp_logit", "bring_logit"],
        dynamic_axes={k: {0: "batch"} for k in ("cat", "num", "glob", "wp_logit", "bring_logit")}, opset_version=17,
    )
    (out / "best.pt").unlink()
    result = {"hyperparams": hp | {"epochs": epochs, "bs": bs, "lr": lr, "bring_weight": bring_weight, "seed": seed,
                                   "weight_decay": weight_decay, "id_dropout": id_dropout,
                                   "id_dropout_preview": id_dropout_preview,
                                   "human_weight": human_weight},
              "best_epoch": best[1], "val_wp_logloss": round(val["all"], 5), "selection_metric": round(best[0], 5),
              "selected_on": "human" if human_weight > 1 else "all", "temperature": round(temp, 4),
              "val_wp_logloss_calibrated": round(val_t["all"], 5),
              "val_wp_logloss_human": round(val["human"], 5) if "human" in val else None, "history": history,
              "parameters": sum(p.numel() for p in model.parameters()), "torch": torch.__version__, "device": device,
              "train_rows": int(len(tr[0])), "val_rows": int(len(va[0]))}
    (out / "train.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--d", type=int, default=128)
    ap.add_argument("--layers", type=int, default=3)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--dropout", type=float, default=0.1)
    ap.add_argument("--bring-weight", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--bs", type=int, default=512)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--human-weight", type=float, default=1.0, help="weight on human rows (they are ~3% of the data)")
    ap.add_argument("--id-dropout-preview", type=float, default=None,
                    help="identity dropout for preview/bring rows only (default: same as --id-dropout). "
                         "Team identity is the whole input before the battle starts, so masking it there "
                         "costs the preview signal without preventing any memorisation.")
    ap.add_argument("--weight-decay", type=float, default=0.05)
    ap.add_argument("--id-dropout", type=float, default=0.0, help="chance of hiding a Pokémon's identity in training")
    a = ap.parse_args()
    r = train(a.data, a.out, epochs=a.epochs, bs=a.bs, d=a.d, layers=a.layers, lr=a.lr, dropout=a.dropout,
              bring_weight=a.bring_weight, seed=a.seed, threads=a.threads, weight_decay=a.weight_decay,
              id_dropout=a.id_dropout, device=a.device, human_weight=a.human_weight,
              id_dropout_preview=a.id_dropout_preview)
    print(json.dumps({k: v for k, v in r.items() if k != "history"}))


if __name__ == "__main__":
    main()

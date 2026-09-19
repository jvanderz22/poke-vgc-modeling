"""Set-encoder WP model: training and ONNX export. Runs in `.venv-train` (torch, numpy 1.26):
it imports nothing from vgc except this file and reads only `.npz` + JSON.

    PYTHONPATH=src .venv-train/bin/python -m vgc.wp.set_torch --data data/features/reg_mc/wp-v1 --out <dir>

Architecture: one token per Pokémon (6 mine, 6 theirs) plus one global token. Each Pokémon
token is built from embeddings (species, current forme, item, ability, mean of known moves) and
its numeric features. There are L pre-norm self-attention blocks with no positional encoding,
so the model is invariant to order within a side, and a side flag in the features tells the
two sides apart. Heads: WP (from the global token and each side's mean token) and bring (per
Pokémon). Loss: BCE(WP) + λ·BCE(bring on the tokens that have a target). The WP logit is
temperature-scaled on the validation set, and the temperature is baked into the export.
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
        return {k: z[k] for k in ("cat", "num", "glob", "y", "bring")}


def _tensors(d: dict[str, np.ndarray]) -> list[torch.Tensor]:
    # num stays float16 in memory (it's the bulk of the data) and is cast per batch.
    return [torch.from_numpy(d["cat"].astype(np.int16)), torch.from_numpy(d["num"].astype(np.float16)),
            torch.from_numpy(d["glob"]), torch.from_numpy(d["y"]), torch.from_numpy(d["bring"])]


def _batch(data: list[torch.Tensor], idx) -> list[torch.Tensor]:
    cat, num, glob, y, bring = (t[idx] for t in data)
    return [cat.long(), num.float(), glob, y, bring]


def _losses(model: SetWP, batch: list[torch.Tensor], bring_weight: float) -> tuple[torch.Tensor, torch.Tensor]:
    cat, num, glob, y, bring = batch
    wp_logit, bring_logit = model(cat, num, glob)
    wp_loss = nn.functional.binary_cross_entropy_with_logits(wp_logit, y)
    mask = bring >= 0
    if mask.any():
        br_loss = nn.functional.binary_cross_entropy_with_logits(bring_logit[mask], bring[mask])
    else:
        br_loss = wp_loss * 0
    return wp_loss, wp_loss + bring_weight * br_loss


@torch.no_grad()
def _eval(model: SetWP, data: list[torch.Tensor], bs: int = 4096) -> tuple[float, np.ndarray]:
    model.eval()
    logits = []
    for i in range(0, len(data[0]), bs):
        logits.append(model(*_batch(data, slice(i, i + bs))[:3])[0])
    lg = torch.cat(logits)
    loss = nn.functional.binary_cross_entropy_with_logits(lg, data[3]).item()
    return loss, lg.numpy()


def _fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    best = (1e9, 1.0)
    for t in np.exp(np.linspace(math.log(0.5), math.log(3.0), 121)):
        p = 1 / (1 + np.exp(-logits / t))
        loss = -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))
        best = min(best, (loss, float(t)))
    return best[1]


def train(data_dir: Path, out: Path, epochs: int = 20, bs: int = 512, lr: float = 3e-4, d: int = 128, layers: int = 3,
          heads: int = 4, dropout: float = 0.1, bring_weight: float = 0.3, seed: int = 0, patience: int = 3,
          threads: int = 6, weight_decay: float = 0.05) -> dict:
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.set_num_threads(threads)
    info = json.loads((data_dir / "info.json").read_text())
    vocab = json.loads((data_dir / "vocab.json").read_text())
    sizes = {k: len(vocab[k]) + 3 for k in ("species", "items", "abilities", "moves")}
    tr, va = _tensors(_load(data_dir / "train.npz")), _tensors(_load(data_dir / "val.npz"))
    hp = {"d": d, "layers": layers, "heads": heads, "dropout": dropout}
    model = SetWP(sizes, info["n_num"], info["n_glob"], **hp)
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
            wp_loss, loss = _losses(model, _batch(tr, idx), bring_weight)
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            total += wp_loss.item() * len(idx)
        val_loss, _ = _eval(model, va)
        history.append({"epoch": ep + 1, "train_wp_logloss": round(total / len(perm), 5), "val_wp_logloss": round(val_loss, 5),
                        "seconds": round(time.perf_counter() - t0, 1)})
        print(json.dumps(history[-1]), flush=True)
        if val_loss < best[0] - 1e-4:
            best, bad = (val_loss, ep + 1), 0
            torch.save(model.state_dict(), out / "best.pt")
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(torch.load(out / "best.pt"))
    _, logits = _eval(model, va)
    temp = _fit_temperature(logits, va[3].numpy())
    model.temperature.fill_(temp)
    val_loss_t, _ = _eval(model, va)
    model.eval()
    n = 2
    torch.onnx.export(
        model, tuple(_batch(tr, slice(0, n))[:3]), str(out / "model.onnx"),
        input_names=["cat", "num", "glob"], output_names=["wp_logit", "bring_logit"],
        dynamic_axes={k: {0: "batch"} for k in ("cat", "num", "glob", "wp_logit", "bring_logit")}, opset_version=17,
    )
    (out / "best.pt").unlink()
    result = {"hyperparams": hp | {"epochs": epochs, "bs": bs, "lr": lr, "bring_weight": bring_weight, "seed": seed,
                                   "weight_decay": weight_decay},
              "best_epoch": best[1], "val_wp_logloss": round(best[0], 5), "temperature": round(temp, 4),
              "val_wp_logloss_calibrated": round(val_loss_t, 5), "history": history,
              "parameters": sum(p.numel() for p in model.parameters()), "torch": torch.__version__,
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
    ap.add_argument("--weight-decay", type=float, default=0.05)
    a = ap.parse_args()
    r = train(a.data, a.out, epochs=a.epochs, d=a.d, layers=a.layers, lr=a.lr, dropout=a.dropout,
              bring_weight=a.bring_weight, seed=a.seed, threads=a.threads, weight_decay=a.weight_decay)
    print(json.dumps({k: v for k, v in r.items() if k != "history"}))


if __name__ == "__main__":
    main()

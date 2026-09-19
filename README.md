# Pokémon Model

A small Python project that models a Pokémon and supports simple battle calculations.

## Features

- Represent a Pokémon with name, types, and stats
- Calculate total stat value
- Evaluate type effectiveness against an attack type
- Estimate damage taken from a move

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest -q
```

## Example

```python
from pokemon_model import Pokemon

bulbasaur = Pokemon(
    name="Bulbasaur",
    types=("grass", "poison"),
    hp=45,
    attack=49,
    defense=49,
    speed=45,
)

print(bulbasaur.total_stats)
print(bulbasaur.damage_taken("fire", 40))
```

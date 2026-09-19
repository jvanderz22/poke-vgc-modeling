"""Team model, Showdown text I/O, and legality validation."""

from vgc.teams.sets import PokemonSet, StatPoints, Team, calc_stats
from vgc.teams.showdown_text import export_team, parse_team
from vgc.teams.validate import Problem, is_legal, validate_team

__all__ = [
    "PokemonSet", "StatPoints", "Team", "calc_stats",
    "parse_team", "export_team",
    "Problem", "validate_team", "is_legal",
]

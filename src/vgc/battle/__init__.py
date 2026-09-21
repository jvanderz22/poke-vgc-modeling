"""L1b — battle state, and the adapters that fill it.

`state` holds what a battle *is*. `vgc.data.observe.Observer` fills it from a Showdown protocol
stream; manual entry fills it from what a person watching a cartridge can see. The state does not
know which, which is the point: win probability and all four belief channels read the same object.
"""

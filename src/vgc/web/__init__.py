"""Battle-companion web app (PLAN.md L5b), served on localhost by `vgc web`.

Everything stays on this machine: the simulator, the calc sidecar and the ONNX models. The
backend calls `vgc.*` directly rather than shelling out to the CLI, so the API and the CLI are
two faces of the same functions.
"""

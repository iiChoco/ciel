"""Reproduce the structural assessment using source only.

**No runtime state.** AST inspection never imports Ciel or constructs services.
**Evidence, not a score.** Print orchestration size, constructor state, and
statements after the endpointing wrapper's unconditional return.
"""
from __future__ import annotations

import ast
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "src/ciel/pipeline.py"
    source = path.read_text()
    tree = ast.parse(source)
    pipeline = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Pipeline")
    methods = [n for n in pipeline.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    init = next(n for n in methods if n.name == "__init__")
    fields = {n.attr for n in ast.walk(init) if isinstance(n, ast.Attribute)
              and isinstance(n.value, ast.Name) and n.value.id == "self"
              and isinstance(n.ctx, ast.Store)}
    print(f"pipeline.py: {len(source.splitlines())} lines; Pipeline: {len(methods)} methods")
    print(f"constructor: lines {init.lineno}-{init.end_lineno}; {len(fields)} directly assigned fields")
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "trails_off")
    position = next(i for i, n in enumerate(fn.body) if isinstance(n, ast.Return))
    dead = fn.body[position + 1:]
    if dead:
        print(f"trails_off: unreachable statements at lines {dead[0].lineno}-{dead[-1].end_lineno}")
    for relative in ("scripts/probe_turns.py", "scripts/probe_hub_arbiter.py"):
        for line, text in enumerate((root / relative).read_text().splitlines(), 1):
            if "Pipeline.__new__(Pipeline)" in text:
                print(f"{relative}:{line}: constructor bypass")


if __name__ == "__main__":
    main()

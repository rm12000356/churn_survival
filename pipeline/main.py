"""Pipeline entry point.

Implemented nodes run end-to-end; unimplemented nodes fail loudly — never fake
success. The system must be allowed to say "I don't know".
"""

from __future__ import annotations

import sys

from logging_setup import configure_logging, get_logger

NODE_NAMES = ("node1", "node2", "node3", "node4", "node5")

IMPLEMENTED_NODES = ("node1", "node2", "node3", "node4", "node5")


class UnimplementedNodeError(NotImplementedError):
    """Raised when a pipeline node is invoked before it is implemented."""


def run_node(node: str, args: list[str] | None = None) -> int:
    """Run a single pipeline node. Unimplemented nodes raise loudly."""
    if node not in NODE_NAMES:
        raise UnimplementedNodeError(f"Unknown pipeline node: {node!r}")
    module_name = _NODE_ENTRYPOINTS.get(node)
    if module_name is None:
        raise UnimplementedNodeError(
            f"Node {node!r} is not implemented yet (see ROADMAP Phase {node[-1]}). "
            "The system must be allowed to say 'I don't know' — no fake success."
        )
    import importlib

    entry = importlib.import_module(module_name).main
    return int(entry(args or []))


#: Node CLI entry points (imported lazily so one node's deps never load another's).
_NODE_ENTRYPOINTS = {node: f"{node}.node" for node in IMPLEMENTED_NODES}


def run_map(args: list[str] | None = None) -> int:
    """Onboarding subcommand: draft/confirm a mapping config (§1.6)."""
    from node1.node import map_main

    return map_main(args or [])


def run_pipeline_command(args: list[str] | None = None) -> int:
    """Full end-to-end run: route -> Node 1 -> ... -> Node 5 (Phase 7)."""
    from orchestration.node import main as run_main

    return run_main(args or [])


def run_gc_command(args: list[str] | None = None) -> int:
    """Retention/GC maintenance (Phase 8): prune runs/artifacts, recover stale."""
    from orchestration.gc import main as gc_main

    return gc_main(args or [])


def run_audit_command(args: list[str] | None = None) -> int:
    """Reproducibility audit (Phase 9): re-run + diff persisted runs."""
    from scripts.audit_reproducibility import main as audit_main

    return audit_main(args or [])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        configure_logging()
    except Exception as exc:  # configuration failure must fail loudly, not crash obscurely
        print(f"ERROR: configuration failed: {exc}", file=sys.stderr)
        return 1

    log = get_logger(node="pipeline")

    if not args:
        print(
            "Usage: churn-survival <node1|node2|node3|node4|node5|run|map|gc|audit>",
            file=sys.stderr,
        )
        return 2

    try:
        if args[0] == "map":
            return run_map(args[1:])
        if args[0] == "run":
            return run_pipeline_command(args[1:])
        if args[0] == "gc":
            return run_gc_command(args[1:])
        if args[0] == "audit":
            return run_audit_command(args[1:])
        return run_node(args[0], args[1:])
    except UnimplementedNodeError as exc:
        log.error("node_unimplemented", node=args[0], message=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

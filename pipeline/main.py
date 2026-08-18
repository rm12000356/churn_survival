"""Pipeline entry point.

Implemented nodes run end-to-end; unimplemented nodes fail loudly — never fake
success. The system must be allowed to say "I don't know".
"""

from __future__ import annotations

import sys

from logging_setup import configure_logging, get_logger

NODE_NAMES = ("node1", "node2", "node3", "node4", "node5")

IMPLEMENTED_NODES = ("node1",)


class UnimplementedNodeError(NotImplementedError):
    """Raised when a pipeline node is invoked before it is implemented."""


def run_node(node: str, args: list[str] | None = None) -> int:
    """Run a single pipeline node. Unimplemented nodes raise loudly."""
    if node not in NODE_NAMES:
        raise UnimplementedNodeError(f"Unknown pipeline node: {node!r}")
    if node in IMPLEMENTED_NODES:
        from node1.node import main as node1_main

        return node1_main(args or [])
    raise UnimplementedNodeError(
        f"Node {node!r} is not implemented yet (see ROADMAP Phase {node[-1]}). "
        "The system must be allowed to say 'I don't know' — no fake success."
    )


def run_map(args: list[str] | None = None) -> int:
    """Onboarding subcommand: draft/confirm a mapping config (§1.6)."""
    from node1.node import map_main

    return map_main(args or [])


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
            "Usage: churn-survival <node1|node2|node3|node4|node5|map>",
            file=sys.stderr,
        )
        return 2

    try:
        if args[0] == "map":
            return run_map(args[1:])
        return run_node(args[0], args[1:])
    except UnimplementedNodeError as exc:
        log.error("node_unimplemented", node=args[0], message=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

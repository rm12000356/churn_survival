"""Pipeline entry point — green skeleton (ROADMAP Task 0.8).

The empty pipeline must fail loudly, never silently fake success. Every
unimplemented node raises and the process exits non-zero with a clear message.
"""

from __future__ import annotations

import sys

from logging_setup import configure_logging, get_logger

NODE_NAMES = ("node1", "node2", "node3", "node4", "node5")


class UnimplementedNodeError(NotImplementedError):
    """Raised when a pipeline node is invoked before it is implemented."""


def run_node(node: str) -> None:
    """Run a single pipeline node. Currently every node is unimplemented."""
    if node not in NODE_NAMES:
        raise UnimplementedNodeError(f"Unknown pipeline node: {node!r}")
    raise UnimplementedNodeError(
        f"Node {node!r} is not implemented yet (see ROADMAP Phase {node[-1]}). "
        "The system must be allowed to say 'I don't know' — no fake success."
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        configure_logging()
    except Exception as exc:  # configuration failure must fail loudly, not crash obscurely
        print(f"ERROR: configuration failed: {exc}", file=sys.stderr)
        return 1

    log = get_logger(node="pipeline")

    if not args:
        print("Usage: churn-survival <node1|node2|node3|node4|node5>", file=sys.stderr)
        return 2

    try:
        run_node(args[0])
    except UnimplementedNodeError as exc:
        log.error("node_unimplemented", node=args[0], message=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

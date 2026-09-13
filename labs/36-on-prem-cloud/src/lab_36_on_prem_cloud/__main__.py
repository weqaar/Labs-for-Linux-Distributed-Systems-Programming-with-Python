"""Command line entry point for inspecting the deployment plan."""

from __future__ import annotations

import argparse

from .infrastructure import (
    SubprocessRunner,
    apply,
    calculate_requirements,
    destroy,
    load_config,
    make_plan,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("action", choices=("plan", "apply", "destroy"), default="plan", nargs="?")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    config = load_config(args.config)
    required = calculate_requirements(config)
    print(
        f"VM requirement: {required.vcpus} vCPU, "
        f"{required.memory_gib} GiB RAM, {required.disk_gib} GiB disk"
    )
    if args.action == "plan":
        for step in make_plan(config):
            print(f"{step.phase:02d} {step.name}: {' '.join(step.argv)}")
    elif args.action == "apply":
        apply(config, SubprocessRunner(), args.confirm)
    else:
        destroy(config, SubprocessRunner(), args.confirm)


if __name__ == "__main__":
    main()

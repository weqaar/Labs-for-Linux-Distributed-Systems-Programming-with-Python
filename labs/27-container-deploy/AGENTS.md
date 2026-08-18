# Lab 27 Container Deploy

Orientation for anyone, human or AI, working in this repository.

## Scope

This lab is the relay deployment checkpoint for Kubernetes and for local runtime
planning. Keep changes inside this lab. The planner is dry-run only. It models
QEMU with TCG, QEMU with KVM, libvirt on KVM, Docker, unprivileged LXC, and
Kata utility VMs for the same relay service.

KVM is a Linux accelerator used by QEMU and libvirt. It is not a standalone VM
manager. If KVM is unavailable, QEMU with TCG is the portable fallback, but it
is slow.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

## Layout

```
src/lab_27_container_deploy/    the package
tests/                the test suite
deploy/               Kubernetes and local-runtime artifacts
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Keep the local runtime planner side-effect free. The CLI should print plans,
  not change the host.
- Docker plans must stay non-root, read-only, and `no-new-privileges` with all
  Linux capabilities dropped.
- QEMU plans must keep explicit acceleration, a Unix QMP socket, and the QEMU
  sandbox.
- libvirt plans must stay session-scoped and ask for sVirt-style confinement.
- LXC plans must stay unprivileged and show uid and gid maps.
- Kata plans must describe the RuntimeClass and utility-VM boundary.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.

# Lab 27 Container Deploy

This checkpoint keeps the relay Kubernetes rollout artifacts offline and adds a
local-runtime planning step for the same service. The lab now covers Azure and
Kubernetes deployment, plus dry-run plans for QEMU with TCG, QEMU with KVM,
libvirt on KVM, Docker, unprivileged LXC, and Kata utility VMs.

KVM matters here as a Linux accelerator that QEMU and libvirt use. It is not a
VM manager on its own. When KVM is absent, QEMU with TCG is the portable
fallback, but it is slow.

## Artifacts

- `Dockerfile` builds the service in two stages and runs as a non-root user.
- `deploy/relay-deployment.yaml` pins the image by digest, splits readiness from
  liveness, declares resource requests and limits, and enables Azure workload
  identity.
- `deploy/rollout-checkpoint.yaml` records the current digest, the previous
  digest, and the one-line rollback command.
- `deploy/relay-local-lxc.conf` shows the unprivileged LXC uid and gid mapping,
  read-only rootfs plan, and capability drop. Replace `/srv/relay/rootfs` with
  the absolute path to the prepared root filesystem on the host.
- `deploy/relay-kata-runtimeclass.yaml` records the RuntimeClass used when relay
  should run inside a Kata utility VM.
- `src/lab_27_container_deploy/checkpoint.py` validates the Kubernetes and
  container artifacts offline.
- `src/lab_27_container_deploy/local_runtime.py` prints dry-run local runtime
  plans and exposes typed Docker, libvirt, and QMP boundaries for relay.

## Local runtime tradeoffs

| Runtime | Boundary | Performance | Main hardening |
| --- | --- | --- | --- |
| QEMU with TCG | Virtual machine | Slowest | explicit `-accel tcg`, Unix QMP, QEMU sandbox, read-only guest disk |
| QEMU with KVM | Virtual machine | Fast | explicit `-accel kvm`, Unix QMP, QEMU sandbox, read-only guest disk |
| libvirt with KVM | Virtual machine | Fast | `qemu:///session`, dynamic sVirt labels, Unix QMP, QEMU sandbox |
| Docker | Container | Fast | non-root user, read-only root, `no-new-privileges`, cap drop |
| Unprivileged LXC | Container | Fast | user namespaces, uid and gid remap, read-only root, AppArmor profile |
| Kata | Utility VM | Medium | RuntimeClass boundary, non-root container, read-only root, seccomp |

The local runtime planner is a checkpoint rather than a launcher. It does not
mutate the host. It lets relay keep one image and one service command while the
book compares local boundaries before a cluster rollout.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

The same validation helpers back the tests and can be run directly:

```bash
python -m lab_27_container_deploy.checkpoint
python -m lab_27_container_deploy.local_runtime plan --runtime auto \
  --minimum-isolation virtual-machine --host-profile portable
python -m lab_27_container_deploy.local_runtime plan --runtime libvirt-kvm \
  --host-profile vm-kvm
python -m lab_27_container_deploy.local_runtime plan --runtime kata \
  --host-profile all --format yaml
```

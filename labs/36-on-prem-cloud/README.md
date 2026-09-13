# Lab 35: SigRaft on-prem cloud infrastructure

This checkpoint adds a Python planner for the SigRaft on-prem cloud. It reads
the profiles in `../../onprem/config`, validates their safety properties,
totals the virtual machine resources, and emits a stable sequence of commands.
Planning never starts a process.

## Prerequisites

Use Python 3.10 or later. To render a plan, no hypervisor or cloud credentials
are needed.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m lab_36_on_prem_cloud ../../onprem/config/complete.yaml plan
```

The complete profile needs a Linux x86-64 host with hardware virtualization,
32 logical CPUs, 128 GiB RAM, and 2 TiB of usable SSD storage. Nested
virtualization is acceptable for learning but slower. Enable Intel VT-x or AMD-V
in firmware and confirm `/dev/kvm` is available. The profile assigns 24 vCPUs,
104 GiB RAM, and 1.6 TiB disk to five VMs, leaving an explicit host reserve.
Raise both host capacity and VM values for more tenants or replicas.

The workstation profile uses 16 CPUs, 64 GiB RAM, and 1 TiB disk. It is for
rendering and functional exercises, not availability tests.

## Safe lifecycle

`plan` is the default and has no mutation path. `apply` and `destroy` require
two separate opt-ins:

1. Change `allow_execution` to `true` in a local configuration that is not
   committed.
2. Pass `--confirm APPLY` or `--confirm DESTROY`.

```bash
export SIGRAFT_SSH_PUBLIC_KEY="$(cat "$HOME/.ssh/id_ed25519.pub")"
export SIGRAFT_SSH_PRIVATE_KEY_FILE="$HOME/.ssh/id_ed25519"
export SIGRAFT_HARBOR_ADMIN_PASSWORD="<distinct-secret>"
export SIGRAFT_POSTGRES_ADMIN_PASSWORD="<distinct-secret>"
export SIGRAFT_POSTGRES_RELAY_PASSWORD="<distinct-secret>"
export SIGRAFT_RABBITMQ_PASSWORD="<distinct-secret>"
export SIGRAFT_VALKEY_PASSWORD="<distinct-secret>"
export SIGRAFT_MINIO_ROOT_USER="<distinct-access-identifier>"
export SIGRAFT_MINIO_ROOT_PASSWORD="<distinct-secret>"
export SIGRAFT_GRAFANA_ADMIN_PASSWORD="<distinct-secret>"
export SIGRAFT_LOKI_S3_ACCESS_KEY="<distinct-access-identifier>"
export SIGRAFT_LOKI_S3_SECRET_KEY="<distinct-secret>"
python -m lab_36_on_prem_cloud config.local.yaml apply --confirm APPLY
python -m lab_36_on_prem_cloud config.local.yaml destroy --confirm DESTROY
```

Execution accepts only `ansible-galaxy`, `ansible-playbook`, and `openstack`.
Arguments are arrays and never pass through a shell. The runner is injected,
so tests record intended calls without changing the host.

The checked-in boundary pins Kolla Ansible 19.5.0 for OpenStack 2024.2,
Kubespray v2.27.0 for Kubernetes v1.31.4, Ansible collections, and every Helm
chart. Ansible creates the libvirt disks, cloud-init media, networks, and
domains before calling those installers. See `../../onprem/README.md` for
operator inputs, bootstrap commands, validation, and bounded teardown.

## Python REPL debugging session

The planner is ordinary Python, so a failed profile can be examined without
running any command:

```pycon
>>> from lab_36_on_prem_cloud import load_config, make_plan
>>> from lab_36_on_prem_cloud.infrastructure import calculate_requirements
>>> config = load_config("../../onprem/config/complete.yaml")
>>> calculate_requirements(config)
Requirements(vcpus=24, memory_gib=104, disk_gib=1600)
>>> config.allow_execution
False
>>> [(step.phase, step.name) for step in make_plan(config)]
[(5, 'install pinned Ansible collections'), (10, 'validate host prerequisites and configuration'), (20, 'create owned libvirt networks, disks, cloud-init media, and virtual machines'), (30, 'run Kolla bootstrap, prechecks, deploy, and post-deploy'), (40, 'create the bounded OpenStack project resources'), (50, 'install Kubernetes with pinned Kubespray'), (60, 'install pinned registry, ingress, data, and observability charts'), (70, 'validate OpenStack, Kubernetes, storage, ingress, and telemetry')]
>>> make_plan(config) == make_plan(config)
True
```

If validation fails, the exception names the unsafe field:

```pycon
>>> load_config("profile-without-kvm.yaml")
Traceback (most recent call last):
...
lab_36_on_prem_cloud.infrastructure.ConfigError: hardware_virtualization must be true
```

## Quality gates

```bash
PATH=/opt/pyvenv/bin:$PATH pybootstrap check
```

The lab is done when the command exits zero. Tests cover resource totals,
stable ordering, CIDR syntax and overlap, referenced artifacts, required roles
and services, capacity rejection, the non-executing plan path, both
confirmations, bounded names, required and distinct per-service secrets, named
telemetry endpoints, and the injected runner.

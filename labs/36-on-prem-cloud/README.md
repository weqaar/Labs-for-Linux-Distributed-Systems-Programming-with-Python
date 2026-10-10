# Lab 36 SigRaft on-prem cloud infrastructure

This lab adds a Python planner for the SigRaft on-prem cloud. It reads
the profiles in `../../onprem/config`, validates their safety properties,
totals the virtual machine resources, and emits a stable sequence of commands.
Planning never starts a process.

## Goal and working order

Check capacity, ownership and lifecycle prerequisites before provisioning an
on-premises platform. You will inspect supplied profiles and plans, deliberately
reject an unsafe configuration and test the injected runner. Actual deployment
is optional, privileged and much larger than the default lab exercise.

Read [`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
Work from this lab directory with the repository's `onprem/` companion tree
present. Python and PyYAML implement validation; Ansible, libvirt, Kolla,
Kubespray, OpenStack and Helm are optional deployment technologies, not services
started by the gate. Dependencies belong in `pyproject.toml`.

The deployment plan uses Ansible for automation and libvirt for virtual
machines. Kolla installs OpenStack, Kubespray installs Kubernetes, and Helm
installs the applications described by charts. This order explains why the
planner checks host capacity before any platform service is available.

1. Install and render the complete profile without credentials.
2. Inspect the resource totals and stable phase order in the REPL.
3. Run `pytest -q tests/test_lab_36_on_prem_cloud.py`. Trace rejection of
   overcommit, overlapping networks and missing execution confirmation.
4. Compare a local copy of the workstation profile. Keep `allow_execution`
   false unless following the full operator procedure in `../../onprem/README.md`.

## Prerequisites

Use Python 3.10 or later. To render a plan, no hypervisor or cloud credentials
are needed.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m lab_36_on_prem_cloud ../../onprem/config/complete.yaml plan
```

Deploying the complete profile needs a Linux x86-64 host with hardware virtualization,
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

The secret examples are placeholders, not usable credentials. Before apply,
also prepare `KOLLA_PASSWORDS_FILE`, `OS_CLIENT_CONFIG_FILE`,
`SIGRAFT_IMAGE_PATH` and `SIGRAFT_IMAGE_SHA256` using the on-prem operator
guide. Keep local configuration paths and its `artifact_root` valid after
copying a profile.

Execution accepts only `ansible-galaxy`, `ansible-playbook`, `kubectl` and `openstack`.
Arguments are arrays and never pass through a shell. The runner is injected,
so tests capture intended calls without changing the host.

The deployment configuration pins Kolla Ansible 19.5.0 for OpenStack 2024.2,
Kubespray v2.27.0 for Kubernetes v1.31.4, Ansible collections, and every Helm
chart. Ansible creates the libvirt disks, cloud-init media, networks, and
domains before calling those installers. See `../../onprem/README.md` for
operator inputs, bootstrap commands, validation, and bounded teardown.

## Tests

A unit test checks one function or class on its own, with clocks, network,
storage and other dependencies replaced by deterministic fakes. A functional
test checks one complete feature through the lab's public interface, the way
a reader would use it.

`tests/test_lab_36_on_prem_cloud.py` holds both kinds. The unit tests check
configuration loading, resource totals, plan order, rejected configurations,
artifacts and secrets. The functional tests are
`test_command_line_defaults_to_a_read_only_plan`,
`test_apply_requires_two_explicit_opt_ins` and
`test_destroy_requires_exact_confirmation_and_is_ordered`. The first calls
`main()` from `lab_36_on_prem_cloud.__main__` with a command line and reads
the printed plan. The other two drive the public `apply` and `destroy`
lifecycle with a fake command runner that keeps each call, so no command
touches the host.

```bash
pytest tests -k "not (command_line or apply_requires or destroy_requires)"
pytest tests -k "command_line or apply_requires or destroy_requires"
```

`pybootstrap check` runs both kinds of test in its test gate.

## Python REPL debugging session

The planner is ordinary Python, so a failed profile can be examined without
running any command:

```pycon
>>> import lab_36_on_prem_cloud as lab
>>> lab.__name__, lab.__file__
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

The totals describe requested capacity, and the repeated plan has identical
steps. Neither result proves the host can execute those steps.
To inspect rejection, copy a supplied profile to a local exercise file,
set its `hardware_virtualization` field to false, and call `load_config`
on that existing file. Expect `ConfigError` naming the field. Merely naming
a nonexistent file would test missing-file handling, not unsafe hardware.

## Quality gates

```bash
pybootstrap check
```

The lab is done when the command exits zero. Tests cover resource totals,
stable ordering, CIDR syntax and overlap, referenced artifacts, required roles
and services, capacity rejection, the non-executing plan path, both
confirmations, bounded names, required and distinct per-service secrets, named
telemetry endpoints, and the injected runner.

This supplies platform planning for the SigRaft job-orchestration web service.
Lab 39 can target that platform but does not import this planner or provision
it at startup. A passing local gate does not establish host availability.
`pybootstrap check` must exit 0; exit 1 means findings and exit 2 means a
gate could not run.

Remove local profile copies after inspection. If you opted into deployment,
use the bounded destroy procedure with the same owned configuration, verify
the named resources are gone and unset secret environment variables. Never
destroy a shared host or unrelated tenant resources.

# Lab 32 Container Deploy

This lab keeps the SigRaft Kubernetes rollout files offline and adds a
local-runtime planning step for the same service. The lab covers Azure and
Kubernetes deployment, plus dry-run plans for QEMU with TCG, QEMU with KVM,
libvirt on KVM, Docker, unprivileged LXC, and Kata utility VMs.

KVM matters here as a Linux accelerator that QEMU and libvirt use. It is not a
VM manager on its own. When KVM is absent, QEMU with TCG is the portable
fallback, but it is slow.

QEMU runs a virtual machine; TCG translates its instructions in software.
libvirt manages virtual machines through a common API. Docker and LXC instead
share the host kernel, while Kata places containers inside a small VM. The
planner compares these boundaries without starting any of them.

## Goal and working order

Connect a container image, a serving path and a rollback decision, while
keeping a runtime plan separate from actually starting that runtime. A
container image holds the program and its runtime files; a digest identifies
its exact contents. You will inspect
the supplied artifacts and fake SDK interactions before optional deployment.
These are deployment plans and checks for the SigRaft job-orchestration web service,
not a launcher automatically imported by Lab 39.

Use Python 3.10 or later here and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
PyYAML parses artifacts; optional adapters use the Docker and Kubernetes Python
SDKs to create containers and update Deployments. Dependencies are in
`pyproject.toml`. Default tests need no Docker socket, kubeconfig, hypervisor
or Azure subscription.

1. Install below and run the artifact and local-plan commands.
2. Run `pytest -q tests/test_lab_32_container_deploy.py tests/test_local_runtime.py`.
   Trace digest validation, bounded waits and cleanup through SDK fakes.
3. Compare a portable QEMU plan with a KVM-capable one and inspect the
   isolation requirements instead of relying on a runtime name.
4. Review and adapt the optional Kubernetes path only on an owned cluster.

## Artifacts

Read the image definition first, then the deployment and rollout records.
The files describe what should run and how it should be replaced; the offline
validator checks their declarations, not the health of a deployed service.

- `Dockerfile` builds the service in two stages and runs as a non-root user.
- `deploy/relay-deployment.yaml` pins the image by digest, separates startup,
  readiness and liveness, enables workload identity, and defines the ClusterIP
  Service, Nginx Ingress, HPA and PodDisruptionBudget.
- `deploy/relay-canary.yaml` runs a second image version and sends ten per cent
  of Nginx traffic to it.
- `deploy/release-strategies.yaml` records rolling, canary and blue-green
  promotion and rollback commands.
- `deploy/relay-environments.yaml` separates staging and production namespaces
  and resource quotas.
- `deploy/kind-cluster.yaml` creates one local control plane and two workers.
- `deploy/rollout-checkpoint.yaml` records the current digest, the previous
  digest, and the one-line rollback command.
- `deploy/relay-local-lxc.conf` shows the unprivileged LXC uid and gid mapping,
  read-only rootfs plan, and capability drop. Replace `/srv/relay/rootfs` with
  the absolute path to the prepared root filesystem on the host.
- `deploy/relay-kata-runtimeclass.yaml` records the RuntimeClass used when relay
  should run inside a Kata utility VM.
- `src/lab_32_container_deploy/checkpoint.py` validates the Kubernetes and
  container artifacts offline.
- `src/lab_32_container_deploy/local_runtime.py` prints dry-run local runtime
  plans and exposes typed Docker, libvirt, and QMP boundaries for relay.
- `src/lab_32_container_deploy/sdk_adapters.py` runs an immutable-image Docker
  smoke check and patches and watches the relay Deployment through typed
  Kubernetes API boundaries.

## Local runtime tradeoffs

| Runtime | Boundary | Execution mechanism | Main hardening |
| --- | --- | --- | --- |
| QEMU with TCG | Virtual machine | Software translation | explicit `-accel tcg`, Unix QMP, QEMU sandbox, read-only guest disk |
| QEMU with KVM | Virtual machine | Hardware acceleration | explicit `-accel kvm`, Unix QMP, QEMU sandbox, read-only guest disk |
| libvirt with KVM | Virtual machine | Managed QEMU/KVM | `qemu:///session`, dynamic sVirt labels, Unix QMP, QEMU sandbox |
| Docker | Container | Shared host kernel | non-root user, read-only root, `no-new-privileges`, cap drop |
| Unprivileged LXC | Container | Shared host kernel | user namespaces, uid and gid remap, read-only root, AppArmor profile |
| Kata | Utility VM | Guest kernel | RuntimeClass boundary, non-root container, read-only root, seccomp |

The local runtime planner prints commands rather than launching them. Compare
the isolation and host requirements of each plan before attempting a rollout.
Printing a command does not prepare an image or guest filesystem.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

The same validation helpers back the tests and can be run directly:

```bash
python -m lab_32_container_deploy.checkpoint
python -m lab_32_container_deploy.local_runtime plan --runtime auto \
  --minimum-isolation virtual-machine --host-profile portable
python -m lab_32_container_deploy.local_runtime plan --runtime libvirt-kvm \
  --host-profile vm-kvm
python -m lab_32_container_deploy.local_runtime plan --runtime kata \
  --host-profile all --format yaml
```

The deterministic tests inject Docker and Kubernetes fakes, so the gate neither
needs the Docker socket nor cluster credentials. For a live exercise, create the
Docker client with `docker_client_from_env()`, or create `AppsV1Api` with
`kubernetes_apps_api(in_cluster=False)` from a restricted kubeconfig. Use
`in_cluster=True` only from a pod with a namespace-scoped service account.

## Local Kubernetes path

This optional integration requires Docker, kind, kubectl and adequate host
resources. The checked-in manifest has an illustrative ACR digest and workload
identity, not the locally built `relay:lab` image. Do not apply it unchanged
and expect kind's image load to replace that digest.

Before deploying, verify the image starts. The current Dockerfile uses
`pip install --no-deps`, but the package imports PyYAML through `checkpoint.py`.
Install that dependency in the image and smoke-test startup; the offline
artifact tests do not check whether the image starts.

After producing a working image, the local adaptation is:

```bash
kind create cluster --name relay --config deploy/kind-cluster.yaml
docker build --tag relay:lab .
kind load docker-image relay:lab --name relay
kubectl --context kind-relay apply -f deploy/relay-environments.yaml
kubectl set image --local -f deploy/relay-deployment.yaml relay=relay:lab \
  -o yaml > relay-kind.yaml
kubectl --context kind-relay --namespace relay-staging apply -f relay-kind.yaml
kubectl --context kind-relay --namespace relay-staging rollout status deployment/relay --timeout=120s
```

Install an Nginx Ingress controller appropriate for kind before testing the
Ingress. Apply `relay-canary.yaml` only after the stable service is ready. Abort
the canary by deleting its Ingress, Service and Deployment. The production
exercise deploys the same artifacts to the AKS cluster created by Lab 08.

The tag override is a local exercise convenience, not release evidence.
For staging/production, publish the working image, record its real digest,
configure real workload identity and use that digest for promotion. HPA needs
a metrics source; Ingress needs its controller. YAML presence does not prove
those controllers are installed.
## Python REPL debugging session

Inspect deployment artifacts before contacting a runtime or cluster:

```pycon
>>> import inspect
>>> import lab_32_container_deploy as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.signature(lab.load_checkpoint)
>>> checkpoint = lab.load_checkpoint()
>>> checkpoint.image
```

The image value is the digest named in the saved rollout file, the object
returned by `load_checkpoint`, not an image fetched from a registry. Compare it with the Deployment and rollback files before
using credentials. In those files, a Service addresses pods, an Ingress
routes incoming HTTP, and the autoscaler changes replica count.

## Completion and cleanup

Finish when you can explain digest identity, probe roles, isolation boundaries
and rollback, and `pybootstrap check` exits 0. Exit 1 means findings; exit 2
means a gate could not run. Optional live evidence must separately show the
working image and rollout. Delete only the owned kind cluster with
`kind delete cluster --name relay`, remove `relay-kind.yaml`, and clean up
any other runtime resources you deliberately created. Never operate a shared
management socket merely to inspect a plan.

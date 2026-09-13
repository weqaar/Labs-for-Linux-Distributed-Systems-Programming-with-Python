# Lab 32 Container Deploy

This checkpoint keeps the relay Kubernetes rollout artifacts offline and adds a
local-runtime planning step for the same service. The lab now covers Azure and
Kubernetes deployment, plus dry-run plans for QEMU with TCG, QEMU with KVM,
libvirt on KVM, Docker, unprivileged LXC, and Kata utility VMs.

KVM matters here as a Linux accelerator that QEMU and libvirt use. It is not a
VM manager on its own. When KVM is absent, QEMU with TCG is the portable
fallback, but it is slow.

## Artifacts

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

```bash
kind create cluster --name relay --config deploy/kind-cluster.yaml
docker build --tag relay:lab .
kind load docker-image relay:lab --name relay
kubectl apply -f deploy/relay-environments.yaml
kubectl --namespace relay-staging apply -f deploy/relay-deployment.yaml
kubectl --namespace relay-staging rollout status deployment/relay
```

Install an Nginx Ingress controller appropriate for kind before testing the
Ingress. Apply `relay-canary.yaml` only after the stable service is ready. Abort
the canary by deleting its Ingress, Service and Deployment. The production
exercise deploys the same artifacts to the AKS cluster created by Lab 08.
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

Inspect Service, Ingress, autoscaler, disruption, and rollout mappings before
using Docker or Kubernetes credentials.

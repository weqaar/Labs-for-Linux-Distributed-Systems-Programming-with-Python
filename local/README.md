# Local relay platform

The Azure labs also run against local open-source resources. The product
contract stays the same; configuration selects a local or Azure adapter.

## Quick start

```bash
docker compose --file local/compose.yaml config
docker compose --file local/compose.yaml up --detach
curl --fail http://127.0.0.1:8080/readyz
```

Add the observability profile when a lab needs traces:

```bash
docker compose --file local/compose.yaml --profile observability up --detach
```

Stop the stack without deleting its volumes:

```bash
docker compose --file local/compose.yaml down
```

Use `down --volumes` only when the lab explicitly asks for a clean store.

## Resource map

| Capability | Azure | Local service |
|---|---|---|
| Blob, queue and table protocols | Azure Storage | Azurite |
| Durable task records | Cosmos DB or Table Storage | PostgreSQL |
| Cache and short leases | Azure Managed Redis | Valkey |
| Brokered work | Service Bus | RabbitMQ |
| Logs, metrics and traces | Azure Monitor | OpenTelemetry Collector and Jaeger |
| Service runtime | Container Apps or AKS | Docker Compose |
| Several Linux hosts | Azure VMs | QEMU/libvirt or LXC |

Published ports bind to `127.0.0.1`. Passwords in `compose.yaml` are disposable
local values and must not be copied into an Azure deployment.

## QEMU without KVM

TCG emulates the guest CPU and works when `/dev/kvm` is unavailable:

```bash
qemu-system-x86_64 \
  -machine q35,accel=tcg \
  -m 2048 -smp 2 \
  -drive file=relay-overlay.qcow2,if=virtio,format=qcow2 \
  -nic user,model=virtio-net-pci,hostfwd=tcp::8080-:8080 \
  -qmp unix:relay-qmp.sock,server=on,wait=off \
  -sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny
```

TCG is portable and slow. Lab 27 reports the selected accelerator rather than
silently changing from KVM to TCG.

## QEMU with KVM and libvirt

```bash
test -r /dev/kvm
virsh --connect qemu:///session define relay.xml
virsh --connect qemu:///session start relay
virsh --connect qemu:///session domifaddr relay
```

Use the Lab 27 Python CLI to render a domain plan before defining it:

```bash
cd labs/27-container-deploy
python -m lab_27_container_deploy.local_runtime plan --runtime libvirt-kvm
```

The per-user `qemu:///session` connection is the default. Use
`qemu:///system` only when the lab needs host networks or storage pools and the
extra privilege has been reviewed.

## LXC

LXC is useful when a lab needs several Linux systems but not separate kernels.
Create unprivileged containers so root in the guest maps to an ordinary host
UID:

```bash
lxc-create --name relay-a --template download -- \
  --dist debian --release bookworm --arch amd64
lxc-start --name relay-a --daemon
lxc-info --name relay-a
```

Do not use privileged LXC containers for untrusted workloads.

## Kata Containers

Kata runs an OCI workload inside a lightweight utility VM. With a local
Kubernetes cluster and the Kata runtime installed, select it through a runtime
class:

```yaml
spec:
  runtimeClassName: kata-qemu
```

Kata needs working hardware virtualisation for practical lab performance. The
container interface remains familiar, while the workload receives a guest
kernel boundary.

## Python control planes

Lab 27 demonstrates injected Python boundaries for Docker, libvirt and QMP.
Tests use fakes, so the gate runs on hosts with none of those tools installed.
Real execution is opt-in because the Docker socket, libvirt socket and QMP
socket can each control a substantial security boundary.

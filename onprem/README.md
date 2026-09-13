# SigRaft on-prem cloud deployment

This directory is the executable deployment boundary for Lab 36. Python reads
one profile and passes it unchanged to Ansible. Ansible creates libvirt
networks, copy-on-write disks, cloud-init media, and domains. It then invokes
the pinned upstream installers and pinned Helm charts listed in
`ansible/group_vars/all.yml`.

## Deployment inputs

Use an Ubuntu 24.04 x86-64 host. The complete profile requires 32 logical CPUs,
128 GiB RAM, 2 TiB usable SSD, hardware virtualization, nested KVM for the
virtualized Nova compute nodes, and `/dev/kvm`. Install Python 3.10 or later,
Ansible Core 2.17, `kubectl`, `openstack`, and Helm 3.
Obtain an Ubuntu 24.04 qcow2 cloud image and verify its published SHA-256 digest.

Confirm nested KVM before apply. Intel reports `Y`; AMD reports `1`:

```bash
cat /sys/module/kvm_intel/parameters/nested
cat /sys/module/kvm_amd/parameters/nested
```

Secrets remain outside this tree:

```bash
export SIGRAFT_SSH_PUBLIC_KEY="$(cat "$HOME/.ssh/id_ed25519.pub")"
export SIGRAFT_SSH_PRIVATE_KEY_FILE="$HOME/.ssh/id_ed25519"
export SIGRAFT_IMAGE_PATH="$HOME/images/ubuntu-24.04-server-cloudimg-amd64.img"
export SIGRAFT_IMAGE_SHA256="<published-64-character-sha256>"
export KOLLA_PASSWORDS_FILE="$HOME/.config/sigraft/passwords.yml"
export OS_CLIENT_CONFIG_FILE=/etc/kolla/clouds.yaml
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
```

Create Kolla's password file with the pinned Kolla virtual environment, store
it outside the repository, and restrict it to the operator:

```bash
python3 -m venv "$HOME/.local/share/sigraft-kolla"
"$HOME/.local/share/sigraft-kolla/bin/pip" install kolla-ansible==19.5.0
"$HOME/.local/share/sigraft-kolla/bin/kolla-genpwd" \
  -p "$KOLLA_PASSWORDS_FILE"
chmod 600 "$KOLLA_PASSWORDS_FILE"
```

Each password must contain at least 16 characters and must differ from every
other service password. The planner checks the complete environment only after
the operator explicitly selects apply. Ansible creates a service-specific
Secret with only the keys required by that chart. Loki receives a separate
MinIO user rather than the MinIO root credential.

## Plan and apply

Install the lab, then render a plan. Neither command below mutates the host:

```bash
pip install -e labs/36-on-prem-cloud
python -m lab_36_on_prem_cloud onprem/config/complete.yaml plan
ansible-playbook --syntax-check -i onprem/ansible/inventory.ini \
  onprem/ansible/site.yml
```

Copy `complete.yaml` to a private local file, set `allow_execution: true`, and
keep the same `artifact_root`. Apply requires a second opt-in:

```bash
python -m lab_36_on_prem_cloud config.local.yaml apply --confirm APPLY
```

The ordered phases install pinned Ansible collections, validate the host and
image, create owned VMs, run Kolla Ansible bootstrap and prechecks, deploy
OpenStack and run post-deploy, create the bounded Heat stack, run Kubespray,
install every pinned platform chart, and validate readiness.

Kolla Ansible 19.5.0 installs the OpenStack 2024.2 control and data planes.
Kubespray v2.27.0 installs Kubernetes v1.31.4. MetalLB advertises the owned
provider pool to the two-replica Nginx ingress controller. Helm installs
Harbor, PostgreSQL, RabbitMQ, Valkey, MinIO, Jaeger, Loki, Prometheus with
Grafana, and the OpenTelemetry Collector. Kubernetes CoreDNS supplies internal
service names. The Collector sends traces to the pinned Jaeger all-in-one
service, metrics to Prometheus, and logs to Loki's native OTLP HTTP endpoint.

The complete checkpoint uses MinIO for S3-compatible object storage. A
multi-host site can replace that chart with Ceph RGW and configure Kolla
Cinder for Ceph while retaining the application's S3 boundary.

Before apply, set `OS_CLIENT_CONFIG_FILE=/etc/kolla/clouds.yaml` so the Heat
phase can use the credentials produced by Kolla post-deploy. Replace the
sample Harbor certificate with a site-issued certificate before accepting
external traffic.

## Validation and teardown

The final phase reruns Kolla prechecks and waits for pods in every owned
namespace. Useful operator checks are:

```bash
openstack endpoint list
openstack network list
kubectl get nodes
kubectl get services -A
kubectl get pods -A
helm list -A
```

Destroy names the exact Helm releases and Heat stack from the profile. It then
removes only libvirt domains and networks carrying the validated
`sigraft-...` ownership prefix and finally removes that prefix's state
directory:

```bash
python -m lab_36_on_prem_cloud config.local.yaml destroy --confirm DESTROY
```

Review the plan before both actions. Do not place passwords, tokens, private
keys, generated kubeconfig files, or Kolla password files in this directory.

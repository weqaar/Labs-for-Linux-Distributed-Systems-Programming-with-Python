# Lab 37: Operating the on-prem cloud with Python

This checkpoint adds one idempotent resource reconciler to `relay`. Narrow
typed protocols separate its policy from OpenStack, Kubernetes and Harbor,
while a read-only Redfish client supplies physical-system inventory.
Concrete adapters use OpenStackSDK, the Kubernetes Python client and Harbor's
v2 HTTP API. Deterministic fakes exercise paging, retries, deadlines and
ambiguous network outcomes without cloud credentials or a running cluster.

## Install and run

Python 3.10 or later is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

`pyproject.toml` declares every runtime and gate dependency. No connection is
opened at import time. A production composition root creates authenticated
clients once, injects the adapters, and closes the clients at shutdown.

## What to inspect

* `core.py` defines resources, pages, operations, failure categories and the
  reconciler.
* `adapters.py` translates the three native client surfaces.
* `fakes.py` supplies a bounded, stateful fake for default gates.
* `redfish.py` discovers a service root and reads bounded, same-origin
  ComputerSystem inventory without exposing management actions.
* The tests cover owned-field comparison, create, update, no-op, pagination,
  rate limits, request loss, response loss, Harbor conflicts and encoded names,
  stable idempotency keys, TLS requirements and complete rollout waits.

An application credential should create the OpenStack connection. A Kubernetes
service account should come from the mounted pod configuration. Harbor should
use a client certificate and an explicit CA trust bundle. Keep secret values
outside desired resource properties because plans and logs are operator
evidence.

A Redfish inventory client uses HTTPS with verified trust and an account limited
to read-only hardware health. Session tokens belong in transport headers, not
URLs or logs. The client rejects cross-origin pagination links and bounds pages
and systems so a BMC cannot redirect or exhaust the collector.

## Python REPL debugging session

The fake makes policy debugging repeatable:

```pycon
>>> import inspect
>>> import lab_37_on_prem_cloud_apis as lab
>>> lab.__file__
'.../lab_37_on_prem_cloud_apis/__init__.py'
>>> "Reconciler" in dir(lab)
True
>>> lab.Reconciler
<class 'lab_37_on_prem_cloud_apis.core.Reconciler'>
>>> inspect.signature(lab.Reconciler)
<Signature (adapters: 'Mapping[str, ResourceAdapter]', *, attempts: 'int' = 3, sleep: 'Callable[[float], None]' = <built-in function sleep>, monotonic: 'Callable[[], float]' = <built-in function monotonic>) -> 'None'>
>>> DesiredResource, FakeAdapter, Reconciler = (
...     lab.DesiredResource, lab.FakeAdapter, lab.Reconciler
... )
>>> server = DesiredResource(
...     "openstack.compute.server",
...     "relay-worker",
...     {"image": "relay-1", "flavor": "small"},
... )
>>> fake = FakeAdapter()
>>> reconciler = Reconciler({server.kind: fake}, sleep=lambda seconds: None)
>>> plan = reconciler.plan([server])
>>> [(change.action.value, change.desired.name) for change in plan]
[('create', 'relay-worker')]
>>> result = reconciler.apply(plan, timeout=10)
>>> result[0].name
'relay-worker'
>>> reconciler.plan([server])[0].action.value
'noop'
>>> len(fake.put_calls)
1
```

Inspect `fake.page_calls`, `fake.put_calls` and `fake.resources` when a test
does not produce the expected plan. Do not print application credentials,
service-account tokens, client keys or complete Secret objects.

## Completion

The checkpoint is complete when:

```bash
PATH=/opt/pyvenv/bin:$PATH pybootstrap check
```

exits zero with no network, OpenStack, Kubernetes, Harbor or Redfish service
available.

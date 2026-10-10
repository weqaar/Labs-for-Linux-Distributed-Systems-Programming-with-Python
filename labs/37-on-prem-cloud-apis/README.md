# Lab 37 Operating the on-prem cloud with Python

This lab compares desired resources with their current state and
plans the changes needed to make them match. Repeating that comparison after
a successful change should require no further write. Typed interfaces
separate this reconciliation policy from OpenStack, Kubernetes and Harbor,
while a read-only Redfish client supplies physical-system inventory.
Concrete adapters use OpenStackSDK, the Kubernetes Python client and Harbor's
v2 HTTP API. Deterministic fakes exercise paging, retries, deadlines and
ambiguous network outcomes without cloud credentials or a running cluster.

## Goal and activities

Converge owned resource fields without duplicating an operation after an
ambiguous failure. You will use the supplied reconciler with fakes, then inspect
the SDK translations and read-only inventory boundary. Installing SDKs does
not connect this lab to an on-prem cloud.

Read [`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`,
and run commands from this directory. HTTPX, OpenStackSDK and the Kubernetes
client are actual declared dependencies. Harbor and Redfish use HTTP APIs;
none of these remote services is required for default tests.

1. Install and run the REPL create/apply/no-op sequence.
2. Run `pytest -q tests/test_lab_37_on_prem_cloud_apis.py`. Compare unchanged
   owned fields with an update and follow a stable idempotency key across retry.
3. Inspect request loss versus response loss. Reconciliation is not permission
   to repeat an arbitrary non-idempotent operation.
4. Change a fake page or rate-limit response and verify bounded paging and
   remaining deadline. Compare with the same-origin Redfish pagination check.

## Install and run

Python 3.10 or later is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

`pyproject.toml` declares every runtime and gate dependency. No connection is
opened at import time. Production startup code creates authenticated clients
once, passes them to the adapters, and closes the clients at shutdown.

## What to inspect

* `core.py` defines resources, pages, operations, failure categories and the
  reconciler.
* `adapters.py` translates resource operations into native SDK or HTTP calls.
* `fakes.py` supplies a bounded, stateful fake for default gates.
* `redfish.py` discovers a service root and reads bounded, same-origin
  ComputerSystem inventory without exposing management actions.
* The tests cover owned-field comparison, create, update, no-op, pagination,
  rate limits, request loss, response loss, Harbor conflicts and encoded names,
  stable idempotency keys, TLS requirements and complete rollout waits.

An application credential should create the OpenStack connection. A Kubernetes
service account should come from the mounted pod configuration. Harbor should
use a client certificate and an explicit CA trust bundle. Keep secret values
outside desired resource properties because operators may inspect those
properties in plans and logs.

A baseboard management controller (BMC) provides hardware inventory independently
of the operating system. The Redfish client uses HTTPS with certificate
verification and an account limited to reading hardware health. Session tokens
belong in transport headers, not
URLs or logs. The client rejects cross-origin pagination links and bounds pages
and systems so a BMC cannot redirect or exhaust the collector.

## Tests

A unit test checks one function or class on its own, with clocks, network,
storage and other dependencies replaced by deterministic fakes. A functional
test checks one complete feature through the lab's public interface, the way
a reader would use it.

`tests/test_lab_37_on_prem_cloud_apis.py` holds the unit tests. They check
the reconciler with `FakeAdapter`, retry and deadline rules, and each
OpenStack, Kubernetes, Redfish and Harbor adapter on its own with fakes.

`tests/test_functional.py` holds the functional tests. They compose the
public `Reconciler` with the real `HarborAdapter` and a stateful fake Harbor
API behind `httpx.MockTransport`. They run plan, apply and plan again for a
project, check an update, a lost create reply that must not create a
duplicate, and a rate limit that stops at the attempt budget.

```bash
pytest tests/test_lab_37_on_prem_cloud_apis.py
pytest tests/test_functional.py
```

`pybootstrap check` runs both kinds of test in its test gate.

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

The first plan creates the missing server in the fake. The second plan says
`noop` because the owned fields already match; the single write in `fake.put_calls`
confirms the second comparison did not create a duplicate.
Inspect `fake.page_calls`, `fake.put_calls` and `fake.resources` when a test
does not produce the expected plan. Do not print application credentials,
service-account tokens, client keys or complete Secret objects.

## Completion

The lab is complete when:

```bash
pybootstrap check
```

exits zero with no network, OpenStack, Kubernetes, Harbor or Redfish service
available.

This lab demonstrates resource reconciliation and inventory collection
for the SigRaft job-orchestration web service. Lab 39's read-only inventory can
be composed with collected data, but it does not automatically import this client
or contact a BMC.
Finish when you can explain create/update/no-op, retry ownership and the
read-only boundary, with `pybootstrap check` exit 0. Exit 1 means findings;
exit 2 means a gate could not run. Fake resources disappear with Python.
Close live clients and remove only resources you explicitly created in an
optional deployment experiment. Never add hardware control actions for cleanup.

# Lab 08 Azure Resources

This lab adds the first description of the Azure resources SigRaft needs,
and the Bicep files that can create them. The Python import name is
`relay`. That name does not mean the program relays traffic. The checks stay
offline. The Bicep source can separately deploy storage, ACR, AKS, Log
Analytics, workload identity and data-plane roles.

Bicep describes Azure resources to create. ACR stores container images and AKS
runs Kubernetes workloads. Resource-management permissions control those
services; separate data permissions control access to stored blobs and queues.

## Goal and working order

Learn to compare desired resources with observed state before making a
deployment. You will first change Python objects that describe the desired
resources, then inspect the Bicep files. Deploying Azure is optional and may
incur charges. Those Python objects do not create cloud resources, and a
repeated local change is not a live deployment test.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The planner uses standard-library dataclasses and enums; its dependencies and
gate tools are declared in `pyproject.toml`. Azure CLI/Bicep, a subscription,
permissions to create resources and assign roles, and an owned resource group
are needed only for live work.

1. Complete the editable install below before the Bicep workflow.
2. Run `pytest -q tests/test_lab_08_azure_resources.py`. Inspect `make_spec`,
   the first apply labels, the empty second plan and reverse teardown order.
3. Change a test's desired storage name and predict the plan before running it.
   Compare the deployer's control-plane role with the worker's data roles.
4. Review environment parameters and generated commands. Only then consider
   a live `what-if`, with a disposable resource group and cleanup plan.

## Capability added here

- model the desired Azure state for relay resources
- check that a second apply to the model has no changes after the first completes
- plan teardown in reverse dependency order so the resource group can be
  destroyed cleanly
- keep `Contributor` separate from blob and queue data-plane roles
- compose reusable storage and platform Bicep modules
- keep dev, staging and production in separate `.bicepparam` files
- enable AKS OIDC workload identity without application credentials
- grant AcrPull to the cluster and data roles to the relay workload identity
- generate reviewable `what-if`, deployment-stack and teardown commands

The intended submission flow below is not implemented by this lab:

```text
relayctl submit --task-id task-17 --action rebuild-search-index
relay stores the task document in blobs and pushes work onto the tasks queue
```

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

Direct commands stay the same locally and in CI:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```
## Tests

The lab has two kinds of test. `tests/test_lab_08_azure_resources.py` holds
the unit tests. They check single pieces such as the apply and destroy plan
labels, the role checks and the command builder in isolation.

`tests/test_functional.py` holds the functional tests. They use the public
package interface the way a deployer would: plan an environment, apply it to
the in-memory state, check worker and deployer access, repair drift and tear
it down. They also load the checked-in Bicep tree and confirm that a copy with
an embedded storage key or a missing environment file is rejected.

Run each kind alone with `pytest tests/test_lab_08_azure_resources.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Python REPL debugging session

Inspect the offline resource model before running an Azure command:

```pycon
>>> import inspect
>>> import lab_08_azure_resources as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> inspect.signature(lab.deployment_commands)
>>> help(lab.RelayDeploymentSpec)
>>> state = lab.DeploymentState()
>>> type(state), repr(state)
>>> spec = lab.RelayDeploymentSpec(
...     subscription_id="sub-0001", location="westeurope",
...     resource_group_name="rg-relay-dev", storage_account_name="relaytasksdev",
...     relay_identity_name="relay-worker",
...     relay_identity_principal_id="principal-relay-worker",
...     deployer_principal_id="principal-relay-deployer",
... )
>>> filled = spec.plan_apply(state).apply(state)
>>> spec.plan_apply(filled).operations
()
```

The identifiers above are model inputs, not real Azure identifiers.
The empty tuple means there are no remaining operations after the first plan
has been applied to the in-memory state.
Inspect the development specification's desired resources and
compare their dependency objects with the Bicep module outputs.

## Bicep workflow

The empty second plan above shows that the model has reached its desired
state. Bicep is a separate step that asks Azure to converge real resources.

The following mutating commands are optional. They assume
`rg-relay-staging` already exists and is owned by this exercise. Review
location, globally unique names and all parameters before use.

Review and compile before using Azure:

```bash
az bicep build --file infra/main.bicep
az bicep lint --file infra/main.bicep
az deployment group what-if \
  --resource-group rg-relay-staging \
  --parameters infra/environments/staging.bicepparam
az deployment group create \
  --resource-group rg-relay-staging \
  --parameters infra/environments/staging.bicepparam
```

For a disposable environment wholly owned by one deployment stack:

```bash
az stack group create \
  --name relay-staging \
  --resource-group rg-relay-staging \
  --parameters infra/environments/staging.bicepparam \
  --deny-settings-mode none \
  --action-on-unmanage deleteAll
az stack group delete \
  --name relay-staging \
  --resource-group rg-relay-staging \
  --action-on-unmanage deleteAll --yes
```

Inspect the preview and stack ownership before either command changes Azure.
The normal gate checks selected declarations and parameter names in the Bicep
text, not compilation or live deployment. It never needs a subscription.

Before live use, check the pinned Kubernetes version against the target region's
available versions. The supplied `AcrPull` assignment targets the cluster
identity, not the kubelet identity used for image pulls. The production registry
also disables public access without provisioning a private endpoint. These
files therefore do not establish that AKS can pull an image; verify identity
and network access separately before deploying a workload.

## Contribution and completion

This independent lab supplies infrastructure and identity design for
the SigRaft job-management web service. Lab 39 retains deployment artifacts
and contracts; it does not import this planner as its runtime.
Finish when you can explain repeat apply, dependency-ordered deletion and why
Contributor does not grant blob access, with `pybootstrap check` exit 0.
Exit 1 means findings; exit 2 means a gate could not run.

The Python model needs no cleanup beyond exiting. For live work, destroy only
the stack or resource group you created, verify deletion in Azure, and account
for resources not owned by that stack. Deleting a local plan does not stop spend.

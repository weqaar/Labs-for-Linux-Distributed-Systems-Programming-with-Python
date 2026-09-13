# Lab 08 Azure Resources

This checkpoint adds the first Azure infrastructure model and the deployable
Bicep platform for the same relay product. The deterministic gate stays
offline. The Bicep source can separately deploy storage, ACR, AKS, Log
Analytics, workload identity and data-plane roles.

## Capability added here

- model the desired Azure state for relay resources
- prove a second apply has no changes when the first one completed
- plan teardown in reverse dependency order so the resource group can be
  destroyed cleanly
- keep `Contributor` separate from blob and queue data-plane roles
- compose reusable storage and platform Bicep modules
- keep dev, staging and production in separate `.bicepparam` files
- enable AKS OIDC workload identity without application credentials
- grant AcrPull to the cluster and data roles to the relay workload identity
- generate reviewable `what-if`, deployment-stack and teardown commands

Example product shape:

```text
relayctl submit --task-id task-17 --action rebuild-search-index
relay stores the task document in blobs and pushes work onto the tasks queue
```

## Bicep workflow

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
The normal gate only validates source contracts and never needs a subscription.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
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
```

Construct a development specification, inspect its desired resources, and
compare their dependency objects with the Bicep module outputs.

# Lab 05: Object-oriented design

This checkpoint establishes the object boundaries used by later `relay`
services. It replaces unstructured task dictionaries with immutable domain
values, keeps storage behind a typed repository protocol, and dispatches task
actions through polymorphic handlers.

## Build the checkpoint

1. At a Python prompt, construct a `Task`. Inspect `type(task)`,
   `isinstance(task, object)`, `id(task)`, `repr(task)`, and the public names
   from `dir(task)`. Confirm that `type(Task) is type`.
2. Compare `service.submit` with `type(service).submit`. Inspect
   `service.submit.__self__`, `service.submit.__func__`, and both signatures to
   see how Python binds `self`.
3. Read `models.py` and identify which invariants belong to construction,
   properties, and the `PositiveInteger` descriptor.
4. Follow `TaskRepository` into the in-memory adapter. Confirm that the service
   depends on the protocol and receives its repository through construction.
5. Add a new action and handler. The metaclass registry should discover it
   without adding a conditional branch to `TaskService`.
6. Inspect `type(handler).__mro__`, `inspect.signature(service.submit)`, and the
   read-only handler registry.
7. Run the complete gate:

```bash
pybootstrap check
```

The checkpoint is complete when invalid domain values cannot enter the
repository, returned collections cannot mutate service state, decorator
metadata remains inspectable, every action uses the common handler operation,
and all gates exit zero.

This capability contributes the domain, service, port, and adapter structure
used by the completed product. Later local and Azure adapters can replace the
memory repository without moving SDK concerns into task objects.

## Python REPL debugging session

```pycon
>>> import inspect
>>> import lab_05_object_oriented_design as lab
>>> task = lab.Task("task-17", lab.TaskAction.INDEX, "documents")
>>> type(task), isinstance(task, object), repr(task)
>>> [name for name in dir(task) if not name.startswith("_")]
>>> service = lab.build_default_service()
>>> service.submit.__self__ is service
True
>>> service.submit.__func__ is type(service).submit
True
>>> inspect.signature(service.submit)
>>> help(type(task))
```

This session separates the object, its class, its public attributes, and the
bound method that supplies `self`.

# Lab 07 Performance diagnostics

This lab diagnoses one SigRaft workload from Python source to the Linux
kernel. The Python import name is `relay`. That name does not mean the
program relays traffic. Debugging locates incorrect behavior, profiling shows
where execution time goes, and benchmarking times a defined operation. The deterministic
gate needs no root access, ptrace, hardware counters, or tracefs.

## Goal and preparation

Choose a measurement that answers a specific performance question, then explain
what it cannot establish. You will investigate a supplied
failure, inspect a profile and compare equivalent implementations, not assume
that native code or a shorter run is automatically better.

Use Python 3.10 or later in this lab directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`. Create and
activate an isolated environment with `python3 -m venv .venv` and
`source .venv/bin/activate`. Python supplies pdb, timeit and cProfile; pyperf
and the gate tools are declared in `pyproject.toml`. A C compiler, GDB, strace,
perf and trace-cmd are optional host tools, not prerequisites for the gate.

Install the lab and run its gate:

```bash
python -m pip install -e ".[dev]"
pybootstrap check
```

## Exercise 1 Python debugging and inspection

The supplied debug target reads a retry count from a dictionary. One dictionary is
valid; the other converts to an integer that violates the retry rule. Run the
valid path, then reproduce that failure:

```bash
python -m lab_07_performance_diagnostics.debug_target --valid
python -m lab_07_performance_diagnostics.debug_target
python -m pdb -m lab_07_performance_diagnostics.debug_target
```

In `pdb`, set a breakpoint in `load_task`, continue, inspect `record`, step
through conversion, continue to the exception, print the stack with `where`,
move with `up` and `down`, and inspect `task`. Compare this live view with
`inspect_object`, `inspect_mapping`, and `inspect_bytecode`.

### Python REPL debugging session

Before entering `pdb`, use the ordinary Python prompt:

```pycon
>>> import lab_07_performance_diagnostics as lab
>>> lab.__name__, lab.__file__
>>> __debug__, type(__debug__)
(True, <class 'bool'>)
>>> dir(__debug__) == dir(True)
True
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> help(lab.inspect_object)
>>> snapshot = lab.inspect_object(lab.inspect_object)
>>> type(snapshot), repr(snapshot)
```

`__debug__` is a Boolean compile-time constant, not a debugger API. Its long
`dir()` result is the `bool` interface and methods inherited from `int`.

The failure shows why conversion and validation are separate: a value can be
an integer and still be an invalid retry count. Locate both steps before
moving from correctness debugging to timing.

## Exercise 2 Timing

Read `timing.py`. `measure_call` accepts a monotonic nanosecond clock so tests
can supply exact readings. `measure_repeated` retains every sample and reports
minimum, median, and maximum.

Compare two `timeit` questions:

```bash
python -m timeit \
  -s 'active=set(map(str, range(1000)))' \
  '"700" in active'
python -m timeit \
  -s 'active=list(map(str, range(1000)))' \
  '"700" in set(active)'
```

The first measures lookup in a prepared set. The second includes set
construction. Decide whether the operation you need to optimize builds a set
on each call or reuses one before comparing numbers.

## Exercise 3 cProfile

cProfile attributes time to function calls. Use it to locate where the supplied
CPU workload spends time, rather than to rank two implementations:

```bash
python -m cProfile -o relay.prof \
  -m lab_07_performance_diagnostics.probe cpu \
  --iterations 2000000
python -m pstats relay.prof
```

At the `pstats` prompt, use:

```text
sort cumulative
stats 20
callers cpu_work
callees cpu_work
sort time
stats 20
```

Then call `profile_to_file` and `read_profile` from Python. Explain the
difference between internal and cumulative time and why a profile duration is
not a benchmark result.

## Exercise 4 pyperf

The suite asks two different questions:

- `relay_classification_list` and `relay_classification_set_rebuilt` measure
  complete classification, including set construction performed by the
  product.
- `relay_lookup_list` and `relay_lookup_set` measure steady-state membership
  after both containers have been prepared.

Start with a short exploratory run:

```bash
python -m lab_07_performance_diagnostics.benchmarks \
  --fast -o exploratory.json
python -m pyperf show exploratory.json
python -m pyperf metadata exploratory.json
python -m pyperf check exploratory.json
python -m pyperf stats exploratory.json
python -m pyperf hist exploratory.json
python -m pyperf dump --verbose exploratory.json
```

Use the same interpreter, dependencies, host, CPU allocation, power state, and
command for the candidate. On a dedicated host, select an appropriate CPU with
`--affinity`. Do not guess an affinity value on a shared machine.
Replace `CPU` below with a permitted CPU ID. Use new output filenames for
each run; pyperf does not overwrite an existing result file.

```bash
python -m lab_07_performance_diagnostics.benchmarks \
  --rigorous --affinity CPU -o baseline.json
# Make one candidate change, then run the identical command to candidate.json.
python -m pyperf compare_to baseline.json candidate.json \
  --table --python-names=baseline:candidate
```

Before comparing, declare a practical threshold such as a ten percent
improvement. Check whether the observed difference meets that threshold, not just whether
pyperf detects a difference. Keep both JSON files because they contain the raw values, warmups,
loop counts, dates, interpreter, platform, and other metadata.

Do not put a nanosecond threshold in `pytest`. The deterministic suite verifies
that both implementations return the same result. `pyperf` collects
benchmark measurements in an environment chosen for that purpose.

## Exercise 5 GDB and core analysis

The Python tools inspect Python calls. The supplied C probe lets you follow
native instructions and inspect a stopped process instead. This optional
exercise requires the host tools listed above.

Build the supplied native probe with symbols:

```bash
cc -g3 -Og -fno-omit-frame-pointer -Wall -Wextra \
  -o relay-probe artifacts/relay_probe.c
gdb --args ./relay-probe 2000000
```

Set a breakpoint on `relay_checksum`, run, inspect `index`, step, print a
backtrace, and disassemble the function with source. The optional `--crash`
argument raises `SIGSEGV` in this disposable process for core-dump practice.
Core files can contain sensitive memory and must follow local retention rules.

Attaching with `gdb -p PID` stops a running process and may be denied by ptrace,
UID, namespace, or capability policy. Do not weaken host policy for this lab.

## Exercise 6 strace, lsof, procfs, and ss

To relate Python resources to kernel objects, start a bounded process that
holds a marker file and a listening socket:

```bash
python -m lab_07_performance_diagnostics.probe \
  wait --seconds 60 --marker relay-probe.json
```

It prints its PID and port. Inspect it from a second terminal:

```bash
strace -ff -tt -T -e trace=network,read,write,openat,close -p PID
lsof -nP -p PID
readlink /proc/PID/cwd
ls -l /proc/PID/fd
grep -E 'State|Threads|VmRSS|ctxt_switches' /proc/PID/status
ss -ltnp
```

Identify the marker descriptor and listening socket. Distinguish a short read,
end of file, `EAGAIN`, `EINTR`, and a long blocking call. Stop the trace and
allow the probe to exit.

## Exercise 7 perf

Count events around the CPU probe:

```bash
perf stat -d -- \
  python -m lab_07_performance_diagnostics.probe \
  cpu --iterations 20000000
```

Interpret elapsed time, task-clock, context switches, migrations, faults,
cycles, and instructions only when those events are available. If policy
permits sampling:

```bash
perf record -g --call-graph dwarf -- \
  python -m lab_07_performance_diagnostics.probe \
  cpu --iterations 20000000
perf report
```

Write down any `perf_event_paranoid`, container, virtualization, symbol, or stack
unwinding limitation with the result.

## Exercise 8 ftrace

Generate and review plans with `DiagnosticPlan.ftrace_record` and
`DiagnosticPlan.ftrace_report`. On an approved host:

```bash
sudo trace-cmd record -p function_graph -P PID \
  -o relay-ftrace.dat sleep 5
trace-cmd report -i relay-ftrace.dat
```

Prefer selected scheduler tracepoints when the question concerns wakeups:

```bash
sudo trace-cmd record \
  -e sched:sched_switch -e sched:sched_wakeup \
  -P PID -o relay-sched.dat sleep 5
```

Keep the interval short, restrict access to recordings, and ensure tracing
stops as part of the same procedure that starts it.

The lab is complete when every deterministic gate passes, each optional
command has a stated question and scope, the saved profile identifies the
expected relay workload, the benchmark files retain their metadata and raw
values, and the final product result is unchanged.

## Tests

The lab has two kinds of test. `tests/test_lab_07_performance_diagnostics.py`
holds the unit tests. They check each helper on its own with controlled
inputs, such as the timing functions with a fake clock, the profile readers,
the inspection helpers and the diagnostic command plans.

`tests/test_functional.py` holds the functional tests. They drive the
`debug_target` and `probe` command-line entry points the way
`python -m lab_07_performance_diagnostics.probe` runs them, and they follow a
saved CPU profile back to the `cpu_work` row. They check the printed output,
the marker file and the errors a reader meets for an invalid retry count or
bad arguments.

Run one kind alone with
`pytest tests/test_lab_07_performance_diagnostics.py` or
`pytest tests/test_functional.py`. `pybootstrap check` runs both.

## Contribution and cleanup

The SigRaft job-management web service needs measurements of latency and
resource use. This lab supplies the diagnostic method and independently
checked probes, not profiling automatically embedded in Lab 39.
Run `pytest -q tests/test_lab_07_performance_diagnostics.py` after changing a
measurement helper, then require `pybootstrap check` exit 0. Exit 1 means
findings; exit 2 means a gate could not run. A performance result is not a
quality-gate threshold or a promise about another machine.

Quit debuggers and profilers, let bounded probes exit, and stop tracing.
Keep selected benchmark results and profiles; delete only exercise outputs you
created, including the marker and native probe. No Azure resource is required.

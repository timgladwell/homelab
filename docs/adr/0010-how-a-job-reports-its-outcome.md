# 10. How a job reports its outcome: through observability, not its exit code

- **Status:** Proposed
- **Date:** 2026-11-14
- **Deciders:** Tim Gladwell

## Changelog

| Date | By | Description |
| --- | --- | --- |
| 2026-11-14 | Tim and Claude | Proposed, from the review of the first job to report its own metrics |

## Context

A job system treats the exit status as an instruction to the scheduler: zero
means "done, don't run me again", and non-zero means "run me again", up to a
limit. Kubernetes Jobs do this (`restartPolicy: OnFailure` with `backoffLimit`),
and so does any other system that reschedules a failed process. The exit status
is therefore **overloaded**: it can't also say whether the job succeeded.

Two ways of using it go wrong:

- **Non-zero on every failure.** The scheduler re-runs work that a restart can't
  fix, such as a job whose configuration is invalid. Each re-run repeats the
  job's side effects, and on a Raspberry Pi that can mean a ten-minute rebuild
  that a retry can't improve. The restarts then bury the one run that explains
  the failure.
- **Reading the scheduler's view as the outcome.** A Job that exits zero is
  `Complete` in Kubernetes and in kube-state-metrics, whatever it did. That
  view only ever knows the exit status.

Constraints:

- **The sites are single Raspberry Pi nodes.** Wasted retries cost real time and
  memory, and each site has to be understood from its own telemetry
  ([ADR 0003](0003-observability-storage-and-stack.md)).
- **Observability already carries what a job needs to report.** Metrics and
  logs reach storage by one contract
  ([ADR 0009](0009-observability-ingest-contract.md)), and alerts are defined by
  the response they need ([ADR 0006](0006-how-observability-is-used.md)).

## Decision

**A job exits non-zero only when it is designed to be retried.** It has
identified a situation that a prompt restart can fix, and it uses the exit
status to reschedule itself. A job that has no such situation always exits zero.
Which failures are retriable is decided in the job's own code, in one place
beside its error codes, with the reason for each.

**Exit zero means "don't run me again", and nothing more.** A failed run can exit
zero. Neither the exit status nor the scheduler's Job status is read as the
result of a run.

**A job reports its result through observability instead:**

1. **The outcome**, as a metric: success or failure, and on failure an error
   code from a closed list. Every signal carries its `site` and `job`
   ([ADR 0009](0009-observability-ingest-contract.md)), so one rule covers every
   job at every site.
2. **Measurements of the run**: how much it changed, how long its requests
   took, and so on, so trends show without reading a log. Duration comes from
   kube-state-metrics, which has a start and a completion time for every job that
   exits zero.
3. **The detail in the logs**: each error with its code, exception and message,
   so a failed run can be explained from its own lines.

An alert looks for the outcome metric reporting failure for a `site` and `job`,
then uses the measurements and the logs to explain it. **A job that reports
nothing is itself a signal:** a run killed by its deadline or by running out of
memory never reaches the point where it reports.

## Consequences

**Positive:**

- One alert shape fits every job, and an error code reads the same across jobs.
- The scheduler retries only when retrying can help, so a configuration mistake
  fails once instead of repeating a long rebuild.
- The cause of a failure is in the logs of the run that failed.

**Negative or risks:**

- `kubectl get jobs` shows `Complete` for a run that failed. Whoever looks has to
  look at the outcome metric.
- A job's retriable list is a judgement, and a wrong entry either wastes a retry
  or gives up on a failure a retry would have fixed. Keeping the list in one
  place beside the error codes makes it easy to revisit.
- Reporting depends on the telemetry path working. A run that can't push its
  metrics still logs its outcome, and the missing metric is what shows it.

## Alternatives considered

**Exit non-zero on any failure.** The simplest rule, and the one most scripts
follow. Rejected: it spends retries on failures a restart can't fix and repeats a
job's side effects each time, and it still wouldn't say why the job failed.

**Encode the result in distinct exit codes.** Rejected: job systems distinguish
only zero from non-zero, and some treat particular codes specially, so the
meaning would depend on the scheduler rather than on the job.

**Read the result from the scheduler's Job status.** Rejected: it only knows the
exit status, so it can't see a run that exited zero after some of its objects
failed, which is the case worth alerting on.

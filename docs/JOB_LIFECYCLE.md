# Owned job lifecycle

Each admitted job retains a stop event and execution owner. Background work owns its thread; synchronous calls own a completion event. Admission coordinates with settings publication and shutdown. Queued cancellation never invokes the callable, and cancellation becomes terminal only after execution actually stops. Exceptions persist failed status; dead-owner recovery records interruption.

Authenticated `POST /api/pipeline/jobs/{jid}/cancel` addresses a job owned by this process/database. Unknown IDs return404; active foreign-process jobs return409. Terminal repeats report that no new cancellation was requested. No unrelated process or inference service is killed.

Cancellation is cooperative between files/stages: an in-flight atomic file may finish. Metadata workers stop only owned subprocesses; cancelled scan reconciliation preserves the previous authoritative snapshot. Description stages check before the next file/batch. CLI synchronous jobs install and restore their temporary main-thread SIGINT handler.

Server shutdown coordinates admission, requests cancellation and waits up to30seconds. It reports remaining owners/admission work on deadline and fails explicitly rather than claiming a live worker stopped. Arbitrary blocking Python threads/filesystem calls cannot be safely forcibly reaped. Existing model/native deadlines still apply.

Office commands use `office_process.run_office_process`: Windows owns the exact child PID/tree; POSIX owns a session/process group. Deadline/error cleanup terminates only that tree, drains/reaps with bounds and retains normal output/exit codes. Conversion has a60second deadline plus bounded termination/drain allowances; this does not override cooperative file-boundary cancellation or the30second shutdown reporting policy.

The browser displays queued and terminal states, supports cancellation and prevents stale polls from reclaiming newer action messages. See [browser ownership](BROWSER_REQUEST_OWNERSHIP.md).

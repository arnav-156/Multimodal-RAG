# Job outcome contract

Stage callables return explicit structured outcomes rather than implying success from a normal return. Scan, metadata and processing report work counts, failures/unsupported/skipped work and bounded diagnostics; processing distinguishes ordinary and recovered chunks. Cancellation is explicit.

The job layer normalizes these outcomes into durable queued/running/terminal records. Failures and remaining work can yield a partial result; exceptions persist failure and restart recovery records interruption. A duplicate active admission is rejected by the existing database/process ownership boundary.

Outcome normalization does not infer parser/model accuracy or prove that a process has stopped. Job cancellation and shutdown own their execution lifecycle separately; see [lifecycle](JOB_LIFECYCLE.md). Native/service deadlines, current-root membership and source-version commit checks still govern the work.

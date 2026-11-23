# Browser request ownership

Search submissions and pagination capture a monotonically increasing version, immutable parameters and the requested offset. Only the latest version can commit results, counts, controls or an error. A current failure clears stale results. Older server requests may finish but cannot render over newer intent.

Document inspection has its own version and owning search version. A newer inspection, search/page or explicit close invalidates earlier previews. Indexed text uses inert `textContent`; downloads use checked document IDs and the preview uses native dialog behavior.

Messages have an action owner and job-status requests have a poll version. A queued notice retains its actual job ID; only its terminal record can replace the still-owned notice. Newer search/settings/validation messages invalidate older job notices. Scheduled or out-of-order polls cannot reclaim messages or roll terminal controls backward. Terminal jobs also trigger a version-owned file/chunk summary refresh; unchanged terminal polls do not repeatedly fetch statistics.

Settings retain dirty drafts through validation, conditional-save conflicts and reauthentication. Old unauthorized responses cannot hide a newly authenticated workspace. A revision precondition prevents a stale editor overwriting a newer save.

Run `node --test tests/browser_intent.test.cjs` from the project root. These tests exercise the actual event handlers with controlled DOM/HTTP completion order; they do not replace actual browser or physical assistive-technology acceptance.

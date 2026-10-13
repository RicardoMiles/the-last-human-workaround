# KiroCrew: Auto model failure during ACP startup

My issue identified a regression in KiroCrew's existing Auto fallback handling: the fallback ran before `set_mode`, while the activated agent spec could later restore an unavailable model. This diagnosis directly informed the scope and validation of [PR #19560](https://github.com/kirodotdev/KiroCrew/pull/19560), which closed [#17519](https://github.com/kirodotdev/KiroCrew/issues/17519).

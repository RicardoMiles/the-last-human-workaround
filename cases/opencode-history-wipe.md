# OpenCode: session history loss after a provider failure

I observed and documented an incident on OpenCode 1.18.25 where a provider failure was followed by session teardown and loss of previously visible history. The maintainer closed the V1 report as `not planned` after OpenCode 2.0 became the active development line and requested a V2 recheck. No public root-cause confirmation or linked fix was provided. The issue has not recurred in my later usage, but V2 behavior remains unverified.

Related issue: [anomalyco/opencode#46402](https://github.com/anomalyco/opencode/issues/46402)

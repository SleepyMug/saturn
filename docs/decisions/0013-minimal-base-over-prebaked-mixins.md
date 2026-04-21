# 0013 — Base stays minimal; mixin installs stay in per-workspace Dockerfiles

> Records the non-decision to reject a proposal to bake mixin installs (ssh, gh, claude, codex) into `localhost/saturn-base:latest` "for shared cross-project caching". Docker's layer cache already shares mixin layers across workspaces that declare identical `RUN` lines on the same parent base; the proposal would optimize something already optimized while giving up the selectivity and registry-freeness that 0012 deliberately bought.

## Context

A recurring question from users who see their per-workspace `.saturn/Dockerfile` contains the same `RUN curl ... claude.ai/install.sh | bash` that every other claude-enabled workspace has: "why not put this in the base image so it's downloaded once?"

The premise sounds right. The remedy mostly isn't needed, and when it is, the existing `saturn base build` path covers it without changing the shipped design.

**What the layer cache actually does.** Docker keys cached layers on (parent-image-digest, instruction text, build-context inputs). Two workspaces built `FROM localhost/saturn-base:latest` with textually identical `RUN curl ... claude.ai/install.sh | bash` lines get a cache hit on the second build — no re-download, no re-install. The duplication is in the source text of each Dockerfile, not in build work or on-disk storage. The observation that motivates the question ("every project installs its own deps") is a mental-model artifact; the wire behavior is close to what the proposal would produce.

**What moving installs into the base would cost.** 0012 explicitly shrank the base ("No ssh/gh/nodejs/claude/codex — those live in per-workspace Dockerfiles") as part of retiring the mixin registry. A pure-Python project doesn't carry the nodejs+npm+claude blob; a CI workspace without any mixin is just `FROM saturn-base` and nothing else. Re-baking mixins into the base reverses that — every workspace pays for every mixin whether or not it wants it — and reintroduces registry work in the base (tracking claude/gh/codex versions, rebuild semantics, opt-out flags).

**What users who actually want a prebaked base should do.** `saturn base build <custom.Dockerfile>` plus `SATURN_BASE_IMAGE=<tag>` already covers this case. A user who builds many workspaces with the same tool set can write a one-screen Dockerfile that pre-installs those tools, build it as their personal base, and have every workspace `FROM` it implicitly.

## Decision

- **`BASE_DOCKERFILE` stays minimal.** Debian trixie + `docker-cli` + `docker-compose` + `python3` + `git` + `curl` + the saturn script. No change.
- **`_DF_INSTALL` entries stay in the saturn script.** `ssh`, `gh`, `claude`, `codex` templates continue to append install `RUN`s to per-workspace Dockerfiles at `saturn new` time.
- **The escape hatch becomes a documented pattern.** Users who want a prebaked base `saturn base build` their own, optionally set `SATURN_BASE_IMAGE` per shell, and point their workspace `.saturn/Dockerfile` `FROM` the custom tag. README gets a "Prebaking tools into the base" subsection near the existing `saturn base build` docs that shows the Dockerfile shape.

## Consequences

- **No code change.** This decision codifies the status quo; the work is documentation.
- **Mental model published.** The README clarification + this record give a referenceable answer to the "why is every project reinstalling things?" question, so future users don't have to re-derive it.
- **Escape hatch promoted.** `SATURN_BASE_IMAGE` moves from a near-hidden optional env var (mentioned once in the README env-var table) to a worked example in the README body. Users who genuinely benefit from a prebaked base find the path faster.
- **Decision flips if cache-sharing ever stops working.** If future docker/podman versions change layer-cache semantics such that identical `RUN` lines stop reusing layers across workspaces, the cost/benefit shifts and this should be revisited.

## Rejected alternatives

- **Move all mixin installs into `BASE_DOCKERFILE`.** Every workspace carries every mixin's footprint regardless of need. Reverses 0012's explicit trajectory away from a mixin registry. Doesn't meaningfully improve what the layer cache already does.
- **Ship multiple base variants (`saturn-base`, `saturn-base-full`, `saturn-base-claude`, …).** Reintroduces the registry problem at a different layer; adds a base-selection axis to `saturn new`; still doesn't cover users with unusual mixin combos. The one-flag `saturn base build` already solves the 90% case without this.
- **Add a `saturn new --base-image <tag>` flag that writes a non-default `FROM` into the workspace Dockerfile.** Marginal over editing the Dockerfile directly (it's two lines) and over setting `SATURN_BASE_IMAGE` in the shell. Not worth the CLI surface.
- **Add a `--no-cache` passthrough to `saturn base default` / `base build`.** Orthogonal to this decision; separate question about whether saturn should expose `docker build` flags. Not resolved here.

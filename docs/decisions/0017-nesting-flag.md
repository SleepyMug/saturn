# 0017 — `--nesting` flag replaces `--socket` + `--host-gateway`

> `saturn new --nesting` seeds both the engine socket bind-mount and a
> `host.docker.internal:host-gateway` `extra_hosts` entry. The split
> `--socket` / `--host-gateway` flags (post-0012 experimental) merge
> into one flag because the two are always wanted together: if a
> container gets the socket, it almost certainly also needs host
> network access. A new `saturn host-addr` command abstracts the
> host-vs-guest address so scripts don't need to know which mode
> they're in.

## Context

Saturn containers live on compose's default bridge network. Reaching
services exposed to the host (dev servers, databases) requires a
cross-network path. Two standard compose mechanisms exist:

1. **`extra_hosts` + `host-gateway`** — adds a DNS entry
   (`host.docker.internal` → host IP) via the container's
   `/etc/hosts`. Keeps network isolation.
2. **`network_mode: host`** — shares the host's network namespace
   directly. Loses isolation; has a known Podman `exec` caveat.

In the pre-0017 experimental phase, `--socket` seeded only the socket
bind-mount and `--host-gateway` seeded only the `extra_hosts` entry.
In practice the two are always wanted together — a container that gets
the engine socket almost certainly also needs to reach host services.

### Why `extra_hosts` + `host-gateway` (not `network_mode: host`)

Debian Trixie (Saturn's base distribution) ships Podman 5.8.1 as of
April 2026 — well above 5.3 where `host-gateway` gained proper support
via pasta's `--map-guest-addr` (maps `169.254.1.2` inside the
container to the real host IP). Docker ≥ 20.10 supports `host-gateway`
via the bridge gateway. Both thresholds are comfortably met.

| | `extra_hosts` + `host-gateway` | `network_mode: host` |
|---|---|---|
| Network isolation | Preserved | Lost |
| `saturn shell` / exec | Works normally | Broken — Podman exec caveat |
| Localhost-only services | Must bind `0.0.0.0` | Reachable as-is |
| Min engine | Podman ≥ 5.3, Docker ≥ 20.10 | Any |

Services "designed to be exposed to the host" already bind to
`0.0.0.0`, so the limitation is self-selecting. `network_mode: host`
remains available as a compose override for users who need it.

## Decision

### `--nesting` seed flag

`saturn new --nesting` seeds both into `.saturn/compose.yaml`:

```yaml
services:
  dev:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ${SATURN_SOCK}:/var/run/docker.sock
```

One flag covers the two things a nested-capable container needs:
engine access (socket) and host-network access (host-gateway DNS).

### `saturn host-addr` command

A new CLI command that prints the address to reach the host from the
current context:

- **Host mode** (`SATURN_IN_GUEST != "1"`): prints `localhost`.
- **Guest mode** (`SATURN_IN_GUEST == "1"`): prints `host.docker.internal`.

Scripts use `$(saturn host-addr):PORT` without knowing which mode
they're in — same pattern as `SATURN_SOCK` abstracting the socket
path.

### No translation-pipeline changes

Both `extra_hosts` and the socket bind-mount are standard compose
features. They pass through `docker compose config --format json` →
`compose.json` → `docker compose -f compose.json` verbatim. Zero
changes to `_translate_compose`, `_translate`, `_current_container_mounts`,
or `passthrough`.

## Consequences

- **`--socket` and `--host-gateway` are removed** from the public
  surface. The `_FLAGS` tuple now carries `"nesting"` instead of
  `"socket"` + `"host-gateway"`.
- **Simpler UX.** One flag to remember for nesting capability; one
  command to get the right address regardless of context.
- **Override-compatible.** Users who need `network_mode: host` or a
  different gateway hostname can layer it via
  `.saturn/compose.override*.yaml` or `SATURN_COMPOSE_OVERRIDES`.
- **Host services must bind `0.0.0.0`** to be reachable via
  `host.docker.internal`. Services bound only to `127.0.0.1` are not
  reachable from the bridge network. This matches the promise of
  "designed to be exposed to the host."
- **Podman 5.3+ required for `host-gateway`** support. Debian Trixie
  ships 5.8.1 — the minimum is met. Docker ≥ 20.10 also works.

## Rejected alternatives

- **`network_mode: host` as the seed default.** Breaks `saturn exec`
  under rootless Podman (kernel limitation — user lacks
  `CAP_SYS_ADMIN` to join root-owned netns). Available as an override
  for users who need localhost-only service access and accept the
  trade-off.
- **Separate `--socket` and `--host-gateway` flags.** Adds
  combinatorial surface for no user benefit. The two are always wanted
  together: a container that gets the engine socket needs host network
  access; a container that needs host network access benefits from
  engine access.
- **Auto-detecting host-gateway support at seed time.** Saturn doesn't
  probe engine version. The minimum is met by the base distribution;
  users on older engines get a clear compose-level error.
- **A `saturn host-addr --gateway` flag.** The command is intentionally
  flagless — it does one thing (return the host address) and the
  context (IS_HOST) is the only input.

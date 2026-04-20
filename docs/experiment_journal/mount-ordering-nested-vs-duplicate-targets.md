# Mount ordering: nested vs duplicate targets

> Dated: 2026-04-20. Docker 26.1.5, podman (same host). Tested because saturn's mixin feature allows users to pick multiple mixins whose target paths can nest (e.g. `gh` at `/home/agent/.config/gh` + `xdg-config` at `/home/agent/.config`).

## Claim under test

"Docker automatically reorders `-v` / `--mount` flags so the shortest-prefix target is mounted first, with deeper-nested targets layered on top."

## Findings

- **True on docker 26.1.5 AND podman.** Both engines produce identical container filesystems regardless of the CLI order of `-v` flags:
  - Outer mount (shorter path, e.g. `/home/agent/.config`) is applied first.
  - Inner mount (longer path, e.g. `/home/agent/.config/gh`) lands on top of the corresponding subpath.
  - At paths not covered by the inner mount, the outer mount's contents are visible.
  - At paths covered by the inner mount, the inner mount's contents win.
- **Exact-duplicate targets** (two mounts at the same path) **error out on both engines**:
  - Docker: `Error response from daemon: Duplicate mount point: /data.` (exit 125)
  - Podman: `Error: /data: duplicate mount destination` (exit 125)

## Test protocol

```sh
# Two volumes with distinctive content
docker volume create mnt_outer
docker volume create mnt_inner
docker run --rm -v mnt_outer:/m alpine sh -c 'echo OUTER > /m/marker; mkdir /m/sub; echo OUTER_SUB > /m/sub/marker'
docker run --rm -v mnt_inner:/m alpine sh -c 'echo INNER > /m/marker'

# Mount both at nested paths — try both orderings
for args in "-v mnt_outer:/cfg -v mnt_inner:/cfg/gh" \
            "-v mnt_inner:/cfg/gh -v mnt_outer:/cfg"; do
  docker run --rm $args alpine sh -c 'cat /cfg/marker; cat /cfg/sub/marker; cat /cfg/gh/marker'
done
```

Both orderings print the same three lines: `OUTER`, `OUTER_SUB`, `INNER`.

Duplicate-target test:

```sh
docker run --rm -v mnt_outer:/data -v mnt_inner:/data alpine true
# -> docker: Error response from daemon: Duplicate mount point: /data.
```

## Implications for saturn

- **Nested mixin targets are safe** on supported engines. Combining e.g. `xdg-config` (`/home/agent/.config`) with `gh` (`/home/agent/.config/gh`) works as naive users expect: the `gh` volume provides `/home/agent/.config/gh`, the `xdg-config` volume provides everything else under `/home/agent/.config`.
- **Exact-target collisions between mixins** (or between a mixin and the ws mount or the socket mount) will fail at `docker run` time. Saturn's fail-fast check (`_check_mount_overlap`) pre-empts this with a clearer error message that names the conflicting labels.
- **Nesting is still worth an advisory note** so users who combine mixins know what's happening — the effective content at the nested subpath comes from the inner mount, not the outer. Saturn prints a stderr `note:` when nesting is detected.

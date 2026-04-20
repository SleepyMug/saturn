# 0001 — Single-file distribution with inlined Containerfile

> Saturn ships as one Python script. The saturn-base Containerfile is inlined; `saturn base default` assembles a temp build context containing both the Containerfile and a copy of saturn itself.

## Context

Early iterations had two on-disk files — a `saturn` script and an adjacent `Containerfile` — expected to live in the same directory. The script resolved `SCRIPT_DIR/Containerfile` by default. Distribution required users to clone a repo or fetch two files and put them together; overrides via `SATURN_BASE_CONTAINERFILE` were available but never the default path.

## Decision

Inline the Containerfile into saturn as `BASE_CONTAINERFILE_TEXT`. On `saturn base default` (or `ensure_base()` on first use), create a `tempfile.TemporaryDirectory`, write the Containerfile text into it, `shutil.copy` the running saturn script alongside it as `saturn`, then `docker build -f <tmp>/Containerfile -t saturn-base <tmp>`. The `COPY saturn /usr/local/bin/saturn` step in the Containerfile works because the build context contains saturn.

Customization is an explicit CLI path, not an env var: `saturn base template > my.Containerfile` prints the inlined default, and `saturn base build my.Containerfile` rebuilds from a user-supplied file. Custom Containerfiles must keep `COPY saturn /usr/local/bin/saturn` because saturn still gets copied into the build context alongside the user's file.

## Consequences

- Distribution collapses to `curl .../saturn -o ~/.local/bin/saturn && chmod +x`.
- The Containerfile is edited as a Python multiline string — OK-sized (~15 lines), infrequently edited.
- `SCRIPT = Path(__file__).resolve()` at module top makes the `shutil.copy` work from any CWD.
- The old repo artifacts (`Containerfile`, `app.py`, `demo.sh`) are superseded and were removed.
- Self-recursion: saturn embeds itself into the base image; inside the base image, saturn at `/usr/local/bin/saturn` is a bit-for-bit copy of what ran the build. Nested invocations use the same version.

## Rejected alternatives

- **Publish saturn-base to a registry** — simplifies `saturn base default` to `docker pull`, but adds CI/versioning ops. Reconsider if saturn grows external users.
- **zipapp bundle** — overkill for a single Python script + a small Containerfile.

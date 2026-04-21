# AppArmor's `pasta` profile blocks podman `runRoot` outside `$HOME`/`/tmp`/`/run/user/$UID`

> Dated: 2026-04-21. Debian 13, kernel 6.12, podman 5.4.2, rootless. Hit while running `saturn up` on a workspace whose host had podman's `runRoot` relocated to an external data disk mounted at `/data`.

## Symptom

```
Error response from daemon: setting up Pasta: pasta failed with exit code 1:
Couldn't open PID file /data/<user>/containers/networks/rootless-netns/rootless-netns-conn.pid: Permission denied
```

The directory itself was owned by the invoking user and empty. `podman unshare touch <that path>` succeeded — i.e. plain user-namespace processes had write access. Only `pasta`, invoked by podman to set up rootless networking, was denied.

## Root cause

Debian's `apparmor` package ships an enforce-mode profile for pasta at `/etc/apparmor.d/usr.bin.pasta`, which uses an **allowlist** for writable paths (via `abstractions/pasta`):

- `@{run}/user/@{uid}/**` — i.e. `/run/user/$UID/**`
- `/tmp/**`
- `owner @{HOME}/**`

Anything else — an external disk mount, `/opt`, `/var/lib/...` — gets denied, regardless of unix ownership. The profile was written assuming podman's default `runRoot` lives under one of those three locations.

Confirm with:

```sh
sudo aa-status | grep -iE 'pasta|passt'       # both should be in enforce mode
cat /etc/apparmor.d/abstractions/pasta         # check the allowlist
grep -E '^(graphroot|runroot)' ~/.config/containers/storage.conf
```

If `runroot` is outside the allowlist, pasta's pid-file open fails even though the unix permissions look correct.

## Fixes (pick based on intent)

1. **Keep AppArmor, move `runRoot` into the allowlist.** Edit `~/.config/containers/storage.conf`:
   ```toml
   runroot = "/run/user/1000/containers"
   ```
   Then `podman system migrate`. `runRoot` is transient state (sockets, pid files, netns handles) — losing the custom placement costs nothing. `graphRoot` (images, layers) can stay on the external disk.

2. **Keep the external `runRoot`, disable the pasta profile.**
   ```sh
   sudo ln -s /etc/apparmor.d/usr.bin.pasta /etc/apparmor.d/disable/
   sudo apparmor_parser -R /etc/apparmor.d/usr.bin.pasta
   ```
   Reversible with `sudo rm /etc/apparmor.d/disable/usr.bin.pasta && sudo apparmor_parser -a /etc/apparmor.d/usr.bin.pasta`. Other AppArmor profiles remain enforced.

3. **Swap pasta for slirp4netns.** Set `default_rootless_network_cmd = "slirp4netns"` in `~/.config/containers/containers.conf`. slirp4netns has no equivalent AppArmor profile on Debian 13, so it's unaffected — but it's slower and on the deprecation path upstream.

## Implications for saturn

- Saturn's rootless-podman story implicitly depends on the engine's rootless network setup working. When it doesn't, the failure surfaces as a `docker compose up` error that looks like it's about saturn or compose, but is actually a host policy problem one layer below.
- No saturn change is warranted — this is an engine/host configuration issue, not something the wrapper can paper over. Worth linking from a "common host-setup gotchas" section if/when that gets written, since users who relocate podman's storage (common on workstations with a separate data disk) will hit this the first time they bring up a workspace.
- Diagnostic signature worth recognising: `pasta failed ... Permission denied` on a pid/log file that appears writable. The tell is that `podman unshare` can write where pasta can't — that's AppArmor, not unix perms.

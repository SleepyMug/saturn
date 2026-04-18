#!/usr/bin/env bash
set -euo pipefail

dev_user=${DEV_USER:-guest}
dev_uid=${DEV_UID:-1000}
dev_gid=${DEV_GID:-1000}
dev_proj_name=${DEV_PROJECT_NAME:-app}
dev_home=/home/"$dev_user"

current_gid=$(getent group "$dev_user" | cut -d: -f3 || true)
if [[ -n "$current_gid" && "$current_gid" != "$dev_gid" ]]; then
  groupmod -g "$dev_gid" "$dev_user"
elif [[ -z "$current_gid" ]]; then
  groupadd -g "$dev_gid" "$dev_user"
fi

current_uid=$(id -u "$dev_user" 2>/dev/null || true)
if [[ -n "$current_uid" && "$current_uid" != "$dev_uid" ]]; then
  usermod -u "$dev_uid" -g "$dev_gid" -d "$dev_home" -m "$dev_user"
elif [[ -z "$current_uid" ]]; then
  useradd --create-home --home-dir "$dev_home" --shell /bin/bash -u "$dev_uid" -g "$dev_gid" "$dev_user"
fi

mkdir -p "$dev_home"/"${dev_proj_name}"
chown -R "$dev_uid:$dev_gid" "$dev_home"

if [ -S /var/run/docker.sock ]; then                            
  sock_gid=$(stat -c '%g' /var/run/docker.sock)
  if ! getent group "$sock_gid" >/dev/null 2>&1; then                                               
    groupadd -g "$sock_gid" docker
  fi
  usermod -aG "$(getent group "$sock_gid" | cut -d: -f1)" "$dev_user"
fi

if [ -e /dev/kvm ]; then                            
  kvm_gid=$(stat -c '%g' /dev/kvm)
  if ! getent group "$kvm_gid" >/dev/null 2>&1; then                                               
    groupadd -g "$kvm_gid" kvm
  fi
  usermod -aG "$(getent group "$kvm_gid" | cut -d: -f1)" "$dev_user"
fi

exec "$@"

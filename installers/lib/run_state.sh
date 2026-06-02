#!/usr/bin/env bash

smart_auto_state_dir() {
  printf '%s\n' "${SMART_AUTO_STATE_DIR:-${REPO_DIR:-$(pwd)}/.cache/run-state}"
}

smart_auto_hash_file() {
  local name="$1"
  printf '%s/%s.hash\n' "$(smart_auto_state_dir)" "$name"
}

ensure_smart_auto_state_dir() {
  mkdir -p "$(smart_auto_state_dir)"
}

_smart_auto_emit_files() {
  local path
  for path in "$@"; do
    [[ -e "$path" ]] || continue
    if [[ -d "$path" ]]; then
      find "$path" -type f \
        ! -path '*/.DS_Store' \
        ! -path '*/__pycache__/*' \
        ! -path '*/.pytest_cache/*' \
        ! -path '*/.mypy_cache/*' \
        ! -path '*/.ruff_cache/*' \
        ! -path '*/.cache/*' \
        ! -path '*/node_modules/*' \
        ! -path '*/.venv/*' \
        -print0
    else
      printf '%s\0' "$path"
    fi
  done
}

compute_path_fingerprint() {
  local files=()
  local path

  while IFS= read -r -d '' path; do
    files+=("$path")
  done < <(_smart_auto_emit_files "$@" | python3 -c 'import sys; data=sys.stdin.buffer.read().split(b"\0"); paths=sorted(p for p in data if p); sys.stdout.buffer.write(b"\0".join(paths)+b"\0" if paths else b"")')

  if [[ ${#files[@]} -eq 0 ]]; then
    printf 'empty\n'
    return 0
  fi

  (
    local file
    for file in "${files[@]}"; do
      printf 'PATH\t%s\n' "$file"
      shasum "$file"
    done
  ) | shasum | awk '{print $1}'
}

smart_auto_hash_changed() {
  local current_hash="$1"
  local hash_file="$2"

  if [[ ! -f "$hash_file" ]]; then
    return 0
  fi

  local previous_hash
  previous_hash="$(tr -d '\n' < "$hash_file")"
  [[ "$current_hash" != "$previous_hash" ]]
}

write_smart_auto_hash() {
  local current_hash="$1"
  local hash_file="$2"

  ensure_smart_auto_state_dir
  printf '%s\n' "$current_hash" > "$hash_file"
}

smart_auto_service_targets() {
  local mode="$1"
  case "$mode" in
    dashboard)
      printf 'app\n'
      ;;
    native)
      printf 'app browser-worker browser-runtime\n'
      ;;
    docker)
      printf 'app worker browser-worker browser-runtime\n'
      ;;
    *)
      return 1
      ;;
  esac
}

smart_auto_should_restart_services() {
  local image_changed="$1"
  local runtime_changed="$2"
  [[ "$image_changed" != "true" && "$runtime_changed" == "true" ]]
}

smart_auto_should_restart_native_worker() {
  local runtime_changed="$1"
  local worker_running="$2"
  [[ "$runtime_changed" == "true" || "$worker_running" != "true" ]]
}

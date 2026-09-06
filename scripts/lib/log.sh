# Labeled logging for shell scripts. Mirrors api/cloudphone_logging.py.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/log.sh"
#   LOG_TYPE=RDR
#   log_info "container started"
#
# Format: TIMESTAMP [TYPE] [LEVEL] MESSAGE   (LOG_FORMAT=json for one JSON/line)
# Logs go to stderr so --json stdout stays machine-readable.

LOG_TYPE="${LOG_TYPE:-SYS}"
LOG_FORMAT="${LOG_FORMAT:-text}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

_log_types="SYS API ORC ADB CMD APM VNC RDR CVD GAP NGX FFM DKR LCT TST"

_log_normalize_type() {
    local candidate
    candidate="$(echo "${1:-SYS}" | tr '[:lower:]' '[:upper:]')"
    case " $_log_types " in
        *" $candidate "*) echo "$candidate" ;;
        *) echo "SYS" ;;
    esac
}

_log_level_rank() {
    case "$(echo "${1:-INFO}" | tr '[:lower:]' '[:upper:]')" in
        DEBUG) echo 10 ;;
        INFO) echo 20 ;;
        WARN|WARNING) echo 30 ;;
        ERROR) echo 40 ;;
        *) echo 20 ;;
    esac
}

_log_json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g'
}

log_emit() {
    local level="$1"; shift
    local msg="$*"
    local type ts
    type="$(_log_normalize_type "$LOG_TYPE")"

    if [[ "$(_log_level_rank "$level")" -lt "$(_log_level_rank "$LOG_LEVEL")" ]]; then
        return 0
    fi

    # 10# forces base 10: nanoseconds like 095826543 are not octal.
    local nanos
    nanos="$(date '+%N' 2>/dev/null || echo 0)"
    ts="$(date '+%Y-%m-%d %H:%M:%S').$(printf '%03d' $(( 10#${nanos:-0} / 1000000 )))"

    if [[ "$LOG_FORMAT" == "json" ]]; then
        printf '{"ts":"%s","type":"%s","level":"%s","msg":"%s"}\n' \
            "$ts" "$type" "$level" "$(_log_json_escape "$msg")" >&2
    else
        printf '%s [%s] [%-5s] %s\n' "$ts" "$type" "$level" "$msg" >&2
    fi
}

log_debug() { log_emit DEBUG "$@"; }
log_info()  { log_emit INFO "$@"; }
log_warn()  { log_emit WARN "$@"; }
log_error() { log_emit ERROR "$@"; }

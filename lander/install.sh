#!/usr/bin/env bash
#
# GonkLander bootstrap for Linux and macOS.
#
#   curl -fsSL https://marclevin.me/gonk | bash
#
# This script is public. It contains no credentials and asks for none.
# It needs no root, writes only under your home directory, and is safe to
# run again: a second run upgrades or repairs the first.
#
# Optional environment variables:
#   GONK_VERSION   tag or branch to install                  (default: main)
#   GONK_REPO      GitHub repository, as owner/name           (default: marclevin/GonkLander)
#   GONK_SOURCE    install from this path or URL instead
#   GONK_PROFILE   after installing, run `gonk land <profile>`

set -euo pipefail

# Everything lives inside main(), which is called on the last line. If the
# download is cut short, bash reaches the end of input before it reaches the
# call, and nothing runs.
main() {
    local repo="${GONK_REPO:-marclevin/GonkLander}"
    local version="${GONK_VERSION:-main}"
    local bin_dir="${HOME}/.local/bin"

    say "GonkLander: making this machine Marc-compatible."
    echo

    detect_platform
    ok "Platform: ${OS} ${ARCH}"

    check_prerequisites
    ensure_uv
    install_gonk "$(choose_source "$repo" "$version")"
    check_path "$bin_dir"

    echo
    say "Gonk has landed."
    echo
    if [ -n "${GONK_PROFILE:-}" ]; then
        # /dev/tty: when this script arrives through a pipe, standard input is
        # the script itself, and gonk could not ask anything on it. The device
        # can exist without being usable (cron, CI), so try opening it.
        if (exec </dev/tty) 2>/dev/null; then
            "${bin_dir}/gonk" land "$GONK_PROFILE" </dev/tty
        else
            "${bin_dir}/gonk" land "$GONK_PROFILE" --yes
        fi
    else
        echo "Next:"
        echo "    gonk doctor      see what this machine has and lacks"
        echo "    gonk land dev    install your usual tools"
    fi
}

# --- output ------------------------------------------------------------------

if [ -t 1 ]; then
    BOLD=$'\033[1m' GREEN=$'\033[32m' RED=$'\033[31m' YELLOW=$'\033[33m' RESET=$'\033[0m'
else
    BOLD="" GREEN="" RED="" YELLOW="" RESET=""
fi

say()  { echo "${BOLD}$*${RESET}"; }
ok()   { echo "  ${GREEN}✓${RESET} $*"; }
warn() { echo "  ${YELLOW}!${RESET} $*"; }

# fail "what went wrong" "what to try" ["another thing to try" ...]
fail() {
    echo >&2
    echo "${RED}${BOLD}$1${RESET}" >&2
    shift
    if [ "$#" -gt 0 ]; then
        echo >&2
        echo "Try:" >&2
        for hint in "$@"; do echo "    ${hint}" >&2; done
    fi
    exit 1
}

# --- steps -------------------------------------------------------------------

detect_platform() {
    case "$(uname -s)" in
        Linux)  OS="linux" ;;
        Darwin) OS="macos" ;;
        MINGW* | MSYS* | CYGWIN*)
            fail "This is the Linux and macOS installer." \
                "In PowerShell: irm https://marclevin.me/gonk.ps1 | iex"
            ;;
        *)
            fail "Gonk does not support $(uname -s) yet." \
                "Supported: Linux, macOS, Windows."
            ;;
    esac

    case "$(uname -m)" in
        x86_64 | amd64)  ARCH="x86_64" ;;
        aarch64 | arm64) ARCH="arm64" ;;
        *)
            fail "Gonk does not support $(uname -m) processors yet." \
                "Supported: x86_64, arm64."
            ;;
    esac
}

check_prerequisites() {
    if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ]; then
        fail "There is no home directory to install into (HOME is '${HOME:-}')."
    fi
    if [ ! -w "$HOME" ]; then
        fail "Your home directory, ${HOME}, is not writable."
    fi

    if command -v curl >/dev/null 2>&1; then
        FETCH="curl"
    elif command -v wget >/dev/null 2>&1; then
        FETCH="wget"
    else
        fail "Gonk needs curl or wget to download itself, and found neither." \
            "Debian/Ubuntu: sudo apt-get install -y curl" \
            "Fedora:        sudo dnf install -y curl" \
            "macOS:         curl is built in; check your PATH"
    fi
    ok "Prerequisites: ${FETCH}"
}

fetch() {
    if [ "$FETCH" = "curl" ]; then
        curl -fsSL "$1"
    else
        wget -qO- "$1"
    fi
}

ensure_uv() {
    export PATH="${HOME}/.local/bin:${PATH}"
    if command -v uv >/dev/null 2>&1; then
        ok "uv: $(uv --version 2>/dev/null | head -n 1)"
        return
    fi

    echo "  Installing uv (Python package manager)…"
    local script
    script="$(fetch https://astral.sh/uv/install.sh)" ||
        fail "Could not download the uv installer." \
            "Check this machine's Internet connection." \
            "Or install uv yourself: https://docs.astral.sh/uv/"

    # Do not let uv edit shell profiles; this script reports on PATH itself.
    printf '%s\n' "$script" | UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 ||
        fail "The uv installer did not finish." \
            "Install uv yourself, then run this again: https://docs.astral.sh/uv/"

    command -v uv >/dev/null 2>&1 ||
        fail "uv was installed, but cannot be found in ${HOME}/.local/bin."
    ok "uv: installed $(uv --version | head -n 1)"
}

# Prints the source to install from. A local checkout wins, so that
# ./lander/install.sh installs the code it sits next to.
choose_source() {
    local repo="$1" version="$2"
    if [ -n "${GONK_SOURCE:-}" ]; then
        echo "$GONK_SOURCE"
        return
    fi

    local script="${BASH_SOURCE[0]:-}"
    if [ -n "$script" ] && [ -f "$script" ]; then
        local root
        root="$(cd "$(dirname "$script")/.." && pwd)"
        if [ -f "${root}/pyproject.toml" ] && grep -q '^name = "gonklander"' "${root}/pyproject.toml"; then
            echo "$root"
            return
        fi
    fi

    case "$version" in
        v[0-9]*) echo "https://github.com/${repo}/archive/refs/tags/${version}.tar.gz" ;;
        *)       echo "https://github.com/${repo}/archive/refs/heads/${version}.tar.gz" ;;
    esac
}

install_gonk() {
    local source="$1" log
    echo "  Installing gonk from ${source}…"
    log="$(mktemp)"

    # --force replaces an existing install; --reinstall rebuilds it even if
    # the version number has not changed. Together: install, upgrade or repair.
    local spec="$source"
    case "$source" in
        http://* | https://*) spec="gonklander @ ${source}" ;;
    esac

    if ! uv tool install --force --reinstall --quiet "$spec" >"$log" 2>&1; then
        echo >&2
        tail -n 15 "$log" >&2
        rm -f "$log"
        fail "gonk could not be installed from ${source}." \
            "Check this machine's Internet connection." \
            "Check that the version exists: GONK_VERSION=${GONK_VERSION:-main}"
    fi
    rm -f "$log"

    local installed="${HOME}/.local/bin/gonk"
    if [ ! -x "$installed" ]; then
        installed="$(uv tool dir --bin 2>/dev/null)/gonk"
    fi
    [ -x "$installed" ] || fail "gonk was installed, but its command cannot be found."
    ok "gonk: $("$installed" --version)"
}

check_path() {
    local bin_dir="$1"
    case ":${ORIGINAL_PATH}:" in
        *":${bin_dir}:"*)
            ok "${bin_dir} is on your PATH"
            ;;
        *)
            warn "${bin_dir} is not on your PATH, so your shell will not find gonk yet."
            echo
            echo "    Add this line to your shell profile (~/.bashrc, ~/.zshrc):"
            echo
            echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
            echo
            echo "    Until then, run gonk as: ${bin_dir}/gonk"
            ;;
    esac
}

ORIGINAL_PATH="${PATH}"
main "$@"

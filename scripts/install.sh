#!/bin/sh
# gaia installer — forges specialist subagents on demand, and grows with you.
#
#   curl -fsSL https://raw.githubusercontent.com/Sho0pi/gaia/master/scripts/install.sh | sh
#
# Installs gaia into a self-contained venv at ~/.gaia/venv (uv-managed), links the
# `gaia` command into ~/.local/bin, sets up the browser runtime, and runs `gaia setup`.
# Re-running upgrades in place. POSIX sh; macOS + Linux (Windows: use WSL).
set -eu

REPO="https://github.com/Sho0pi/gaia"
GAIA_HOME="${GAIA_HOME:-$HOME/.gaia}"
VENV="$GAIA_HOME/venv"
BIN_DIR="$HOME/.local/bin"
PYTHON="3.11"
REF=""
DO_BROWSER=1
DO_SETUP=1
SHOW_BANNER=0

while [ $# -gt 0 ]; do
	case "$1" in
		--ref) REF="${2:-}"; shift 2 ;;
		--ref=*) REF="${1#*=}"; shift ;;
		--no-browser) DO_BROWSER=0; shift ;;
		--no-setup) DO_SETUP=0; shift ;;
		--non-interactive) DO_SETUP=0; shift ;;
		--banner) SHOW_BANNER=1; shift ;;
		-h|--help)
			printf 'usage: install.sh [--ref REF] [--no-browser] [--no-setup] [--non-interactive] [--banner]\n'
			exit 0 ;;
		*) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
	esac
done

# --- colors (truecolor, only on a real terminal) -------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
	GLOW=$(printf '\033[38;2;34;240;168m'); MOSS=$(printf '\033[38;2;123;199;159m')
	GOLD=$(printf '\033[38;2;212;176;101m'); DIM=$(printf '\033[38;2;90;138;114m')
	BOLD=$(printf '\033[1m'); RST=$(printf '\033[0m')
else
	GLOW=""; MOSS=""; GOLD=""; DIM=""; BOLD=""; RST=""
fi

ok()  { printf '      %s✓%s %s\n' "$GLOW" "$RST" "$*"; }
warn() { printf '      %s!%s %s\n' "$GOLD" "$RST" "$*" >&2; }
die() { printf '\n%s✗%s %s\n' "$GOLD" "$RST" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

logo() {
	[ -n "$GLOW" ] || return 0
	printf '%b\n' \
  ' \033[38;2;0;0;0m     \033[38;2;11;111;149m▁\033[38;2;22;144;164m▃\033[38;2;50;188;186m▃\033[38;2;79;200;181m▃\033[38;2;109;207;178m▂\033[38;2;74;122;103m▁      \033[0m' \
  ' \033[38;2;0;0;0m   \033[38;2;13;87;168m▗\033[38;2;13;115;189m▆\033[38;2;13;137;194;48;2;13;149;191m▌\033[38;2;15;160;187;48;2;22;170;183m▌\033[38;2;34;181;180;48;2;48;189;177m▌\033[38;2;56;194;171;48;2;77;202;175m▖\033[38;2;89;207;169;48;2;110;213;175m▖\033[38;2;132;207;173;48;2;135;221;176m▔\033[0m\033[38;2;140;207;162m▆\033[38;2;110;158;122m▖    \033[0m' \
  ' \033[38;2;0;0;0m  \033[38;2;25;58;178m▗\033[38;2;23;68;185;48;2;23;83;200m▏\033[38;2;14;112;193;48;2;17;97;193m▝\033[38;2;8;137;185;48;2;10;119;186m▝\033[38;2;5;140;176;48;2;8;154;179m▄\033[38;2;6;162;167;48;2;18;173;171m▄\033[38;2;19;179;160;48;2;40;189;164m▄\033[38;2;39;193;155;48;2;70;203;161m▄\033[38;2;66;204;153;48;2;101;214;162m▄\033[38;2;97;214;154;48;2;129;222;165m▄\033[38;2;112;185;132;48;2;133;223;162m▕\033[0m\033[38;2;92;156;110m▖   \033[0m' \
  ' \033[38;2;0;0;0m  \033[7m\033[38;2;34;47;190m▍\033[0m\033[38;2;34;51;199;48;2;28;64;196m▖\033[38;2;24;68;192;48;2;19;82;190m▖\033[38;2;9;111;182;48;2;13;94;184m▝\033[38;2;2;133;171;48;2;5;116;175m▝\033[38;2;0;130;166;48;2;0;147;163m▖\033[38;2;0;156;152;48;2;4;168;155m▄\033[38;2;4;172;145;48;2;18;184;150m▅\033[38;2;15;185;139;48;2;37;195;145m▅\033[38;2;33;196;135;48;2;61;205;143m▄\033[38;2;60;207;135;48;2;90;215;146m▄\033[0m\033[38;2;89;209;136m▌   \033[0m' \
  ' \033[38;2;0;0;0m  \033[38;2;30;26;147m▝\033[38;2;27;29;149;48;2;35;42;196m▁\033[38;2;23;67;189;48;2;27;55;192m▝\033[38;2;14;87;181;48;2;18;74;184m▝\033[38;2;6;106;171;48;2;10;93;175m▝\033[38;2;3;113;166;48;2;0;125;160m▌\033[38;2;0;136;153;48;2;0;145;148m▌\033[38;2;0;150;141;48;2;0;160;139m▖\033[38;2;0;168;129;48;2;3;176;134m▄\033[38;2;5;182;121;48;2;15;189;128m▄\033[38;2;10;129;76;48;2;30;191;119m▁\033[0m\033[38;2;30;113;68m▘   \033[0m' \
  ' \033[38;2;0;0;0m   \033[38;2;19;23;111m▝\033[7m\033[38;2;25;49;178m▃\033[0m\033[38;2;8;49;118;48;2;17;68;181m▁\033[38;2;11;81;174;48;2;7;91;169m▌\033[38;2;3;103;164;48;2;0;116;155m▌\033[38;2;0;128;147;48;2;0;137;141m▌\033[38;2;0;145;134;48;2;0;152;127m▌\033[38;2;0;90;64;48;2;0;161;119m▁\033[0m\033[7m\033[38;2;1;159;101m▃\033[0m \033[38;2;4;87;51m    \033[0m' \
  ' \033[38;2;0;0;0m     \033[0m \033[38;2;3;86;162m▔\033[7m\033[38;2;0;108;151m▆\033[38;2;0;128;133m▆\033[0m\033[38;2;0;138;115m▔\033[0m \033[38;2;0;16;11m      \033[0m'
}

banner() {
	printf '\n'
	logo
	printf '%s%s' "$GLOW" "$BOLD"
	cat <<'WORDMARK'
        ___   _   ___   _
       / __| /_\ |_ _| /_\
      | (_ |/ _ \ | | / _ \
       \___/_/ \_\___/_/ \_\
WORDMARK
	printf '%s' "$RST"
	printf '      %sForges specialist subagents on demand — and grows with you.%s\n\n' "$MOSS" "$RST"
}

STEP=0
TOTAL=5
step() {
	STEP=$((STEP + 1))
	bar=""; i=0
	while [ "$i" -lt "$TOTAL" ]; do
		if [ "$i" -lt "$STEP" ]; then bar="${bar}█"; else bar="${bar}░"; fi
		i=$((i + 1))
	done
	printf '%s[%d/%d]%s %s%s%s  %s%s%s\n' \
		"$DIM" "$STEP" "$TOTAL" "$RST" "$GLOW" "$bar" "$RST" "$BOLD" "$1" "$RST"
}

ensure_path() {
	case ":$PATH:" in *":$1:"*) return 0 ;; esac
	export PATH="$1:$PATH"
	rc=""
	case "$(basename "${SHELL:-/bin/sh}")" in
		zsh) rc="$HOME/.zshrc" ;;
		bash) rc="$HOME/.bashrc" ;;
		*) rc="$HOME/.profile" ;;
	esac
	if [ -n "$rc" ] && ! grep -qs "$1" "$rc" 2>/dev/null; then
		# shellcheck disable=SC2016  # the literal $PATH must land in the rc file, not expand here
		printf '\n# gaia\nexport PATH="%s:$PATH"\n' "$1" >>"$rc"
		warn "added $1 to PATH in $rc — restart your shell (or: export PATH=\"$1:\$PATH\")"
	fi
}

# --- run -----------------------------------------------------------------------------
banner
[ "$SHOW_BANNER" = 1 ] && exit 0  # preview the banner without installing

case "$(uname -s)" in
	Darwin | Linux) : ;;
	*) die "Unsupported OS. On Windows, install under WSL." ;;
esac

step "Checking uv + git"
if ! have uv; then
	curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || die "could not install uv"
	export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
have uv || die "uv is not on PATH after install"
have git || die "git is required (macOS: xcode-select --install; Linux: install git)"
ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

step "Installing gaia + all features (this pulls a fair bit — give it a minute)"
spec="gaia[all] @ git+$REPO"
[ -n "$REF" ] && spec="gaia[all] @ git+$REPO@$REF"
uv venv "$VENV" --python "$PYTHON" >/dev/null 2>&1 || die "could not create the venv at $VENV"
uv pip install --python "$VENV" "$spec" >/dev/null 2>&1 || die "could not install gaia"
ok "gaia → $VENV"

step "Linking the gaia command"
mkdir -p "$BIN_DIR"
cat >"$BIN_DIR/gaia" <<EOF
#!/bin/sh
unset PYTHONPATH PYTHONHOME 2>/dev/null || true
exec "$VENV/bin/gaia" "\$@"
EOF
chmod +x "$BIN_DIR/gaia"
ensure_path "$BIN_DIR"
ok "gaia → $BIN_DIR/gaia"

if [ "$DO_BROWSER" -eq 1 ]; then
	step "Setting up the browser (bun + Chromium)"
	if ! have bun; then
		curl -fsSL https://bun.sh/install | bash >/dev/null 2>&1 || warn "bun install failed (the playwright-mcp browser backend won't be available)"
	fi
	[ -d "$HOME/.bun/bin" ] && ensure_path "$HOME/.bun/bin"
	"$VENV/bin/playwright" install chromium >/dev/null 2>&1 || warn "Chromium install failed (the native browser fallback won't be available)"
	ok "browser ready"
else
	step "Skipping the browser (--no-browser)"
	ok "skipped"
fi

step "Done"
ok "installed $("$BIN_DIR/gaia" --version 2>/dev/null || printf 'gaia')"

if [ "$DO_SETUP" -eq 1 ] && [ -e /dev/tty ]; then
	printf '\n%sLet'\''s configure gaia.%s\n\n' "$GLOW" "$RST"
	"$BIN_DIR/gaia" setup </dev/tty || true
else
	printf '\n%s✓ gaia is installed.%s  Next:\n' "$GLOW" "$RST"
	printf '    %sgaia setup%s   configure a model + connectors\n' "$BOLD" "$RST"
	printf '    %sgaia start%s   run it in the background\n\n' "$BOLD" "$RST"
fi

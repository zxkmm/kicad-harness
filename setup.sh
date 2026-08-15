#!/usr/bin/env bash
# Set up kicad-harness in a venv that can still see KiCad's bundled `pcbnew`.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> checking system tools"
missing=()
for tool in kicad-cli rsvg-convert python3; do
    command -v "$tool" >/dev/null || missing+=("$tool")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "missing: ${missing[*]}"
    echo "  kicad-cli    ships with KiCad"
    echo "  rsvg-convert Debian/Ubuntu: librsvg2-bin   Arch: librsvg   Fedora: librsvg2-tools"
    exit 1
fi
echo "    kicad-cli $(kicad-cli version)"

# --system-site-packages is required: pcbnew is installed by KiCad into the
# system interpreter and cannot be pip-installed.
echo "==> creating .venv"
python3 -m venv --system-site-packages .venv

echo "==> installing"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e .

echo "==> verifying"
./.venv/bin/python - <<'PY'
import sys
ok = True
try:
    from kicad_harness.geom import pcbnew
    print("    pcbnew  ", pcbnew.GetBuildVersion())
except Exception as e:
    print("    pcbnew   FAILED:", e); ok = False
try:
    import kipy
    print("    kipy     ok")
except Exception as e:
    print("    kipy     FAILED:", e); ok = False
sys.exit(0 if ok else 1)
PY

cat <<'EOF'

Done. Use it with:

    ./.venv/bin/kh info --pcb /path/to/project
    ./.venv/bin/kh view --pcb /path/to/project --refs L1,C1 --out /tmp/v.png

For the live layer (editing a running KiCad), enable the API server:
    KiCad -> Preferences -> Plugins -> tick "Enable KiCad API"
then check it with:
    ./.venv/bin/kh live
EOF

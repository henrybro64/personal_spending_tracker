#!/usr/bin/env bash
# Decrypt password-protected PDFs with qpdf for batch import.
#
# Install qpdf:
#   macOS:  brew install qpdf
#   Ubuntu: sudo apt-get install -y qpdf
#
# Examples:
#   PDF_PASSWORD='your-bank-password' ./scripts/decrypt-pdfs.sh ./Credit_Card_PDF ./Credit_Card_PDF_decrypted
#   ./scripts/decrypt-pdfs.sh -f ~/.pdf-password -r ./Credit_Card_PDF ./Credit_Card_PDF_decrypted
#
# Then copy the decrypted folder into the cluster:
#   kubectl cp ./Credit_Card_PDF_decrypted/. "$POD:/app/batch/pdf/" -c backend

set -euo pipefail

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
  echo "error: $*" >&2
  exit 1
}

command -v qpdf >/dev/null 2>&1 || die "qpdf not found. Install with: brew install qpdf  (or apt install qpdf)"

SOURCE_DIR=""
OUTPUT_DIR=""
PASSWORD="${PDF_PASSWORD:-}"
PASSWORD_FILE=""
RECURSIVE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--password)
      [[ $# -ge 2 ]] || die "missing value for $1"
      PASSWORD="$2"
      shift 2
      ;;
    -f|--password-file)
      [[ $# -ge 2 ]] || die "missing value for $1"
      PASSWORD_FILE="$2"
      shift 2
      ;;
    -r|--recursive)
      RECURSIVE=1
      shift
      ;;
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      die "unknown option: $1 (try --help)"
      ;;
    *)
      if [[ -z "$SOURCE_DIR" ]]; then
        SOURCE_DIR="$1"
      elif [[ -z "$OUTPUT_DIR" ]]; then
        OUTPUT_DIR="$1"
      else
        die "unexpected argument: $1"
      fi
      shift
      ;;
  esac
done

[[ -n "$SOURCE_DIR" && -n "$OUTPUT_DIR" ]] || {
  usage
  exit 1
}

[[ -d "$SOURCE_DIR" ]] || die "source directory not found: $SOURCE_DIR"

if [[ -n "$PASSWORD_FILE" ]]; then
  [[ -f "$PASSWORD_FILE" ]] || die "password file not found: $PASSWORD_FILE"
  PASSWORD="$(head -n 1 "$PASSWORD_FILE" | tr -d '\r\n')"
fi

mkdir -p "$OUTPUT_DIR"

# Avoid overwriting the source tree by accident.
SOURCE_ABS="$(cd "$SOURCE_DIR" && pwd)"
OUTPUT_ABS="$(cd "$OUTPUT_DIR" && pwd)"
[[ "$SOURCE_ABS" != "$OUTPUT_ABS" ]] || die "source and output must be different directories"

ok_count=0
plain_count=0
fail_count=0
failed_files=()

pdf_is_encrypted() {
  local info
  info="$(qpdf --show-encryption "$1" 2>/dev/null)" || return 0
  ! grep -qi "not encrypted" <<<"$info"
}

decrypt_one() {
  local src="$1"
  local rel="$2"
  local dst="$OUTPUT_ABS/$rel"

  mkdir -p "$(dirname "$dst")"

  if [[ "$DRY_RUN" == 1 ]]; then
    echo "[dry-run] $src -> $dst"
    return 0
  fi

  # Unencrypted PDFs: copy via --decrypt without a password.
  if ! pdf_is_encrypted "$src"; then
    if qpdf --decrypt "$src" "$dst" >/dev/null 2>&1; then
      echo "copied (already plain): $rel"
      plain_count=$((plain_count + 1))
      return 0
    fi
    echo "FAILED: $rel" >&2
    fail_count=$((fail_count + 1))
    failed_files+=("$rel")
    rm -f "$dst"
    return 1
  fi

  if [[ -z "$PASSWORD" ]]; then
    echo "FAILED (password required): $rel" >&2
    fail_count=$((fail_count + 1))
    failed_files+=("$rel")
    return 1
  fi

  if qpdf --password="$PASSWORD" --decrypt "$src" "$dst" >/dev/null 2>&1; then
    echo "decrypted: $rel"
    ok_count=$((ok_count + 1))
    return 0
  fi

  echo "FAILED (wrong password?): $rel" >&2
  fail_count=$((fail_count + 1))
  failed_files+=("$rel")
  rm -f "$dst"
  return 1
}

process_pdf() {
  local src="$1"
  local rel="$2"
  decrypt_one "$src" "$rel" || true
}

if [[ "$RECURSIVE" == 1 ]]; then
  while IFS= read -r -d '' src; do
    rel="${src#"$SOURCE_ABS"/}"
    process_pdf "$src" "$rel"
  done < <(find "$SOURCE_ABS" -type f -iname '*.pdf' -print0)
else
  shopt -s nullglob
  for src in "$SOURCE_ABS"/*.pdf "$SOURCE_ABS"/*.PDF; do
    [[ -f "$src" ]] || continue
    rel="$(basename "$src")"
    process_pdf "$src" "$rel"
  done
  shopt -u nullglob
fi

echo ""
echo "Done."
echo "  decrypted:  $ok_count"
echo "  plain copy: $plain_count"
echo "  failed:     $fail_count"
echo "  output dir: $OUTPUT_ABS"

if [[ "$fail_count" -gt 0 ]]; then
  echo ""
  echo "Failed files:"
  printf '  %s\n' "${failed_files[@]}"
  echo ""
  echo "Tips:"
  echo "  - Wrong password: set PDF_PASSWORD or use -p / -f"
  echo "  - Different passwords per bank: run the script once per folder/password"
  echo "  - Unsupported encryption: open in Preview and Print -> Save as PDF"
  exit 1
fi

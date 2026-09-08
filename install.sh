#!/bin/bash
# maestro-evidence のセットアップ。配置を確認し、Claude Code 側の symlink を作り、
# 環境診断を実行する。既存のファイルやディレクトリは上書きしない。
set -u

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_TARGET="${HOME}/.codex/skills/maestro-evidence"
CLAUDE_TARGET="${HOME}/.claude/skills/maestro-evidence"

fail() { printf '%s\n' "$1" >&2; exit 1; }

[ -f "${SOURCE_DIR}/SKILL.md" ] || fail "SKILL.md が見つかりません: ${SOURCE_DIR}"
[ -f "${SOURCE_DIR}/scripts/me.py" ] || fail "scripts/me.py が見つかりません: ${SOURCE_DIR}"

printf 'maestro-evidence のセットアップ\n\n'
printf '配置元: %s\n' "${SOURCE_DIR}"

# --- Codex CLI 側
PLACED=0
if [ "${SOURCE_DIR}" = "${CODEX_TARGET}" ]; then
  printf 'Codex : %s（配置済み）\n' "${CODEX_TARGET}"
  PLACED=1
elif [ -e "${CODEX_TARGET}" ]; then
  printf 'Codex : %s に別のものがあります。上書きしません。\n' "${CODEX_TARGET}"
  printf '        中身を確認し、不要なら手動で入れ替えてください。\n'
else
  printf 'Codex : %s がありません。\n' "${CODEX_TARGET}"
fi

# --- Claude Code 側
# 正本の場所から実行されていない場合は symlink を作らない。展開先（Downloads 等）を
# 指す symlink ができると、正本をコピーした後も古い場所を参照し続けるため
if [ "${PLACED}" -eq 0 ]; then
  printf 'Claude: symlink はまだ作りません。先に正本を置いてください。\n'
  printf '\n次を実行してから、置いた先の install.sh をもう一度実行してください。\n'
  printf '  mkdir -p "%s"\n' "${HOME}/.codex/skills"
  printf '  cp -R "%s" "%s"\n' "${SOURCE_DIR}" "${CODEX_TARGET}"
  printf '  "%s/install.sh"\n' "${CODEX_TARGET}"
  printf '\nClaude Code だけで使う場合は、このディレクトリを %s へ置けば symlink は要りません。\n' \
    "${CLAUDE_TARGET}"
elif [ -L "${CLAUDE_TARGET}" ]; then
  CURRENT="$(readlink "${CLAUDE_TARGET}")"
  if [ "${CURRENT}" = "${SOURCE_DIR}" ]; then
    printf 'Claude: %s -> %s（symlink 済み）\n' "${CLAUDE_TARGET}" "${CURRENT}"
  else
    printf 'Claude: %s が別の場所を指しています: %s\n' "${CLAUDE_TARGET}" "${CURRENT}"
    printf '        古い配置が残っています。消してから再実行してください。\n'
  fi
elif [ -e "${CLAUDE_TARGET}" ]; then
  printf 'Claude: %s に実体があります。上書きしません。\n' "${CLAUDE_TARGET}"
  printf '        Claude Code 側で古い版を使い続けることになるので、中身を確認してください。\n'
else
  mkdir -p "${HOME}/.claude/skills" || fail "~/.claude/skills を作れません"
  if ln -s "${SOURCE_DIR}" "${CLAUDE_TARGET}"; then
    printf 'Claude: %s -> %s を作成しました。\n' "${CLAUDE_TARGET}" "${SOURCE_DIR}"
  else
    printf 'Claude: symlink を作れませんでした。\n' >&2
  fi
fi

printf '\n'
PYTHON_BIN="$(command -v python3 || true)"
[ -n "${PYTHON_BIN}" ] || fail "python3 が見つかりません"
"${PYTHON_BIN}" "${SOURCE_DIR}/scripts/me.py" doctor > /dev/null
STATUS=$?
printf '\n'
if [ "${STATUS}" -eq 0 ]; then
  printf '次は README.md の「初回セットアップ」へ進んでください。\n'
else
  printf '足りないものがあります。上の「要対応」を解消してから再実行してください。\n'
fi
exit "${STATUS}"

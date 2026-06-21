// 24-bit ANSI color helpers. Mirrors src2 _fg_rgb / _supports_color.
import process from 'node:process';

function supportsColor() {
  if (process.env.NO_COLOR) return false;
  if ((process.env.CLICOLOR_FORCE ?? '') !== '' && process.env.CLICOLOR_FORCE !== '0') return true;
  if ((process.env.FORCE_COLOR ?? '') !== '' && process.env.FORCE_COLOR !== '0') return true;
  return Boolean(process.stdout.isTTY) && process.env.TERM !== 'dumb';
}

const ENABLED = supportsColor();

/** Wrap text in a 24-bit foreground color (no-op when color disabled). */
export function rgb(text, r, g, b) {
  if (!ENABLED) return text;
  return `\u001b[38;2;${r};${g};${b}m${text}\u001b[0m`;
}

// Named palette matching the Python tool.
export const green = (t) => rgb(t, 66, 184, 131);
export const indigo = (t) => rgb(t, 193, 145, 255);
export const yellow = (t) => rgb(t, 250, 208, 44);
export const muted = (t) => rgb(t, 140, 153, 173);
export const white = (t) => rgb(t, 218, 224, 232);
export const blueText = (t) => rgb(t, 214, 224, 255);
export const dim = (t) => rgb(t, 90, 98, 110);
export const label = (t) => rgb(t, 180, 190, 203);

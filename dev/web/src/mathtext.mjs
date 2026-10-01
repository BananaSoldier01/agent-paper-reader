// Split reader text into literal prose and TeX math.
// \$ is a literal dollar (kept, not a delimiter). $$...$$ is display math.
// A currency amount ($30, $0.015, $30,000) is prose: the $ is followed by a
// number and then whitespace or punctuation, not by a math closer.
// Inline $...$ still requires a non-space after the opener, a non-space before
// the closer, and a closer that is not followed by a digit.

const CURRENCY_AMOUNT = /^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?/;

function isSpace(ch) {
  return ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r' || ch === '\u00a0';
}

function currencyEnd(src, i) {
  if (src[i] !== '$') return -1;
  const match = CURRENCY_AMOUNT.exec(src.slice(i + 1));
  if (!match) return -1;
  const after = i + 1 + match[0].length;
  const next = src[after];
  if (next === undefined || isSpace(next)) return after;
  if ('.;:!?%)]}\'\"'.includes(next) || next === '\u2019' || next === '\u201d') return after;
  if (next === ',' && (src[after + 1] === undefined || isSpace(src[after + 1]))) return after;
  return -1;
}

function findDisplayClose(src, bodyStart) {
  for (let j = bodyStart; j < src.length - 1; j++) {
    if (src[j] === '\\' && src[j + 1] === '$') {
      j += 1;
      continue;
    }
    if (src[j] === '$' && src[j + 1] === '$') {
      return j === bodyStart ? -1 : j;
    }
  }
  return -1;
}

function findInlineClose(src, bodyStart) {
  if (bodyStart >= src.length) return -1;
  const first = src[bodyStart];
  if (isSpace(first) || first === '$') return -1;
  for (let j = bodyStart; j < src.length; j++) {
    const ch = src[j];
    if (ch === '\n' || ch === '\r') return -1;
    if (ch === '\\' && src[j + 1] === '$') {
      j += 1;
      continue;
    }
    if (ch !== '$') continue;
    const prev = src[j - 1];
    if (isSpace(prev) || prev === '$' || prev === '\\') continue;
    const next = src[j + 1];
    if (next >= '0' && next <= '9') continue;
    return j;
  }
  return -1;
}

function looksLikeCurrencyProse(body) {
  return /^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s/.test(body);
}

export function splitMath(src) {
  const out = [];
  let text = '';
  const flush = () => {
    if (text) {
      out.push({type: 'text', value: text});
      text = '';
    }
  };
  const n = src.length;
  let i = 0;
  while (i < n) {
    if (src[i] === '\\' && src[i + 1] === '$') {
      text += '\\$';
      i += 2;
      continue;
    }
    if (src[i] === '$' && src[i + 1] === '$') {
      const close = findDisplayClose(src, i + 2);
      const body = close >= 0 ? src.slice(i + 2, close) : '';
      if (close >= 0 && body.trim()) {
        flush();
        out.push({type: 'display', value: body});
        i = close + 2;
        continue;
      }
    }
    if (src[i] === '$') {
      const money = currencyEnd(src, i);
      if (money > i) {
        text += src.slice(i, money);
        i = money;
        continue;
      }
      const close = findInlineClose(src, i + 1);
      const body = close >= 0 ? src.slice(i + 1, close) : '';
      if (close >= 0 && body.trim() && !looksLikeCurrencyProse(body)) {
        flush();
        out.push({type: 'inline', value: body});
        i = close + 1;
        continue;
      }
    }
    text += src[i];
    i += 1;
  }
  flush();
  return out;
}

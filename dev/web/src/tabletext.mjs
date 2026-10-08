// Preserve Unicode offsets for alignment and notes; never fill or shift cells.
export function parseTable(text) {
  const chars = Array.from(text);
  const rows = [];
  let lineStart = 0;
  let delimiter;
  for (let end = 0; end <= chars.length; end++) {
    if (end < chars.length && chars[end] !== '\n') continue;
    let start = lineStart, stop = end;
    lineStart = end + 1;
    while (start < stop && /[ \r]/.test(chars[start])) start++;
    while (stop > start && /[ \r]/.test(chars[stop - 1])) stop--;
    if (start === stop) continue;
    const pipes = [];
    for (let i = start; i < stop; i++) {
      if (chars[i] !== '|') continue;
      let backslashes = 0;
      for (let j = i - 1; j >= start && chars[j] === '\\'; j--) backslashes++;
      if (backslashes % 2 === 0) pipes.push(i);
    }
    const separator = pipes.length ? '|' : '\t';
    if (delimiter && delimiter !== separator) return null;
    delimiter = separator;
    const cuts = separator === '|' ? pipes : Array.from({length: stop - start}, (_, i) => start + i).filter(i => chars[i] === '\t');
    if (!cuts.length) return null;
    const bounds = [start - 1, ...cuts, stop];
    let cells = bounds.slice(0, -1).map((left, i) => {
      let a = left + 1, b = bounds[i + 1];
      while (a < b && /\s/.test(chars[a])) a++;
      while (b > a && /\s/.test(chars[b - 1])) b--;
      return {text: chars.slice(a, b).join(''), start: a, end: b};
    });
    const bordered = separator === '|' && cuts[0] === start;
    if (bordered) cells = cells.slice(1);
    if (bordered && cuts.at(-1) === stop - 1) cells = cells.slice(0, -1);
    if (cells.length < 2 || (rows.length && cells.length !== rows[0].length)) return null;
    rows.push(cells);
  }
  if (rows.length < 2) return null;
  // Optional trailing Markdown border is unambiguous only with a separator row.
  if (rows[1].at(-1).text === '' && rows[1].slice(0, -1).every(cell => /^:?-{3,}:?$/.test(cell.text)) &&
      rows.every(row => row.at(-1).text === '')) rows.forEach(row => row.pop());
  const header = rows[1].every(cell => /^:?-{3,}:?$/.test(cell.text));
  if (header) rows.splice(1, 1);
  return {rows, header};
}

const normalize = text => text.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

export function termMatches(text, name) {
  const haystack = ` ${normalize(text)} `;
  // A spaced slash separates aliases; lexical slashes (input/output) stay one term.
  return [name, ...name.split(/\s+\/\s*|\s*\/\s+/)].some(alias => {
    const needle = normalize(alias);
    return needle && haystack.includes(` ${needle} `);
  });
}

export function tableLabelPairs(block, notes) {
  const pairs = [...(block.translation?.pairs || [])].sort((a, b) =>
    Math.min(...a.source.map(s => s[0])) - Math.min(...b.source.map(s => s[0])));
  if (block.user_edited) return pairs;
  const texts = {source: Array.from(block.text), target: Array.from(block.translation?.text || '')};
  const attached = notes.filter(n => n.block_id === block.id && n.status !== 'orphaned' &&
    texts[n.side]?.slice(n.start, n.end).join('') === n.quote);
  const seen = new Set();
  return pairs.filter(pair => {
    const key = JSON.stringify(['source', 'target'].map(side => pair[side].map(([start, end]) =>
      texts[side].slice(start, end).join('').trim().replace(/\s+/gu, ' '))));
    const annotated = attached.some(n => pair[n.side].some(([start, end]) => n.start < end && n.end > start));
    const duplicate = seen.has(key);
    seen.add(key);
    // Only hide identical label/meaning pairs. Keep differing meanings and annotated occurrences.
    return !duplicate || annotated;
  });
}

export type MathChunk = {type: 'text' | 'inline' | 'display'; value: string};
export function splitMath(src: string): MathChunk[];

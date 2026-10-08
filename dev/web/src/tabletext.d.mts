export type TableCell = {text:string;start:number;end:number};
export type TextTable = {rows:TableCell[][];header:boolean};
export function parseTable(text:string):TextTable|null;

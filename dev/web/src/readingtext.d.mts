type Pair = {id:string;source:number[][];target:number[][]};
type LabelBlock = {id:string;text:string;translation:{text:string;pairs:Pair[]}|null;user_edited:boolean};
type LabelNote = {block_id:string;side:string;start:number;end:number;quote:string;status?:string};
export function termMatches(text:string,name:string):boolean;
export function tableLabelPairs(block:LabelBlock,notes:LabelNote[]):Pair[];

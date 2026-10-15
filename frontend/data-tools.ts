export type Dataset={headers:string[];rows:string[][]};
export function parseCSV(text:string):Dataset{
 const rows:string[][]=[];let row:string[]=[],value='',quoted=false;
 text=text.replace(/^\uFEFF/,'');
 for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){value+='"';i++;}else if(!quoted&&value!=='')throw Error('Unexpected quote in CSV.');else quoted=!quoted;}else if(c===','&&!quoted){row.push(value);value='';}else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(value);if(row.some(x=>x.trim()))rows.push(row);row=[];value='';}else value+=c;}
 if(quoted)throw Error('The CSV has an unclosed quote.');row.push(value);if(row.some(x=>x.trim()))rows.push(row);
 const headers=rows.shift()?.map(h=>h.trim())||[];if(!headers.length||headers.some(h=>!h)||new Set(headers).size!==headers.length)throw Error('Use unique, nonempty column names in the first row.');
 if(headers.length>50||rows.length>10000)throw Error('Use up to 50 columns and 10,000 rows.');if(!rows.length||rows.some(r=>r.length!==headers.length))throw Error('Each data row must have the same number of columns as the header.');return {headers,rows};
}
export function number(s:string){return s.trim()!==''&&Number.isFinite(Number(s))?Number(s):null;}
export function profile(data:Dataset){return data.headers.map((name,i)=>{const present=data.rows.map(r=>r[i]).filter(s=>s.trim()!=='');const nums=present.map(number);const numeric=present.length>0&&nums.every(n=>n!==null);const values=numeric?nums as number[]:[];return {name,missing:data.rows.length-present.length,numeric,values,unique:new Set(present).size,mean:values.length?values.reduce((a,b)=>a+b,0)/values.length:0,min:values.length?Math.min(...values):0,max:values.length?Math.max(...values):0};});}
function fit(x:number[],y:number[]){const mx=x.reduce((a,b)=>a+b,0)/x.length,my=y.reduce((a,b)=>a+b,0)/y.length;const den=x.reduce((s,v)=>s+(v-mx)**2,0);const slope=den?x.reduce((s,v,i)=>s+(v-mx)*(y[i]-my),0)/den:0;return (v:number)=>my+slope*(v-mx);}
export type Result={mae:number;baseline:number;train:number;test:number;dropped:number;points:{label:string;actual:number;predicted:number}[];future?:{label:string;predicted:number}[];description:string};
export function runPrediction(data:Dataset,feature:string,target:string,forecast:boolean,horizon=5):Result{
 const xi=data.headers.indexOf(feature),yi=data.headers.indexOf(target);if(xi<0||yi<0||xi===yi)throw Error('Choose two different columns.');
 let samples=data.rows.map(r=>({x:forecast?(/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(r[xi])?Date.parse(r[xi]):NaN):number(r[xi]),y:number(r[yi])})).filter(p=>p.x!==null&&Number.isFinite(p.x)&&p.y!==null) as {x:number;y:number}[];
 if(samples.length<10)throw Error('At least 10 complete rows are needed for a train/test split.');
 if(forecast){samples.sort((a,b)=>a.x-b.x);if(new Set(samples.map(s=>s.x)).size!==samples.length)throw Error('Use one row per date. Aggregate duplicate dates before uploading.');const step=samples[1].x-samples[0].x;if(samples.some((s,i)=>i>0&&s.x-samples[i-1].x!==step))throw Error('Forecasting needs evenly spaced timestamps. Fill missing periods or resample the CSV first.');}else {let seed=42;for(let i=samples.length-1;i>0;i--){seed=(seed*1664525+1013904223)>>>0;const j=seed%(i+1);[samples[i],samples[j]]=[samples[j],samples[i]];}}
 const split=Math.floor(samples.length*.8),train=samples.slice(0,split),test=samples.slice(split);const predict=fit(train.map(s=>s.x),train.map(s=>s.y));const base=forecast?train[train.length-1].y:train.reduce((s,p)=>s+p.y,0)/train.length;
 const points=test.map((p,i)=>({label:forecast?new Date(p.x).toISOString().slice(0,10):String(i+1),actual:p.y,predicted:predict(p.x)}));const mae=points.reduce((s,p)=>s+Math.abs(p.actual-p.predicted),0)/test.length,baseline=test.reduce((s,p)=>s+Math.abs(p.y-base),0)/test.length;
 let future:Result['future'];if(forecast){const full=fit(samples.map(s=>s.x),samples.map(s=>s.y)),last=samples[samples.length-1].x,step=samples[1].x-samples[0].x;future=Array.from({length:horizon},(_,i)=>({label:new Date(last+step*(i+1)).toISOString().slice(0,10),predicted:full(last+step*(i+1))}));}
 if(!Number.isFinite(mae)||points.some(p=>!Number.isFinite(p.predicted)))throw Error('Values are too large to fit reliably. Rescale your data.');
 return {mae,baseline,train:train.length,test:test.length,dropped:data.rows.length-samples.length,points,future,description:forecast?'Linear trend forecast. Last 20% held out chronologically; compared with the last observed training value. Future estimates refit on all valid rows. No seasonality or uncertainty intervals.':'Single-feature linear regression. Reproducible random 80/20 split; compared with the training mean. Use forecasting for time-ordered data.'};
}

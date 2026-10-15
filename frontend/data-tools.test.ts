import assert from 'node:assert/strict';
import {test} from 'node:test';
import {parseCSV,profile,runPrediction} from './data-tools.ts';
const data=parseCSV('date,x,y\n'+Array.from({length:40},(_,i)=>`${new Date(Date.UTC(2026,0,i+1)).toISOString().slice(0,10)},${i},${3*i+7}`).join('\n'));
test('CSV quoting, missing data and invalid inputs',()=>{
 assert.deepEqual(parseCSV('a,b\r\n"x,y","line\nnext"').rows,[['x,y','line\nnext']]);
 for(const csv of ['a,a\n1,2','a,b\n1','a,b\n"unclosed,2'])assert.throws(()=>parseCSV(csv));
 assert.equal(profile(parseCSV('a,b\n,2\n3,4'))[0].missing,1);
 assert.equal(profile(data)[1].mean,19.5);
});
test('regression uses held-out samples and matches known linear relation',()=>{
 const r=runPrediction(data,'x','y',false);assert.ok(r.mae<1e-10);assert.equal(r.train,32);assert.equal(r.test,8);assert.ok(r.baseline>r.mae);
 assert.deepEqual(r,runPrediction(data,'x','y',false));
});
test('forecast holds out last dates and predicts future periods',()=>{
 const r=runPrediction(data,'date','y',true,5);assert.ok(r.mae<1e-10);assert.equal(r.future?.length,5);assert.equal(r.points[0].label,'2026-02-02');assert.ok(Math.abs(r.future![0].predicted-127)<1e-10);
});
test('forecast rejects irregular and duplicate dates and small samples',()=>{
 assert.throws(()=>runPrediction({...data,rows:data.rows.filter((_,i)=>i!==5)},'date','y',true),/evenly spaced/);
 assert.throws(()=>runPrediction({...data,rows:[...data.rows,data.rows[0]]},'date','y',true),/one row per date/);
 assert.throws(()=>runPrediction({...data,rows:data.rows.slice(0,4)},'x','y',false),/At least 10/);
});

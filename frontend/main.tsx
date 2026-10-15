import {useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import DatasetWorkspace from './dataset-workspace';
type Item={id:string;kind:string;title:string;content:string;updatedAt?:string};
function App(){const [records,setRecords]=useState<Item[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true);useEffect(()=>{fetch('/api/datasets').then(async r=>{if(!r.ok)throw Error('Could not load saved datasets. Reload to retry.');return r.json() as Promise<Item[]>;}).then(setRecords).catch(e=>setError(e.message)).finally(()=>setLoading(false));},[]);return error?<div role="alert" className="notice">{error}</div>:loading?<p>Loading your datasets…</p>:<DatasetWorkspace records={records} onChange={setRecords}/>;}
const root=document.getElementById('dataset-root');if(root)createRoot(root).render(<App/>);

const ts=require('typescript'),fs=require('fs'),path=require('path');
const dir=process.argv[2];
const files=fs.readdirSync(dir).filter(f=>/^page.*\.js$/.test(f)).sort();
const uiAttr=new Set(['placeholder','title','aria-label','alt']);
const uiCalls=['setError','setNotice','setMessage','setActionMessage','setMemberShareMessage','setStatus','setFeedback','alert','confirm','prompt'];
const uiProp=/^(?:title|description|help|label|message|text|placeholder|loading|failed|failure|success|error|empty|invalid|unsupported|warning|notice|body|subtitle|summary|caption|tooltip|ariaLabel|displayName|name)$/i;
function natural(s){
 s=String(s||'').trim(); if(!s||!/\b[A-Za-zÀ-ÿ]{2,}\b/.test(s))return false;
 if(s==='use client')return false;
 if(/^\.?[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_.-]+)+$/.test(s))return false;
 if(/^\.(pdf|docx|jpg|jpeg|png|xlsx|html|htm|pptx|txt|csv|json|md|mp3|mp4|mov|mkv|zip|ogg)$/i.test(s))return false;
 if(/^(application|image|audio|video)\//.test(s)||/^\/api\//.test(s)||/^\/auth\//.test(s)||/^https?:/.test(s)||/^redocx:/.test(s))return false;
 if(/^(?:border|bg|text-|cursor|rounded|hover:|app-|md:|lg:|flex|grid|w-|h-|px-|py-|mt-|mb-|gap-|min-|max-|object-|overflow|transition|shadow|items|justify|absolute|relative|inline-flex|block |space-y|truncate|whitespace|shrink|ring-)/.test(s))return false;
 return /\s|[.!?…:;’]/.test(s);
}
function line(sf,n){return sf.getLineAndCharacterOfPosition(n.getStart(sf)).line+1}
for(const file of files){
 const text=fs.readFileSync(path.join(dir,file),'utf8'); const sf=ts.createSourceFile(file,text,ts.ScriptTarget.Latest,true,ts.ScriptKind.JSX); const out=[];
 function add(n,k,s){if(natural(s))out.push([line(sf,n),k,String(s).replace(/\s+/g,' ').trim().slice(0,240)])}
 function visit(n){
   if(ts.isJsxText(n)) add(n,'JSX',n.getText(sf));
   if(ts.isStringLiteral(n)||ts.isNoSubstitutionTemplateLiteral(n)){
     const s=n.text, p=n.parent;
     if(ts.isJsxAttribute(p) && uiAttr.has(p.name.getText(sf))) add(n,'ATTR:'+p.name.getText(sf),s);
     if((ts.isCallExpression(p)||ts.isNewExpression(p))){
       const callee=p.expression?.getText(sf)||'';
       if(uiCalls.some(x=>callee.endsWith(x)) || callee==='Error' || callee==='ApiClientError') add(n,'CALL:'+callee,s);
     }
     if(ts.isBinaryExpression(p) && ['||','??'].includes(p.operatorToken.getText(sf)) && p.right===n) add(n,'FALLBACK',s);
     if(ts.isConditionalExpression(p) && (p.whenTrue===n||p.whenFalse===n)) add(n,'COND',s);
     if(ts.isPropertyAssignment(p)){
       const pn=p.name.getText(sf).replace(/^['"]|['"]$/g,''); if(uiProp.test(pn)) add(n,'PROP:'+pn,s);
     }
     if(ts.isReturnStatement(p)) add(n,'RETURN',s);
   }
   if(ts.isTemplateExpression(n)){
     const p=n.parent, raw=n.getText(sf);
     if((ts.isCallExpression(p)&&uiCalls.some(x=>(p.expression?.getText(sf)||'').endsWith(x))) || ts.isReturnStatement(p) || (ts.isBinaryExpression(p)&&['||','??'].includes(p.operatorToken.getText(sf)))) add(n,'TEMPLATE',raw);
   }
   ts.forEachChild(n,visit);
 }
 visit(sf);
 if(out.length){console.log('\n### '+file); for(const r of out)console.log(r.join('\t'));}
}

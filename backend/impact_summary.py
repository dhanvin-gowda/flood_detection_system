from __future__ import annotations
import json
from typing import Any, Dict, Optional
from backend import config

def _g(fc: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(fc, dict): return None
    props = fc.get('properties') or {}
    n = props.get('featureCount')
    if isinstance(n,int) and n>=0: return n
    feats = fc.get('features')
    if isinstance(feats,list): return len(feats)
    return None

def _r(fc: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(fc,dict): return None
    m=0.0; found=False
    for f in fc.get('features',[]):
        if not isinstance(f,dict): continue
        v=f.get('properties',{}).get('affectedLengthM')
        if isinstance(v,(int,float)) and v>0:
            m+=float(v); found=True
    if not found: return None
    return round(m/1000.0,3)

def build_summary_payload(results: Dict[str,Any], floodvit_layers: Optional[Dict[str,Dict[str,Any]]]=None)->Dict[str,Any]:
    pl={}
    a=results.get('aoi')or{}
    if a.get('date'): pl['event_date']=a['date']
    if isinstance(results.get('floodAreaKm2'),(int,float)): pl['flood_area_km2']=round(float(results['floodAreaKm2']),4)
    if isinstance(results.get('aoiAreaKm2'),(int,float)): pl['aoi_area_km2']=round(float(results['aoiAreaKm2']),4)
    if isinstance(results.get('floodPixels'),int): pl['flood_pixels']=results['floodPixels']
    if isinstance(results.get('validPixels'),int): pl['valid_pixels']=results['validPixels']
    if isinstance(results.get('coveragePct'),(int,float)): pl['coverage_pct']=results['coveragePct']
    if floodvit_layers:
        r=_r(floodvit_layers.get('affected_roads'))
        if r is not None: pl['affected_roads_km']=r
        pl['affected_bridges']=_g(floodvit_layers.get('affected_bridges'))
        pl['disconnected_routes']=_g(floodvit_layers.get('disconnected_routes'))
        pl['disconnected_settlements']=_g(floodvit_layers.get('disconnected_settlements'))
        pl['hospitals']=_g(floodvit_layers.get('hospitals'))
    return pl
def _generate_via_groq(payload):
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError(f'openai not available: {e}')
    api_key = config.settings.groq_api_key
    if not api_key:
        raise RuntimeError('GROQ_API_KEY not configured')
    client = OpenAI(api_key=api_key, base_url='https://api.groq.com/openai/v1')
    model = config.settings.groq_model or 'llama-3.1-8b-instant'
    system = '''You are an emergency flood-impact assessment assistant. You receive verified geospatial analysis results produced by a flood-mapping system. Your job is to transform those verified results into a concise operational impact summary and recommended response actions. STRICT RULES: Never invent statistics, locations, casualties, infrastructure damage, or events. Never infer a number that is not explicitly provided. Never claim that a building, road, bridge, hospital, or settlement is damaged unless the input explicitly indicates this. Clearly distinguish between detected flood exposure and confirmed physical damage. If the data only indicates potential impact, use terms such as 'potentially affected' or 'within the detected flood extent'. Recommendations must be based only on the supplied analysis. Do not provide unsupported medical, evacuation, or safety claims. Be concise and useful for emergency-response decision makers. Return ONLY valid JSON matching the requested schema.'''
    import json as _j
    user = _j.dumps({'verified_analysis': payload}, ensure_ascii=False)
    comp = client.chat.completions.create(
        model=model,
        messages=[{'role':'system','content':system},{'role':'user','content':user}],
        temperature=0.2,
        max_tokens=800,
    )
    content = comp.choices[0].message.content if comp.choices else ''
    if not content: raise RuntimeError('empty response from Groq')
    content = content.strip()
    if content.startswith('`'):
        content = content.strip('')
        if '\n' in content:
            first,rest = content.split('\n',1)
            content = rest if first.strip() else rest
    data = _j.loads(content)
    req = ['overview','infrastructure_impact','settlement_impact','critical_locations','recommended_actions','limitations']
    for k in req:
        if k not in data:
            data[k] = [] if k in ('critical_locations','recommended_actions','limitations') else ''
    return data

def generate_impact_summary(analysis_id: str, results: Dict[str,Any], floodvit_layers: Optional[Dict[str,Any]]=None):
    payload = build_summary_payload(results, floodvit_layers or {})
    try:
        summary = _generate_via_groq(payload)
        return {'analysis_id':analysis_id,'payload':payload,'impact_summary':summary,'status':'ok','error':None}
    except Exception as e:
        return {'analysis_id':analysis_id,'payload':payload,'impact_summary':{
            'overview':'AI summary unavailable. Showing verified flood analysis only.',
            'infrastructure_impact':'Infrastructure impact could not be summarized by AI.',
            'settlement_impact':'Settlement impact could not be summarized by AI.',
            'critical_locations':[],'recommended_actions':[],'limitations':[f'AI summary generation failed: {e}']
        },'status':'error','error':str(e)}

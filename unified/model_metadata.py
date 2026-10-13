"""Truthful catalog metadata; channel declarations take priority over model specs."""
from copy import deepcopy

OFFICIAL_SPECS = {
    'glm-5.3-flash': {'context_window':1048576,'max_output_tokens':131072,
        'reasoning':True,'reasoning_efforts':['low','high','max'],'default_reasoning_effort':'max',
        'input_modalities':['text','image'],'spec_source':'https://docs.z.ai/guides/vlm/glm-5.3-flash'},
    'deepseek-v4.1-flash': {'context_window':1048576,'max_output_tokens':393216,
        'reasoning':True,'reasoning_efforts':['low','high','max'],'default_reasoning_effort':'high',
        'input_modalities':['text','image'],'spec_source':'https://api-docs.deepseek.com/zh-cn/api/list-models/'},
}


def positive(value):
    return value if type(value) is int and value > 0 else None


def enrich_model(row):
    result=deepcopy(row)
    key=str(row.get('id','')).partition('/')[2].lower()
    official=OFFICIAL_SPECS.get(key)
    if official:
        result['official_limits']={field:official[field] for field in ('context_window','max_output_tokens')}
        result['spec_source']=official['spec_source']
        for field in ('context_window','max_output_tokens','reasoning','reasoning_efforts','default_reasoning_effort'):
            if result.get(field) is None:
                result[field]=deepcopy(official[field])
        result['limits_source']='catalog' if positive(row.get('context_window')) or positive(row.get('max_output_tokens')) else 'official-model'
        # A model's vision support alone does not prove its transport accepts images.
        result['official_input_modalities']=list(official['input_modalities'])
    contexts=row.get('context_config')
    if isinstance(contexts,dict):
        options=[value for value in contexts.values() if isinstance(value,dict) and positive(value.get('token_count'))]
        if options:
            default=next((value for value in options if value.get('is_default')),options[0])
            result['context_window']=default['token_count']
            result['context_options']=sorted({value['token_count'] for value in options})
            result['limits_source']=row.get('catalog_source','catalog')
    thinking=row.get('thinking_config')
    if isinstance(thinking,dict):
        enabled=thinking.get('enabled') or {}
        efforts=enabled.get('efforts',{}) if isinstance(enabled,dict) else {}
        if isinstance(efforts,dict) and efforts:
            result['reasoning']=True
            result['reasoning_efforts']=list(efforts)
            result['default_reasoning_effort']=next((name for name,value in efforts.items() if isinstance(value,dict) and value.get('is_default')),None)
        result['reasoning_can_disable']='disabled' in thinking
    for source,target in (('context_length','context_window'),('max_completion_tokens','max_output_tokens')):
        if positive(row.get(source)) and not positive(row.get(target)):
            result[target]=row[source]
    if type(row.get('is_vl')) is bool:
        result['vision']=row['is_vl']
    if type(result.get('vision')) is bool:
        result['input_modalities']=['text','image'] if result['vision'] else ['text']
    if type(row.get('enable')) is bool:
        result['available']=row['enable']
    elif 'available' not in result:
        result['available']=True
    promotion=row.get('promotion')
    if isinstance(promotion,dict):
        result['promotion']=deepcopy(promotion)
    if positive(result.get('context_window')):
        result['context_length']=result['context_window']
    if positive(result.get('max_output_tokens')):
        result['max_completion_tokens']=result['max_output_tokens']
    return result

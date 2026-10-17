"""OpenAI chat ↔ Cloud Code Assist Gemini envelopes; agent2api reference noted in notices."""
import hashlib
import json
import re
import secrets
import time
from fastapi import HTTPException


def schema(value, depth=0):
    if not isinstance(value, dict) or depth > 24: return {'type':'object'}
    allowed = {'type','format','description','enum','nullable','minimum','maximum','minItems','maxItems'}
    result = {k:v for k,v in value.items() if k in allowed}
    if isinstance(result.get('type'), list):
        types = result['type']; result['type'] = next((t for t in types if t != 'null'), 'string')
        if 'null' in types: result['nullable'] = True
    if isinstance(value.get('properties'), dict):
        result['properties'] = {k:schema(v,depth+1) for k,v in value['properties'].items()}
        result['required'] = [k for k in value.get('required',[]) if k in result['properties']]
    if 'items' in value: result['items'] = schema(value['items'],depth+1)
    if isinstance(value.get('anyOf'),list): result['anyOf'] = [schema(v,depth+1) for v in value['anyOf']]
    return result or {'type':'object'}


def parts(content):
    if isinstance(content,str): return [{'text':content}] if content else []
    output = []
    for part in content or []:
        if not isinstance(part, dict): raise HTTPException(400, '消息内容格式无效')
        if part.get('type') in ('text','input_text','output_text'):
            output.append({'text':str(part.get('text',''))})
        elif part.get('type') == 'image_url':
            image = part.get('image_url', {})
            url = image.get('url') if isinstance(image,dict) else image
            match = re.fullmatch(r'data:(image/[a-zA-Z0-9.+-]+);base64,([a-zA-Z0-9+/=\s]+)',url or '')
            if not match: raise HTTPException(400, 'Antigravity 图片请使用 base64 data URL')
            output.append({'inlineData':{'mimeType':match[1], 'data':match[2]}})
        else: raise HTTPException(400, 'Antigravity 不支持此消息内容类型')
    return output


def request_envelope(chat, project, enterprise=False):
    messages = chat.get('messages')
    if not isinstance(messages,list) or not messages: raise HTTPException(400, '请提供有效消息')
    system, contents, functions = [], [], {}
    def turn(role, values):
        if not values: return
        if contents and contents[-1]['role'] == role: contents[-1]['parts'].extend(values)
        else: contents.append({'role':role, 'parts':values})
    for message in messages:
        if not isinstance(message,dict): raise HTTPException(400, '消息格式无效')
        role = message.get('role')
        if role in ('system','developer'):
            system.extend(p['text'] for p in parts(message.get('content')) if 'text' in p)
        elif role == 'tool':
            name = functions.get(message.get('tool_call_id')) or message.get('name')
            if not name: raise HTTPException(400, '工具结果缺少对应的工具调用')
            value = message.get('content','')
            if isinstance(value,str):
                try: value = json.loads(value)
                except ValueError: pass
            turn('user',[{'functionResponse':{'name':name, 'response':value if isinstance(value,dict) else {'result':value}}}])
        elif role in ('assistant','user'):
            values = parts(message.get('content'))
            for call in message.get('tool_calls') or []:
                fn = call.get('function') or {}; name = fn.get('name'); args = fn.get('arguments',{})
                if not isinstance(name,str) or not name: raise HTTPException(400, '工具调用缺少名称')
                if isinstance(args,str):
                    try: args = json.loads(args)
                    except ValueError: raise HTTPException(400, '工具参数必须是有效 JSON') from None
                if not isinstance(args,dict): raise HTTPException(400, '工具参数必须是 JSON 对象')
                item = {'functionCall':{'name':name,'args':args}}
                signature = ((call.get('extra_content') or {}).get('google') or {}).get('thought_signature')
                if isinstance(signature,str) and signature: item['thoughtSignature'] = signature
                functions[call.get('id')] = name; values.append(item)
            turn('model' if role=='assistant' else 'user', values)
        else: raise HTTPException(400, 'Antigravity 消息角色无效')
    if not contents: raise HTTPException(400, '请提供用户消息')
    if contents[0]['role']!='user': contents.insert(0,{'role':'user','parts':[{'text':'Begin.'}]})
    if contents[-1]['role']!='user': contents.append({'role':'user','parts':[{'text':'Continue.'}]})
    config = {}
    for source,target in [('temperature','temperature'),('top_p','topP'),('top_k','topK')]:
        value=chat.get(source)
        if type(value) in (int,float): config[target]=value
    limit=chat.get('max_completion_tokens',chat.get('max_tokens'))
    if limit is not None:
        if type(limit) is not int or limit<=0: raise HTTPException(400, '输出 Token 上限必须是正整数')
        config['maxOutputTokens']=limit
    effort=chat.get('reasoning_effort') or (chat.get('reasoning') or {}).get('effort')
    if effort:
        budget={'minimal':1000,'low':1000,'medium':4000,'high':10000,'xhigh':10000,'max':10000}.get(effort)
        if effort in ('none','off','disabled'): config['thinkingConfig']={'includeThoughts':False,'thinkingBudget':0}
        elif budget:
            budget=min(budget,max(0,(limit or 65536)-1))
            config['thinkingConfig']={'includeThoughts':True,'thinkingBudget':budget}
        else: raise HTTPException(400, 'Antigravity 不支持此推理强度')
    if chat.get('stop'): config['stopSequences']=[chat['stop']] if isinstance(chat['stop'],str) else chat['stop']
    response_format=chat.get('response_format') or {}
    if response_format.get('type') in ('json_object','json_schema'):
        config['responseMimeType']='application/json'
        if response_format.get('type')=='json_schema': config['responseSchema']=schema((response_format.get('json_schema') or {}).get('schema',{}))
    request={'contents':contents, 'generationConfig':config}
    if system: request['systemInstruction']={'role':'user','parts':[{'text':'\n\n'.join(system)}]}
    declarations=[]
    for tool in chat.get('tools') or []:
        fn=tool.get('function',tool)
        if not isinstance(fn.get('name'),str) or not fn['name']: raise HTTPException(400, '工具缺少名称')
        declarations.append({'name':fn['name'],'description':str(fn.get('description','')),'parameters':schema(fn.get('parameters',{}))})
    choice=chat.get('tool_choice','auto')
    if declarations and choice!='none':
        request['tools']=[{'functionDeclarations':declarations}]
        mode={'mode':'AUTO'}
        if choice=='required': mode={'mode':'ANY'}
        if isinstance(choice,dict): mode={'mode':'ANY','allowedFunctionNames':[(choice.get('function') or {}).get('name')]}
        request['toolConfig']={'functionCallingConfig':mode}
    mid=chat['model']; first=json.dumps(contents[0],ensure_ascii=False,sort_keys=True)
    session=hashlib.sha256((project+'|'+mid+'|'+ '\n'.join(system)+'|'+first).encode()).digest()[:8]
    request['sessionId']=str(int.from_bytes(session,'big',signed=True))
    result={'model':mid,'userAgent':'jetski' if enterprise else 'antigravity','requestId':f'agent/{int(time.time()*1000)}/{secrets.token_hex(4)}','request':request}
    if project: result['project']=project
    return result


async def gemini_frames(lines):
    """Parse SSE without synthesizing success when an upstream stream is truncated."""
    started, finished, tool_count, pending, usage, signature = False, False, 0, [], None, None
    finish='stop'
    seen=set(); total_bytes=0
    async def events():
        nonlocal pending,total_bytes
        async for line in lines:
            total_bytes+=len(line)
            if total_bytes>32*1024*1024: raise HTTPException(502, 'Antigravity 响应超出大小限制')
            if line.startswith('data:'): pending.append(line[5:].lstrip())
            elif not line and pending:
                value='\n'.join(pending); pending=[]; yield value
        if pending: yield '\n'.join(pending)
    async for raw in events():
        if raw=='[DONE]': finished=True; break
        try: event=json.loads(raw)
        except ValueError: raise HTTPException(502, 'Antigravity 返回了无效的流事件') from None
        if not isinstance(event,dict): raise HTTPException(502, 'Antigravity 流事件格式无效')
        if event.get('error'): raise HTTPException(502, 'Antigravity 上游流返回错误')
        inner=event.get('response',event)
        if not isinstance(inner,dict): raise HTTPException(502, 'Antigravity 响应信封格式无效')
        if inner.get('error'): raise HTTPException(502, 'Antigravity 上游流返回错误')
        if (inner.get('promptFeedback') or {}).get('blockReason'): raise HTTPException(400, 'Antigravity 上游拦截了此请求')
        if isinstance(inner.get('usageMetadata'),dict):
            value=inner['usageMetadata']
            def num(k): return max(0,value[k]) if type(value.get(k)) is int else 0
            prompt,thought,total=num('promptTokenCount'),num('thoughtsTokenCount'),num('totalTokenCount')
            output=num('candidatesTokenCount') or max(0,total-prompt-thought)
            usage={'prompt_tokens':prompt,'completion_tokens':output+thought,'total_tokens':total or prompt+output+thought,
                   'prompt_tokens_details':{'cached_tokens':num('cachedContentTokenCount')},'completion_tokens_details':{'reasoning_tokens':thought}}
        candidates=inner.get('candidates') or []
        if not isinstance(candidates,list): raise HTTPException(502, 'Antigravity candidates 格式无效')
        if not candidates: continue
        candidate=candidates[0]
        if not isinstance(candidate,dict): raise HTTPException(502, 'Antigravity candidate 格式无效')
        content=candidate.get('content') or {}
        if not isinstance(content,dict) or not isinstance(content.get('parts',[]),list): raise HTTPException(502,'Antigravity content 格式无效')
        for part in content.get('parts') or []:
            if not isinstance(part,dict): raise HTTPException(502,'Antigravity part 格式无效')
            if part.get('thoughtSignature'): signature=part['thoughtSignature']
            delta={}
            if 'text' in part: delta['reasoning_content' if part.get('thought') else 'content']=part['text']
            call=part.get('functionCall')
            if call:
                if not isinstance(call,dict) or not isinstance(call.get('name'),str) or not isinstance(call.get('args',{}),dict):
                    raise HTTPException(502,'Antigravity 工具调用格式无效')
                cid=call.get('id') or 'call_'+secrets.token_hex(12)
                key=(cid,json.dumps(call.get('args',{}),sort_keys=True))
                if key in seen: continue
                seen.add(key)
                fn={'index':tool_count,'id':cid,'type':'function','function':{'name':call.get('name',''),'arguments':json.dumps(call.get('args',{}),ensure_ascii=False)}}
                if signature: fn['extra_content']={'google':{'thought_signature':signature}}; signature=None
                delta['tool_calls']=[fn]; tool_count+=1
            image=part.get('inlineData')
            if image and isinstance(image.get('data'),str):
                delta['content']=f"![Generated image](data:{image.get('mimeType','image/png')};base64,{image['data']})"
            if delta:
                if not started:
                    started=True; yield {'choices':[{'index':0,'delta':{'role':'assistant'},'finish_reason':None}]}
                yield {'choices':[{'index':0,'delta':delta,'finish_reason':None}]}
        if candidate.get('finishReason'):
            reason=candidate['finishReason']
            if reason in ('SAFETY','RECITATION','PROHIBITED_CONTENT','BLOCKLIST'): raise HTTPException(400, 'Antigravity 上游拦截了模型输出')
            if not started:
                started=True; yield {'choices':[{'index':0,'delta':{'role':'assistant'},'finish_reason':None}]}
            finished=True
            finish='length' if reason=='MAX_TOKENS' else 'tool_calls' if tool_count else 'stop'
    if not finished: raise HTTPException(502, 'Antigravity 流提前结束，缺少 finishReason')
    if not started: raise HTTPException(502, 'Antigravity 没有返回模型输出')
    if tool_count and finish=='stop': finish='tool_calls'
    yield {'choices':[{'index':0,'delta':{},'finish_reason':finish}]}
    if usage: yield {'choices':[], 'usage':usage}

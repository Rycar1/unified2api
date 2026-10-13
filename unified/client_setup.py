"""Private client setup archives are produced in memory; supplied keys never go to server disk."""
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile
from fastapi import HTTPException,Request
from fastapi.responses import Response

CLIENTS={'codex':'Codex','claude-code':'Claude Code','opencode':'OpenCode','dsh':'DeepSeek Harness','crush':'Crush','zcode':'Zcode'}
RUNNER='''import json
import sys
from pathlib import Path
import wb_agents as agents
root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
settings=json.loads((root/'client-config.json').read_text(encoding='utf-8'))
agents.PROVIDER_ID='unified2api'
agents.PROVIDER_NAME='Unified2API'
mode=sys.argv[1] if len(sys.argv)>1 else 'apply'
target_home=sys.argv[2] if len(sys.argv)>2 else None
if target_home:
    agents._codex_home=lambda home:str(Path(target_home)/'.codex')
try:
    if mode=='restore':
        result=agents.restore(str(root/'.setup-state'),settings['client'],home=target_home)
    else:
        result=agents.integrate(str(root/'.setup-state'),settings['client'],settings['base_url'],settings['api_key'],
            model=settings['model'],models=settings.get('models') or [],home=target_home)
    print('Configuration restored.' if mode=='restore' else 'Unified2API configuration applied.')
    print('Original configuration backups are kept in .setup-state.')
except Exception as error:
    print('Configuration failed: '+str(error).replace(settings['api_key'],'[redacted]'))
    sys.exit(1)
'''


def build_bundle(body,models):
    client=body.get('client')
    if client not in CLIENTS: raise HTTPException(400,'请选择支持的客户端')
    parsed=urlsplit(str(body.get('base_url','')).strip())
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(400,'请输入有效的 API Base URL，不要在地址中包含凭据')
    key=body.get('api_key')
    if not isinstance(key,str) or not 1<=len(key)<=4096 or any(c in key for c in '\r\n\0'):
        raise HTTPException(400,'请输入客户端 API 密钥')
    model=body.get('model')
    row=next((row for row in models if row['id']==model),None)
    if row is None: raise HTTPException(400,'请选择模型目录中的模型')
    protocol=(row.get('capabilities') or {}).get('protocols',{})
    required='messages' if client=='claude-code' else 'responses' if client=='codex' else 'chat'
    if protocol.get(required)=='unsupported': raise HTTPException(400,'所选模型不支持该客户端需要的协议')
    output=io.BytesIO()
    source=Path(__file__).resolve().parents[1]/'vendor/workbuddy2api-hub/wb_agents.py'
    metadata=[]
    for item in models:
        caps=item.get('capabilities') or {}
        entry={'id':item['id']}
        for field in ('context_window','max_output_tokens','vision','reasoning'):
            value=caps.get(field) if field in caps else item.get(field)
            if value is not None: entry[field]=value
        metadata.append(entry)
    config={'client':client,'base_url':str(body['base_url']).strip().rstrip('/'),'api_key':key,'model':model,'models':metadata}
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('client-config.json',json.dumps(config,ensure_ascii=False,indent=2))
        archive.writestr('configure.py',RUNNER)
        archive.writestr('wb_agents.py',source.read_bytes())
        archive.writestr('configure.cmd','@echo off\r\ncd /d "%~dp0"\r\nif exist configure.exe (configure.exe apply) else (py -3 configure.py apply)\r\npause\r\n')
        archive.writestr('restore.cmd','@echo off\r\ncd /d "%~dp0"\r\nif exist configure.exe (configure.exe restore) else (py -3 configure.py restore)\r\npause\r\n')
        for filename,mode in (('configure.command','apply'),('restore.command','restore')):
            info=zipfile.ZipInfo(filename)
            info.create_system=3;info.external_attr=0o100755<<16;info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,'#!/bin/sh\ncd "$(dirname "$0")"\npython3 configure.py '+mode+'\n')
        binary=Path(__file__).parent/'downloads/client-configure.exe'
        if binary.is_file(): archive.write(binary,'configure.exe')
        archive.writestr('说明.txt','解压后在客户端电脑上运行 configure.cmd（Windows）或 configure.command（macOS/Linux）。\n恢复使用 restore.cmd 或 restore.command。\n配置包含你填写的 API 密钥，请自行保管；原配置备份在 .setup-state。\nWindows 提供 configure.exe 时无需安装 Python，其他平台使用 Python 3。\nCodex 使用 Responses 格式；远程压缩能力由所选上游决定。\n')
    return output.getvalue()


def register_client_setup(app,store,api,body_json):
    @app.get('/admin/api/unified/client-setup')
    async def options(req:Request):
        store.require_admin(req)
        return {'clients':[{'id':key,'name':name} for key,name in CLIENTS.items()],
                'windows_standalone':(Path(__file__).parent/'downloads/client-configure.exe').is_file()}
    @app.post('/admin/api/unified/client-setup/download')
    async def download(req:Request):
        store.require_admin(req)
        body=await body_json(req)
        blob=build_bundle(body,(await api.models())['data'])
        return Response(blob,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="unified-client-setup.zip"',
            'Cache-Control':'no-store'})

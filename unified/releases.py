"""Admin-controlled, persistent release jobs; credentials stay in Docker."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
import httpx
from fastapi import HTTPException
from admin.server import write_json
from .docker_engine import DockerEngine, EngineError, image_reference

REVISION = re.compile(r'^[0-9a-f]{40}$')
REPOSITORY = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
PUBLIC_JOB_FIELDS = {'id','action','revision','status','phase','failed_phase','progress','started','finished','rolled_back','error_code',
                     'downloaded_bytes','total_bytes','download_attempt','updated'}


class ReleaseManager:
    def __init__(self,root,engine=None,repository=None,container=None,client_factory=None):
        self.root = Path(root)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.repository = repository or os.getenv('UNIFIED_UPDATE_REPOSITORY','Rycar1/unified2api')
        if not REPOSITORY.fullmatch(self.repository):
            raise ValueError('Invalid update repository')
        self.container = container or os.getenv('HOSTNAME') or socket.gethostname()
        self.engine = engine
        self.client_factory = client_factory or (lambda:httpx.Client(timeout=20,follow_redirects=False,trust_env=False))
        self.lock = threading.RLock()
        self.latest = None

    def docker(self):
        with self.lock:
            if self.engine:
                return self.engine
            path = '/var/run/docker.sock'
            if not Path(path).exists():
                raise HTTPException(503,'当前安装未连接 Docker 控制接口；使用更新后的 Compose 配置启用网页升级')
            self.engine = DockerEngine(path)
            return self.engine

    def parent(self):
        value = self.docker().inspect(self.container)
        if value.get('Config',{}).get('Labels',{}).get('io.unified2api.role')!='gateway':
            raise HTTPException(503,'当前容器没有受管理网关标记；请使用新版 Dockerfile 构建一次')
        return value

    def job(self):
        path = self.root/'operation.json'
        job = json.loads(path.read_text()) if path.exists() else None
        if job and job.get('status') in {'queued','running'} and job.get('worker_id'):
            stopped = False
            try:
                stopped = self.docker().inspect(job['worker_id'])['State']['Status'] in {'exited','dead'}
            except EngineError as exc:
                stopped = exc.status==404
            except (HTTPException,httpx.HTTPError,OSError):
                pass
            if stopped:
                # Re-read after inspect: a worker can finish just before exiting.
                job = json.loads(path.read_text())
                if job.get('status') in {'queued','running'}:
                    job.update(status='failed',failed_phase=job.get('phase'),phase='failed',
                               error_code='worker_exited',finished=int(time.time()))
                    write_json(path,job)
        return job

    def status(self):
        try:
            parent = self.parent()
            image = self.docker().image(parent['Image'])
            current = image.get('Config',{}).get('Labels',{}).get('org.opencontainers.image.revision','dev')
            available,reason = True,''
        except (HTTPException,EngineError,httpx.HTTPError,OSError):
            current,available,reason = os.getenv('UNIFIED_RELEASE_REVISION','dev'),False,'当前安装未连接 Docker 控制接口或缺少网关标记'
        previous = self.root/'previous.json'
        previous = json.loads(previous.read_text()) if previous.exists() else None
        job = self.job()
        return {'available':available,'reason':reason,'current':current,'repository':self.repository,'latest':self.latest,
                'previous':{'revision':previous.get('revision','unknown'),'created':previous.get('created')} if previous else None,
                'operation':{key:value for key,value in job.items() if key in PUBLIC_JOB_FIELDS} if job else None}

    def check(self):
        with self.client_factory() as client:
            try:
                response = client.get('https://api.github.com/repos/'+self.repository+'/commits/main',
                                      headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
                if response.status_code!=200:
                    raise HTTPException(502,'版本查询失败（GitHub HTTP '+str(response.status_code)+'）')
                value = response.json()
                revision = value.get('sha','')
                if not REVISION.fullmatch(revision):
                    raise ValueError('invalid revision')
                support = client.get('https://api.github.com/repos/'+self.repository+'/contents/unified/release_worker.py',
                    params={'ref':revision},headers={'Accept':'application/vnd.github+json'})
                if support.status_code not in (200,404):
                    raise HTTPException(502,'无法确认目标版本的升级支持（GitHub HTTP '+str(support.status_code)+'）')
                self.latest = {'revision':revision,'published':value.get('commit',{}).get('committer',{}).get('date'),
                               'upgrade_supported':support.status_code==200,
                               'url':'https://github.com/'+self.repository+'/commit/'+revision}
            except (httpx.HTTPError,ValueError):
                raise HTTPException(502,'无法读取可信仓库的版本信息') from None
        return self.status()

    def start(self,action,revision=None):
        if action not in {'upgrade','rollback'}:
            raise HTTPException(400,'不支持的版本操作')
        with self.lock:
            current = self.job()
            if current and current.get('status') in {'queued','running'}:
                try:
                    worker = self.docker().inspect(current['worker_id'])
                    if worker['State']['Status'] in {'created','running','restarting'}:
                        raise HTTPException(409,'已有版本操作正在进行')
                except EngineError:
                    pass
            parent = self.parent()
            try:
                image_reference(parent)
            except ValueError:
                raise HTTPException(400,'当前使用固定镜像摘要，请改为受管理镜像标签后再升级') from None
            if action=='upgrade':
                latest = self.check()['latest']
                if not latest['upgrade_supported']:
                    raise HTTPException(409,'仓库当前版本尚未包含网页升级功能，请发布新版后再升级')
                if not revision or revision!=latest['revision']:
                    raise HTTPException(409,'目标版本已变化，请重新检查并确认')
            elif not (self.root/'previous.json').exists():
                raise HTTPException(409,'没有可回滚版本')
            backing = next((mount for mount in parent.get('Mounts',[]) if mount.get('Type')=='volume' and
                            self.root.as_posix().startswith(mount['Destination'].rstrip('/')+'/')),None)
            if not backing:
                raise HTTPException(503,'升级状态目录必须位于持久化数据卷')
            job_id,ticket = secrets.token_hex(12),secrets.token_urlsafe(32)
            job = {'id':job_id,'action':action,'revision':revision,'status':'queued','phase':'queued','progress':0,
                   'started':int(time.time()),'ticket_hash':hashlib.sha256(ticket.encode()).hexdigest(),
                   'parent_id':parent['Id'],'name':parent['Name'].lstrip('/'),'repository':self.repository}
            mounts = [{'Type':'bind','Source':'/var/run/docker.sock','Target':'/var/run/docker.sock'},
                      {'Type':'volume','Source':backing['Name'],'Target':backing['Destination']}]
            config = {'Image':parent['Image'],'Entrypoint':['python','-m','unified.release_worker'],
                      'Cmd':[str(self.root),job_id],'WorkingDir':'/app','Labels':{'io.unified2api.role':'release-worker'},
                      'HostConfig':{'Mounts':mounts,'AutoRemove':True,'RestartPolicy':{'Name':'no'}}}
            write_json(self.root/'operation.json',job)
            try:
                identifier = self.docker().create('unified-release-'+job_id,config)
                job['worker_id'] = identifier
                write_json(self.root/'operation.json',job)
                self.docker().start(identifier)
            except (EngineError,httpx.HTTPError,OSError):
                job.update(status='failed',phase='launch',error_code='worker_launch_failed',finished=int(time.time()))
                write_json(self.root/'operation.json',job)
                raise HTTPException(502,'无法启动版本操作任务') from None
            return {'id':job_id,'ticket':ticket,'operation':{key:value for key,value in job.items() if key in PUBLIC_JOB_FIELDS}}

    def progress(self,job_id,ticket=None):
        job = self.job()
        if not job or job['id']!=job_id:
            raise HTTPException(404,'版本操作不存在')
        if ticket is not None and (time.time()-job['started']>10800 or not hmac.compare_digest(hashlib.sha256(ticket.encode()).hexdigest(),job.get('ticket_hash',''))):
            raise HTTPException(403,'版本查看凭证已过期或无效')
        return {key:value for key,value in job.items() if key in PUBLIC_JOB_FIELDS}

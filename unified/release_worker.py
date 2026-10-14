"""Temporary Python worker which survives replacement of the gateway container."""
import io
import asyncio
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import time
from urllib.parse import quote
import httpx
from admin.server import write_json
from .docker_engine import DockerEngine, replacement_config, image_reference
from .releases import REPOSITORY, REVISION
from .release_download import SourceDownloadError, download_source


def source_context(blob,revision):
    """Reject traversal, links, devices and private files in an update archive."""
    if not REVISION.fullmatch(revision):
        raise ValueError('invalid revision')
    result = io.BytesIO()
    total,count = 0,0
    required = set()
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as source,tarfile.open(fileobj=result,mode='w:gz') as target:
        for member in source:
            count += 1
            if count>30000:
                raise ValueError('archive member limit')
            parts = member.name.split('/')
            relative = '/'.join(parts[1:])
            if not relative:
                continue
            if member.name.startswith('/') or '..' in parts or not (member.isfile() or member.isdir()):
                raise ValueError('unsafe archive member')
            if relative.startswith(('data/','auths/','trae-data/','.git/')) or Path(relative).name in {'.env','credentials.json','accounts.json','hub-machine.json','hub-migration.json'}:
                raise ValueError('private file in source archive')
            total += member.size
            if total>512*1024*1024:
                raise ValueError('archive size limit')
            required.add(relative)
            member.name = relative
            member.pax_headers = {}
            member.uid = member.gid = 0
            member.uname = member.gname = ''
            member.mode &= 0o777
            target.addfile(member,source.extractfile(member) if member.isfile() else None)
    if not {'Dockerfile','unified/server.py','unified/release_worker.py'}<=required:
        raise ValueError('target lacks managed update support')
    return result.getvalue()


class ReleaseWorker:
    def __init__(self,root,job_id,engine=None,clock=time.time,sleep=time.sleep):
        self.root,self.job_id,self.engine = Path(root),job_id,engine or DockerEngine()
        self.clock,self.sleep = clock,sleep
        self.path = self.root/'operation.json'
        self.job = json.loads(self.path.read_text())
        if self.job['id']!=job_id or not REPOSITORY.fullmatch(self.job['repository']):
            raise ValueError('invalid operation')

    def state(self,phase,progress,**extra):
        self.job.update(phase=phase,progress=progress,**extra)
        write_json(self.path,self.job)

    def health(self,identifier):
        deadline = self.clock()+120
        for _ in range(65):
            value = self.engine.inspect(identifier)
            if value['State'].get('Health',{}).get('Status')=='healthy':
                return
            if value['State']['Status'] in {'exited','dead'} or self.clock()>deadline:
                break
            self.sleep(2)
        raise RuntimeError('health check failed')

    def build(self):
        revision = self.job['revision']
        if not REVISION.fullmatch(revision or ''):
            raise ValueError('invalid revision')
        with httpx.Client(timeout=30,follow_redirects=False,trust_env=False) as client:
            meta = client.get('https://api.github.com/repos/'+self.job['repository']+'/commits/'+revision,
                              headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
            meta.raise_for_status()
            if meta.json().get('sha')!=revision:
                raise ValueError('source revision mismatch')
        last_update = 0
        def progress(received,total,attempt):
            nonlocal last_update
            now = time.monotonic()
            if received and received != total and now-last_update < 1:
                return
            last_update = now
            percent = 15 + min(14,int(14*received/total)) if total else 15
            self.state('download',percent,downloaded_bytes=received,total_bytes=total,
                       download_attempt=attempt,updated=int(self.clock()))
        blob = asyncio.run(download_source(self.job['repository'],revision,progress))
        self.state('validate',29)
        context = source_context(blob,revision)
        self.state('build',30)
        with tempfile.TemporaryDirectory(prefix='unified-build-') as folder:
            archive = Path(folder)/'context.tar.gz'
            archive.write_bytes(context)
            return self.engine.build(archive,'unified2api-managed:'+revision[:12],revision)

    def replace(self,parent,new_image):
        if not parent['Config'].get('Healthcheck'):
            raise ValueError('health check required')
        name = self.job['name']
        backup_name = name+'-rollback-'+self.job_id
        previous = {'container_id':parent['Id'],'name':name,'backup_name':backup_name,
                    'image_id':parent['Image'],'image_ref':image_reference(parent),
                    'revision':self.engine.image(parent['Image']).get('Config',{}).get('Labels',{}).get('org.opencontainers.image.revision','unknown'),
                    'created':int(self.clock())}
        replacement = replacement_config(parent,new_image)
        replacement['Labels'].update({'org.opencontainers.image.revision':self.job['revision'],
                                      'io.unified2api.image-ref':previous['image_ref'],
                                      'com.docker.compose.image':new_image,'io.unified2api.role':'gateway'})
        new_id,renamed,stopped = None,False,False
        try:
            self.state('switch',65)
            self.engine.stop(parent['Id'])
            stopped = True
            self.engine.rename(parent['Id'],backup_name)
            renamed = True
            new_id = self.engine.create(name,replacement)
            self.engine.start(new_id)
            self.state('health',80)
            self.health(new_id)
            self.engine.tag(new_image,previous['image_ref'])
            write_json(self.root/'previous.json',previous)
            self.state('complete',100,status='succeeded',finished=int(self.clock()),rolled_back=False)
        except Exception:
            failed_phase = self.job.get('phase')
            if new_id:
                self.engine.remove(new_id)
            if renamed:
                self.engine.rename(parent['Id'],name)
            if stopped:
                self.engine.start(parent['Id'])
                self.health(parent['Id'])
            self.state('failed',100,status='failed',failed_phase=failed_phase,finished=int(self.clock()),rolled_back=stopped,error_code='upgrade_failed')
            raise

    def rollback(self):
        previous = json.loads((self.root/'previous.json').read_text())
        current = self.engine.inspect(self.job['parent_id'])
        # Validate the backup before stopping the serving container.
        backup = self.engine.inspect(previous['container_id'])
        if backup['Config'].get('Labels',{}).get('io.unified2api.role')!='gateway':
            raise ValueError('invalid backup container')
        parked = self.job['name']+'-rolled-forward-'+self.job_id
        stopped,renamed,restored = False,False,False
        try:
            self.state('switch',40)
            self.engine.stop(current['Id'])
            stopped = True
            self.engine.rename(current['Id'],parked)
            renamed = True
            self.engine.rename(previous['container_id'],self.job['name'])
            restored = True
            self.engine.start(previous['container_id'])
            self.state('health',80)
            self.health(previous['container_id'])
            self.engine.tag(previous['image_id'],previous['image_ref'])
            write_json(self.root/'previous.json',{'container_id':current['Id'],'image_id':current['Image'],
                       'image_ref':image_reference(current),'name':self.job['name'],'backup_name':parked,
                       'revision':self.engine.image(current['Image']).get('Config',{}).get('Labels',{}).get('org.opencontainers.image.revision','unknown'),
                       'created':int(self.clock())})
            self.state('complete',100,status='succeeded',finished=int(self.clock()),revision=previous.get('revision'),rolled_back=True)
        except Exception:
            failed_phase = self.job.get('phase')
            if restored:
                self.engine.stop(previous['container_id'])
                self.engine.rename(previous['container_id'],previous['backup_name'])
            if renamed:
                self.engine.rename(current['Id'],self.job['name'])
            if stopped:
                self.engine.start(current['Id'])
                self.health(current['Id'])
            self.state('failed',100,status='failed',failed_phase=failed_phase,finished=int(self.clock()),rolled_back=False,error_code='rollback_failed')
            raise

    def run(self):
        self.state('prepare',5,status='running')
        try:
            if self.job['action']=='rollback':
                self.rollback()
            else:
                image = self.build()
                parent = self.engine.inspect(self.job['parent_id'])
                if parent['Config'].get('Labels',{}).get('io.unified2api.role')!='gateway':
                    raise ValueError('invalid parent')
                self.replace(parent,image)
        except Exception as exc:
            if self.job.get('status')!='failed':
                code = exc.code if isinstance(exc,SourceDownloadError) else 'release_failed'
                self.state('failed',100,status='failed',failed_phase=self.job.get('phase'),finished=int(self.clock()),error_code=code)
            # No raw build output, exception body, deployment environment or secrets.
            print(json.dumps({'id':self.job_id,'status':'failed','phase':self.job['phase']}),flush=True)


if __name__=='__main__':
    ReleaseWorker(sys.argv[1],sys.argv[2]).run()

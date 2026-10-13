import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import httpx
from fastapi import HTTPException

from unified.docker_engine import replacement_config
from unified.release_worker import ReleaseWorker, source_context
from unified.releases import ReleaseManager

OLD = '1'*40
NEW = '2'*40


class FakeEngine:
    def __init__(self):
        self.parent = {'Id':'old-id','Name':'/test-gateway','Image':'old-image',
                       'Config':{'Image':'unified:managed','Labels':{'io.unified2api.role':'gateway'},
                                 'Env':['ADMIN_KEY=PRIVATE-ADMIN','UNIFIED_API_KEY=PRIVATE-KEY'],
                                 'Hostname':'old-host','Cmd':['python','-m','unified.server'],
                                 'Healthcheck':{'Test':['CMD','health']}},
                       'HostConfig':{'PortBindings':{'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'8080'}]},
                                     'Binds':['existing-data:/data','/var/run/docker.sock:/var/run/docker.sock']},
                       'NetworkSettings':{'Networks':{'test':{'Aliases':['gateway','old-id']}}},
                       'State':{'Status':'running','Health':{'Status':'healthy'}}}
        self.containers = {'old-id':self.parent}
        self.fail = False
        self.tagged = []

    def inspect(self,identifier):
        return self.containers[identifier]

    def image(self,identifier):
        return {'Config':{'Labels':{'org.opencontainers.image.revision':OLD if identifier=='old-image' else NEW}}}

    def create(self,name,config):
        self.containers['new-id'] = {'Id':'new-id','Name':'/'+name,'Image':config['Image'],'Config':config,
                                      'HostConfig':config['HostConfig'],'State':{'Status':'created','Health':{'Status':'starting'}}}
        return 'new-id'

    def stop(self,identifier):
        self.containers[identifier]['State']['Status'] = 'exited'

    def start(self,identifier):
        self.containers[identifier]['State'] = {'Status':'exited' if self.fail and identifier=='new-id' else 'running',
                                               'Health':{'Status':'unhealthy' if self.fail and identifier=='new-id' else 'healthy'}}

    def rename(self,identifier,name):
        self.containers[identifier]['Name'] = '/'+name

    def remove(self,identifier):
        del self.containers[identifier]

    def tag(self,image,reference):
        self.tagged.append((image,reference))


class Releases(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = FakeEngine()
        self.job = {'id':'job-one','action':'upgrade','revision':NEW,'parent_id':'old-id',
                    'repository':'synthetic/project','name':'test-gateway','started':1}
        (self.root/'operation.json').write_text(json.dumps(self.job))
        self.worker = ReleaseWorker(self.root,'job-one',engine=self.engine,sleep=lambda _:None)

    def tearDown(self):
        self.temp.cleanup()

    def test_clone_preserves_ports_mounts_and_credentials_without_keeping_hostname(self):
        config = replacement_config(self.engine.parent,'new-image')
        self.assertEqual(config['HostConfig'],self.engine.parent['HostConfig'])
        self.assertEqual(config['Env'],self.engine.parent['Config']['Env'])
        self.assertEqual(config['Hostname'],'')
        self.assertNotIn('old-id',config['NetworkingConfig']['EndpointsConfig']['test']['Aliases'])

    def test_successful_upgrade_and_manual_rollback(self):
        self.worker.replace(copy.deepcopy(self.engine.parent),'new-image')
        self.assertEqual(json.loads((self.root/'operation.json').read_text())['status'],'succeeded')
        self.assertEqual(self.engine.containers['new-id']['Name'],'/test-gateway')
        self.assertEqual(self.engine.containers['old-id']['State']['Status'],'exited')
        self.assertNotIn('PRIVATE-', ''.join(path.read_text() for path in self.root.glob('*.json')))
        self.job.update(id='job-two',action='rollback',parent_id='new-id')
        (self.root/'operation.json').write_text(json.dumps(self.job))
        rollback = ReleaseWorker(self.root,'job-two',engine=self.engine,sleep=lambda _:None)
        rollback.rollback()
        self.assertEqual(self.engine.containers['old-id']['Name'],'/test-gateway')
        self.assertEqual(self.engine.containers['old-id']['State']['Status'],'running')
        self.assertTrue(json.loads((self.root/'operation.json').read_text())['rolled_back'])

    def test_failed_health_check_restores_serving_container(self):
        self.engine.fail = True
        with self.assertRaises(RuntimeError):
            self.worker.replace(copy.deepcopy(self.engine.parent),'new-image')
        self.assertEqual(self.engine.parent['Name'],'/test-gateway')
        self.assertEqual(self.engine.parent['State']['Status'],'running')
        self.assertNotIn('new-id',self.engine.containers)
        self.assertTrue(json.loads((self.root/'operation.json').read_text())['rolled_back'])

    def test_upgrade_keeps_mutable_image_reference_for_following_upgrades(self):
        self.worker.replace(copy.deepcopy(self.engine.parent),'new-image')
        labels = self.engine.containers['new-id']['Config']['Labels']
        self.assertEqual(labels.get('io.unified2api.image-ref'),'unified:managed')
        self.worker.job.update(action='rollback',parent_id='new-id')
        self.worker.rollback()
        previous = json.loads((self.root/'previous.json').read_text())
        self.assertEqual(previous['image_ref'],'unified:managed')

    def test_latest_commit_without_update_module_cannot_replace_running_service(self):
        def reply(request):
            if '/commits/' in request.url.path:
                return httpx.Response(200,json={'sha':NEW,'commit':{'committer':{'date':'synthetic-date'}}})
            return httpx.Response(404,json={})
        manager = ReleaseManager(self.root,engine=self.engine,repository='synthetic/project',container='old-id',
            client_factory=lambda:httpx.Client(transport=httpx.MockTransport(reply)))
        self.assertFalse(manager.check()['latest']['upgrade_supported'])
        with self.assertRaises(HTTPException) as caught:
            manager.start('upgrade',NEW)
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(self.engine.parent['State']['Status'],'running')
        self.assertNotIn('PRIVATE-',json.dumps(manager.status()))

    def test_exited_worker_marks_job_failed_instead_of_polling_forever(self):
        self.job.update(worker_id='worker-id',status='running',phase='build')
        (self.root/'operation.json').write_text(json.dumps(self.job))
        self.engine.containers['worker-id'] = {'State':{'Status':'exited'}}
        manager = ReleaseManager(self.root,engine=self.engine,repository='synthetic/project',container='old-id')
        value = manager.progress(self.job['id'])
        self.assertEqual(value['status'],'failed')
        self.assertEqual(value['failed_phase'],'build')
        self.assertEqual(value['error_code'],'worker_exited')

    def archive(self,names):
        blob = io.BytesIO()
        with tarfile.open(fileobj=blob,mode='w:gz') as archive:
            for name in names:
                info = tarfile.TarInfo('project-'+NEW+'/'+name)
                data = b'synthetic'
                info.size = len(data)
                archive.addfile(info,io.BytesIO(data))
        return blob.getvalue()

    def test_source_validation_rejects_private_files_and_traversal(self):
        required = ['Dockerfile','unified/server.py','unified/release_worker.py']
        context = source_context(self.archive(required),NEW)
        with tarfile.open(fileobj=io.BytesIO(context),mode='r:gz') as archive:
            self.assertEqual(set(archive.getnames()),set(required))
        for file in ['../escape','.env','data/auth/real.info','vendor/provider/accounts.json']:
            with self.assertRaises(ValueError):
                source_context(self.archive(required+[file]),NEW)

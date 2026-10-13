"""Docker Engine over its local Unix socket; no TCP control interface."""
import copy
import json
from urllib.parse import quote, urlencode
import httpx


class EngineError(Exception):
    def __init__(self,status,operation):
        super().__init__(f'Docker {operation} failed ({status})')
        self.status,self.operation = status,operation


class DockerEngine:
    def __init__(self,socket='/var/run/docker.sock'):
        self.client = httpx.Client(transport=httpx.HTTPTransport(uds=socket),base_url='http://docker',timeout=60)
        self.prefix = ''

    def request(self,method,path,body=None,allow=()):
        response = self.client.request(method,self.prefix+path,json=body)
        if response.status_code>=400 and response.status_code not in allow:
            raise EngineError(response.status_code,path.split('?')[0])
        return response.json() if response.content and response.status_code not in allow else {}

    def inspect(self,identifier):
        return self.request('GET','/containers/'+quote(identifier,safe='')+'/json')

    def image(self,identifier):
        return self.request('GET','/images/'+quote(identifier,safe='')+'/json')

    def create(self,name,config):
        return self.request('POST','/containers/create?'+urlencode({'name':name}),config)['Id']

    def start(self,identifier):
        self.request('POST','/containers/'+quote(identifier,safe='')+'/start',allow=(304,))

    def stop(self,identifier):
        self.request('POST','/containers/'+quote(identifier,safe='')+'/stop?t=30',allow=(304,))

    def rename(self,identifier,name):
        self.request('POST','/containers/'+quote(identifier,safe='')+'/rename?'+urlencode({'name':name}))

    def remove(self,identifier):
        self.request('DELETE','/containers/'+quote(identifier,safe='')+'?force=true',allow=(404,))

    def tag(self,image,reference):
        if '@' in reference:
            raise ValueError('Digest references cannot be updated')
        repo,separator,tag = reference.rpartition(':')
        if not separator or '/' in tag:
            repo,tag = reference,'latest'
        self.request('POST','/images/'+quote(image,safe='')+'/tag?'+urlencode({'repo':repo,'tag':tag}))

    def build(self,archive,tag,revision):
        query = urlencode({'t':tag,'rm':'true','labels':json.dumps({'org.opencontainers.image.revision':revision,'io.unified2api.role':'gateway'}),
                           'buildargs':json.dumps({'UNIFIED_RELEASE_REVISION':revision})})
        def chunks():
            with open(archive,'rb') as source:
                for data in iter(lambda:source.read(1024*1024),b''):
                    yield data
        with self.client.stream('POST',self.prefix+'/build?'+query,content=chunks(),
                                headers={'Content-Type':'application/x-tar'},timeout=httpx.Timeout(1800,connect=15)) as response:
            if response.status_code>=400:
                raise EngineError(response.status_code,'build')
            for line in response.iter_lines():
                if len(line)>1024*1024:
                    raise EngineError(502,'build output')
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('error') or event.get('errorDetail'):
                    raise EngineError(500,'build')
        return self.image(tag)['Id']


def image_reference(parent):
    reference = parent['Config'].get('Labels',{}).get('io.unified2api.image-ref') or parent['Config']['Image']
    if not reference or '@' in reference or reference.startswith('sha256:'):
        raise ValueError('A mutable deployment image tag is required')
    return reference


def replacement_config(parent,image):
    """Copy deployment parameters in memory. Never serialize credentials to disk."""
    accepted = {'Hostname','Domainname','User','AttachStdin','AttachStdout','AttachStderr','ExposedPorts','Tty',
                'OpenStdin','StdinOnce','Env','Cmd','Healthcheck','ArgsEscaped','Volumes','WorkingDir','Entrypoint',
                'NetworkDisabled','MacAddress','Labels','StopSignal','StopTimeout','Shell'}
    config = {key:copy.deepcopy(value) for key,value in parent['Config'].items() if key in accepted}
    config.update(Image=image,Hostname='')
    host = copy.deepcopy(parent['HostConfig'])
    host.pop('ContainerIDFile',None)
    config['HostConfig'] = host
    networks = parent.get('NetworkSettings',{}).get('Networks',{})
    config['NetworkingConfig'] = {'EndpointsConfig':{name:{'Aliases':[alias for alias in value.get('Aliases') or []
                                                    if alias!=parent['Id'][:12] and alias!=parent['Id']]}
                                                   for name,value in networks.items()}}
    config['Labels'].pop('com.docker.compose.config-hash',None)
    return config

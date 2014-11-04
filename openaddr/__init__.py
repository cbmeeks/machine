from subprocess import Popen
from multiprocessing import Process
from tempfile import mkdtemp
from os.path import realpath, join, basename, splitext, exists
from shutil import copy, move, rmtree
from mimetypes import guess_extension
from StringIO import StringIO
from logging import getLogger
from datetime import datetime
from os import mkdir, environ
from time import sleep, time
from zipfile import ZipFile
import json
import boto

from osgeo import ogr
from requests import get
from boto import connect_s3
from . import paths
from openaddr.cache import (
    CacheResult,
    DownloadTask,
    Urllib2DownloadTask,
)

from openaddr.conform import (
    ConformResult,
    DecompressionTask,
    ConvertToCsvTask,
)

class ExcerptResult:
    sample_data = None

    def __init__(self, sample_data):
        self.sample_data = sample_data
    
    @staticmethod
    def empty():
        return ExcerptResult(None)

    def todict(self):
        return dict(sample_data=self.sample_data)

class S3:
    bucketname = None

    def __init__(self, key, secret, bucketname):
        self._key, self._secret = key, secret
        self.bucketname = bucketname
        self._bucket = connect_s3(key, secret).get_bucket(bucketname)
    
    def toenv(self):
        env = dict(environ)
        env.update(AWS_ACCESS_KEY_ID=self._key, AWS_SECRET_ACCESS_KEY=self._secret)
        return env
    
    def get_key(self, name):
        return self._bucket.get_key(name)
    
    def new_key(self, name):
        return self._bucket.new_key(name)

def cache(srcjson, destdir, extras, s3):
    ''' Python wrapper for openaddress-cache.

        Return a dictionary of cache details, including URL and md5 hash:

          {
            "cache": URL of cached data,
            "fingerprint": md5 hash of data,
            "version": data version as date?
          }
    '''
    start = datetime.now()
    source, _ = splitext(basename(srcjson))
    workdir = mkdtemp(prefix='cache-')
    logger = getLogger('openaddr')
    tmpjson = _tmp_json(workdir, srcjson, extras)

    def thread_work(json_filepath, workdir):
        with open(json_filepath, 'r') as j:
            data = json.load(j)

        source_urls = data.get('data')
        if not isinstance(source_urls, list):
            source_urls = [source_urls]

        task = DownloadTask.from_type_string(data.get('type'))
        downloaded_files = task.download(source_urls, workdir)

        # FIXME: I wrote the download stuff to assume multiple files because
        # sometimes a Shapefile fileset is splayed across multiple files instead
        # of zipped up nicely. When the downloader downloads multiple files,
        # we should zip them together before uploading to S3 instead of picking
        # the first one only.
        filepath_to_upload = downloaded_files[0]

        version = datetime.utcnow().strftime('%Y%m%d')
        key = '/{}/{}'.format(version, basename(filepath_to_upload))

        k = s3.new_key(key)
        kwargs = dict(policy='public-read', reduced_redundancy=True)
        k.set_contents_from_filename(filepath_to_upload, **kwargs)

        data['cache'] = k.generate_url(expires_in=0, query_auth=False)
        data['fingerprint'] = k.md5
        data['version'] = version

        with open(json_filepath, 'w') as j:
            json.dump(data, j)

    p = Process(target=thread_work, args=(tmpjson, workdir), name='oa-cache-'+source)
    p.start()
    # FIXME: We could add an integer argument to join() for the number of seconds
    # to wait for this process to finish
    p.join()

    with open(tmpjson, 'r') as tmp_file:
        data = json.load(tmp_file)

    rmtree(workdir)

    return CacheResult(data.get('cache', None),
                       data.get('fingerprint', None),
                       data.get('version', None),
                       datetime.now() - start)

def conform(srcjson, destdir, extras, s3):
    ''' Python wrapper for openaddresses-conform.

        Return a dictionary of conformed details, a CSV URL and local path:

          {
            "processed": URL of conformed CSV,
            "path": Local filesystem path to conformed CSV
          }
    '''
    start = datetime.now()
    source, _ = splitext(basename(srcjson))
    workdir = mkdtemp(prefix='conform-')
    logger = getLogger('openaddr')
    tmpjson = _tmp_json(workdir, srcjson, extras)

    def thread_work(json_filepath, workdir, source_key):
        with open(json_filepath, 'r') as j:
            data = json.load(j)

        source_urls = data.get('cache')
        if not isinstance(source_urls, list):
            source_urls = [source_urls]

        task = Urllib2DownloadTask()
        downloaded_path = task.download(source_urls, workdir)

        task = DecompressionTask.from_type_string(data.get('compression'))
        decompressed_paths = task.decompress(downloaded_path, workdir)

        task = ConvertToCsvTask()
        csv_paths = task.convert(decompressed_paths, workdir)

        version = datetime.utcnow().strftime('%Y%m%d')
        key = '/{}/{}.csv'.format(version, source_key)

        k = s3.new_key(key)
        kwargs = dict(policy='public-read', reduced_redundancy=True)
        k.set_contents_from_filename(csv_paths[0], **kwargs)

        data['processed'] = k.generate_url(expires_in=0, query_auth=False)

        with open(json_filepath, 'w') as j:
            json.dump(data, j)

    p = Process(target=thread_work, args=(tmpjson, workdir, source), name='oa-conform-'+source)
    p.start()
    # FIXME: We could add an integer argument to join() for the number of seconds
    # to wait for this process to finish
    p.join()

    with open(tmpjson, 'r') as tmp_file:
        data = json.load(tmp_file)

    rmtree(workdir)

    return ConformResult(data.get('processed', None),
                         datetime.now() - start)

def excerpt(srcjson, destdir, extras, s3):
    ''' 
    '''
    start = datetime.now()
    source, _ = splitext(basename(srcjson))
    workdir = mkdtemp(prefix='excerpt-')
    logger = getLogger('openaddr')
    tmpjson = _tmp_json(workdir, srcjson, extras)

    #
    sample_data = None
    got = get(extras['cache'])
    _, ext = splitext(got.url)
    
    if not ext:
        ext = guess_extension(got.headers['content-type'])
    
    if ext == '.zip':
        zbuff = StringIO(got.content)
        zf = ZipFile(zbuff, 'r')
        
        for name in zf.namelist():
            _, ext = splitext(name)
            
            if ext in ('.shp', '.shx', '.dbf'):
                with open(join(workdir, 'cache'+ext), 'w') as file:
                    file.write(zf.read(name))
        
        if exists(join(workdir, 'cache.shp')):
            ds = ogr.Open(join(workdir, 'cache.shp'))
        else:
            ds = None
    
    elif ext == '.json':
        with open(join(workdir, 'cache.json'), 'w') as file:
            file.write(got.content)
        
        ds = ogr.Open(join(workdir, 'cache.json'))
    
    else:
        ds = None
    
    if ds:
        layer = ds.GetLayer(0)
        defn = layer.GetLayerDefn()
        field_count = defn.GetFieldCount()
        sample_data = [[defn.GetFieldDefn(i).name for i in range(field_count)]]
        
        for feature in layer:
            sample_data += [[feature.GetField(i) for i in range(field_count)]]
            
            if len(sample_data) == 6:
                break
    
    rmtree(workdir)
    
    dir = datetime.now().strftime('%Y%m%d')
    key = s3.new_key(join(dir, 'samples', source+'.json'))
    args = dict(policy='public-read', headers={'Content-Type': 'text/json'})
    key.set_contents_from_string(json.dumps(sample_data, indent=2), **args)
    
    return ExcerptResult('http://s3.amazonaws.com/{}/{}'.format(s3.bucketname, key.name))

def _wait_for_it(command, seconds):
    ''' Run command for a limited number of seconds, then kill it.
    '''
    due = time() + seconds
    
    while True:
        if command.poll() is not None:
            # Command complete
            break
        
        elif time() > due:
            # Went overtime
            command.kill()

        else:
            # Keep waiting
            sleep(.5)

def _tmp_json(workdir, srcjson, extras):
    ''' Work on a copy of source JSON in a safe directory, with extras grafted in.
    
        Return path to the new JSON file.
    '''
    mkdir(join(workdir, 'source'))
    tmpjson = join(workdir, 'source', basename(srcjson))

    with open(srcjson, 'r') as src_file, open(tmpjson, 'w') as tmp_file:
        data = json.load(src_file)
        data.update(extras)
        json.dump(data, tmp_file)
    
    return tmpjson

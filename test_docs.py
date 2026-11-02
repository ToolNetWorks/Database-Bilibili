from fastapi import FastAPI, Security, HTTPException
from fastapi.security import APIKeyHeader
from fastapi.testclient import TestClient
from workers.bilibili.api.docs_api import install_docs_api

class MockStore:
    VIDEO_FIELDS="bvid, channel_uid, url, title, thumbnail, aid, cid, duration_seconds, status"
    @staticmethod
    def enabled():return True
    @staticmethod
    def fetch_all(sql,params):
        ids=params[0]
        if 'BV1bn4P6bE59' in ids:return [{'bvid':'BV1bn4P6bE59','channel_uid':'98240066','url':'https://www.bilibili.com/video/BV1bn4P6bE59','title':'Test','thumbnail':None,'aid':None,'cid':None,'duration_seconds':6080,'status':'FREE_CANDIDATE'}]
        return []
    @staticmethod
    def normalize_video(row, fmt):return {'bvid':row['bvid'],'duration_display':fmt(row['duration_seconds'])}

header=APIKeyHeader(name="X-API-Key",auto_error=False)
def require_api_key(key=Security(header)):
    if key!='secure-key':raise HTTPException(401,'Invalid API key')
app=FastAPI()
install_docs_api(app,require_api_key,MockStore(),lambda s:'01:41:20')
client=TestClient(app)
assert client.get('/',follow_redirects=False).status_code==307
assert client.get('/api-docs').status_code==200
assert '/v1/videos/batch' in client.get('/openapi.json').json()['paths']
assert client.post('/v1/videos/batch',json={'bvids':['BV1bn4P6bE59']}).status_code==401
response=client.post('/v1/videos/batch',headers={'X-API-Key':'secure-key'},json={'bvids':['BV1bn4P6bE59','BV12s4P6dEgq']})
print(response.status_code,response.json())
assert response.status_code==200 and response.json()['found']==1
assert response.json()['missing']==['BV12s4P6dEgq']
assert client.post('/v1/videos/batch',headers={'X-API-Key':'secure-key'},json={'bvids':['bad']}).status_code==422
assert client.post('/v1/videos/batch',headers={'X-API-Key':'secure-key'},json={'bvids':[]}).status_code==422
print('TESTS PASSED')

from pathlib import Path
import httpx
from vaani.groq import GroqClient

def test_fake_http_server_roundtrip(tmp_path):
    def handler(req):
        if req.url.path.endswith('transcriptions'): return httpx.Response(200,json={'text':'ok'})
        return httpx.Response(200,json={'data':[]})
    p=tmp_path/'a.wav'; p.write_bytes(b'RIFF')
    c=GroqClient(transport=httpx.MockTransport(handler)); assert c.transcribe(p,'runtime').text=='ok'

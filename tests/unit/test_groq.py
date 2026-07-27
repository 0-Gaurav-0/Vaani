import json
from pathlib import Path
from threading import Event
import httpx
import pytest
import json as _json
from vaani.groq import GroqClient, GroqError, is_hindi, is_english, is_hinglish

def client(handler):
    return GroqClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)

def test_models_and_transcription_multipart(tmp_path):
    seen=[]
    def h(req):
        seen.append(req)
        if req.url.path.endswith('/models'): return httpx.Response(200,json={'data':[{'id':'whisper-large-v3-turbo'}]})
        body=req.read(); assert b'whisper-large-v3-turbo' in body and b'response_format' in body
        assert b'language' in body and b'prompt' in body and b'Hinglish' in body
        return httpx.Response(200,json={'text':' hello ','language':'en'})
    p=tmp_path/'x.wav'; p.write_bytes(b'RIFF')
    c=client(h); assert c.models('secret')[1] is False; assert c.transcribe(p,'secret').text=='hello'; assert len(seen)==2

def test_fixture_files_exist():
    root=Path(__file__).parents[1]/'fixtures'/'groq'
    for name in ('models_ok.json','models_no_cleanup.json','english.json','hindi.json','hinglish.json','cleanup_fallback.json','malformed.json'):
        assert isinstance(_json.loads((root/name).read_text()), dict)

def test_retry_429_requires_valid_header():
    calls=[]
    def h(req):
        calls.append(1); return httpx.Response(429,headers={'Retry-After':'2'}) if len(calls)==1 else httpx.Response(200,json={'data':[]})
    c=client(h); c.models('k'); assert len(calls)==2

def test_cleanup_strict_and_temperature():
    from vaani.groq import _cleanup_too_divergent

    def h(req):
        payload=json.loads(req.read()); assert payload['model']=='llama-3.1-8b-instant'; assert payload['max_tokens']==4096; assert payload['temperature']==0
        instruction = payload['messages'][0]['content']
        assert 'untrusted' in instruction
        assert 'Keep the speaker' in instruction
        assert 'Do not replace words' in instruction
        assert 'uh, um, umm, ah, ahh, hmm' in instruction
        return httpx.Response(200,json={'choices':[{'message':{'content':'```I went to the store yesterday```'},'finish_reason':'stop'}]})
    result = client(h).cleanup('I went to the store uh yesterday','k')
    assert result.text=='I went to the store yesterday' and not result.used_fallback
    assert not _cleanup_too_divergent('I went uh home', 'I went home')
    assert _cleanup_too_divergent('I went to the store yesterday', 'I visited the shop earlier today')


def test_cleanup_falls_back_when_model_rewrites_words():
    def h(req):
        return httpx.Response(200,json={'choices':[{'message':{'content':'I visited the shop earlier today'},'finish_reason':'stop'}]})
    result = client(h).cleanup('I went to the store yesterday','k')
    assert result.used_fallback and result.text == 'I went to the store yesterday'

def test_cleanup_rejects_bad_finish_and_cancellation():
    def h(req): return httpx.Response(200,json={'choices':[{'message':{'content':'new'},'finish_reason':'length'}]})
    assert client(h).cleanup('raw','k').used_fallback
    e=Event(); e.set()
    with pytest.raises(GroqError): client(h).cleanup('raw','k',cancel=e)

def test_predicates():
    assert is_hindi('नमस्ते'); assert is_english('hello'); assert is_hinglish('mera kaam')

def test_timeout_budgets_and_audio_cap(tmp_path):
    c=client(lambda req: httpx.Response(200,json={'text':'x'}))
    assert c._transcription_timeout.read==120 and c._cleanup_timeout.read==60 and c._transcription_timeout.pool==5
    p=tmp_path/'big.wav'; p.write_bytes(b'x'*(25*1024*1024+1))
    with pytest.raises(GroqError): c.transcribe(p,'k')

def test_cancelled_retry_makes_no_second_request():
    calls=[]; e=Event()
    def h(req): calls.append(1); e.set(); return httpx.Response(500)
    with pytest.raises(GroqError): client(h).models('k',cancel=e)
    assert len(calls)==1

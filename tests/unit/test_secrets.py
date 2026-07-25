from types import SimpleNamespace
import pytest
from vaani.secrets import *

class Fake:
 def __init__(self): self.v=None
 def get_password(self,s,a): return self.v
 def set_password(self,s,a,v): self.v=v
 def delete_password(self,s,a):
  if self.v is None: raise Exception('No password')
  self.v=None

def settings(): return SimpleNamespace(base_url='https://x', transcription_model='trans', cleanup_model='clean')
def test_override_and_mask():
 f=Fake(); s=SecretServiceKeyStore(f); f.v='stored'
 assert effective_key(s, {'GROQ_API_KEY':'  '}).value=='stored'
 assert effective_key(s, {'GROQ_API_KEY':'run'}).runtime_override
 assert masked_key_state('x')=='API key configured (masked)'
def test_lock_and_guard():
 class L(Fake):
  def get_password(self,*a): raise Exception('collection locked')
 with pytest.raises(KeyringLocked): SecretServiceKeyStore(L()).get()
 with pytest.raises(RuntimeError): SecretServiceKeyStore(Fake()).set('x', state='Recording')
 with pytest.raises(RuntimeError): SecretServiceKeyStore(Fake()).remove(state='Processing')
def test_delete_missing(): SecretServiceKeyStore(Fake()).remove()
def test_malformed_response():
 class C:
  def get(self,*a,**k): return SimpleNamespace(status_code=200, json=lambda: {'oops':1})
 assert not validate_key('k', settings(), client=C()).valid

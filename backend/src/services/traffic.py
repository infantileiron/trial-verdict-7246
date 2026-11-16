import hashlib,hmac,secrets
from datetime import datetime,timezone
def new_salt():return secrets.token_hex(16)
def stable_bucket(salt,user_key,scope):
 digest=hmac.new(str(salt).encode(),(scope+'|'+user_key).encode(),hashlib.sha256).digest()
 return int.from_bytes(digest[:8],'big')/2**64
def experiment_bucket(salt,user_key):return stable_bucket(salt,user_key,'experiment')
def group_bucket(salt,user_key,code):return stable_bucket(salt,user_key,'group|'+code)
def pick_allocation(allocations,value):
 cursor=0.0
 for item in sorted(allocations or [],key=lambda x:x['experiment_id']):
  cursor+=float(item['ratio'])
  if value<cursor:return item
 return None
def normalize(allocations):
 return sorted([{'experiment_id':a.experiment_id,'ratio':round(a.ratio,6)} for a in allocations],key=lambda x:x['experiment_id'])
def group_for(ratio,value):return 'A' if value<ratio else 'B'
def now_iso():return datetime.now(timezone.utc).isoformat()
def parse(value):return datetime.fromisoformat(value)

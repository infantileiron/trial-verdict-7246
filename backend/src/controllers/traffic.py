from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.models.entities import Layer,Experiment,TrafficRule,Assignment,Conflict,Rejection,Event
from src.services.business import dump,audit
from src.services.traffic import new_salt,experiment_bucket,group_bucket,pick_allocation,normalize,group_for,now_iso

def layer(s,i):
 r=s.get(Layer,i)
 if not r:raise HTTPException(404,'流量层不存在')
 return r

def rule(s,i):
 r=s.get(TrafficRule,i)
 if not r:raise HTTPException(404,'分流规则不存在')
 return r

def active_rule(s,layer_id):
 return s.query(TrafficRule).filter_by(layer_id=layer_id,active_slot=1).first()

def rules_for(s,layer_id):
 return s.query(TrafficRule).filter_by(layer_id=layer_id).order_by(TrafficRule.version).all()

def _exp_code(s,experiment_id):
 exp=s.get(Experiment,experiment_id)
 return exp.code if exp else '#'+str(experiment_id)

def rule_dump(s,r):
 d=dump(r);d['experiment_codes']={str(a['experiment_id']):_exp_code(s,a['experiment_id']) for a in (r.allocations or [])}
 return d

def layer_dump(s,l):
 active=active_rule(s,l.id)
 experiments=s.query(Experiment).filter_by(layer_id=l.id).order_by(Experiment.id).all()
 public={k:v for k,v in dump(l).items() if k!='salt'}
 return {**public,'salt_hint':(l.salt or '')[:8],
  'active_rule':rule_dump(s,active) if active else None,
  'rules':[rule_dump(s,r) for r in rules_for(s,l.id)],
  'experiments':[{**dump(e),
   'assigned_active':s.query(Assignment).filter_by(experiment_id=e.id,status='active').count(),
   'assigned_total':s.query(Assignment).filter_by(experiment_id=e.id).count(),
   'exposures':s.query(Event).filter_by(experiment_id=e.id,kind='exposure').count(),
   'conflicts':s.query(Conflict).filter_by(experiment_id=e.id,status='open').count()} for e in experiments]}

def _alloc_pairs(allocations):
 for a in allocations:
  if isinstance(a,dict):yield int(a['experiment_id']),float(a['ratio'])
  else:yield int(a.experiment_id),float(a.ratio)

def validate_allocations(s,l,allocations):
 for experiment_id,_ in _alloc_pairs(allocations):
  exp=s.get(Experiment,experiment_id)
  if not exp:raise HTTPException(422,f'实验 #{experiment_id} 不存在')
  if exp.layer_id!=l.id:raise HTTPException(409,f'实验 {exp.code} 不属于流量层 {l.code}，不能跨层分流')
  if exp.status!='active':raise HTTPException(409,f'实验 {exp.code} 已停用，不能写入新分流规则')
  if datetime.now(timezone.utc)>=datetime.fromisoformat(exp.ends_at):
   raise HTTPException(409,f'实验 {exp.code} 观察期已结束，不能写入新分流规则')

def create_layer(s,data,user):
 if s.query(Layer).filter_by(code=data.code).first():raise HTTPException(409,'流量层编号已存在')
 l=Layer(code=data.code,name=data.name,description=data.description,salt=new_salt(),created_by=user.email,created_at=now_iso())
 s.add(l)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'流量层编号已存在')
 audit(s,user,'建立流量层 '+l.code);return l

def edit_layer(s,l,data,user):
 if data.code!=l.code and s.query(Layer).filter_by(code=data.code).first():raise HTTPException(409,'流量层编号已存在')
 if data.code!=l.code and s.query(TrafficRule).filter_by(layer_id=l.id).first():raise HTTPException(409,'已有分流规则的流量层不可修改编号，盐值需保持稳定')
 l.code=data.code;l.name=data.name;l.description=data.description;s.commit()
 audit(s,user,'修订流量层 '+str(l.id));return l

def _normalized(allocations):
 normalized=normalize(allocations)
 if sum(a['ratio'] for a in normalized)>1.000001:raise HTTPException(422,'分流比例合计超过100%，请核对小数位')
 return normalized

def create_rule(s,l,data,user):
 validate_allocations(s,l,data.allocations)
 allocations=_normalized(data.allocations)
 last=s.query(TrafficRule).filter_by(layer_id=l.id).order_by(TrafficRule.version.desc()).first()
 version=last.version+1 if last else 1
 r=TrafficRule(layer_id=l.id,version=version,status='draft',allocations=allocations,note=data.note,created_by=user.email,created_at=now_iso())
 s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'规则版本冲突，请重试')
 audit(s,user,f'起草分流规则 {l.code} v{version}');return r

def edit_rule(s,r,data,user):
 l=layer(s,r.layer_id)
 if r.status!='draft':raise HTTPException(409,'只有草稿规则可以修改，生效规则已是新来访的分流依据')
 validate_allocations(s,l,data.allocations)
 r.allocations=_normalized(data.allocations);r.note=data.note;s.commit()
 audit(s,user,f'修订分流规则 {l.code} v{r.version}');return r

def activate_rule(s,r,user):
 l=layer(s,r.layer_id)
 if r.status=='active':return r
 if r.status!='draft':raise HTTPException(409,'只有草稿规则可以生效')
 validate_allocations(s,l,r.allocations)
 current=active_rule(s,l.id)
 if current:
  current.status='archived';current.active_slot=None;current.archived_at=now_iso()
 r.status='active';r.active_slot=1;r.activated_by=user.email;r.activated_at=now_iso()
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'规则生效冲突，请重试')
 audit(s,user,f'生效分流规则 {l.code} v{r.version}，既有资格保持不变');return r

def resolve_layer(s,data):
 if data.layer_id:return layer(s,data.layer_id)
 l=s.query(Layer).filter_by(code=data.layer_code).first()
 if not l:raise HTTPException(404,'流量层不存在')
 return l

def _assignment_payload(s,row,reason):
 return {'created':False,'assignment':assignment_view(s,row) if row else None,'experiment':dump(s.get(Experiment,row.experiment_id)) if row else None,'reason':reason}

def assign(s,data,user):
 l=resolve_layer(s,data)
 r=active_rule(s,l.id)
 if not r:raise HTTPException(409,f'流量层 {l.code} 尚无生效分流规则，请先让规则草稿生效')
 # 幂等：同一流量层内已有有效资格时直接返回，重复/并发请求不会重复分配
 current=(s.query(Assignment).filter_by(layer_id=l.id,active_slot=data.user_key).first()
          or s.query(Assignment).filter_by(layer_id=l.id,user_key=data.user_key,status='active').first())
 if current:return _assignment_payload(s,current,'已有生效资格，保持原实验与组别不变')
 bucket=experiment_bucket(l.salt,data.user_key)
 allocation=pick_allocation(r.allocations,bucket)
 total=sum(float(a['ratio']) for a in (r.allocations or []))
 if not allocation:
  return {'created':False,'assignment':None,'experiment':None,'reason':f'未命中层内任何实验（当前规则覆盖 {total*100:.2f}% 流量），保留未分配'}
 exp=s.get(Experiment,allocation['experiment_id'])
 if not exp or exp.status!='active':
  return {'created':False,'assignment':None,'experiment':dump(exp) if exp else None,'reason':f'命中的实验 {_exp_code(s,allocation["experiment_id"])} 已停用，不再接收新参与者'}
 if datetime.now(timezone.utc)>=datetime.fromisoformat(exp.ends_at):
  return {'created':False,'assignment':None,'experiment':dump(exp),'reason':f'实验 {exp.code} 观察期已结束，不再接收新参与者'}
 released=s.query(Assignment).filter_by(experiment_id=exp.id,user_key=data.user_key).first()
 if released:return _assignment_payload(s,released,'该用户在本实验的资格已释放，迟到事件仍归原实验，不重新入组')
 gb=group_bucket(l.salt,data.user_key,exp.code)
 group=group_for(exp.a_ratio,gb)
 row=Assignment(layer_id=l.id,experiment_id=exp.id,user_key=data.user_key,group=group,rule_id=r.id,rule_version=r.version,
                status='active',active_slot=data.user_key,experiment_bucket=bucket,group_bucket=gb,assigned_at=now_iso())
 s.add(row)
 try:s.commit()
 except IntegrityError:
  # 并发写入同一资格（唯一键冲突）：回读胜者，保证只有一个分配
  s.rollback()
  winner=(s.query(Assignment).filter_by(layer_id=l.id,active_slot=data.user_key).first()
          or s.query(Assignment).filter_by(experiment_id=exp.id,user_key=data.user_key).first())
  if not winner:raise HTTPException(409,'分流并发冲突，请重试')
  return _assignment_payload(s,winner,'并发请求已完成分配，返回同一资格')
 audit(s,user,f'分流 {l.code} {data.user_key} → {exp.code}/{group} (v{r.version})')
 return {'created':True,'assignment':assignment_view(s,row),'experiment':dump(exp),'reason':'按生效规则完成稳定分流'}

def stop_experiment(s,exp,user):
 if exp.status=='stopped':return exp
 rows=s.query(Assignment).filter_by(experiment_id=exp.id,status='active').all()
 stamp=now_iso()
 for a in rows:
  a.status='released';a.active_slot=None;a.released_at=stamp;a.release_reason='实验停用释放资格；迟到事件仍归本实验'
 exp.status='stopped';exp.stopped_at=stamp;exp.stopped_by=user.email;s.commit()
 audit(s,user,f'停用实验 {exp.code}，释放 {len(rows)} 个资格')
 return exp

def assignment_view(s,a):
 exp=s.get(Experiment,a.experiment_id)
 return {**dump(a),'experiment_code':exp.code if exp else None,'experiment_name':exp.name if exp else None,
         'experiment_status':exp.status if exp else None,
         'exposures':s.query(Event).filter_by(experiment_id=a.experiment_id,user_key=a.user_key,kind='exposure').count(),
         'activations':s.query(Event).filter_by(experiment_id=a.experiment_id,user_key=a.user_key,kind='activation').count()}

def record_conflict(s,experiment_id,layer_id,user_key,kind,reason,detail=''):
 row=s.query(Conflict).filter_by(experiment_id=experiment_id,user_key=user_key,kind=kind).first()
 if row:
  # 已解除的冲突再次出现时重新打开，继续阻止发布；不新建重复记录
  if row.status=='resolved':
   row.status='open';row.resolved_by='';row.resolved_at='';row.resolution=''
  if len(detail)>=len(row.detail or ''):row.reason=reason;row.detail=detail
  s.commit();return row
 row=Conflict(experiment_id=experiment_id,layer_id=layer_id,user_key=user_key,kind=kind,reason=reason,detail=detail,created=now_iso())
 s.add(row)
 try:s.commit()
 except IntegrityError:
  s.rollback()
  return s.query(Conflict).filter_by(experiment_id=experiment_id,user_key=user_key,kind=kind).first()
 return row

def record_rejection(s,experiment_id,layer_id,user_key,kind,group,reason):
 s.add(Rejection(experiment_id=experiment_id,layer_id=layer_id,user_key=user_key,kind=kind,group=group,reason=reason,created=now_iso()))
 s.commit()

def experiment_conflicts(s,experiment_id,status=None):
 q=s.query(Conflict).filter_by(experiment_id=experiment_id)
 if status:q=q.filter_by(status=status)
 return [dump(c) for c in q.order_by(Conflict.id).all()]

def open_conflicts(s,experiment_id):
 return experiment_conflicts(s,experiment_id,status='open')

def resolve_conflict(s,c,user,resolution):
 if c.status!='open':raise HTTPException(409,'该冲突已处理')
 if len(resolution)<5:raise HTTPException(422,'请填写至少5字排查结论')
 c.status='resolved';c.resolved_by=user.email;c.resolved_at=now_iso();c.resolution=resolution;s.commit()
 audit(s,user,f'解除归属冲突 #{c.id}：{resolution[:80]}');return c

def experiment_rejections(s,experiment_id,limit=50):
 return [dump(c) for c in s.query(Rejection).filter_by(experiment_id=experiment_id).order_by(Rejection.id.desc()).limit(limit).all()]

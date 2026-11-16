from fastapi import HTTPException
from datetime import datetime,timezone
from src.models.entities import Experiment,Event,Decision,Assignment,Layer,TrafficRule
from src.services.business import dump,utc
from src.services.analysis import calculate
from src.controllers.traffic import (active_rule,rule_dump,record_conflict,record_rejection,
                                     experiment_conflicts,open_conflicts)

def experiment(s,i):
 r=s.get(Experiment,i)
 if not r:raise HTTPException(404,'实验不存在')
 return r

def decision(s,i):
 r=s.get(Decision,i)
 if not r:raise HTTPException(404,'裁决记录不存在')
 return r

def _group_conflict(s,exp,user_key,reported,expected,source):
 conflict=record_conflict(s,exp.id,exp.layer_id,user_key,'group_mismatch',
  f'组别与分流资格不符：资格为 {expected}，埋点自报 {reported}',
  f'{source}；实验 {exp.code}；用户 {user_key}；资格组 {expected}；自报组 {reported}')
 return conflict

def _cross_experiment_conflict(s,layer,exp,user_key,other):
 conflict=record_conflict(s,exp.id,layer.id,user_key,'layer_reentry',
  f'同层重复参与：用户已归属实验 {other.code}，又上报本实验事件',
  f'流量层 {layer.code}；用户 {user_key}；已有资格实验 {other.code}；新上报实验 {exp.code}')
 return conflict

def validate_event(s,data):
 exp=experiment(s,data.experiment_id);stamp=utc(data.occurred_at)
 if not (exp.starts_at<=stamp<=exp.ends_at):raise HTTPException(422,'事件时间必须落在实验预定区间内')
 if data.occurred_at>datetime.now(timezone.utc):raise HTTPException(422,'不能录入未来事件')
 prior=s.query(Event).filter_by(experiment_id=exp.id,user_key=data.user_key).all()
 if any(e.kind==data.kind for e in prior):raise HTTPException(409,'重复事件：同一实验/用户/事件类型只记一次')
 assignment=None;late=0;group=data.group
 if exp.layer_id:
  layer=s.get(Layer,exp.layer_id)
  assignment=s.query(Assignment).filter_by(experiment_id=exp.id,user_key=data.user_key).first()
  if not assignment:
   other=(s.query(Assignment).filter(Assignment.layer_id==exp.layer_id,Assignment.status=='active')
          .filter(Assignment.experiment_id!=exp.id,Assignment.user_key==data.user_key).first())
   if other:
    other_exp=s.get(Experiment,other.experiment_id)
    _cross_experiment_conflict(s,layer,exp,data.user_key,other_exp)
    record_rejection(s,exp.id,exp.layer_id,data.user_key,data.kind,group,'拒收：该用户已在同一流量层参与其他实验，归属冲突')
    raise HTTPException(409,f'拒收：同一流量层内用户已归属实验 {other_exp.code}，不能重复参与；冲突已记录')
   record_rejection(s,exp.id,exp.layer_id,data.user_key,data.kind,group,'拒收：用户未在本实验获得分流资格')
   raise HTTPException(422,'拒收：该用户未在本实验获得分流资格，请先调用分流入口或核对埋点用户键')
  if group is None:group=assignment.group
  if group!=assignment.group:
   _group_conflict(s,exp,data.user_key,group,assignment.group,'埋点自报组别与资格冲突')
   record_rejection(s,exp.id,exp.layer_id,data.user_key,data.kind,group,f'拒收：自报组别 {group} 与资格组别 {assignment.group} 不符')
   raise HTTPException(409,f'拒收：自报组别 {group} 与分流资格 {assignment.group} 不符；归属冲突已记录，将阻止发布')
  if assignment.status=='released':
   late=1
 else:
  if group is None:raise HTTPException(422,'非流量层实验必须由埋点自报组别 A 或 B')
  if any(e.group!=group for e in prior):
   _group_conflict(s,exp,data.user_key,group,next(e.group for e in prior if e.group!=group),'同一用户历史事件已属另一组')
   record_rejection(s,exp.id,None,data.user_key,data.kind,group,'拒收：同一用户不能跨组')
   raise HTTPException(409,'同一用户不能跨组，需排查分流污染；冲突已记录，将阻止发布')
 if data.kind=='activation':
  exposure=next((e for e in prior if e.kind=='exposure'),None)
  if not exposure:raise HTTPException(422,'先录入该用户曝光，再录入激活')
  if stamp<exposure.occurred_at:raise HTTPException(422,'激活时间不能早于曝光')
 return stamp,group,assignment,late

def snapshot(s,exp):
 layer=s.get(Layer,exp.layer_id) if exp.layer_id else None
 rule=active_rule(s,layer.id) if layer else None
 rows=s.query(Assignment).filter_by(experiment_id=exp.id).order_by(Assignment.id).all()
 referenced=[]
 for rid in sorted({a.rule_id for a in rows if a.rule_id}):
  ref=s.get(TrafficRule,rid)
  if ref:referenced.append(rule_dump(s,ref))
 layer_snapshot={k:v for k,v in dump(layer).items() if k!='salt'} if layer else None
 return {'experiment':dump(exp),
  'layer':layer_snapshot,
  'rule':rule_dump(s,rule) if rule else None,
  'referenced_rules':referenced,
  'events':[dump(e) for e in s.query(Event).filter_by(experiment_id=exp.id).order_by(Event.id).all()],
  'assignments':[dump(a) for a in rows],
  'conflicts':experiment_conflicts(s,exp.id)}

def publish(s,item,action,user):
 if item.state!='done':raise HTTPException(409,'计算完成后才可发布')
 if item.published_at:raise HTTPException(409,'该裁决已发布，记录不可覆盖')
 if len(item.rationale)<10:raise HTTPException(409,'请先补充至少10字裁决理由')
 live=open_conflicts(s,item.experiment_id)
 if live:raise HTTPException(409,'存在归属冲突，必须先排查后再发布：'+'；'.join(sorted({c['reason'] for c in live}))[:300])
 try:result=calculate(item.snapshot)
 except ValueError as e:raise HTTPException(409,str(e))
 if not result['release_eligible']:raise HTTPException(409,'；'.join(result['block_reasons']))
 if action=='publish_b' and not result['significant_positive']:raise HTTPException(409,'上线B需B−A为正且双侧p<0.05')
 item.action=action;item.published_by=user.email;item.published_at=datetime.now(timezone.utc).isoformat();s.commit();return item

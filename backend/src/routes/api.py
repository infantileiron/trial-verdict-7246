import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response,Query
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Experiment,Event,Decision,Audit,Layer,TrafficRule,Assignment,Conflict,Rejection
from src.schemas.business import (Login,LayerInput,ExperimentInput,RuleInput,AssignInput,EventInput,RunInput,RationaleInput,PublishInput,ResolveInput)
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump,utc
from src.controllers.business import experiment,decision,validate_event,snapshot,publish
from src.controllers import traffic
router=APIRouter()

def _layer_code(s,layer_id):
 l=s.get(Layer,layer_id) if layer_id else None
 return l.code if l else None

@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)

@router.get('/layers')
def layers(u=Depends(current),s=Depends(db)):
 return [traffic.layer_dump(s,l) for l in s.query(Layer).order_by(Layer.id)]
@router.post('/layers')
def create_layer(data:LayerInput,u=Depends(editor),s=Depends(db)):
 return traffic.layer_dump(s,traffic.create_layer(s,data,u))
@router.get('/layers/{i}')
def layer_detail(i:int,u=Depends(current),s=Depends(db)):return traffic.layer_dump(s,traffic.layer(s,i))
@router.put('/layers/{i}')
def edit_layer(i:int,data:LayerInput,u=Depends(editor),s=Depends(db)):
 return traffic.layer_dump(s,traffic.edit_layer(s,traffic.layer(s,i),data,u))
@router.get('/layers/{i}/rules')
def layer_rules(i:int,u=Depends(current),s=Depends(db)):
 l=traffic.layer(s,i);return [traffic.rule_dump(s,r) for r in traffic.rules_for(s,l.id)]
@router.post('/layers/{i}/rules')
def create_rule(i:int,data:RuleInput,u=Depends(editor),s=Depends(db)):
 return traffic.rule_dump(s,traffic.create_rule(s,traffic.layer(s,i),data,u))
@router.put('/rules/{i}')
def edit_rule(i:int,data:RuleInput,u=Depends(editor),s=Depends(db)):
 return traffic.rule_dump(s,traffic.edit_rule(s,traffic.rule(s,i),data,u))
@router.post('/rules/{i}/activate')
def activate_rule(i:int,u=Depends(editor),s=Depends(db)):
 return traffic.rule_dump(s,traffic.activate_rule(s,traffic.rule(s,i),u))
@router.post('/assign')
def assign(data:AssignInput,u=Depends(editor),s=Depends(db)):
 result=traffic.assign(s,data,u)
 if result.get('created'):audit(s,u,f"分流请求 {data.user_key} 新建资格")
 return result
@router.get('/assignments')
def assignments(experiment_id:int|None=None,layer_id:int|None=None,status:str|None=None,user_key:str|None=None,
                limit:int=Query(default=100,ge=1,le=1000),u=Depends(current),s=Depends(db)):
 q=s.query(Assignment)
 if experiment_id:q=q.filter_by(experiment_id=experiment_id)
 if layer_id:q=q.filter_by(layer_id=layer_id)
 if status:q=q.filter_by(status=status)
 if user_key:q=q.filter_by(user_key=user_key)
 return [traffic.assignment_view(s,a) for a in q.order_by(Assignment.id.desc()).limit(limit)]
@router.get('/assignments/{i}')
def assignment_detail(i:int,u=Depends(current),s=Depends(db)):
 a=s.get(Assignment,i)
 if not a:raise HTTPException(404,'分流资格不存在')
 return traffic.assignment_view(s,a)
@router.get('/conflicts')
def conflicts(experiment_id:int|None=None,limit:int=Query(default=100,ge=1,le=1000),u=Depends(current),s=Depends(db)):
 q=s.query(Conflict)
 if experiment_id:q=q.filter_by(experiment_id=experiment_id)
 return [dump(c) for c in q.order_by(Conflict.id).limit(limit)]
@router.post('/conflicts/{i}/resolve')
def resolve_conflict(i:int,data:ResolveInput,u=Depends(editor),s=Depends(db)):
 c=s.get(Conflict,i)
 if not c:raise HTTPException(404,'冲突记录不存在')
 return dump(traffic.resolve_conflict(s,c,u,data.resolution))
@router.get('/rejections')
def rejections(experiment_id:int|None=None,limit:int=Query(default=100,ge=1,le=1000),u=Depends(current),s=Depends(db)):
 q=s.query(Rejection)
 if experiment_id:q=q.filter_by(experiment_id=experiment_id)
 return [dump(c) for c in q.order_by(Rejection.id.desc()).limit(limit)]

@router.get('/experiments')
def experiments(u=Depends(current),s=Depends(db)):
 return [{**dump(e),'exposures':s.query(Event).filter_by(experiment_id=e.id,kind='exposure').count(),
          'layer_code':_layer_code(s,e.layer_id),
          'assigned_active':s.query(Assignment).filter_by(experiment_id=e.id,status='active').count(),
          'conflicts':s.query(Conflict).filter_by(experiment_id=e.id,status='open').count()} for e in s.query(Experiment).order_by(Experiment.id.desc())]
@router.post('/experiments')
def create_experiment(data:ExperimentInput,u=Depends(editor),s=Depends(db)):
 d=data.model_dump();d['starts_at']=utc(data.starts_at);d['ends_at']=utc(data.ends_at)
 if data.layer_id and not s.get(Layer,data.layer_id):raise HTTPException(422,'流量层不存在')
 r=Experiment(**d);s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'实验编号已存在')
 audit(s,u,'建立实验 '+r.code);return dump(r)
@router.put('/experiments/{i}')
def edit_experiment(i:int,data:ExperimentInput,u=Depends(editor),s=Depends(db)):
 r=experiment(s,i)
 if s.query(Event).filter_by(experiment_id=i).first():raise HTTPException(409,'已有事件，禁止事后修改实验定义；请建立新实验')
 if s.query(Assignment).filter_by(experiment_id=i).first():
  if abs(data.a_ratio-r.a_ratio)>1e-9:raise HTTPException(409,'已有分流资格，A组占比是既有组别的依据，不可事后修改；请建立新实验')
  if data.layer_id!=r.layer_id:raise HTTPException(409,'已有分流资格，不可更换流量层；请建立新实验')
 if r.layer_id!=data.layer_id and s.query(TrafficRule).filter_by(layer_id=r.layer_id).first() and r.layer_id:
  raise HTTPException(409,'已有分流规则引用的实验不可更换流量层')
 if data.layer_id and not s.get(Layer,data.layer_id):raise HTTPException(422,'流量层不存在')
 d=data.model_dump();d['starts_at']=utc(data.starts_at);d['ends_at']=utc(data.ends_at)
 for k,v in d.items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'实验编号已存在')
 audit(s,u,'修订实验 '+str(i));return dump(r)
@router.post('/experiments/{i}/stop')
def stop_experiment(i:int,u=Depends(admin),s=Depends(db)):
 exp=traffic.stop_experiment(s,experiment(s,i),u);return dump(exp)

@router.get('/events')
def events(experiment_id:int|None=None,u=Depends(current),s=Depends(db)):
 q=s.query(Event)
 if experiment_id:q=q.filter_by(experiment_id=experiment_id)
 return [dump(e) for e in q.order_by(Event.id.desc())]
@router.post('/events')
def create_event(data:EventInput,u=Depends(editor),s=Depends(db)):
 stamp,group,assignment,late=validate_event(s,data)
 d=data.model_dump();d.update(occurred_at=stamp,group=group,assignment_id=assignment.id if assignment else None,late=late)
 r=Event(**d);s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'该用户事件已存在')
 audit(s,u,'采集事件 '+str(r.id));return dump(r)

@router.get('/decisions')
def decisions(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Decision).order_by(Decision.id.desc())]
@router.post('/decisions')
def run(data:RunInput,u=Depends(editor),s=Depends(db)):
 exp=experiment(s,data.experiment_id)
 if s.query(Decision).filter(Decision.state.in_(['queued','running'])).count()>=10:raise HTTPException(429,'队列已满，请等待任务完成')
 r=Decision(experiment_id=exp.id,snapshot=snapshot(s,exp));s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('trial:decisions',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，恢复后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交实验裁决 '+str(r.id));return dump(r)
@router.get('/decisions/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(decision(s,i))
@router.put('/decisions/{i}/rationale')
def rationale(i:int,data:RationaleInput,u=Depends(editor),s=Depends(db)):
 r=decision(s,i)
 if r.published_at:raise HTTPException(409,'已发布裁决不可覆盖，请重新创建裁决')
 if r.state!='done':raise HTTPException(409,'计算完成后再填写裁决理由')
 r.rationale=data.rationale;s.commit();audit(s,u,'保存裁决理由 '+str(i));return dump(r)
@router.post('/decisions/{i}/publish')
def release(i:int,data:PublishInput,u=Depends(admin),s=Depends(db)):
 r=publish(s,decision(s,i),data.action,u);audit(s,u,'发布裁决 '+str(i)+' '+data.action);return dump(r)
@router.get('/decisions/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=decision(s,i)
 if r.state!='done':raise HTTPException(409,'尚无成功结果')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="trial-verdict-{i}.json"'})
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]

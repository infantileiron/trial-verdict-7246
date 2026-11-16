from datetime import datetime,timezone,timedelta
from src.config.database import Base,engine,Session
from src.models.entities import User,Layer,Experiment,TrafficRule,Assignment,Event
from src.utils.security import hash_password
from src.services.traffic import new_salt,experiment_bucket,group_bucket

SEED_USERS=1200
def seed():
 Base.metadata.create_all(engine)
 with Session() as s:
  for role in ('admin','analyst','viewer'):
   email=role+'@demo.local'
   if not s.query(User).filter_by(email=email).first():s.add(User(email=email,role=role,password=hash_password('Demo123!')))
  s.commit()
  today=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
  layer=s.query(Layer).filter_by(code='onboarding').first()
  if not layer:
   layer=Layer(code='onboarding',name='首次引导流量层',description='首次引导相关实验共享同一流量层，同一用户层内只进入一个实验',salt=new_salt(),created_by='seed',created_at=datetime.now(timezone.utc).isoformat());s.add(layer);s.flush()
  experiments={}
  for code,name,ends in [('LJ-1115-A','首次引导文案 / 成熟对照',today-timedelta(days=1)),('LJ-1115-B','快捷模板入口 / 观察中',today+timedelta(days=3))]:
   exp=s.query(Experiment).filter_by(code=code).first()
   if not exp:
    exp=Experiment(code=code,name=name,starts_at=(today-timedelta(days=7)).isoformat(),ends_at=ends.isoformat(),a_ratio=.5,hypothesis='更清晰的首次引导能提升首次激活率，预先固定观察期与分流比例',layer_id=layer.id,status='active');s.add(exp);s.flush()
   elif exp.layer_id is None:
    exp.layer_id=layer.id
   experiments[code]=exp
  s.commit()
  exp_a=experiments['LJ-1115-A'];exp_b=experiments['LJ-1115-B']
  rule=s.query(TrafficRule).filter_by(layer_id=layer.id,version=1).first()
  if not rule:
   # v1 是历史分流依据：成熟实验与观察中实验各占50%，1200 个既有资格均引用它
   rule=TrafficRule(layer_id=layer.id,version=1,status='archived',
                    allocations=sorted([{'experiment_id':exp_a.id,'ratio':.5},{'experiment_id':exp_b.id,'ratio':.5}],key=lambda x:x['experiment_id']),
                    note='历史规则：成熟实验与观察中实验各占50%流量',created_by='seed',created_at=(today+timedelta(hours=1)).isoformat(),
                    activated_by='seed',activated_at=(today+timedelta(hours=1)).isoformat(),archived_at=(today+timedelta(hours=2)).isoformat());s.add(rule);s.flush()
  if not s.query(TrafficRule).filter_by(layer_id=layer.id,version=2).first():
   # v2 为当前生效规则：成熟实验观察期已结束，新来访只进入观察中的实验
   current=TrafficRule(layer_id=layer.id,version=2,status='active',active_slot=1,
                    allocations=[{'experiment_id':exp_b.id,'ratio':1.0}],
                    note='成熟实验观察期结束后，新来访全部进入观察中实验；既有资格不迁移',created_by='seed',created_at=(today+timedelta(hours=2)).isoformat(),
                    activated_by='seed',activated_at=(today+timedelta(hours=2)).isoformat());s.add(current);s.flush()
  stamp=(datetime.fromisoformat(exp_a.starts_at)+timedelta(hours=12)).isoformat()
  if s.query(Assignment).filter_by(experiment_id=exp_a.id).first():
   rows={a.user_key:a for a in s.query(Assignment).filter_by(experiment_id=exp_a.id).all()}
   for key,a in rows.items():
    for kind in ['exposure']+(['activation'] if s.query(Event).filter_by(experiment_id=exp_a.id,user_key=key,kind='activation').first() else []):
     if not s.query(Event).filter_by(experiment_id=exp_a.id,user_key=key,kind=kind).first():
      s.add(Event(experiment_id=exp_a.id,user_key=key,group=a.group,kind=kind,occurred_at=stamp,assignment_id=a.id))
   s.commit();print({'users':s.query(User).count(),'experiments':s.query(Experiment).count(),'layers':s.query(Layer).count(),'rules':s.query(TrafficRule).count(),'assignments':s.query(Assignment).count(),'events':s.query(Event).count()});return
  # 演示数据固定 600/600，便于核对 A=20%、B=30% 与 SRM p=1；真实分流使用流量层盐值稳定哈希
  members={'A':[],'B':[]}
  for i in range(SEED_USERS):
   key=f'LJ-U{i+1:04}'
   group='A' if i<600 else 'B'
   row=Assignment(layer_id=layer.id,experiment_id=exp_a.id,user_key=key,group=group,rule_id=rule.id,rule_version=rule.version,
                  status='active',active_slot=key,experiment_bucket=experiment_bucket(layer.salt,key),
                  group_bucket=group_bucket(layer.salt,key,exp_a.code),assigned_at=stamp)
   s.add(row);s.flush();members[group].append((key,row.id))
  for group,target in [('A',.2),('B',.3)]:
   activated=round(len(members[group])*target)
   for idx,(key,aid) in enumerate(members[group]):
    s.add(Event(experiment_id=exp_a.id,user_key=key,group=group,kind='exposure',occurred_at=stamp,assignment_id=aid))
    if idx<activated:s.add(Event(experiment_id=exp_a.id,user_key=key,group=group,kind='activation',occurred_at=stamp,assignment_id=aid))
  s.commit()
  print({'users':s.query(User).count(),'experiments':s.query(Experiment).count(),'layers':s.query(Layer).count(),
         'rules':s.query(TrafficRule).count(),'assignments':s.query(Assignment).count(),'events':s.query(Event).count()})
if __name__=='__main__':seed()

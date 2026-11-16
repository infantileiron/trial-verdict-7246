from sqlalchemy import Column,Integer,String,Float,JSON,Text,UniqueConstraint,ForeignKey
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class Layer(Base):
 __tablename__='layers'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True);name=Column(String(120));description=Column(Text,default='');salt=Column(String(64));created_by=Column(String(100),default='');created_at=Column(String(40),default='')
class Experiment(Base):
 __tablename__='experiments'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True);name=Column(String(120));starts_at=Column(String(40));ends_at=Column(String(40));a_ratio=Column(Float);hypothesis=Column(Text)
 layer_id=Column(Integer,ForeignKey('layers.id'),nullable=True);status=Column(String(20),default='active');stopped_at=Column(String(40),default='');stopped_by=Column(String(100),default='')
class TrafficRule(Base):
 __tablename__='traffic_rules'
 __table_args__=(UniqueConstraint('layer_id','version',name='uq_rule_layer_version'),UniqueConstraint('layer_id','active_slot',name='uq_rule_layer_active'))
 id=Column(Integer,primary_key=True);layer_id=Column(Integer,ForeignKey('layers.id'));version=Column(Integer);status=Column(String(20),default='draft');allocations=Column(JSON);note=Column(Text,default='');created_by=Column(String(100),default='');created_at=Column(String(40),default='');activated_by=Column(String(100),default='');activated_at=Column(String(40),default='');archived_at=Column(String(40),default='');active_slot=Column(Integer,nullable=True)
class Assignment(Base):
 __tablename__='assignments'
 __table_args__=(UniqueConstraint('experiment_id','user_key',name='uq_assign_exp_user'),UniqueConstraint('layer_id','active_slot',name='uq_assign_layer_active'))
 id=Column(Integer,primary_key=True);layer_id=Column(Integer,ForeignKey('layers.id'));experiment_id=Column(Integer,ForeignKey('experiments.id'));user_key=Column(String(80));group=Column(String(1));rule_id=Column(Integer,ForeignKey('traffic_rules.id'),nullable=True);rule_version=Column(Integer,default=0);status=Column(String(20),default='active');active_slot=Column(String(80),nullable=True);experiment_bucket=Column(Float,nullable=True);group_bucket=Column(Float,nullable=True);assigned_at=Column(String(40),default='');released_at=Column(String(40),default='');release_reason=Column(Text,default='')
class Event(Base):
 __tablename__='events'
 __table_args__=(UniqueConstraint('experiment_id','user_key','kind',name='uq_exp_user_kind'),)
 id=Column(Integer,primary_key=True);experiment_id=Column(Integer,ForeignKey('experiments.id'));user_key=Column(String(80));group=Column(String(1));kind=Column(String(20));occurred_at=Column(String(40));assignment_id=Column(Integer,ForeignKey('assignments.id'),nullable=True);late=Column(Integer,default=0)
class Decision(Base):
 __tablename__='decisions'
 id=Column(Integer,primary_key=True);experiment_id=Column(Integer,ForeignKey('experiments.id'));state=Column(String(20),default='queued');snapshot=Column(JSON);result=Column(JSON,nullable=True);error=Column(Text,default='');rationale=Column(Text,default='');action=Column(String(30),default='');published_by=Column(String(100),default='');published_at=Column(String(40),default='')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40),default='')
class Conflict(Base):
 __tablename__='conflicts'
 __table_args__=(UniqueConstraint('experiment_id','user_key','kind',name='uq_conflict_exp_user_kind'),)
 id=Column(Integer,primary_key=True);experiment_id=Column(Integer,ForeignKey('experiments.id'));layer_id=Column(Integer,ForeignKey('layers.id'),nullable=True);user_key=Column(String(80));kind=Column(String(40));reason=Column(Text,default='');detail=Column(Text,default='');status=Column(String(20),default='open');resolved_by=Column(String(100),default='');resolved_at=Column(String(40),default='');resolution=Column(Text,default='');created=Column(String(40),default='')
class Rejection(Base):
 __tablename__='rejections'
 id=Column(Integer,primary_key=True);experiment_id=Column(Integer,ForeignKey('experiments.id'),nullable=True);layer_id=Column(Integer,ForeignKey('layers.id'),nullable=True);user_key=Column(String(80));kind=Column(String(20));group=Column(String(1),nullable=True);reason=Column(Text);created=Column(String(40),default='')

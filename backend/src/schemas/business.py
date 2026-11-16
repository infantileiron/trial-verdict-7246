from datetime import datetime
from typing import Literal
from pydantic import BaseModel,Field,model_validator
class Login(BaseModel):
 email:str;password:str
class LayerInput(BaseModel):
 code:str=Field(min_length=1,max_length=60);name:str=Field(min_length=2,max_length=120);description:str=Field(default='',max_length=1000)
class ExperimentInput(BaseModel):
 code:str=Field(min_length=1,max_length=60);name:str=Field(min_length=2,max_length=120)
 starts_at:datetime;ends_at:datetime;a_ratio:float=Field(default=.5,ge=.1,le=.9,allow_inf_nan=False)
 hypothesis:str=Field(min_length=5,max_length=2000);layer_id:int|None=Field(default=None,gt=0)
 @model_validator(mode='after')
 def dates(self):
  if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:raise ValueError('时间必须带时区')
  if self.ends_at<=self.starts_at:raise ValueError('结束时间必须晚于开始时间')
  return self
class AllocationInput(BaseModel):
 experiment_id:int=Field(gt=0);ratio:float=Field(gt=0,le=1,allow_inf_nan=False)
class RuleInput(BaseModel):
 allocations:list[AllocationInput]=Field(min_length=1,max_length=20);note:str=Field(default='',max_length=1000)
 @model_validator(mode='after')
 def ratios(self):
  ids=[a.experiment_id for a in self.allocations]
  if len(ids)!=len(set(ids)):raise ValueError('同一实验在同一规则中只能出现一次')
  if sum(a.ratio for a in self.allocations)>1.000001:raise ValueError('分流比例合计不能超过100%')
  return self
class AssignInput(BaseModel):
 layer_id:int|None=Field(default=None,gt=0);layer_code:str|None=Field(default=None,max_length=60);user_key:str=Field(min_length=1,max_length=80)
 @model_validator(mode='after')
 def target(self):
  if bool(self.layer_id)==bool(self.layer_code):raise ValueError('请且仅请指定 layer_id 或 layer_code')
  return self
class EventInput(BaseModel):
 experiment_id:int=Field(gt=0);user_key:str=Field(min_length=1,max_length=80);group:Literal['A','B']|None=None;kind:Literal['exposure','activation'];occurred_at:datetime
 @model_validator(mode='after')
 def dates(self):
  if self.occurred_at.tzinfo is None:raise ValueError('事件时间必须带时区')
  return self
class ResolveInput(BaseModel):
 resolution:str=Field(min_length=5,max_length=1000)
class RunInput(BaseModel):
 experiment_id:int=Field(gt=0)
class RationaleInput(BaseModel):
 rationale:str=Field(min_length=10,max_length=3000)
class PublishInput(BaseModel):
 action:Literal['publish_b','keep_a']

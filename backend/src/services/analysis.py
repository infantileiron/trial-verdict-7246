import math,hashlib,json
from datetime import datetime,timezone
from scipy.stats import norm,chi2
VERSION='two-proportion-srm-2.0'
def _checksum(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def _reconcile(events,assignments):
 by_user={}
 for a in assignments or []:by_user.setdefault(a['user_key'],a)
 exposure_group={}
 for e in events:
  if e['kind']=='exposure':exposure_group.setdefault(e['user_key'],e['group'])
 assigned={'A':0,'B':0}
 for a in by_user.values():assigned[a['group']]=assigned.get(a['group'],0)+1
 assigned_users=set(by_user);exposure_users=set(exposure_group)
 unexposed=sorted(assigned_users-exposure_users)
 unassigned=sorted(exposure_users-assigned_users)
 wrong=sum(1 for u in exposure_users&assigned_users if by_user[u]['group']!=exposure_group[u])
 # 层内独占：每个用户最多只能有一个生效资格
 by_active={}
 for a in assignments or []:
  if a.get('status')=='active':by_active.setdefault(a['user_key'],[]).append(a['experiment_id'])
 multi=sorted(u for u,ids in by_active.items() if len(ids)>1)
 return assigned,unexposed,unassigned,wrong,multi

def calculate(snapshot):
 exp=snapshot['experiment'];events=snapshot['events'];assignments=snapshot.get('assignments') or []
 conflicts=[c for c in (snapshot.get('conflicts') or []) if c.get('status','open')=='open']
 exposed={g:{e['user_key'] for e in events if e['group']==g and e['kind']=='exposure'} for g in ('A','B')}
 activated={g:{e['user_key'] for e in events if e['group']==g and e['kind']=='activation'}&exposed[g] for g in ('A','B')}
 n0,n1=[len(exposed[g]) for g in ('A','B')];c0,c1=[len(activated[g]) for g in ('A','B')]
 if min(n0,n1)<2:raise ValueError('每组至少需2名曝光用户，无法计算当前比例差异')
 p0,p1=c0/n0,c1/n1;delta=p1-p0;pooled=(c0+c1)/(n0+n1)
 se0=math.sqrt(pooled*(1-pooled)*(1/n0+1/n1))
 z=delta/se0 if se0 else 0.0;pv=float(2*norm.sf(abs(z))) if se0 else 1.0
 se=math.sqrt(p0*(1-p0)/n0+p1*(1-p1)/n1);margin=float(norm.ppf(.975))*se
 expected=[(n0+n1)*exp['a_ratio'],(n0+n1)*(1-exp['a_ratio'])]
 srm=sum((a-b)**2/b for a,b in zip([n0,n1],expected));srm_p=float(chi2.sf(srm,1))
 approximate=min(n0,n1)>=20 and min(c0,n0-c0,c1,n1-c1)>=5
 matured=datetime.now(timezone.utc)>=datetime.fromisoformat(exp['ends_at'])
 assigned,unexposed,unassigned,wrong,multi=_reconcile(events,assignments)
 layer=snapshot.get('layer');rule=snapshot.get('rule')
 late_events=[e for e in events if e.get('late')]
 reasons=[]
 if not matured:reasons.append('实验未到预定结束时间，禁止提前发布')
 if srm_p<.01:reasons.append('SRM p<0.01，先排查随机分流与事件采集')
 if not approximate:reasons.append('每组至少20人且成功/失败各至少5人，当前不满足正态近似')
 if conflicts:reasons.append(f'冻结快照记录了 {len(conflicts)} 条未解除归属冲突，需排查并重新提交裁决')
 if wrong:reasons.append(f'有 {wrong} 名用户的曝光组别与分流资格不一致，先排查采集链路')
 if multi:reasons.append(f'有 {len(multi)} 名用户在同一流量层持有多个生效资格，违背独占分流')
 referenced=snapshot.get('referenced_rules') or []
 rule_basis={'layer_code':layer['code'] if layer else None,'rule_version':rule['version'] if rule else None,
             'allocations':rule['allocations'] if rule else None,
             'allocation_codes':{str(k):v for k,v in (rule.get('experiment_codes') or {}).items()} if rule else {},'referenced_allocations':{str(r['version']):{str(k):v for k,v in (r.get('experiment_codes') or {}).items()} for r in referenced},'activated_at':rule['activated_at'] if rule else None,
             'rule_checksum':_checksum(rule) if rule else None,'a_ratio':exp['a_ratio'],
             'referenced_versions':sorted({r['version'] for r in referenced}),
             'referenced_checksums':{str(r['version']):_checksum(r) for r in referenced}}
 reconciliation={'assigned_a':assigned['A'],'assigned_b':assigned['B'],'assigned_total':assigned['A']+assigned['B'],
                 'exposed_a':n0,'exposed_b':n1,
                 'active_assignments':len([a for a in assignments if a.get('status')=='active']),
                 'released_assignments':len([a for a in assignments if a.get('status')=='released']),
                 'assigned_without_exposure':len(unexposed),'exposure_without_assignment':len(unassigned),
                 'group_mismatch':wrong,'late_events':len(late_events),'conflicts':len(conflicts),'multi_assignment':len(multi),
                 'assigned_without_exposure_users':unexposed[:20],'exposure_without_assignment_users':unassigned[:20],'multi_assignment_users':multi[:20]}
 return dict(n_a=n0,n_b=n1,activated_a=c0,activated_b=c1,rate_a=p0,rate_b=p1,difference=delta,relative_lift=delta/p0 if p0 else None,z=z,p_value=pv,ci_low=max(-1,delta-margin),ci_high=min(1,delta+margin),srm_chi2=srm,srm_p=srm_p,expected_a=expected[0],expected_b=expected[1],matured=matured,approximation_valid=approximate,release_eligible=not reasons,block_reasons=reasons,significant_positive=delta>0 and pv<.05,version=VERSION,checksum=_checksum(snapshot),assigned_a=assigned['A'],assigned_b=assigned['B'],rule_basis=rule_basis,reconciliation=reconciliation,conflict_count=len(conflicts),method='双侧双比例z检验；95%绝对提升Wald区间；SRM卡方df=1；曝光用户去重，激活为曝光子集；差异方向B−A')

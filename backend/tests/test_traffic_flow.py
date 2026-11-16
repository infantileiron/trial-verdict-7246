"""流量层分流、资格独占、事件采集、停用与裁决冻结的端到端测试。

运行：cd backend && python -m unittest discover -s tests -v
使用临时 SQLite 与内存假 Redis，不依赖 MySQL/Redis 服务。
每个用例自建独立的流量层，避免共享演示数据的副作用。
"""
import os
import sys
import tempfile
import unittest
import concurrent.futures
from datetime import datetime, timedelta, timezone

TMP = tempfile.mkdtemp(prefix='trial-verdict-test-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(TMP, 'test.db')
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ['SEED_DEMO'] = 'false'

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import src.routes.api as api_module


class FakeRedis:
    pushed = []

    @classmethod
    def from_url(cls, *args, **kwargs):
        return cls()

    def rpush(self, key, value):
        FakeRedis.pushed.append((key, value))
        return 1

    def ping(self):
        return True


api_module.redis.Redis = FakeRedis

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402
from src.config.database import Base, Session, engine  # noqa: E402
from src.models.entities import Decision  # noqa: E402
from src.services.analysis import calculate  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

SHANGHAI = timezone(timedelta(hours=8))


def iso(hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).astimezone(SHANGHAI).isoformat()


class TrafficFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)
        seed()
        cls.c = TestClient(app)
        cls.analyst = cls.auth('analyst@demo.local')
        cls.admin = cls.auth('admin@demo.local')
        cls.viewer = cls.auth('viewer@demo.local')
        cls.seed_layer = cls.c.get('/layers', headers=cls.analyst).json()[0]
        cls.seed_a = next(e for e in cls.seed_layer['experiments'] if e['code'] == 'LJ-1115-A')
        cls.seed_b = next(e for e in cls.seed_layer['experiments'] if e['code'] == 'LJ-1115-B')

    @classmethod
    def auth(cls, email):
        r = cls.c.post('/auth/login', json={'email': email, 'password': 'Demo123!'})
        assert r.status_code == 200, r.text
        return {'Authorization': 'Bearer ' + r.json()['token']}

    # ---- fixtures -------------------------------------------------------
    _seq = 0

    def unique(self, prefix):
        TrafficFlowTest._seq += 1
        return f'{prefix}-{TrafficFlowTest._seq}'

    def make_layer(self):
        code = self.unique('layer')
        r = self.c.post('/layers', json={'code': code, 'name': '测试流量层 ' + code}, headers=self.analyst)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def make_experiment(self, layer_id, start_h=-48, end_h=48, a_ratio=0.5):
        code = self.unique('EXP')
        r = self.c.post('/experiments', json={
            'code': code, 'name': '测试实验 ' + code, 'starts_at': iso(start_h), 'ends_at': iso(end_h),
            'a_ratio': a_ratio, 'hypothesis': '验证稳定分流与独占资格', 'layer_id': layer_id}, headers=self.analyst)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def make_rule(self, layer_id, allocations, activate=True):
        r = self.c.post(f'/layers/{layer_id}/rules', json={'allocations': allocations, 'note': '测试规则'}, headers=self.analyst)
        self.assertEqual(r.status_code, 200, r.text)
        rule = r.json()
        if activate:
            r = self.c.post(f"/rules/{rule['id']}/activate", headers=self.analyst)
            self.assertEqual(r.status_code, 200, r.text)
            rule = r.json()
        return rule

    def assign(self, layer_code, user):
        r = self.c.post('/assign', json={'layer_code': layer_code, 'user_key': user}, headers=self.analyst)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def event(self, experiment_id, user, kind, occurred_at=None, group=None):
        body = {'experiment_id': experiment_id, 'user_key': user, 'kind': kind, 'occurred_at': occurred_at or iso(-1)}
        if group is not None:
            body['group'] = group
        return self.c.post('/events', json=body, headers=self.analyst)

    def single_layer(self, name='single', start_h=-48, end_h=48, a_ratio=0.5):
        layer = self.make_layer()
        exp = self.make_experiment(layer['id'], start_h, end_h, a_ratio)
        self.make_rule(layer['id'], [{'experiment_id': exp['id'], 'ratio': 1.0}])
        return layer, exp

    def populated_experiment(self, name='publish', users=120, start_h=-7 * 24, end_h=1):
        """成熟实验：先在观察期内采集每组约60人/20人激活，再把观察期结束时间移到过去。"""
        layer, exp = self.single_layer(name, start_h=start_h, end_h=end_h)
        stamp = (datetime.now(timezone.utc) + timedelta(hours=start_h + 12)).astimezone(SHANGHAI).isoformat()
        groups = {'A': [], 'B': []}
        for i in range(users):
            result = self.assign(layer['code'], f'{name}-U{i:04d}')
            self.assertIsNotNone(result['assignment'], result)
            groups[result['assignment']['group']].append((f'{name}-U{i:04d}', result['assignment']))
        for group, rows in groups.items():
            for idx, (user, assignment) in enumerate(rows):
                self.assertEqual(self.event(exp['id'], user, 'exposure', stamp).status_code, 200)
                if idx < 20:
                    self.assertEqual(self.event(exp['id'], user, 'activation', stamp).status_code, 200)
        self.expire(exp['id'])
        return layer, exp, groups

    def expire(self, experiment_id, hours_ago=1):
        from src.models.entities import Experiment
        with Session() as s:
            row = s.get(Experiment, experiment_id)
            row.ends_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
            s.commit()

    # ---- tests ----------------------------------------------------------
    def test_01_seed_layer_and_rule_history(self):
        layers = self.c.get('/layers', headers=self.analyst).json()
        self.assertEqual(len(layers), 1)
        layer = layers[0]
        self.assertEqual(layer['code'], 'onboarding')
        self.assertEqual(layer['active_rule']['version'], 2)
        self.assertEqual([r['version'] for r in layer['rules']], [1, 2])
        self.assertEqual(layer['rules'][0]['status'], 'archived')
        self.assertTrue(layer['rules'][0]['activated_at'])
        self.assertTrue(layer['rules'][0]['archived_at'])
        self.assertEqual(layer['experiments'][0]['assigned_active'], 1200)
        self.assertEqual(layer['active_rule']['allocations'], [{'experiment_id': self.seed_b['id'], 'ratio': 1.0}])

    def test_02_assign_stable_and_idempotent(self):
        layer, exp = self.single_layer()
        first = self.assign(layer['code'], 'STABLE-1')
        second = self.assign(layer['code'], 'STABLE-1')
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertEqual(first['assignment']['id'], second['assignment']['id'])
        self.assertEqual(first['assignment']['experiment_bucket'], second['assignment']['experiment_bucket'])
        self.assertEqual(first['assignment']['group_bucket'], second['assignment']['group_bucket'])
        self.assertIn(first['assignment']['group'], ('A', 'B'))
        self.assertAlmostEqual(first['assignment']['experiment_bucket'], 0.0, delta=1.0)

    def test_03_concurrent_assign_single_row(self):
        layer, _ = self.single_layer()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.assign(layer['code'], 'RACE-1'), range(8)))
        ids = {r['assignment']['id'] for r in results}
        self.assertEqual(len(ids), 1)
        rows = self.c.get('/assignments?user_key=RACE-1&limit=50', headers=self.analyst).json()
        self.assertEqual(len(rows), 1)

    def test_04_rule_draft_activation_keeps_existing(self):
        layer = self.make_layer()
        exp_one = self.make_experiment(layer['id'])
        exp_two = self.make_experiment(layer['id'])
        v1 = self.make_rule(layer['id'], [{'experiment_id': exp_one['id'], 'ratio': 1.0}])
        old = self.assign(layer['code'], 'OLD-1')
        self.assertEqual(old['assignment']['rule_version'], v1['version'])
        draft = self.make_rule(layer['id'], [{'experiment_id': exp_one['id'], 'ratio': 0.4}, {'experiment_id': exp_two['id'], 'ratio': 0.6}], activate=False)
        self.assertEqual(draft['status'], 'draft')
        self.assertEqual(draft['version'], 2)
        mid = self.assign(layer['code'], 'MID-1')
        self.assertEqual(mid['assignment']['rule_version'], 1)
        edited = self.c.put(f"/rules/{draft['id']}", json={'allocations': [{'experiment_id': exp_one['id'], 'ratio': 0.5}, {'experiment_id': exp_two['id'], 'ratio': 0.5}], 'note': '修订草稿'}, headers=self.analyst)
        self.assertEqual(edited.status_code, 200, edited.text)
        activated = self.c.post(f"/rules/{draft['id']}/activate", headers=self.analyst)
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()['status'], 'active')
        kept = self.assign(layer['code'], 'OLD-1')
        self.assertFalse(kept['created'])
        self.assertEqual(kept['assignment']['id'], old['assignment']['id'])
        self.assertEqual(kept['assignment']['rule_version'], 1)
        again = self.assign(layer['code'], 'MID-1')
        self.assertEqual(again['assignment']['rule_version'], 1)
        fresh = self.assign(layer['code'], 'FRESH-1')
        self.assertTrue(fresh['created'])
        self.assertEqual(fresh['assignment']['rule_version'], 2)
        self.assertIn(fresh['assignment']['experiment_id'], (exp_one['id'], exp_two['id']))
        rewrite = self.c.put(f"/rules/{draft['id']}", json={'allocations': [{'experiment_id': exp_two['id'], 'ratio': 1}]}, headers=self.analyst)
        self.assertEqual(rewrite.status_code, 409)
        history = self.c.get(f"/layers/{layer['id']}/rules", headers=self.analyst).json()
        self.assertEqual([h['version'] for h in history], [1, 2])
        self.assertEqual(history[0]['status'], 'archived')
        self.assertEqual(history[1]['activated_by'], 'analyst@demo.local')
        self.assertTrue(history[1]['activated_at'])
        self.assertEqual(history[1]['experiment_codes'][str(exp_two['id'])], exp_two['code'])

    def test_05_events_require_matching_assignment(self):
        layer, exp = self.single_layer()
        assigned = self.assign(layer['code'], 'EVENT-1')
        group = assigned['assignment']['group']
        ok = self.event(exp['id'], 'EVENT-1', 'exposure', iso(-1))
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()['group'], group)
        self.assertEqual(ok.json()['assignment_id'], assigned['assignment']['id'])
        wrong = 'B' if group == 'A' else 'A'
        bad = self.event(exp['id'], 'EVENT-1', 'activation', iso(-1), group=wrong)
        self.assertEqual(bad.status_code, 409)
        self.assertIn('拒收', bad.json()['detail'])
        ghost = self.event(exp['id'], 'EVENT-GHOST', 'exposure', iso(-1), group='A')
        self.assertEqual(ghost.status_code, 422)
        self.assertIn('未在本实验获得分流资格', ghost.json()['detail'])
        rejections = self.c.get(f"/rejections?experiment_id={exp['id']}", headers=self.analyst).json()
        self.assertGreaterEqual(len(rejections), 2)
        self.assertTrue(all(r['reason'] for r in rejections))
        conflicts = self.c.get(f"/conflicts?experiment_id={exp['id']}", headers=self.analyst).json()
        self.assertTrue(any(c['kind'] == 'group_mismatch' and c['status'] == 'open' for c in conflicts))

    def test_06_cross_experiment_reentry_conflict(self):
        layer = self.make_layer()
        exp_one = self.make_experiment(layer['id'])
        exp_two = self.make_experiment(layer['id'])
        self.make_rule(layer['id'], [{'experiment_id': exp_one['id'], 'ratio': 0.5}, {'experiment_id': exp_two['id'], 'ratio': 0.5}])
        first = self.assign(layer['code'], 'REENTRY-1')
        other = exp_two['id'] if first['assignment']['experiment_id'] == exp_one['id'] else exp_one['id']
        r = self.event(other, 'REENTRY-1', 'exposure', iso(-1), group=first['assignment']['group'])
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn('同一流量层', r.json()['detail'])
        conflicts = self.c.get(f'/conflicts?experiment_id={other}', headers=self.analyst).json()
        self.assertTrue(any(c['kind'] == 'layer_reentry' and c['status'] == 'open' for c in conflicts))

    def test_07_stop_releases_and_late_events_stay(self):
        layer, exp = self.single_layer()
        assigned = self.assign(layer['code'], 'STOP-1')
        self.assertEqual(self.event(exp['id'], 'STOP-1', 'exposure', iso(-2)).status_code, 200)
        # 另一名用户在停用前也获得资格并曝光，用于验证释放后的迟到事件
        late_member = self.assign(layer['code'], 'STOP-2')
        self.assertEqual(late_member['assignment']['experiment_id'], exp['id'])
        self.assertEqual(self.event(exp['id'], 'STOP-2', 'exposure', iso(-2)).status_code, 200)
        stop = self.c.post(f"/experiments/{exp['id']}/stop", headers=self.admin)
        self.assertEqual(stop.status_code, 200, stop.text)
        self.assertEqual(stop.json()['status'], 'stopped')
        rows = self.c.get(f"/assignments?experiment_id={exp['id']}&limit=100", headers=self.analyst).json()
        self.assertTrue(rows and all(a['status'] == 'released' and a['release_reason'] for a in rows))
        late = self.event(exp['id'], 'STOP-1', 'activation', iso(-1), group=assigned['assignment']['group'])
        self.assertEqual(late.status_code, 200, late.text)
        self.assertEqual(late.json()['late'], 1)
        self.assertEqual(late.json()['assignment_id'], assigned['assignment']['id'])
        wrong = 'B' if assigned['assignment']['group'] == 'A' else 'A'
        self.assertEqual(self.event(exp['id'], 'STOP-1', 'activation', iso(-1), group=wrong).status_code, 409)
        after = self.assign(layer['code'], 'STOP-1')
        self.assertFalse(after['assignment'] and after['assignment']['experiment_id'] == exp['id'] and after['assignment']['status'] == 'active')
        self.assertIn('已停用', after['reason'])
        # 停用实验不能再写入新规则
        blocked_rule = self.c.post(f"/layers/{layer['id']}/rules", json={'allocations': [{'experiment_id': exp['id'], 'ratio': 1}]}, headers=self.analyst)
        self.assertEqual(blocked_rule.status_code, 409)
        # 同层重新分配后可进入新实验；原实验的迟到事件仍锁原实验与原组别
        successor = self.make_experiment(layer['id'])
        self.make_rule(layer['id'], [{'experiment_id': successor['id'], 'ratio': 1.0}])
        moved = self.assign(layer['code'], 'STOP-1')
        self.assertEqual(moved['assignment']['experiment_id'], successor['id'])
        self.assertEqual(moved['assignment']['rule_version'], 2)
        late_user = self.assign(layer['code'], 'STOP-2')
        self.assertEqual(late_user['assignment']['experiment_id'], successor['id'])
        still_old = self.event(exp['id'], 'STOP-2', 'activation', iso(-1))
        self.assertEqual(still_old.status_code, 200, still_old.text)
        self.assertEqual(still_old.json()['experiment_id'], exp['id'])
        self.assertEqual(still_old.json()['group'], late_member['assignment']['group'])
        self.assertEqual(still_old.json()['late'], 1)
        # 原实验的迟到事件不能用新实验的组别改写
        other_group = 'B' if late_member['assignment']['group'] == 'A' else 'A'
        self.assertEqual(self.event(exp['id'], 'STOP-2', 'activation', iso(-1), group=other_group).status_code, 409)
        # 新实验的事件使用新资格，不串到旧实验
        fresh_event = self.event(successor['id'], 'STOP-1', 'exposure', iso(-1))
        self.assertEqual(fresh_event.status_code, 200, fresh_event.text)
        self.assertEqual(fresh_event.json()['assignment_id'], moved['assignment']['id'])

    def test_08_decision_freezes_rule_and_reconciles(self):
        layer, exp, groups = self.populated_experiment('freeze')
        rule = self.c.get(f"/layers/{layer['id']}", headers=self.analyst).json()['active_rule']
        created = self.c.post('/decisions', json={'experiment_id': exp['id']}, headers=self.analyst)
        self.assertEqual(created.status_code, 200, created.text)
        did = created.json()['id']
        with Session() as s:
            row = s.get(Decision, did)
            self.assertEqual(row.snapshot['layer']['code'], layer['code'])
            self.assertEqual(row.snapshot['rule']['version'], rule['version'])
            self.assertEqual({r['version'] for r in row.snapshot['referenced_rules']}, {rule['version']})
            self.assertEqual(len(row.snapshot['assignments']), len(groups['A']) + len(groups['B']))
            row.result = calculate(row.snapshot)
            row.state = 'done'
            s.commit()
        result = self.c.get(f'/decisions/{did}', headers=self.analyst).json()['result']
        self.assertEqual(result['assigned_a'], len(groups['A']))
        self.assertEqual(result['assigned_b'], len(groups['B']))
        self.assertEqual(result['reconciliation']['assigned_total'], len(groups['A']) + len(groups['B']))
        self.assertEqual(result['reconciliation']['group_mismatch'], 0)
        self.assertEqual(result['reconciliation']['multi_assignment'], 0)
        self.assertEqual(result['reconciliation']['exposure_without_assignment'], 0)
        self.assertEqual(result['rule_basis']['referenced_versions'], [rule['version']])
        self.assertTrue(result['rule_basis']['rule_checksum'])
        self.assertEqual(result['rule_basis']['allocation_codes'][str(exp['id'])], exp['code'])
        self.assertTrue(result['matured'])
        self.assertTrue(result['release_eligible'], result['block_reasons'])
        self.c.put(f'/decisions/{did}/rationale', json={'rationale': '观察期结束，分流与曝光对账一致，保留原引导。'}, headers=self.analyst)
        pub = self.c.post(f'/decisions/{did}/publish', json={'action': 'keep_a'}, headers=self.admin)
        self.assertEqual(pub.status_code, 200, pub.text)
        again = self.c.post(f'/decisions/{did}/publish', json={'action': 'keep_a'}, headers=self.admin)
        self.assertEqual(again.status_code, 409)

    def test_09_conflict_blocks_publish_until_resolved(self):
        layer, exp, groups = self.populated_experiment('block')
        # 选一个只曝光未激活的用户，用激活事件自报错误组别：事件被拒收但冲突入库
        victim = groups['A'][-1][0]
        stamp = (datetime.now(timezone.utc) + timedelta(hours=-7 * 24 + 12)).astimezone(SHANGHAI).isoformat()
        rejected = self.event(exp['id'], victim, 'activation', stamp, group='B')
        self.assertEqual(rejected.status_code, 409, rejected.text)
        created = self.c.post('/decisions', json={'experiment_id': exp['id']}, headers=self.analyst)
        did = created.json()['id']
        with Session() as s:
            row = s.get(Decision, did)
            row.result = calculate(row.snapshot)
            row.state = 'done'
            s.commit()
        result = self.c.get(f'/decisions/{did}', headers=self.analyst).json()['result']
        self.assertGreaterEqual(result['conflict_count'], 1)
        self.assertEqual(result['reconciliation']['group_mismatch'], 0)  # 被拒收事件不会进入曝光集合
        self.assertFalse(result['release_eligible'])
        self.assertTrue(any('归属冲突' in reason for reason in result['block_reasons']))
        self.c.put(f'/decisions/{did}/rationale', json={'rationale': '先排查归属冲突，再评估是否保留原引导。'}, headers=self.analyst)
        blocked = self.c.post(f'/decisions/{did}/publish', json={'action': 'keep_a'}, headers=self.admin)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn('归属冲突', blocked.json()['detail'])
        open_conflicts = [c for c in self.c.get(f"/conflicts?experiment_id={exp['id']}", headers=self.analyst).json() if c['status'] == 'open']
        for conflict in open_conflicts:
            resolved = self.c.post(f"/conflicts/{conflict['id']}/resolve", json={'resolution': '已核对埋点组别，按资格组修正采集'}, headers=self.analyst)
            self.assertEqual(resolved.status_code, 200, resolved.text)
        # 冲突解除后，旧快照仍保留冲突记录；发布旧裁决要求重新提交以刷新证据
        stale = self.c.post(f'/decisions/{did}/publish', json={'action': 'keep_a'}, headers=self.admin)
        self.assertEqual(stale.status_code, 409)
        self.assertIn('重新提交裁决', stale.json()['detail'])
        new_decision = self.c.post('/decisions', json={'experiment_id': exp['id']}, headers=self.analyst).json()
        with Session() as s:
            row = s.get(Decision, new_decision['id'])
            row.result = calculate(row.snapshot)
            row.state = 'done'
            s.commit()
        fresh = self.c.get(f"/decisions/{new_decision['id']}", headers=self.analyst).json()['result']
        self.assertEqual(fresh['conflict_count'], 0)
        self.assertTrue(fresh['release_eligible'], fresh['block_reasons'])
        # 同一埋点重试：事件仍被拒收（未入库不构成重复），已解除冲突重新打开
        retried = self.event(exp['id'], victim, 'activation', stamp, group='B')
        self.assertEqual(retried.status_code, 409, retried.text)
        reopened = self.c.get(f"/conflicts?experiment_id={exp['id']}", headers=self.analyst).json()
        self.assertTrue(reopened and all(c['status'] == 'open' for c in reopened), reopened)
        self.assertTrue(all(c['resolved_at'] == '' for c in reopened), reopened)

    def test_10_permissions(self):
        layer, exp = self.single_layer()
        self.assertEqual(self.c.post('/assign', json={'layer_code': layer['code'], 'user_key': 'V-1'}, headers=self.viewer).status_code, 403)
        self.assertEqual(self.c.post(f"/layers/{layer['id']}/rules", json={'allocations': [{'experiment_id': exp['id'], 'ratio': 0.5}]}, headers=self.viewer).status_code, 403)
        self.assertEqual(self.c.post(f"/experiments/{exp['id']}/stop", headers=self.analyst).status_code, 403)
        self.assertEqual(self.c.post('/conflicts/1/resolve', json={'resolution': '观察员不能处理'}, headers=self.viewer).status_code, 403)

    def test_11_rule_validation(self):
        layer = self.make_layer()
        exp = self.make_experiment(layer['id'])
        duplicate = self.c.post(f"/layers/{layer['id']}/rules", json={'allocations': [{'experiment_id': exp['id'], 'ratio': 0.7}, {'experiment_id': exp['id'], 'ratio': 0.6}]}, headers=self.analyst)
        self.assertEqual(duplicate.status_code, 422)
        over = self.c.post(f"/layers/{layer['id']}/rules", json={'allocations': [{'experiment_id': exp['id'], 'ratio': 1.0}], 'note': ''}, headers=self.analyst)
        self.assertEqual(over.status_code, 200)
        other_layer = self.make_layer()
        wrong_layer = self.c.post(f"/layers/{other_layer['id']}/rules", json={'allocations': [{'experiment_id': exp['id'], 'ratio': 0.5}]}, headers=self.analyst)
        self.assertEqual(wrong_layer.status_code, 409)
        self.assertIn('不属于流量层', wrong_layer.json()['detail'])
        no_rule = self.c.post('/assign', json={'layer_code': other_layer['code'], 'user_key': 'X-1'}, headers=self.analyst)
        self.assertEqual(no_rule.status_code, 409)
        self.assertIn('尚无生效分流规则', no_rule.json()['detail'])

    def test_12_experiment_protocol_locked_after_assignment(self):
        layer, exp = self.single_layer()
        self.assign(layer['code'], 'LOCK-1')
        update = self.c.put(f"/experiments/{exp['id']}", json={
            'code': exp['code'], 'name': exp['name'], 'starts_at': iso(-48), 'ends_at': iso(48),
            'a_ratio': 0.7, 'hypothesis': exp['hypothesis'], 'layer_id': layer['id']}, headers=self.analyst)
        self.assertEqual(update.status_code, 409)
        self.assertIn('A组占比', update.json()['detail'])
        move = self.c.put(f"/experiments/{exp['id']}", json={
            'code': exp['code'], 'name': exp['name'], 'starts_at': iso(-48), 'ends_at': iso(48),
            'a_ratio': 0.5, 'hypothesis': exp['hypothesis'], 'layer_id': None}, headers=self.analyst)
        self.assertEqual(move.status_code, 409)

    def test_13_unmatched_traffic_stays_unassigned(self):
        layer = self.make_layer()
        exp = self.make_experiment(layer['id'])
        self.make_rule(layer['id'], [{'experiment_id': exp['id'], 'ratio': 0.5}])
        results = [self.assign(layer['code'], f'UNMATCHED-{i}') for i in range(40)]
        unmatched = [r for r in results if r['assignment'] is None]
        self.assertTrue(unmatched, '50% 覆盖应产生未分配流量')
        self.assertTrue(all('未命中' in r['reason'] for r in unmatched))
        self.assertEqual(len({r['assignment']['id'] for r in results if r['assignment']}), len([r for r in results if r['assignment']]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
